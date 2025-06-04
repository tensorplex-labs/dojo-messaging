from typing import Any


from loguru import logger
from tenacity import (
    RetryCallState,
)

import zstandard as zstd
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel

from .types import HOTKEY_HEADER, MESSAGE_HEADER, SIGNATURE_HEADER


def create_response(
    body: dict[str, Any],
    status_code: int = 200,
    error: str | None = None,
    metadata: dict[str, Any] = {},
):
    """
    Helper function to create standardized RESTful API responses

    Args:
        body: The response data
        status_code: HTTP status code (default: 200)
        error: Optional error message for error responses
        metadata: Optional metadata like pagination info, request ID, etc.
    """
    content = {"body": jsonable_encoder(body), "error": error, "metadata": {}}  # pyright: ignore

    if metadata:
        content["metadata"] = jsonable_encoder(metadata)

    return ORJSONResponse(content=content, status_code=status_code)


def encode_body(model: BaseModel, headers: dict[str, Any]) -> bytes:
    content_encoding = headers.get("content-encoding")
    if content_encoding:
        if content_encoding.lower() == "zstd":
            json_data = model.model_dump_json().encode()
            compressor = zstd.ZstdCompressor(level=3)
            compressed = compressor.compress(json_data)
            return compressed
        else:
            raise NotImplementedError(
                f"Content encoding of type {content_encoding} is not supported at the moment"
            )

    return model.model_dump_json().encode()


async def decode_body(request: Request) -> bytes:
    """Handle zstd decoding to make transmission over network smaller"""
    body = await request.body()
    if (
        "content-encoding" in request.headers
        and "zstd" in request.headers["content-encoding"]
    ):
        try:
            decompressor = zstd.ZstdDecompressor()
            body = decompressor.decompress(body)
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Failed to decompress zstd data: {str(e)}"
            )

    return body


def extract_headers(request: Request) -> tuple[str, str, str]:
    """Based on the headers, extract the hotkey, message and signature"""
    try:
        headers: dict[str, Any] = {}
        for header, value in request.headers.items():
            if header.startswith("X-"):
                headers[header] = value
        signature = headers.get(SIGNATURE_HEADER, "")
        hotkey = headers.get(HOTKEY_HEADER, "")
        message = headers.get(MESSAGE_HEADER, "")
        return hotkey, message, signature

    except Exception as e:
        logger.warning(f"Failed to extract_headers: {e}")
        return "", "", ""


def retry_log(retry_state: RetryCallState):
    """Custom retry logger that works well with loguru"""
    func_name = getattr(retry_state.fn, "__name__", "<unknown_function>")
    logger.debug(
        f"Retrying {func_name} attempt {retry_state.attempt_number} "
        f"after {retry_state.seconds_since_start:.1f}s due to: {retry_state.outcome.exception() if retry_state.outcome else ''}"
    )
