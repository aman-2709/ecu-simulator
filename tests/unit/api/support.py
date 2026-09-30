import asyncio


def build(*, prepare=None, **kw):
    from ecu_simulator import app
    from ecu_simulator.api.options import ApiOptions
    from ecu_simulator.api.server import ApiServer
    from ecu_simulator.cli import default_profile_path
    from ecu_simulator.config import load_profile

    config = app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0")
    runtime = app.build_runtime(config)
    if prepare is not None:
        prepare(runtime)                    # before ApiServer: what it sees at construction
    options = ApiOptions("127.0.0.1", 0, profile="profiles/ice_default.yaml", version="test")
    return ApiServer(runtime, app.build_endpoints(config), options, **kw)


def url(server, path):
    return f"http://127.0.0.1:{server.port}{path}"


async def raw_request(port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = await reader.read(65536)
    writer.close()
    await writer.wait_closed()
    return data


UNKNOWN_ALLOWED_CODES = ("1013", "1006")   # 0010 P5(h): forced, or reset/vanished


def check_delivery_unknown_allowance(stats) -> dict[str, int]:
    """Assert the 0010 P5(h) rule and return the delivery_unknown counts per close code.

    A connection closed 1013 or 1006 may carry at most one delivery_unknown; every other
    connection, open or closed, none. Tests assert the returned counts explicitly.
    """
    totals = stats["closed_totals"]
    by_code = totals["delivery_unknown_by_close_code"]
    assert stats["closed_unresolved"] == 0, "a closed connection still has a send in flight"
    assert totals["delivery_unknown"] == sum(by_code.values()), by_code
    assert totals["delivery_unknown_over_allowance"] == 0, "a closed connection exceeded its allowance"
    for code, unknown in by_code.items():
        allowed = totals["close_codes"].get(code, 0) if code in UNKNOWN_ALLOWED_CODES else 0
        assert unknown <= allowed, f"close code {code}: delivery_unknown {unknown} > allowed {allowed}"
    for ledger in stats["connections"] + stats["closed_connections"]:
        limit = 1 if str(ledger["close_code"]) in UNKNOWN_ALLOWED_CODES else 0
        assert ledger["delivery_unknown"] <= limit, ledger
    return dict(by_code)
