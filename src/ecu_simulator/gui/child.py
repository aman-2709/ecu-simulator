"""Private GUI child. stdin/stdout carry newline-delimited JSON commands and events."""

import asyncio
import json
import sys
from contextlib import suppress
from typing import Any

from ecu_simulator import app
from ecu_simulator.config import load_profile_text


async def serve(text: str) -> None:
    config = app.RuntimeConfig.build(load_profile_text(text))
    codec = None
    if config.profile.restbus is not None:
        from ecu_simulator.restbus import WheelCodec

        codec = WheelCodec()
    paused = False
    stop = asyncio.Event()

    dbc_metadata: dict[str, Any] = {}

    def report_ready() -> None:
        event = {"ready": True, "restbus": codec.metadata() if codec else None, "dbc": dbc_metadata or None}
        print(json.dumps(event), flush=True)

    reader = asyncio.StreamReader()
    loop = asyncio.get_running_loop()
    pipe, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    async def commands() -> None:
        nonlocal paused
        try:
            while line := await reader.readline():
                command = json.loads(line)
                if codec is None or type(command.get("paused")) is not bool:
                    raise ValueError("Invalid wheel-speed publisher command")
                paused = command["paused"]
                print(json.dumps({"paused": paused, "sequence": command["sequence"]}), flush=True)
        finally:
            stop.set()

    listener = asyncio.create_task(commands())
    try:
        await app.run(
            config, stop=stop, ready=report_ready, wheel_codec=codec,
            dbc_ready=dbc_metadata.update, broadcast_paused=lambda: paused,
        )
    finally:
        listener.cancel()
        pipe.close()
        with suppress(asyncio.CancelledError):
            await listener


def main() -> int:
    try:
        text = json.loads(sys.stdin.buffer.readline())["profile"]
        asyncio.run(serve(text))
    except Exception as error:
        print(json.dumps({"error": str(error) or type(error).__name__}), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
