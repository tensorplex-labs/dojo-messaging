import asyncio
import http
from typing import Any, Sequence

import aiohttp
import orjson
import zstandard as zstd
from aiohttp.client import ClientSession
from kami import KamiClient
from loguru import logger
from orjson import JSONDecodeError
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    RetryError,
    stop_after_attempt,
    wait_exponential,
)

from .utils import retry_log

from .types import (
    HOTKEY_HEADER,
    MESSAGE_HEADER,
    SIGNATURE_HEADER,
    PydanticModel,
    StdResponse,
)
from .utils import encode_body


def get_client(conn_limit: int = None, limit_per_host: int = None) -> ClientSession:  # type: ignore[assignment]
    if not conn_limit:
        conn_limit = 256
    if not limit_per_host:
        limit_per_host = 10

    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            ssl=False,
            limit=conn_limit,
            limit_per_host=limit_per_host,
            enable_cleanup_closed=True,
        )
    )


def _build_url(url: str, model: BaseModel, protocol: str = "http") -> str:
    if not url.startswith("http") and not url.startswith("https"):
        url = f"{protocol}://{url}"

    return f"{url}/{model.__class__.__name__}"


async def _log_context(response: StdResponse[PydanticModel]) -> None:
    if response.exception:
        logger.trace(f"Error due to exception: {response.exception}")
    elif response.error:
        url = ""
        if response.client_response:
            url = f"at URL: {response.client_response.url}"
        logger.error(f"Error from server: {url} {response.error}")
    else:
        client_response = response.client_response
        if client_response and client_response.status != http.HTTPStatus.OK:
            logger.error(
                f"NOT OK, received HTTP status: {client_response.status}, text: {await client_response.text()} error: {response.error if response else ''}, metadata: {response.metadata if response else ''}"
            )


