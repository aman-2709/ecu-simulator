"""Allow `python -m ecu_simulator` alongside the `ecu-simulator` console script."""
import sys

from ecu_simulator.main import main

if __name__ == "__main__":
    sys.exit(main())
