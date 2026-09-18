"""The transport package must not import ECU, protocol, config or state modules."""

import subprocess
import sys

FORBIDDEN_PREFIXES = (
    "ecu_simulator.obd",
    "ecu_simulator.uds",
    "ecu_simulator.ecu_config",
    "ecu_simulator.addresses",
    "ecu_simulator.dtc_utils",
    "ecu_simulator.loggers",
    "ecu_simulator.app",
    "ecu_simulator.cli",
    "ecu_simulator.main",
)

PROBE = """
import sys
import ecu_simulator.transport
import ecu_simulator.transport.socketcan
import ecu_simulator.transport.socketcan.transport
print(",".join(sorted(m for m in sys.modules if m.startswith("ecu_simulator"))))
"""


def test_transport_imports_no_domain_modules():
    result = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, check=True)
    loaded = result.stdout.strip().split(",")
    leaked = [m for m in loaded if m.startswith(FORBIDDEN_PREFIXES)]
    assert leaked == [], f"transport imported domain modules: {leaked}"
    assert "ecu_simulator.transport.socketcan.transport" in loaded
