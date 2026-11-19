"""Private manager child. stdout is exclusively its readiness pipe."""

import asyncio
import json
import sys

from ecu_simulator import app
from ecu_simulator.config import load_profile_text


def main() -> int:
    try:
        config = app.RuntimeConfig.build(load_profile_text(sys.stdin.buffer.read().decode("utf-8")))
        asyncio.run(app.run(config, ready=lambda: print(json.dumps({"ready": True}), flush=True)))
    except Exception as error:
        print(json.dumps({"error": str(error) or type(error).__name__}), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
