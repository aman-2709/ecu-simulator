import asyncio


def build(**kw):
    from ecu_simulator import app
    from ecu_simulator.api.options import ApiOptions
    from ecu_simulator.api.server import ApiServer
    from ecu_simulator.cli import default_profile_path
    from ecu_simulator.config import load_profile

    config = app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0")
    runtime = app.build_runtime(config)
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
