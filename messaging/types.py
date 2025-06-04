import logging
import traceback
from typing import Any, Awaitable, Callable, Generic, TypeAlias, TypeVar

import aiohttp
from fastapi import Request
from loguru import logger
from pydantic import BaseModel, ConfigDict, field_serializer

PydanticModel = TypeVar("PydanticModel", bound=BaseModel)
# define a pydantic model here so that we can apply these to child of BaseModel
ServerHandlerFunc: TypeAlias = Callable[[Request, PydanticModel], Awaitable[Any]]

SIGNATURE_HEADER = "x-signature"
HOTKEY_HEADER = "x-hotkey"
MESSAGE_HEADER = "x-message"


class StdResponse(BaseModel, Generic[PydanticModel]):
    """Standardized response that preserves error and metadata returned from `server.py`"""

    # WARN: extra ignore to ignore extra fields,
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    body: PydanticModel
    error: str | None = None
    metadata: dict[str, Any] = {}
    client_response: aiohttp.ClientResponse | None
    exception: BaseException | None = None

    @field_serializer("client_response")
    def serialize_client_response(
        self, value: aiohttp.ClientResponse | None
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "status": value.status,
            "headers": dict(value.headers),
            "url": str(value.url),
            "host": value.host,
        }

    @field_serializer("exception")
    def serialize_exception(self, value: BaseException | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "type": type(value).__name__,
            "message": str(value),
            "traceback": traceback.format_exception(
                type(value), value, value.__traceback__
            ),
        }


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Use the logging record's information instead of trying to calculate depth
        try:
            logger.patch(
                lambda r: r.update(
                    name=record.name,
                    function=record.funcName,
                    file=record.pathname,
                    line=record.lineno,
                    module=record.module,
                )
            ).log(level, record.getMessage())
        except:  # noqa: E722
            pass
