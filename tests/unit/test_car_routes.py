"""The Car panel's routes over ``server.application``: choose, check, upload, review and start.

A real aiohttp server and client talk over loopback. The simulator child is a stand-in process that prints the ready
line a car run prints, and the interface checks are patched, so nothing here needs a SocketCAN interface.
"""

import asyncio
import builtins
import functools
import hashlib
import json
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("aiohttp")
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from ecu_simulator.car import busload
from ecu_simulator.car.busload import LinkInfo
from ecu_simulator.car.catalog import load_catalog
from ecu_simulator.car.rules import RuleStore
from ecu_simulator.cli import default_profile_path
from ecu_simulator.dbc import DbcStore
from ecu_simulator.gui import car_routes, car_workflow, dbc_workflow, replay, server
from ecu_simulator.gui.manager import Manager
from tests.unit.test_car_run import synthetic_dbc
from tests.unit.test_car_workflow import LOG_DBC, synthetic_log

VCAN = LinkInfo("vcan0", True, None, None)
RULE_FILE = "version: 1\nrules:\n  - messages: [SPEED]\n    checksum: {signal: CHECKSUM, algorithm: xor}\n"
SIMULATOR = (
    "import json, sys, time\n"
    "sys.stdin.readline()\n"
    "print(json.dumps({'ready': True, 'car': json.loads(sys.argv[1])}), flush=True)\n"
    "time.sleep(60)\n"
)


def demo_text(interface: str = "vcan0") -> str:
    return default_profile_path().with_name("car_toyota_demo.yaml").read_text().replace("vcan0", interface)


