"""Local profile editor and SocketCAN control API."""

import argparse
import socket
from pathlib import Path
from typing import Any

from aiohttp import web

from ecu_simulator.api.options import allowed_hosts
from ecu_simulator.api.server import FRONTEND_HEADERS
from ecu_simulator.config import ConfigError, load_profile_text
from ecu_simulator.gui.manager import Manager, RunConflict
from ecu_simulator.gui.profiles import Profiles, change_interface
from ecu_simulator.transport.errors import TransportError


async def fields(request: web.Request, *names: str) -> list[str]:
    data = await request.json()
    if not isinstance(data, dict) or not all(isinstance(data.get(name), str) for name in names):
        raise ValueError(f"Expected text fields: {', '.join(names)}")
    return [data[name] for name in names]


@web.middleware
async def local_access(request: web.Request, handler: Any) -> web.StreamResponse:
    assert request.transport
    port = request.transport.get_extra_info("sockname")[1]
    host = request.headers.get("Host", "").lower()
    hostnames = allowed_hosts("127.0.0.1", port)
    if port == 80:
        host = host.removesuffix(":80")
        hostnames = frozenset(name.removesuffix(":80") for name in hostnames)
    if host not in hostnames:
        raise web.HTTPMisdirectedRequest(text="Host must match the local manager")
    if request.method == "POST" and request.headers.get("Origin") != f"http://{host}":
        raise web.HTTPForbidden(text="Origin must match the local manager")
    try:
        return await handler(request)
    except (RunConflict, FileExistsError) as error:
        return web.json_response({"error": str(error)}, status=409)
    except (ValueError, TransportError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except OSError as error:
        return web.json_response({"error": str(error)}, status=404 if isinstance(error, FileNotFoundError) else 500)


def application(directory: Path | None = None) -> web.Application:
    manager, profiles = Manager(), Profiles(directory)
    app = web.Application(middlewares=[local_access])
    routes = web.RouteTableDef()

    @routes.get("/profiles")
    async def catalog(request: web.Request) -> web.Response:
        interfaces = []
        for _, name in socket.if_nameindex():
            try:
                with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as probe:
                    probe.bind((name,))
            except OSError:
                continue
            interfaces.append(name)
        return web.json_response({**profiles.names(), "interfaces": interfaces})

    @routes.get("/profiles/{source}/{name}")
    async def read(request: web.Request) -> web.Response:
        text = profiles.read(request.match_info["source"], request.match_info["name"])
        try:
            interface = load_profile_text(text).transport.interface
        except ConfigError:
            interface = ""
        return web.json_response({"text": text, "interface": interface})

    @routes.post("/profiles")
    async def save(request: web.Request) -> web.Response:
        name, text = await fields(request, "name", "text")
        profiles.save(name, text)
        return web.json_response({"name": name}, status=201)

    @routes.post("/draft/interface")
    async def update_interface(request: web.Request) -> web.Response:
        text, interface = await fields(request, "text", "interface")
        return web.json_response({"text": change_interface(text, interface)})

    @routes.get("/run")
    async def status(request: web.Request) -> web.Response:
        return web.json_response(manager.status(), headers={"Cache-Control": "no-store"})

    @routes.post("/run")
    async def start(request: web.Request) -> web.Response:
        text, = await fields(request, "text")
        return web.json_response(await manager.start(text), status=202)

    @routes.post("/run/send")
    @routes.post("/run/stop")
    async def missing_id(request: web.Request) -> web.Response:
        raise ValueError("A run ID is required")

    @routes.post("/run/{id}/send")
    async def send(request: web.Request) -> web.Response:
        endpoint, payload = await fields(request, "endpoint", "payload")
        return web.json_response(await manager.send(request.match_info["id"], endpoint, payload))

    @routes.post("/run/{id}/stop")
    async def stop(request: web.Request) -> web.Response:
        return web.json_response(await manager.stop(request.match_info["id"]))

    async def close(app: web.Application) -> None:
        await manager.close()

    app.add_routes(routes)
    static = Path(__file__).with_name("static")
    for route, filename in (("/", "control.html"), ("/control.js", "control.js"), ("/control.css", "control.css")):
        async def asset(request: web.Request, path: Path = static / filename) -> web.FileResponse:
            return web.FileResponse(path, headers=FRONTEND_HEADERS)
        app.router.add_get(route, asset)
    app.on_cleanup.append(close)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Local SocketCAN simulator controls")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    web.run_app(application(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
