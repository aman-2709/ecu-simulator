"""Import isolation between the transport and the domain (plan rules 1 to 3).

* ``transport/`` must not import ECU, protocol, config, state or logging-context modules.
* ``ecu/`` and ``protocols/`` must not import a transport implementation or a socket
  library; the only transport module they may see is the addressing-only ``messages``.
"""

import subprocess
import sys

DOMAIN_PREFIXES = (
    "ecu_simulator.obd",
    "ecu_simulator.uds",
    "ecu_simulator.ecu",
    "ecu_simulator.protocols",
    "ecu_simulator.logging",
    "ecu_simulator.ecu_config",
    "ecu_simulator.addresses",
    "ecu_simulator.dtc_utils",
    "ecu_simulator.loggers",
    "ecu_simulator.app",
    "ecu_simulator.cli",
    "ecu_simulator.main",
)

# The kernel ISO-TP implementation and its binding. (Stdlib ``socket`` is not a usable
# signal: ``logging.handlers``, imported by the legacy logger, loads it.)
TRANSPORT_IMPLEMENTATION_PREFIXES = (
    "ecu_simulator.transport.socketcan",
    "isotp",
)


def loaded_modules(*imports: str) -> list[str]:
    probe = "import sys\n" + "".join(f"import {m}\n" for m in imports)
    probe += 'print(",".join(sorted(sys.modules)))\n'
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    return result.stdout.strip().split(",")


def test_transport_imports_no_domain_modules():
    loaded = loaded_modules(
        "ecu_simulator.transport", "ecu_simulator.transport.socketcan", "ecu_simulator.transport.socketcan.transport"
    )
    leaked = [m for m in loaded if m.startswith(DOMAIN_PREFIXES)]
    assert leaked == [], f"transport imported domain modules: {leaked}"
    assert "ecu_simulator.transport.socketcan.transport" in loaded


def test_ecu_and_protocols_import_no_transport_implementation():
    loaded = loaded_modules(
        "ecu_simulator.ecu",
        "ecu_simulator.ecu.dispatcher",
        "ecu_simulator.protocols",
        "ecu_simulator.protocols.obd",
        "ecu_simulator.protocols.uds",
    )
    leaked = [m for m in loaded if m.startswith(TRANSPORT_IMPLEMENTATION_PREFIXES)]
    assert leaked == [], f"ecu/protocols imported transport implementation modules: {leaked}"
    assert "ecu_simulator.transport.messages" in loaded
