from http import HTTPStatus

from aiohttp import ClientSession, TCPConnector
from pydantic import BaseModel, Field
from typing import Any

from .client import Client


# retry logic handled under the hood
class ExampleModel(BaseModel):
    field: bool = Field(default=False, description="Example field")


async def main():
    # provide a session is optional, or use the convenience function
    client = Client(
        hotkey="your hotkey",  # used for signing and verification,
        session=ClientSession(
            connector=TCPConnector(
                limit=100
            )  # specify your own session, connector, etc. as needed
        ),
    )

    example_model = ExampleModel()
    # any other kwargs may be propagated to the underlying POST request using aiohttp
    kwargs: dict[str, Any] = {}
    response = await client.send(
        "http://ip:port",
        model=example_model,
        timeout_sec=12,
        max_retries=10,
        max_wait_sec=100,
        **kwargs,
    )

    # check for errors
    if response.error:
        # check for response.error that comes from server side
        print(f"Error received from server: {response.error}")
    if response.exception:
        # check for response.exception that comes from client side
        print(f"Exception occurred {response.exception}")

    if response.client_response and response.client_response.status != HTTPStatus.OK:
        # access fields from ExampleModel directly
        print(f"Received {response.body.field=}")

    # dedicated batch_send method
    # use an asyncio semaphore for concurrency & batch control
    # if semaphore isn't provided, asyncio.gather is called
    responses = await client.batch_send(
        urls=["http://ip:port"] * 5, models=[ExampleModel()] * 5
    )
    for response in responses:
        # access fields as needed...

        # check for errors
        if response.error:
            # check for response.error that comes from server side
            print(f"Error received from server: {response.error}")
        if response.exception:
            # check for response.exception that comes from client side
            print(f"Exception occurred {response.exception}")

        if (
            response.client_response
            and response.client_response.status != HTTPStatus.OK
        ):
            # access fields from ExampleModel directly
            print(f"Received {response.body.field=}")
