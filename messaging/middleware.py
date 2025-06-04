import http
from typing import Awaitable, Callable

from starlette.types import ASGIApp
import zstandard as zstd
from fastapi import Request, Response
from fastapi.responses import ORJSONResponse
from kami import KamiClient
from loguru import logger
from starlette.concurrency import iterate_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from .types import HOTKEY_HEADER, MESSAGE_HEADER, SIGNATURE_HEADER
from .utils import (
    create_response,
    decode_body,
)

compressor = zstd.ZstdCompressor(level=3)


class SignatureMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        kami: KamiClient,
        whitelisted_routes: list[str] | None = None,
    ):
        super().__init__(app)
        self.kami = kami
        self.whitelisted_routes = whitelisted_routes or []
        # always whitelisted
        if "/docs" not in self.whitelisted_routes:
            self.whitelisted_routes.append("/docs")

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> ORJSONResponse | Response:
        if request.url.path in self.whitelisted_routes:
            return await call_next(request)

        signature = request.headers.get(SIGNATURE_HEADER, "")
        hotkey = request.headers.get(HOTKEY_HEADER, "")
        message = request.headers.get(MESSAGE_HEADER, "")
        if not hotkey or not signature or not message:
            message = f"{http.HTTPStatus(400).phrase}, missing \
                    headers, expected: {SIGNATURE_HEADER}, {HOTKEY_HEADER}, {MESSAGE_HEADER}, \
                    got: {hotkey=}, {signature=}, {message=}"
            return create_response(body={}, status_code=400, error=message)

        if not await self.kami.verify(
            hotkey=hotkey, message=message, signature=signature
        ):
            return create_response(
                body={},
                status_code=403,
                error=f"{http.HTTPStatus(403).phrase} due to invalid signature",
            )

        # otherwise, proceed with the normal request
        response = await call_next(request)
        return response


class ZstdMiddleware(BaseHTTPMiddleware):
    """Middleware that handles zstd compression/decompression for request and response bodies.

    This middleware:
    1. Decompresses incoming request bodies with content-encoding: zstd
    2. Compresses outgoing response bodies when Accept-Encoding includes zstd

    NOTE: The /docs endpoint is excluded from compression/decompression.
    """

    def __init__(self, app: ASGIApp, whitelisted_routes: list[str] | None = None):
        super().__init__(app)
        self.whitelisted_routes = whitelisted_routes or []
        # always whitelisted
        if "/docs" not in self.whitelisted_routes:
            self.whitelisted_routes.append("/docs")

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> ORJSONResponse | Response:
        logger.debug(f"Request headers: {request.headers}")
        if request.url.path in self.whitelisted_routes:
            return await call_next(request)

        # Skip compression/decompression for HEAD requests (no body to process)
        if request.method == "HEAD":
            return await call_next(request)

        encoding = request.headers.get("content-encoding", "").lower()
        if encoding == "zstd":
            decompressed_body = await decode_body(request)
            # NOTE: this probably won't work for streaming
            request._body = decompressed_body  # pyright: ignore[reportPrivateUsage]
            logger.debug("Server decompressed request body")

        # Process the request
        response = await call_next(request)
        logger.debug(f"raw response: {response=}")

        response_body = [section async for section in response.body_iterator]  # type: ignore
        response.body_iterator = iterate_in_threadpool(iter(response_body))  # type: ignore
        bytes_response = response_body[0]  # type: ignore
        logger.debug(f"response_body={bytes_response.decode()}")

        accept_encoding = request.headers.get("accept-encoding", "").lower()
        if response_body and "zstd" in accept_encoding:
            compressed_body = compressor.compress(bytes_response)

            new_response = Response(
                content=compressed_body,
                status_code=response.status_code,
                headers=dict(response.headers),
            )

            new_response.headers["content-encoding"] = "zstd"
            new_response.headers["content-length"] = str(len(compressed_body))

            logger.debug(
                f"Compressed response: original_size={len(bytes_response)}, compressed_size={len(compressed_body)}"
            )
            return new_response
        else:
            if response_body:
                response_content = response_body[0]
                response.headers["content-length"] = str(len(response_content))

            logger.debug(
                f"Not compressing response: accept_encoding={accept_encoding}, has_body={bool(response_body)}, body_size={len(response_body[0]) if response_body else 0}"
            )

        return response