class Page:
    """The browser's side: every POST carries the Origin the manager insists on."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.origin = str(client.make_url("/")).rstrip("/")

    async def get(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        response = await self.client.get(path, **kwargs)
        return response.status, await response.json()

    async def post(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        response = await self.client.post(path, headers={"Origin": self.origin}, **kwargs)
        return response.status, await response.json()


@asynccontextmanager
async def serving(home: Path, *, watch: Any = None):
    app = server.application(home / "profiles")
    if watch is not None:
        app.middlewares.append(watch)
    async with TestClient(TestServer(app)) as client:
        yield Page(client)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(car_workflow, "read_link", lambda interface: VCAN)
    return tmp_path


@functools.cache
def reported_car() -> str:
    """The car metadata a simulator child reports in its ready line, as JSON."""
    car = car_workflow.check(demo_text(), VCAN)
    return json.dumps({key: car[key] for key in ("platform", "dbc", "revision", "rule", "bus_load", "messages")})


def simulator(monkeypatch, car: str | None = None) -> None:
    command = (sys.executable, "-c", SIMULATOR, car or reported_car())
    monkeypatch.setattr(server, "Manager", lambda: Manager(child_command=command))


def bench(monkeypatch, *, virtual: bool, conflicts: list[str] | None = None) -> list[tuple[str, list[dict]]]:
    """Decide virtual or physical for every interface, and observe nothing on the bus."""
    observed: list[tuple[str, list[dict]]] = []

    async def nothing_seen(interface: str, messages: list[dict]) -> list[str]:
        observed.append((interface, messages))
        return conflicts or []

    monkeypatch.setattr(dbc_workflow, "virtual_can", lambda interface: virtual)
    monkeypatch.setattr(dbc_workflow, "observed_ids", nothing_seen)
    return observed


async def running(page: Page) -> dict[str, Any]:
    async with asyncio.timeout(10):
        while (body := (await page.get("/run"))[1])["state"] != "Running":
            assert body["state"] in {"Starting", "Running"}, body
            await asyncio.sleep(0.05)
    return body


def upload_dbc(messages: list[tuple[str, int, int, bool, int | None, bool]]) -> tuple[str, bytes]:
    raw = synthetic_dbc(messages)
    DbcStore().import_file("bench.dbc", raw)
    return hashlib.sha256(raw).hexdigest(), raw


# --- catalog, draft, check --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_route(home):
    async with serving(home) as page:
        status, body = await page.get("/car/catalog")
    assert status == 200
    assert body["revision"] == load_catalog().revision and list(body["brands"]) == ["honda", "hyundai", "toyota"]
    assert any(p["id"] == "TOYOTA_COROLLA" and p["buses"] == ["pt", "radar"] for p in body["brands"]["toyota"])


@pytest.mark.asyncio
async def test_draft_then_check_round_trip(home):
    base = default_profile_path().read_text()
    async with serving(home) as page:
        status, drafted = await page.post("/car/draft", json={
            "text": base, "platform": "TOYOTA_COROLLA", "bus": "pt", "periods": {"WHEEL_SPEEDS": 30},
        })
        assert status == 200 and "TOYOTA_COROLLA" in drafted["text"]
        status, checked = await page.post("/car/check", json={"text": drafted["text"]})
    assert status == 200
    assert checked["platform"] == "TOYOTA_COROLLA" and checked["rule"] == "toyota"
    assert sum(checked["counts"].values()) == len(checked["messages"]) > 20
    wheels = next(m for m in checked["messages"] if m["name"] == "WHEEL_SPEEDS")
    assert (wheels["period_ms"], wheels["period_source"]) == (30, "override")
    assert checked["bus_load"]["assumed"] is True and checked["warnings"] == []


@pytest.mark.asyncio
async def test_check_of_an_interface_that_does_not_exist_says_why_and_lists_the_messages(home, monkeypatch):
    monkeypatch.setattr(car_workflow, "read_link", busload.read_link)  # ip says the device is missing, or is not there
    async with serving(home) as page:
        status, body = await page.post("/car/check", json={"text": demo_text("nosuch0")})
    assert status == 200
    assert "nosuch0" in body["bus_load"]["unavailable"] and body["bus_load"]["estimate"] is None
    assert body["messages"] and body["bus_load"]["refused"] is None


@pytest.mark.asyncio
async def test_check_warns_about_a_misspelled_period_name(home):
    sha, _ = upload_dbc([("SPEED", 0x100, 8, False, 20, False)])
    async with serving(home) as page:
        _, drafted = await page.post("/car/draft", json={
            "text": default_profile_path().read_text(), "sha256": sha, "filename": "bench.dbc",
            "periods": {"SPEEED": 40}, "log_periods": {"OLD_LOG": 50},
        })
        status, body = await page.post("/car/check", json={"text": drafted["text"]})
    assert status == 200
    assert body["warnings"] == ["periods: 'SPEEED' is not a message of this DBC, so its override is ignored"]


@pytest.mark.asyncio
async def test_draft_keeps_the_comments_of_the_working_copy(home):
    text = default_profile_path().read_text() + "\n# my bench notes\n"
    async with serving(home) as page:
        _, body = await page.post("/car/draft", json={"text": text, "platform": "HONDA_CIVIC"})
    assert "# my bench notes" in body["text"] and body["text"].startswith(text.splitlines()[0])


@pytest.mark.asyncio
async def test_draft_of_an_unknown_dbc_is_a_400_naming_its_hash(home):
    digest = "ab" * 32
    async with serving(home) as page:
        status, body = await page.post("/car/draft", json={
            "text": default_profile_path().read_text(), "sha256": digest, "filename": "bench.dbc",
        })
    assert status == 400 and digest in body["error"]


@pytest.mark.asyncio
async def test_draft_with_no_working_copy_is_a_400(home):
    async with serving(home) as page:
        assert (await page.post("/car/draft", json={"platform": "TOYOTA_COROLLA"}))[0] == 400
        assert (await page.post("/car/draft", json={"text": 5}))[0] == 400
        assert (await page.post("/car/check", json={"text": default_profile_path().read_text()}))[0] == 400


@pytest.mark.asyncio
async def test_a_car_filename_is_display_text_and_never_reaches_the_filesystem(home, monkeypatch):
    sha, _ = upload_dbc([("A", 0x100, 8, False, 20, False)])
    touched: list[str] = []
    real_read_bytes, real_open = Path.read_bytes, builtins.open

    def note(path: object) -> None:
        parts = Path(str(path)).parts
        if ".." in parts or "x" in parts:
            touched.append(str(path))

    def read_bytes(self: Path) -> bytes:
        note(self)
        return real_read_bytes(self)

    def open_(file: Any, *args: Any, **kwargs: Any) -> Any:
        note(file)
        return real_open(file, *args, **kwargs)

    async with serving(home) as page:
        monkeypatch.setattr(Path, "read_bytes", read_bytes)
        monkeypatch.setattr(builtins, "open", open_)
        status, body = await page.post("/car/draft", json={
            "text": default_profile_path().read_text(), "sha256": sha, "filename": "../x",
        })
        assert status == 400 and "path" in body["error"]
        _, drafted = await page.post("/car/draft", json={
            "text": default_profile_path().read_text(), "sha256": sha, "filename": "..",
        })
        status, checked = await page.post("/car/check", json={"text": drafted["text"]})
        assert status == 200 and checked["dbc"] == {"filename": "..", "sha256": sha, "name": None}
        hostile = demo_text().replace("platform: TOYOTA_COROLLA\n  bus: pt", f"sha256: {sha}\n  filename: ../x")
        status, body = await page.post("/car/check", json={"text": hostile})
        assert status == 400 and "path" in body["error"]
    assert touched == []


# --- rule upload ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_upload_stores_a_valid_file_by_digest(home):
    async with serving(home) as page:
        status, body = await page.post("/car/rule", params={"filename": "mine.yaml"}, data=RULE_FILE.encode())
    digest = hashlib.sha256(RULE_FILE.encode()).hexdigest()
    assert (status, body) == (200, {"sha256": digest, "rules": 1})
    assert (home / "ecu-simulator" / "rules" / f"{digest}.yaml").read_text() == RULE_FILE


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "refusal"), [
    pytest.param(b"version: 1\nrules: &r []\nagain: *r\n", "anchor", id="anchor-and-alias"),
    pytest.param(b"version: 1\nrules: !!python/object/apply:os.system [x]\n", "tag", id="tag"),
    pytest.param(b"#" * (300 * 1024), "256 KiB", id="300KiB"),
    pytest.param(b"\xff\xfe not text", "UTF-8", id="not-utf8"),
    pytest.param(b"version: 2\nrules: []\n", "version", id="wrong-version"),
    pytest.param(b"", "empty", id="empty"),
])
async def test_rule_upload_rejects_hostile_file_with_400(home, body, refusal):
    async with serving(home) as page:
        status, error = await page.post("/car/rule", data=body)
    assert status == 400 and refusal in error["error"]
    assert not (home / "ecu-simulator" / "rules").exists()  # a refused file is never written, nor its directory


@pytest.mark.asyncio
async def test_a_rule_body_over_the_limit_is_cut_off_while_it_streams(home):
    seen: list[int] = []

    @web.middleware
    async def watch(request: web.Request, handler: Any) -> web.StreamResponse:
        try:
            return await handler(request)
        finally:
            seen.append(request.content.total_bytes)

    async def ten_mebibytes():
        for _ in range(160):
            yield b"x" * 65536
            await asyncio.sleep(0)

    async with serving(home, watch=watch) as page:
        status, error = await page.post("/car/rule", data=ten_mebibytes())
    assert status == 400 and "256 KiB" in error["error"]
    assert len(seen) == 1 and seen[0] < 2 * 1024 * 1024, f"the server read {seen[0]} of 10485760 bytes"


@pytest.mark.asyncio
async def test_a_rule_body_that_declares_its_size_is_refused_before_it_is_read(home):
    async with serving(home) as page:
        status, error = await page.post("/car/rule", data=b"#" * (257 * 1024))
    assert status == 400 and "256 KiB" in error["error"]


@pytest.mark.asyncio
async def test_two_rule_uploads_are_processed_one_at_a_time(home, monkeypatch):
    active, most, calls = 0, 0, []
    guard = threading.Lock()
    real = RuleStore.import_parsed

    def slow(self: RuleStore, text: str):
        nonlocal active, most
        with guard:
            active += 1
            most = max(most, active)
        calls.append(text)
        time.sleep(0.15)
        try:
            return real(self, text)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(RuleStore, "import_parsed", slow)
    async with serving(home) as page:
        first, second = await asyncio.gather(
            page.post("/car/rule", data=RULE_FILE.encode()),
            page.post("/car/rule", data=RULE_FILE.replace("SPEED", "SPEED2").encode()),
        )
    assert first[0] == second[0] == 200 and len(calls) == 2
    assert most == 1


@pytest.mark.asyncio
async def test_an_upload_does_not_hold_up_the_event_loop(home, monkeypatch):
    def slow(self: RuleStore, text: str):
        time.sleep(0.5)
        return "0" * 64, SimpleNamespace(rules=[1])

    monkeypatch.setattr(RuleStore, "import_parsed", slow)
    async with serving(home) as page:
        upload = asyncio.create_task(page.post("/car/rule", data=RULE_FILE.encode()))
        started = time.monotonic()
        await asyncio.sleep(0.1)
        assert time.monotonic() - started < 0.3, "the event loop was held while the file was parsed"
        status, _ = await page.get("/car/catalog")
        assert status == 200 and not upload.done()
        assert (await upload)[0] == 200


# --- the recorded log -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_route_reports_matched_and_unmatched_ids(home):
    DbcStore().import_file("log.dbc", LOG_DBC)
    sha = hashlib.sha256(LOG_DBC).hexdigest()
    async with serving(home) as page:
        status, body = await page.post("/car/log", params={"sha256": sha}, data=synthetic_log())
    assert status == 200
    assert body == {
        "periods": {"MSG_A": 20, "MSG_EXT": 50, "MSG_FAST": 10}, "matched": 3, "clamped": ["MSG_FAST"],
        "unmatched_ids": ["0x7FF", "0x00000100"], "unmatched_more": 0,
    }


@pytest.mark.asyncio
async def test_log_route_caps_the_unmatched_list(home):
    DbcStore().import_file("log.dbc", LOG_DBC)
    sha = hashlib.sha256(LOG_DBC).hexdigest()
    lines = [f"(1000.{10 * n:03d}000) can0 {0x300 + index:03X}#00\n" for index in range(60) for n in range(3)]
    log = b"".join(sorted((line.encode() for line in lines), key=lambda line: float(line.split()[0][1:-1])))
    async with serving(home) as page:
        status, body = await page.post("/car/log", params={"sha256": sha}, data=log)
    assert status == 200 and body["matched"] == 0 and body["periods"] == {}
    assert len(body["unmatched_ids"]) == 50 and body["unmatched_more"] == 10
    assert body["unmatched_ids"][0] == "0x300" and body["unmatched_ids"][-1] == "0x331"


@pytest.mark.asyncio
async def test_log_route_needs_a_stored_dbc_and_a_valid_candump(home, monkeypatch):
    DbcStore().import_file("log.dbc", LOG_DBC)
    sha = hashlib.sha256(LOG_DBC).hexdigest()
    async with serving(home) as page:
        assert (await page.post("/car/log", data=synthetic_log()))[0] == 400
        status, body = await page.post("/car/log", params={"sha256": "cd" * 32}, data=synthetic_log())
        assert status == 400 and "cd" * 32 in body["error"]
        status, body = await page.post("/car/log", params={"sha256": sha}, data=b"garbage\n")
        assert status == 400 and "Line 1: expected a timestamped Classical CAN candump frame" in body["error"]
        with monkeypatch.context() as limited:
            limited.setattr(replay, "MAX_FRAMES", 5)
            status, body = await page.post("/car/log", params={"sha256": sha}, data=synthetic_log())
            assert status == 400 and body["error"] == "Select a recording with at most 5 frames"
        too_big = await page.client.post("/car/log", params={"sha256": sha}, data=b"x" * (16 * 1024 * 1024 + 1),
                                         headers={"Origin": page.origin})
        assert too_big.status == 413  # aiohttp's own limit, as on the replay route


# --- the link ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_route_returns_the_statistics_and_the_link(home, monkeypatch):
    monkeypatch.setattr(car_routes, "read_link", lambda interface: LinkInfo(interface, False, 500000, "ERROR-ACTIVE"))
    monkeypatch.setattr(car_routes, "read_link_stats", lambda interface: {
        "state": "ERROR-ACTIVE", "bitrate": 500000, "bus_off": 0, "bus_errors": 2, "tx_dropped": None,
    })
    async with serving(home) as page:
        status, body = await page.get("/car/link", params={"interface": "can0"})
    assert status == 200
    assert body == {"interface": "can0", "virtual": False, "bitrate": 500000, "state": "ERROR-ACTIVE",
                    "bus_off": 0, "bus_errors": 2, "tx_dropped": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "-oops", "a;b", "x" * 16, "can 0", "can0\n"])
async def test_a_bad_interface_name_is_a_400_and_never_reaches_ip(home, monkeypatch, name):
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"ip was run with {args}")

    monkeypatch.setattr("subprocess.run", forbidden)
    async with serving(home) as page:
        status, body = await page.get("/car/link", params={"interface": name})
    assert status == 400 and "interface" in body["error"].lower()


@pytest.mark.asyncio
async def test_a_bad_interface_in_the_working_copy_never_reaches_ip(home, monkeypatch):
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"ip was run with {args}")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr(car_workflow, "read_link", busload.read_link)
    text = demo_text("-oops")
    async with serving(home) as page:
        status, body = await page.post("/car/check", json={"text": text})
        assert status == 200 and "Invalid interface name" in body["bus_load"]["unavailable"]
        assert body["messages"]
        status, body = await page.post("/car/review", json={"text": text})
        assert status == 400 and "-oops" in body["error"]
        status, body = await page.post("/run", json={"text": text})
        assert status == 400 and "-oops" in body["error"]


@pytest.mark.asyncio
async def test_a_missing_interface_is_a_400_with_the_reason(home):
    async with serving(home) as page:
        status, body = await page.get("/car/link", params={"interface": "nosuch0"})
    assert status == 400 and "nosuch0" in body["error"]


# --- the review and the start -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_of_a_virtual_interface_is_not_required(home, monkeypatch):
    observed = bench(monkeypatch, virtual=True)
    async with serving(home) as page:
        status, body = await page.post("/car/review", json={"text": demo_text()})
        assert (status, body) == (200, {"required": False, "interface": "vcan0", "messages": [], "conflicts": []})
        status, body = await page.post("/car/review", json={"text": default_profile_path().read_text()})
        assert status == 200 and body["required"] is False
    assert observed == []


@pytest.mark.asyncio
async def test_review_lists_the_generated_frames_and_says_they_are_not_a_recording(home, monkeypatch):
    observed = bench(monkeypatch, virtual=False, conflicts=["0x0AA already observed on lo"])
    async with serving(home) as page:
        status, body = await page.post("/car/review", json={"text": demo_text("lo")})
    assert status == 200 and body["required"] is True and body["interface"] == "lo"
    assert body["conflicts"] == ["0x0AA already observed on lo"]
    assert body["observation_ms"] == dbc_workflow.OBSERVATION_MS and body["token"]
    assert "generated" in body["note"] and "not a recording" in body["note"] and "first frame" in body["note"]
    wheels = next(m for m in body["messages"] if m["name"] == "WHEEL_SPEEDS")
    assert (wheels["id"], wheels["extended"], wheels["dlc"], wheels["period_ms"]) == (0xAA, False, 8, 12)
    assert len(wheels["raw"].split()) == 8
    assert observed == [("lo", observed[0][1])] and len(observed[0][1]) == len(body["messages"]) > 20


@pytest.mark.asyncio
async def test_run_on_physical_requires_review_token(home, monkeypatch):
    bench(monkeypatch, virtual=False)
    simulator(monkeypatch)
    text = demo_text("lo")
    async with serving(home) as page:
        status, body = await page.post("/run", json={"text": text})
        assert status == 400 and "review" in body["error"]
        status, body = await page.post("/run", json={"text": text, "review": "not-a-token"})
        assert status == 400 and "review" in body["error"]

        _, other = await page.post("/car/review", json={"text": text})
        status, body = await page.post("/run", json={"text": text + "# edited\n", "review": other["token"]})
        assert status == 400 and "review" in body["error"], "a token for a different working copy was accepted"
        status, body = await page.post("/run", json={"text": text, "review": other["token"]})

        _, review = await page.post("/car/review", json={"text": text})
        status, body = await page.post("/run", json={"text": text, "review": review["token"]})
        assert status == 202 and body["state"] == "Starting"
        status, body = await page.post("/run", json={"text": text, "review": review["token"]})
        assert status == 400
        await running(page)


@pytest.mark.asyncio
async def test_run_on_physical_with_a_token_for_another_interface_instance_is_refused(home, monkeypatch):
    bench(monkeypatch, virtual=False)
    simulator(monkeypatch)
    text = demo_text("lo")
    async with serving(home) as page:
        _, review = await page.post("/car/review", json={"text": text})
        monkeypatch.setattr(socket, "if_nametoindex", lambda name: 99)
        status, body = await page.post("/run", json={"text": text, "review": review["token"]})
    assert status == 400 and "review" in body["error"]


@pytest.mark.asyncio
async def test_run_on_vcan_needs_no_token(home, monkeypatch):
    bench(monkeypatch, virtual=True)
    simulator(monkeypatch)
    async with serving(home) as page:
        status, body = await page.post("/run", json={"text": demo_text()})
        assert status == 202 and body["state"] == "Starting"
        await running(page)


@pytest.mark.asyncio
async def test_run_on_physical_of_a_car_that_sends_nothing_names_that_and_not_the_review(home, monkeypatch):
    bench(monkeypatch, virtual=False)
    simulator(monkeypatch)
    sha, _ = upload_dbc([("WIDE", 0x103, 16, False, 20, False)])
    text = demo_text("lo").replace("platform: TOYOTA_COROLLA\n  bus: pt", f"sha256: {sha}\n  filename: bench.dbc")
    async with serving(home) as page:
        status, body = await page.post("/run", json={"text": text})
        assert status == 400 and "no message can be transmitted" in body["error"]
        status, body = await page.post("/car/review", json={"text": text})
        assert status == 400 and "no message can be transmitted" in body["error"]


@pytest.mark.asyncio
async def test_a_plain_profile_on_a_physical_interface_still_needs_no_review(home, monkeypatch):
    bench(monkeypatch, virtual=False)
    simulator(monkeypatch, "{}")
    async with serving(home) as page:
        status, _ = await page.post("/run", json={"text": default_profile_path().read_text().replace("vcan0", "lo")})
    assert status == 202


# --- the run status ---------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_status_carries_a_summary_and_car_run_the_whole_list(home, monkeypatch):
    bench(monkeypatch, virtual=True)
    car = car_workflow.check(demo_text(), VCAN)
    simulator(monkeypatch)
    async with serving(home) as page:
        status, body = await page.get("/car/run")
        assert status == 404 and set(body) == {"error"} and len(body["error"]) < 80
        assert (await page.get("/run"))[1]["car"] is None

        assert (await page.post("/run", json={"text": demo_text()}))[0] == 202
        assert (await page.get("/car/run"))[0] == 404  # still starting
        state = await running(page)
        assert state["car"] == {
            "platform": "TOYOTA_COROLLA", "dbc": {"filename": car["dbc"]["filename"], "sha256": car["dbc"]["sha256"]},
            "revision": car["revision"], "rule": "toyota", "bus_load": car["bus_load"], "counts": car["counts"],
        }
        assert "messages" not in state["car"]
        assert len(json.dumps(state)) < 1500

        status, whole = await page.get("/car/run")
        assert status == 200 and whole["messages"] == car["messages"] and whole["dbc"] == car["dbc"]

        assert (await page.post(f"/run/{state['id']}/stop", json={}))[0] == 200
        assert (await page.get("/car/run"))[0] == 404
        assert (await page.get("/run"))[1]["car"] is None


# --- the middleware's rules still apply -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_car_posts_need_the_local_origin(home):
    async with serving(home) as page:
        for path in ("/car/draft", "/car/check", "/car/review", "/car/rule", "/car/log"):
            response = await page.client.post(path, data=b"x", headers={"Origin": "http://foreign.test"})
            assert response.status == 403, path
        response = await page.client.get("/car/catalog", headers={"Host": "foreign.test"})
        assert response.status == 421


@pytest.mark.asyncio
async def test_the_car_script_is_served(home):
    async with serving(home) as page:
        response = await page.client.get("/car.js")
        assert response.status == 200
        assert response.headers["Content-Type"].startswith(("text/javascript", "application/javascript"))