class Client:
    def __init__(
        self,
        hotkey: str,
        session: ClientSession | None = None,
    ) -> None:
        self._kami = KamiClient()
        self._hotkey = hotkey
        self._session: ClientSession = session or get_client()
        self._compression_headers = {
            "content-encoding": "zstd",
            "accept-encoding": "zstd",
        }

    async def _build_headers(
        self,
        include_compression: bool = True,
        message: str = None,  # type: ignore[assignment]
    ) -> dict[str, str]:
        if not message:
            message: str = (
                f"I solemnly swear that I am up to some good. Hotkey: {self._hotkey}"
            )

        signature: str = await self._kami.sign_message(message)
        headers: dict[str, str] = {
            "content-type": "application/json",
            SIGNATURE_HEADER: signature,
            HOTKEY_HEADER: self._hotkey,
            MESSAGE_HEADER: message,
        }
        if include_compression:
            headers.update(self._compression_headers)
        logger.trace(f"Sending request with headers: {headers}")
        return headers

    async def _ensure_session(self):
        """Recreate session if it's closed"""
        if not self._session or self._session.closed:
            self._session = get_client()

    async def batch_send(
        self,
        urls: list[str],
        models: list[PydanticModel],
        semaphore: asyncio.BoundedSemaphore | None = None,
        **kwargs: Any,
    ) -> Sequence[StdResponse[PydanticModel]]:
        """Sends the following payloads to the given URLs concurrently.
        Expects that the endpoint is hosted at:
            http://<url>/<model_name> where model_name is the name of the Pydantic model

        Args:
            urls (list[str]): urls
            models (list[PydanticModel]): models
            keypair (substrateinterface.Keypair): keypair

        Returns:
            list[Response]: Returns both the aiohttp Response, and the model that
                was returned from the server, or the exception if the request failed
        """
        if semaphore is None:
            logger.info("Attempting to batch sending requests without semaphore")
            responses = await asyncio.gather(
                *[self.send(url, model, **kwargs) for url, model in zip(urls, models)],
            )
            for r in responses:
                await _log_context(r)

            return responses

        async def _send_with_semaphore(
            url: str, model: PydanticModel
        ) -> StdResponse[PydanticModel]:
            async with semaphore:
                return await self.send(url, model, **kwargs)

        responses = await asyncio.gather(
            *[_send_with_semaphore(url, model) for url, model in zip(urls, models)],
        )
        for r in responses:
            await _log_context(r)

        return responses

    async def send(
        self,
        url: str,
        model: PydanticModel,
        timeout_sec: int = 10,
        max_retries: int = 2,
        max_wait_sec: int = 4,
        wait_exponential_factor: int = 2,
        enable_preflight: bool = True,
        **kwargs: Any,
    ) -> StdResponse[PydanticModel]:
        """Sends the following payload to the given URL.
        Expects that the endpoint is hosted at:
            http://<url>/<model_name> where model_name is the name of the Pydantic model

        Args:
            url (str): url
            model (PydanticModel): model
            keypair (substrateinterface.Keypair): keypair
            max_retries (int): max number of retries
            max_wait_sec (int): max wait in unit of seconds

        Returns:
            Response: Returns both the aiohttp Response, and the model that
                was returned from the server
        """
        # NOTE: here we set some defaults to AT LEAST retry some
        model_name = model.__class__.__name__
        client_resp: aiohttp.ClientResponse | None = None
        context_msg = f"{url=}, {model_name=}, {max_retries=}, {max_wait_sec=}"
        try:
            await self._ensure_session()
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_retries),
                wait=wait_exponential(
                    multiplier=wait_exponential_factor, max=max_wait_sec
                ),
                before_sleep=retry_log,
            ):
                with attempt:
                    target_url = _build_url(url, model)

                    if enable_preflight:
                        _head_headers = await self._build_headers(
                            include_compression=False
                        )
                        async with self._session.head(
                            target_url,
                            headers=_head_headers,
                            timeout=aiohttp.ClientTimeout(total=timeout_sec),
                        ) as head_resp:
                            head_resp.raise_for_status()
                            logger.debug(f"HEAD preflight successful for {target_url}")

                    _headers = await self._build_headers()
                    payload = encode_body(model, _headers)
                    async with self._session.post(
                        target_url,
                        data=payload,
                        headers=_headers,
                        timeout=aiohttp.ClientTimeout(total=timeout_sec),
                    ) as client_resp:
                        # raise exception so we can retry
                        client_resp.raise_for_status()

                        logger.info(
                            f"Received response with status: {client_resp.status}, {context_msg}"
                        )
                        response_json = {}
                        try:
                            if (
                                client_resp.headers.get("content-encoding", "").lower()
                                == "zstd"
                            ):
                                logger.debug(
                                    f"Attempting zstd decoding for {model_name}"
                                )
                                response_bytes = await client_resp.read()
                                dctx = zstd.ZstdDecompressor()
                                decompressed_bytes = dctx.decompress(response_bytes)
                                response_text = decompressed_bytes.decode()
                                response_json = orjson.loads(response_text)
                                logger.debug(
                                    f"Successfully parsed JSON: {response_json}, type: {type(response_json)}, {context_msg}"
                                )
                            else:
                                response_json = await client_resp.json()
                        except JSONDecodeError as e:
                            logger.error(
                                f"Failed to decode response: {await client_resp.text()}, {context_msg}, exception: {e}"
                            )
                            raise

                        if not response_json:
                            logger.warning("Empty response JSON received")
                            return StdResponse(
                                # NOTE: here we're creating an empty instance
                                body=model.model_construct(),
                                exception=ValueError(
                                    f"Empty response JSON received for {model_name}"
                                ),
                                client_response=client_resp,
                            )

                        try:
                            error: str | None = response_json.get("error", None)
                            metadata: dict[str, Any] = response_json.get("metadata", {})
                            body: dict[str, Any] = response_json.get("body", {})

                            if body:
                                try:
                                    # parse object to the specific model
                                    pydantic_model = model.model_validate(body)
                                    logger.success(
                                        f"Successfully received response, {context_msg}"
                                    )
                                    return StdResponse(
                                        body=pydantic_model,
                                        error=error,
                                        metadata=metadata,
                                        client_response=client_resp,
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"Failed to validate model with body: {e}, returning the raw body"
                                    )
                                    # Return the raw body if validation fails
                                    return StdResponse(
                                        body=model.model_construct(**body),
                                        error=error,
                                        metadata=metadata,
                                        client_response=client_resp,
                                    )
                            else:
                                logger.warning(
                                    "Response body is empty, skipped parsing."
                                )
                                return StdResponse(
                                    body=model.model_construct(),
                                    error=error,
                                    metadata=metadata,
                                    client_response=client_resp,
                                )

                        except JSONDecodeError as e:
                            logger.error(f"Failed to decode response: {e}")
                            raise

            return StdResponse(
                body=model.model_construct(),
                exception=None,
                client_response=client_resp,
            )
        except asyncio.CancelledError:
            logger.warning(f"Request to {url} was cancelled but may still be running")
            # You can choose to return a response or re-raise
            return StdResponse(
                body=model.model_construct(),
                exception=asyncio.CancelledError("Request was cancelled"),
                client_response=client_resp,
            )
        except KeyboardInterrupt:
            logger.warning(f"KeyboardInterrupt during request to {url}")
            return StdResponse(
                body=model.model_construct(),
                exception=KeyboardInterrupt("Request interrupted"),
                client_response=client_resp,
            )

        except RetryError as e:
            logger.trace(
                f"All retries to {url} for {model_name} with {max_retries=}, {max_wait_sec=} were exhausted"
            )
            logger.trace(f"Final exception: {e.last_attempt.exception()}")
            return StdResponse(
                body=model.model_construct(), exception=e, client_response=client_resp
            )
        except (Exception, BaseException) as e:
            return StdResponse(
                body=model.model_construct(), exception=e, client_response=client_resp
            )

    async def close(self):
        try:
            await self._session.close()
        except Exception:
            pass
