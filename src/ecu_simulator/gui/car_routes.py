"""The Car panel's HTTP routes: choose a car, check it, upload a rule file or a log, read the link, review and start.

Every route that parses, opens a DBC or runs ``ip`` does it in a worker thread. A rule file is the one upload that can
occupy a thread for seconds (the parser is pure Python and holds the GIL), so uploads are read with a cap, refused
before parsing when too large, and parsed one at a time.
"""

import asyncio
import dataclasses
import socket
from typing import Any

from aiohttp import web
from pydantic import TypeAdapter, ValidationError

from ecu_simulator.car.busload import read_link, read_link_stats
from ecu_simulator.car.rules import MAX_RULE_BYTES, RuleError, RuleStore
from ecu_simulator.config import load_profile_text
from ecu_simulator.config.schema import PlainFileName
from ecu_simulator.dbc import DbcStore, ImportedDb
from ecu_simulator.gui import car_workflow, dbc_workflow
from ecu_simulator.gui.manager import Manager

MAX_UNMATCHED = 50
# A car working copy is a few kilobytes (the demo is under 1 KiB; 2000 period overrides are 89 KB). The draft's ruamel
# round trip costs about 2.6 s per MiB on the development machine (10 MB took 51 s), so 256 KiB bounds one request to
# well under a second of a worker thread.
MAX_WORKING_COPY_BYTES = 256 * 1024
MAX_ECHOED = 100
READ_CHUNK = 64 * 1024
_FILE_NAME = TypeAdapter(PlainFileName)
REVIEW_NOTE = (
    "This car is generated from its DBC, not a recording. Each message below shows one stamped sample, the first "
    "frame the run will send; from there counters advance and signal values follow the simulated drive."
)


async def json_object(request: web.Request) -> dict[str, Any]:
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object")
    return body


async def working_copy(request: web.Request) -> tuple[str, dict[str, Any]]:
    """The body's working copy, at most ``MAX_WORKING_COPY_BYTES`` of UTF-8, and the body's other fields."""
    body = await json_object(request)
    text = body.pop("text", None)
    if not isinstance(text, str):
        raise ValueError("Expected a profile working copy")
    size = len(text.encode("utf-8", "surrogatepass"))
    if size > MAX_WORKING_COPY_BYTES:
        raise ValueError(
            f"The working copy is {size} bytes; the Car panel takes at most {MAX_WORKING_COPY_BYTES} "
            f"({MAX_WORKING_COPY_BYTES // 1024} KiB)"
        )
    return text, body


def display_name(value: str) -> str:
    """``value`` if a profile would accept it as ``car.filename``, else a ``ValueError`` with the reason, cut short.

    A name is display text. It is checked so that a path or control characters never travel as one, and it is not
    used to build a path.
    """
    try:
        return _FILE_NAME.validate_python(value)
    except ValidationError as error:
        reason = error.errors(include_url=False, include_context=False, include_input=False)[0]["msg"]
        shown = reason if len(reason) <= MAX_ECHOED else reason[:MAX_ECHOED] + "..."
        raise ValueError(f"filename: {shown}") from None


async def read_limited(request: web.Request, limit: int) -> bytes:
    """The request body, or ``RuleError`` as soon as it is known to be over ``limit`` bytes.

    The stream is read in chunks and abandoned at the limit, so a body of any declared or chunked size costs at most
    ``limit`` plus one chunk. A ``Content-Length`` over the limit is refused before anything is read.
    """
    refusal = RuleError("", f"rule file is over the {limit}-byte ({limit // 1024} KiB) limit")
    if request.content_length is not None and request.content_length > limit:
        raise refusal
    body = bytearray()
    async for chunk in request.content.iter_chunked(READ_CHUNK):
        body += chunk
        if len(body) > limit:
            raise refusal
    return bytes(body)


def link_view(interface: str) -> dict[str, Any]:
    """The link as ``ip`` reports it, with its error counters. Two reads: the counters come from ``ip -s``."""
    link = dataclasses.asdict(read_link(interface))
    return {**link, **read_link_stats(interface)}


async def stored_dbc(store: DbcStore, sha256: str) -> ImportedDb:
    try:
        return await asyncio.to_thread(store.load, sha256)
    except (ValueError, OSError) as error:
        raise ValueError(f"car DBC {sha256}: {error}") from error


def install_car_routes(
    routes: web.RouteTableDef, store: DbcStore, rules: RuleStore, reviews: dbc_workflow.ReviewTokens, manager: Manager
) -> None:
    rule_parsing = asyncio.Lock()

    @routes.get("/car/catalog")
    async def catalog(request: web.Request) -> web.Response:
        return web.json_response(car_workflow.catalog_view())

    @routes.post("/car/draft")
    async def draft(request: web.Request) -> web.Response:
        text, selection = await working_copy(request)
        drafted = await asyncio.to_thread(car_workflow.car_draft, text, selection)
        if isinstance(selection.get("sha256"), str):
            await stored_dbc(store, selection["sha256"])
        return web.json_response({"text": drafted})

    @routes.post("/car/check")
    async def check(request: web.Request) -> web.Response:
        text, _ = await working_copy(request)
        return web.json_response(await asyncio.to_thread(car_workflow.check, text))

    @routes.post("/car/rule")
    async def upload_rule(request: web.Request) -> web.Response:
        for name in request.query.getall("filename", []):
            display_name(name)
        body = await read_limited(request, MAX_RULE_BYTES)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise RuleError("", "rule file is not valid UTF-8 text") from None
        async with rule_parsing:
            digest, parsed = await asyncio.to_thread(rules.import_parsed, text)
        return web.json_response({"sha256": digest, "rules": len(parsed.rules)})

    @routes.post("/car/log")
    async def measure_log(request: web.Request) -> web.Response:
        db = await stored_dbc(store, request.query.get("sha256", ""))
        raw = await request.read()  # limited to the server's client_max_size while it is read, as the replay route is
        measured = await asyncio.to_thread(car_workflow.measure_log, db, raw)
        shown = measured.unmatched[:MAX_UNMATCHED]
        return web.json_response({
            "periods": measured.periods, "matched": len(measured.periods), "clamped": list(measured.clamped),
            "unmatched_ids": shown, "unmatched_more": len(measured.unmatched) - len(shown),
        })

    @routes.get("/car/link")
    async def link(request: web.Request) -> web.Response:
        view = await asyncio.to_thread(link_view, request.query.get("interface", ""))
        return web.json_response(view, headers={"Cache-Control": "no-store"})

    @routes.post("/car/review")
    async def review(request: web.Request) -> web.Response:
        text, _ = await working_copy(request)
        profile = await asyncio.to_thread(load_profile_text, text)
        interface = profile.transport.interface
        if profile.car is None or await asyncio.to_thread(dbc_workflow.virtual_can, interface):
            return web.json_response({"required": False, "interface": interface, "messages": [], "conflicts": []})
        messages = await asyncio.to_thread(car_workflow.transmittable, text)
        index = socket.if_nametoindex(interface)
        conflicts = await dbc_workflow.observed_ids(interface, messages)
        return web.json_response({
            "required": True, "interface": interface, "messages": messages, "conflicts": conflicts,
            "observation_ms": dbc_workflow.OBSERVATION_MS, "note": REVIEW_NOTE, "token": reviews.issue(text, index),
        })

    @routes.get("/car/run")
    async def current(request: web.Request) -> web.Response:
        status = manager.status()
        if status["state"] != "Running" or not status["car"]:
            return web.json_response({"error": "No car run is running"}, status=404)
        return web.json_response(status["car"], headers={"Cache-Control": "no-store"})
