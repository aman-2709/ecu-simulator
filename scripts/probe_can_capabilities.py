#!/usr/bin/env python3
"""Report which Linux CAN facilities are available on this host.

Read-only. It never loads modules or configures interfaces; run
scripts/setup_vcan.sh (as root) first if you want vcan0 to exist. Used by CI
to record whether kernel-dependent integration tests can run on the runner.

Exit status is always 0 unless --require is given, in which case it is 1 when
any of the named capabilities is missing.
"""
import argparse
import json
import os
import platform
import shutil
import socket
import sys

CAPABILITIES = (
    "python_can_isotp_constant",
    "vcan_module_loaded",
    "can_isotp_module_loaded",
    "vcan0_interface_present",
    "can_raw_socket_bind",
    "can_isotp_socket_create",
    "can_isotp_socket_bind",
)


def module_loaded(name: str) -> bool:
    return os.path.isdir(f"/sys/module/{name}")


def interface_present(name: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{name}")


def try_socket(family: int, kind: int, proto: int, address=None) -> tuple[bool, str]:
    try:
        sock = socket.socket(family, kind, proto)
    except OSError as error:
        return False, f"socket(): {error}"
    try:
        if address is not None:
            sock.bind(address)
        return True, "ok"
    except OSError as error:
        return False, f"bind(): {error}"
    finally:
        sock.close()


def probe(interface: str) -> dict:
    result: dict[str, object] = {
        "kernel": platform.release(),
        "python": platform.python_version(),
        "interface": interface,
        "can_utils": {
            tool: shutil.which(tool) is not None for tool in ("candump", "cansend", "isotpsend", "isotprecv")
        },
        "capabilities": {},
        "notes": {},
    }
    caps: dict[str, bool] = result["capabilities"]  # type: ignore[assignment]
    notes: dict[str, str] = result["notes"]  # type: ignore[assignment]

    caps["python_can_isotp_constant"] = hasattr(socket, "CAN_ISOTP")
    caps["vcan_module_loaded"] = module_loaded("vcan")
    caps["vcan0_interface_present"] = interface_present(interface)

    ok, note = try_socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW, (interface,))
    caps["can_raw_socket_bind"] = ok
    notes["can_raw_socket_bind"] = note

    if caps["python_can_isotp_constant"]:
        # Creating the socket auto-loads can_isotp through its module alias on kernels
        # that ship it in-tree, so check the module *after* this call as well.
        ok, note = try_socket(socket.AF_CAN, socket.SOCK_DGRAM, socket.CAN_ISOTP)
        caps["can_isotp_socket_create"] = ok
        notes["can_isotp_socket_create"] = note
        ok, note = try_socket(socket.AF_CAN, socket.SOCK_DGRAM, socket.CAN_ISOTP, (interface, 0x7E0, 0x7E8))
        caps["can_isotp_socket_bind"] = ok
        notes["can_isotp_socket_bind"] = note
    else:
        caps["can_isotp_socket_create"] = False
        caps["can_isotp_socket_bind"] = False
    caps["can_isotp_module_loaded"] = module_loaded("can_isotp")
    return result


def as_markdown(result: dict) -> str:
    lines = [
        "### Linux CAN capability probe",
        "",
        f"Kernel `{result['kernel']}`, Python `{result['python']}`, interface `{result['interface']}`",
        "",
        "| Capability | Available | Note |",
        "|---|---|---|",
    ]
    for name in CAPABILITIES:
        available = result["capabilities"].get(name, False)
        note = result["notes"].get(name, "")
        lines.append(f"| `{name}` | {'yes' if available else 'no'} | {note} |")
    tools = ", ".join(f"`{tool}`: {'yes' if present else 'no'}" for tool, present in result["can_utils"].items())
    lines += ["", f"can-utils: {tools}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interface", default="vcan0", help="CAN interface to probe (default: vcan0)")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="CAPABILITY",
        help=f"exit 1 if this capability is missing; may repeat. One of: {', '.join(CAPABILITIES)}",
    )
    args = parser.parse_args(argv)

    unknown = sorted(set(args.require) - set(CAPABILITIES))
    if unknown:
        parser.error(f"unknown capability: {', '.join(unknown)}")

    result = probe(args.interface)
    print(json.dumps(result, indent=2) if args.format == "json" else as_markdown(result))

    missing = [name for name in args.require if not result["capabilities"].get(name)]
    if missing:
        print(f"missing required capabilities: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
