"""Local DBC import, preview and bench transmission review for the GUI."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import secrets
import subprocess
import time
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML

from ecu_simulator import app as simulator
from ecu_simulator.config import load_profile_text, read_yaml
from ecu_simulator.gui.capture import open_capture, read_batch
from ecu_simulator.transport.socketcan.interface import check_interface

if TYPE_CHECKING:
    from ecu_simulator.dbc import ImportedDb

OBSERVATION_MS = 500


def browser_numbers(value: Any) -> Any:
    """Send wide integers as decimal text so JavaScript can display and edit them exactly."""
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, dict):
        return {key: browser_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [browser_numbers(item) for item in value]
    return value


def numeric_inputs(values: dict) -> dict:
    return {key: int(v) if isinstance(v, str) and re.fullmatch(r"[+-]?[0-9]+", v) else v
            for key, v in values.items()}


class ReviewTokens:
    """Short-lived one-time receipt for the exact profile and interface instance."""

    def __init__(self) -> None:
        self.pending: dict[str, tuple[float, bytes, int]] = {}

    def issue(self, text: str, index: int) -> str:
        now = time.monotonic()
        self.pending = {key: value for key, value in self.pending.items() if value[0] > now}
        token = secrets.token_urlsafe(24)
        self.pending[token] = now + 120, hashlib.sha256(text.encode()).digest(), index
        return token

    def consume(self, token: str, text: str, index: int) -> None:
        receipt = self.pending.pop(token, None)
        expected = (hashlib.sha256(text.encode()).digest(), index)
        if not receipt or receipt[0] <= time.monotonic() or receipt[1:] != expected:
            raise ValueError("Complete the physical CAN transmission review for this working copy")


def changed_draft(text: str, mapping: dict | None) -> str:
    if isinstance(mapping, dict) and isinstance(mapping.get("messages"), list):
        for message in mapping["messages"]:
            if isinstance(message, dict) and isinstance(message.get("values"), dict):
                message["values"] = numeric_inputs(message["values"])
    original = read_yaml(text, "working copy")
    yaml = YAML()
    yaml.preserve_quotes = True
    document = yaml.load(text)
    if mapping is None:
        document.pop("dbc", None)
        original.pop("dbc", None)
    else:
        document["dbc"] = mapping
        original["dbc"] = mapping
    output = io.StringIO()
    yaml.dump(document, output)
    result = output.getvalue()
    if read_yaml(result, "working copy") != original:
        raise ValueError("Edit this mapping in the YAML working copy")
    load_profile_text(result)
    return result


def virtual_can(interface: str) -> bool:
    check_interface(interface)
    # sysfs may be mounted from a different network namespace.
    try:
        result = subprocess.run(["ip", "-j", "-d", "link", "show", "dev", interface],
                                check=True, capture_output=True, timeout=2)
        return json.loads(result.stdout)[0].get("linkinfo", {}).get("info_kind") in {"vcan", "vxcan"}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return False


def preview(db: ImportedDb, name: str, values: dict, text: str) -> tuple[str, dict]:
    config = simulator.RuntimeConfig.build(load_profile_text(text))
    runtime = simulator.build_runtime(config)
    vehicle = runtime.vehicle
    # Preview the first scenario sample, including generators with a nonzero initial value.
    if runtime.runner is not None:
        runtime.runner.apply(0)
    actual: dict[str, float] = {}
    for signal, value in numeric_inputs(values).items():
        if isinstance(value, str):
            if not vehicle.has(value):
                raise ValueError(f"Unknown scenario signal {value!r}")
            value = vehicle.get(value)
        if type(value) not in (int, float):
            raise ValueError(f"{signal}: choose a numeric constant or a numeric scenario signal")
        actual[signal] = value
    raw = db.encode(name, actual)
    msg = db.message(name)
    decoded = db.decode(msg["id"], msg["extended"], raw)
    assert decoded and "signals" in decoded
    expanded = {key: values.get(key, entry["value"]) for key, entry in decoded["signals"].items()}
    return raw.hex(" ").upper(), expanded


async def observed_ids(interface: str, messages: list[dict]) -> list[str]:
    selected = {f'{m["id"]:0{8 if m["extended"] else 3}X}' for m in messages}
    observed: set[str] = set()
    with open_capture(interface) as sock:
        until = time.monotonic() + OBSERVATION_MS / 1000
        while time.monotonic() < until:
            frames, _ = read_batch(sock)
            observed.update(frame["id"] for frame in frames if not frame["kind"] and frame["id"] in selected)
            await asyncio.sleep(0.01)
    return [f"0x{identifier} already observed on {interface}" for identifier in sorted(observed)]


def validate_dbc(text: str) -> None:
    profile = load_profile_text(text)
    if profile.dbc:
        simulator.prepare_dbc(simulator.RuntimeConfig.build(profile))


def decode_batch(db: ImportedDb, frames: list[dict], previous: dict[tuple[int, bool], int]) -> None:
    for frame in frames:
        if frame["kind"]:
            continue
        key = (int(frame["id"], 16), len(frame["id"]) == 8)
        decoded = db.decode(*key, bytes(frame["bytes"]))
        if decoded is None:
            continue
        then = previous.get(key)
        timestamp = frame["time"]
        frame["dbc"] = {**browser_numbers(decoded), "period_ms": None if then is None else (timestamp - then) / 1000}
        previous[key] = timestamp
