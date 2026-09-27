"""``--api HOST:PORT``: parsing and the loopback rule (decisions/0010 §6). Standard library only."""

from __future__ import annotations

from dataclasses import dataclass

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class ApiStartupError(Exception):
    """The API cannot start: bad address, missing [gui] extra, busy port or oversized state. Exit status 2."""


@dataclass(frozen=True)
class ApiOptions:
    host: str       # the literal bind address: "127.0.0.1" or "::1"
    port: int       # 0 asks the kernel for a free port (tests)
    profile: str    # reported by GET /status
    version: str    # reported by GET /status


def parse_api(value: str, profile: str, version: str) -> ApiOptions:
    host, sep, port_text = value.rpartition(":")
    if not sep:
        raise ApiStartupError(f"--api expects HOST:PORT, got {value!r}")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host not in LOOPBACK_HOSTS:
        raise ApiStartupError(
            f"--api host must be 127.0.0.1, ::1 or localhost (loopback only, decisions/0010 §6), got {host!r}"
        )
    if not port_text.isdigit() or int(port_text) > 65535:
        raise ApiStartupError(f"--api port must be 0-65535, got {port_text!r}")
    # "localhost" binds IPv4 only, so the bound address and the Host allowlist are exact.
    return ApiOptions("127.0.0.1" if host == "localhost" else host, int(port_text), profile, version)


def allowed_hosts(bind_host: str, port: int) -> frozenset[str]:
    literal = f"[{bind_host}]" if ":" in bind_host else bind_host
    return frozenset({f"{literal}:{port}", f"localhost:{port}"})
