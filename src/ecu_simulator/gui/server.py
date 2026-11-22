"""Local profile editor and SocketCAN control API."""

import argparse
import asyncio
import gzip
import json
import socket
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from ecu_simulator.api.options import allowed_hosts
from ecu_simulator.api.server import FRONTEND_HEADERS
from ecu_simulator.config import ConfigError, load_profile_text
from ecu_simulator.dbc import DbcStore
from ecu_simulator.gui import dbc_workflow
from ecu_simulator.gui.capture import BATCH_LIMIT, WheelMonitor, open_capture, read_batch
from ecu_simulator.gui.manager import Manager, RunConflict
from ecu_simulator.gui.profiles import Profiles, change_interface
from ecu_simulator.gui.replay import Replay, parse_recording
from ecu_simulator.transport.errors import TransportError


async def fields(request: web.Request, *names: str) -> list[str]:
    data = await request.json()
    if not isinstance(data, dict) or not all(isinstance(data.get(name), str) for name in names):
        raise ValueError(f"Expected text fields: {', '.join(names)}")
    return [data[name] for name in names]


async def json_body(request: web.Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object")
    return body


def install_dbc_routes(routes: web.RouteTableDef, store: DbcStore, tokens: dbc_workflow.ReviewTokens) -> None:
    @routes.post("/dbc/import")
    async def import_file(request: web.Request) -> web.Response:
        filename = request.query.get("filename", "")
        data = await request.read()
        result = await asyncio.to_thread(store.import_file, filename, data)
        return web.json_response(dbc_workflow.browser_numbers(result))

    @routes.get("/dbc/{sha256}")
    async def catalog(request: web.Request) -> web.Response:
        db = await asyncio.to_thread(store.load, request.match_info["sha256"])
        catalog_data = db.catalog(request.query.get("filename", "imported.dbc"))
        return web.json_response(dbc_workflow.browser_numbers(catalog_data))

    @routes.post("/dbc/preview")
    async def render_preview(request: web.Request) -> web.Response:
        data = await json_body(request)
        if not isinstance(data.get("values", {}), dict) or not all(isinstance(data.get(k), str)
                                                                 for k in ("sha256", "name", "text")):
            raise ValueError("Preview requires a DBC, message, signal values and working copy")
        db = await asyncio.to_thread(store.load, data["sha256"])
        raw, expanded = dbc_workflow.preview(db, data["name"], data.get("values", {}), data["text"])
        return web.json_response({"raw": raw, "values": dbc_workflow.browser_numbers(expanded)})

    @routes.post("/dbc/config")
    async def read_config(request: web.Request) -> web.Response:
        data = await json_body(request)
        if not isinstance(data.get("text"), str):
            raise ValueError("Expected a working copy")
        profile = load_profile_text(data["text"])
        settings = dbc_workflow.browser_numbers(profile.dbc.model_dump()) if profile.dbc else None
        return web.json_response({"dbc": settings})

    @routes.post("/dbc/draft")
    async def update_draft(request: web.Request) -> web.Response:
        data = await json_body(request)
        if not isinstance(data.get("text"), str) or "dbc" not in data or not isinstance(data["dbc"], dict | type(None)):
            raise ValueError("Expected a working copy and DBC configuration")
        text = dbc_workflow.changed_draft(data["text"], data["dbc"])
        await asyncio.to_thread(dbc_workflow.validate_dbc, text)
        return web.json_response({"text": text})

    @routes.post("/dbc/review")
    async def review(request: web.Request) -> web.Response:
        data = await json_body(request)
        text = data.get("text")
        if not isinstance(text, str):
            raise ValueError("Expected a profile working copy")
        profile = load_profile_text(text)
        interface = profile.transport.interface
        if not profile.dbc or not profile.dbc.messages or await asyncio.to_thread(dbc_workflow.virtual_can, interface):
            return web.json_response({"required": False, "interface": interface, "messages": [], "conflicts": []})
        await asyncio.to_thread(dbc_workflow.validate_dbc, text)
        index = socket.if_nametoindex(interface)
        db = await asyncio.to_thread(store.load, profile.dbc.sha256)
        messages = []
        for entry in profile.dbc.messages:
            msg = db.message(entry.name)
            raw, values = dbc_workflow.preview(db, entry.name, entry.values, text)
            messages.append({**{k: msg[k] for k in ("id", "extended", "dlc", "name")},
                             "raw": raw, "period_ms": entry.period_ms, "values": dbc_workflow.browser_numbers(values)})
        conflicts = await dbc_workflow.observed_ids(interface, messages)
        return web.json_response({"required": True, "interface": interface, "messages": messages,
                                  "conflicts": conflicts, "observation_ms": dbc_workflow.OBSERVATION_MS,
                                  "token": tokens.issue(text, index)})


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
    store = DbcStore()
    reviews = dbc_workflow.ReviewTokens()
    app = web.Application(middlewares=[local_access], client_max_size=16 * 1024 * 1024)
    routes = web.RouteTableDef()

    install_dbc_routes(routes, store, reviews)
    shutting_down = asyncio.Event()

    replay = Replay()
    replay_lock = asyncio.Lock()

    @routes.get("/replay")
    async def replay_status(request: web.Request) -> web.Response:
        return web.json_response(replay.info)

    @routes.post("/replay")
    async def play_recording(request: web.Request) -> web.Response:
        if replay_lock.locked() or replay.info["state"] == "Playing":
            raise RunConflict("A recording is already loading or playing")
        async with replay_lock:
            name = request.query.get("name", "")[:200]
            samples = {"kenworth-drive": ("2015 Kenworth T660 · drive", "kenworth-t660.candump.gz"),
                       "kenworth": ("2014 Kenworth T270", "kenworth-t270.candump.gz")}
            if request.query.get("sample") in samples:
                name, filename = samples[request.query["sample"]]
                sample = Path(__file__).with_name("samples") / filename
                raw = gzip.decompress(sample.read_bytes())
            else:
                raw = await request.read()
            recording = await asyncio.to_thread(parse_recording, raw)
            return web.json_response(replay.start(recording, name, request.query.get("loop") == "1"))

    @routes.post("/replay/{id}/stop")
    async def stop_recording(request: web.Request) -> web.Response:
        return web.json_response(await replay.stop(request.match_info["id"]))

    @routes.post("/capture")
    async def capture(request: web.Request) -> web.StreamResponse:
        settings = await json_body(request)
        interface = settings.get("interface")
        selected = settings.get("sha256")
        if not isinstance(interface, str) or (selected is not None and not isinstance(selected, str)):
            raise ValueError("Select a CAN interface and a valid DBC")
        current_hash = selected or None
        db = await asyncio.to_thread(store.load, current_hash) if current_hash else None
        previous: dict[tuple[int, bool], int] = {}
        monitor = WheelMonitor(interface)
        with open_capture(interface) as sock:
            response = web.StreamResponse(headers={
                "Content-Type": "application/x-ndjson", "Cache-Control": "no-store",
            })
            await response.prepare(request)
            try:
                await response.write(b'{"ready":true}\n')
                while not shutting_down.is_set() and request.transport and not request.transport.is_closing():
                    frames, dropped = read_batch(sock)
                    run = manager.status()
                    active = run.get("dbc") if run["state"] == "Running" and run["interface"] == interface else None
                    wanted = selected or (active and active["sha256"])
                    if wanted != current_hash:
                        current_hash = wanted
                        db = await asyncio.to_thread(store.load, wanted) if wanted else None
                        previous.clear()
                    if db:
                        dbc_workflow.decode_batch(db, frames, previous)
                    wheel = monitor.observe(run, frames, time.time_ns() // 1000)
                    if frames or wheel is not None:
                        batch = json.dumps({"frames": frames, "dropped": dropped, "wheel": wheel,
                                            "loop_boundary_us": replay.loop_boundary_us if interface == "vcan0" else 0},
                                           separators=(",", ":"))
                        await asyncio.wait_for(response.write((batch + "\n").encode()), timeout=3)
                    # Drain a busy socket promptly, yielding between bounded batches.
                    await asyncio.sleep(0 if len(frames) == BATCH_LIMIT else 0.05)
            except (ConnectionError, TimeoutError):
                pass
            except OSError as error:
                await response.write((json.dumps({"error": str(error)}) + "\n").encode())
            return response

    async def end_captures(app: web.Application) -> None:
        shutting_down.set()

    app.on_shutdown.append(end_captures)

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
        return web.json_response({**profiles.names(), "interfaces": interfaces, "dbc_import": True, "replay": True,
                                  "kenworth_drive": True})

    @routes.get("/profiles/{source}/{name}")
    async def read(request: web.Request) -> web.Response:
        text = profiles.read(request.match_info["source"], request.match_info["name"])
        try:
            profile = load_profile_text(text)
            interface = profile.transport.interface
            dbc = dbc_workflow.browser_numbers(profile.dbc.model_dump()) if profile.dbc else None
        except ConfigError:
            interface, dbc = "", None
        return web.json_response({"text": text, "interface": interface, "dbc": dbc})

    @routes.post("/profiles")
    async def save(request: web.Request) -> web.Response:
        name, text = await fields(request, "name", "text")
        await asyncio.to_thread(dbc_workflow.validate_dbc, text)
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
        data = await json_body(request)
        text = data.get("text")
        if not isinstance(text, str):
            raise ValueError("Expected a working copy")
        profile = load_profile_text(text)
        iface = profile.transport.interface
        if profile.dbc and profile.dbc.messages and not await asyncio.to_thread(dbc_workflow.virtual_can, iface):
            token = data.get("review")
            reviews.consume(token if isinstance(token, str) else "", text, socket.if_nametoindex(iface))
        return web.json_response(await manager.start(text), status=202)

    @routes.post("/run/send")
    @routes.post("/run/stop")
    async def missing_id(request: web.Request) -> web.Response:
        raise ValueError("A run ID is required")

    @routes.post("/run/{id}/broadcast")
    async def broadcast(request: web.Request) -> web.Response:
        data = await request.json()
        if not isinstance(data, dict) or type(data.get("paused")) is not bool:
            raise ValueError("Expected a boolean paused field")
        return web.json_response(await manager.broadcast(request.match_info["id"], data["paused"]))

    @routes.post("/run/{id}/send")
    async def send(request: web.Request) -> web.Response:
        endpoint, payload = await fields(request, "endpoint", "payload")
        return web.json_response(await manager.send(request.match_info["id"], endpoint, payload))

    @routes.post("/run/{id}/stop")
    async def stop(request: web.Request) -> web.Response:
        return web.json_response(await manager.stop(request.match_info["id"]))

    async def close(app: web.Application) -> None:
        if "id" in replay.info:
            await replay.stop(replay.info["id"])
        await manager.close()

    app.add_routes(routes)
    static = Path(__file__).with_name("static")
    for route, filename in (("/", "control.html"), ("/control.js", "control.js"), ("/control.css", "control.css"),
                            ("/live.js", "live.js"), ("/dbc.js", "dbc.js")):
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
