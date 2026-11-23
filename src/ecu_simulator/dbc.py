"""Import, validate and encode local DBC files against their original content hash."""

import hashlib
import math
import os
import re
import tempfile
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from cantools.database.can import Database, Message, Signal
from cantools.database.can.formats.dbc import DbcParser
from cantools.database.errors import Error as CanDatabaseError

from ecu_simulator.car.catalog import bundled_dbc_path

# Common integrity field names in opendbc. A DBC describes the field layout,
# but does not supply the ECU-specific algorithm for generating these values.
_INTEGRITY = re.compile(r"checksum|counter|alivecount|chksum|chksm|crc|(?:^|_)(?:cnt|ctr|cs|cntr)(?:_|$)", re.I)
_IMPORT = re.compile(r'(?:^|;)\s*CM_\s+"IMPORT\s+', re.I | re.M)
_BUNDLED_DATABASES = {
    "c238d3938e4dc461b96b5055e790ccea01d03fc8c84d44ebeb35446654988f07": "bmw_e9x_e8x.dbc",
}
MAX_DBC_BYTES = 5 * 1024 * 1024


def _validate_dimensions(source: str) -> None:
    # The codec builder allocates buffers from declared widths and expands mux
    # ranges. Read the DBC grammar before those allocations take place.
    records = cast(dict[str, list[list[Any]]], DbcParser().parse(source))
    for message in records.get("BO_", []):
        if not 0 <= Decimal(message[4]) <= 64:
            raise ValueError("Message length exceeds CAN FD dimensions (0–64 bytes)")
        for signal in message[6]:
            if not (0 <= Decimal(signal[3]) < 512 and 0 < Decimal(signal[5]) <= 512):
                raise ValueError("Signal start or width exceeds CAN FD dimensions")
    expanded = 0
    for record in records.get("SG_MUL_VAL_", []):
        for lower, upper in record[4]:
            count = int(upper[1:]) - int(lower) + 1
            expanded += count
            if count <= 0 or expanded > 65536:
                raise ValueError("Invalid or excessively large multiplex selector range")


def _signal_info(signal: Signal, branches: set[int]) -> dict[str, Any]:
    low, high = signal.minimum, signal.maximum
    if signal.scale == 0 or not all(math.isfinite(n) for n in (signal.scale, signal.offset)):
        raise ValueError(f"Invalid scale or offset for {signal.name}")
    if not signal.is_float:
        raw_low = -(1 << (signal.length - 1)) if signal.is_signed else 0
        raw_high = raw_low + (1 << signal.length) - 1
        ends = [cast(float, signal.raw_to_scaled(v, decode_choices=False)) for v in (raw_low, raw_high)]
        low = max(min(ends), low) if low is not None else min(ends)
        high = min(max(ends), high) if high is not None else max(ends)
    if low is not None and high is not None and low > high:
        raise ValueError(f"Invalid physical range for {signal.name}")
    default = 0.0
    if signal.raw_initial is not None:
        default = cast(float, signal.raw_to_scaled(signal.raw_initial, decode_choices=False))
    if low is not None:
        default = max(low, default)
    if high is not None:
        default = min(high, default)
    if signal.is_multiplexer and branches and default not in branches:
        default = min(branches)
    return {
        "name": signal.name, "unit": signal.unit or "", "min": low, "max": high, "default": default,
        "mux": signal.multiplexer_signal, "mux_ids": signal.multiplexer_ids, "is_mux": signal.is_multiplexer,
        "start": signal.start, "length": signal.length, "little_endian": signal.byte_order == "little_endian",
    }


class ImportedDb:
    def __init__(self, raw: bytes, digest: str) -> None:
        self.sha256 = digest
        try:
            source = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            source = raw.decode("cp1252")
        if _IMPORT.search(source):
            raise ValueError("Unresolved DBC IMPORT directive. Import the generated, flattened DBC instead.")
        try:
            _validate_dimensions(source)
            db = Database(strict=False)
            db.add_dbc_string(source)
        except Exception as exc:
            raise ValueError(f"Invalid DBC: {exc}") from exc
        self._frames: dict[tuple[int, bool], Message] = {}
        self._messages: dict[str, dict[str, Any]] = {}
        self._layout_errors: dict[str, str] = {}
        for frame in db.messages:
            key = (frame.frame_id, frame.is_extended_frame)
            if key in self._frames or frame.name in self._messages:
                raise ValueError(f"Duplicate DBC message name or arbitration ID at {frame.name}")
            self._frames[key] = frame
            issues: list[str] = []
            signals: list[dict[str, Any]] = []
            try:
                if len({s.name for s in frame.signals}) != len(frame.signals):
                    raise ValueError("Duplicate signal name")
                frame.refresh(strict=True)
                branches: dict[str, set[int]] = {}
                for signal in frame.signals:
                    if signal.multiplexer_signal:
                        branches.setdefault(signal.multiplexer_signal, set()).update(signal.multiplexer_ids or [])
                signals = [_signal_info(s, branches.get(s.name, set())) for s in frame.signals]
            except (CanDatabaseError, ValueError, OverflowError) as exc:
                issues.append(str(exc))
                self._layout_errors[frame.name] = str(exc)
            if frame.is_fd or frame.length > 8:
                issues.append("CAN FD transmission is unsupported")
            protected = [s.name for s in frame.signals if _INTEGRITY.search(s.name)]
            layout_issue = "; ".join(issues) or None
            if protected:
                issues.append(f"Counter/checksum generation is unsupported for {', '.join(protected)}")
            self._messages[frame.name] = {
                "name": frame.name, "id": key[0], "extended": key[1], "dlc": frame.length,
                "period_ms": frame.cycle_time or None, "issues": issues, "tx_issue": "; ".join(issues) or None,
                "signals": signals, "integrity_fields": protected, "layout_issue": layout_issue,
            }
        if len(self._layout_errors) == len(self._frames):
            reason = next(iter(self._layout_errors.values()), "No messages defined")
            raise ValueError(f"No readable DBC messages: {reason}")

    def catalog(self, filename: str) -> dict[str, Any]:
        return {"filename": filename, "sha256": self.sha256, "messages": list(self._messages.values())}

    def message(self, name: str) -> dict[str, Any]:
        try:
            return self._messages[name]
        except KeyError as exc:
            raise ValueError(f"Unknown DBC message {name!r}") from exc

    def encode(self, name: str, values: dict[str, float], *, integrity_ok: bool = False) -> bytes:
        """Encode ``values``; ``integrity_ok`` accepts counter/checksum fields and leaves them at their defaults."""
        info = self.message(name)
        blocking = info["layout_issue"] if integrity_ok else info["tx_issue"]
        if blocking:
            raise ValueError(info["tx_issue"])
        frame = self._frames[info["id"], info["extended"]]
        fields = {s["name"]: s for s in info["signals"]}
        selected = {s: f["default"] for s, f in fields.items()}
        for field, value in values.items():
            if field not in fields:
                raise ValueError(f"Unknown signal {field!r} in {name}")
            spec = fields[field]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"Expected a finite number for {field}")
            if spec["is_mux"] and value != int(value):
                raise ValueError(f"Expected integer selector for {field}")
            if (spec["min"] is not None and value < spec["min"]) or (spec["max"] is not None and value > spec["max"]):
                raise ValueError(f"{field} is outside its physical range")
            selected[field] = value
        try:
            active = frame.gather_signals(selected)
            if unused := values.keys() - active.keys():
                raise ValueError(f"Inactive signal(s) for selected multiplex branch: {', '.join(sorted(unused))}")
            return frame.encode(active)
        except (CanDatabaseError, OverflowError, TypeError) as exc:
            raise ValueError(f"Cannot encode {name}: {exc}") from exc

    def decode(self, can_id: int, extended: bool, data: bytes) -> dict[str, Any] | None:
        frame = self._frames.get((can_id, extended))
        if frame is None:
            return None
        if frame.name in self._layout_errors:
            return {"name": frame.name, "error": self._layout_errors[frame.name]}
        if len(data) != frame.length:
            return {"name": frame.name, "error": f"Expected {frame.length} bytes, received {len(data)}"}
        try:
            values = frame.decode_simple(data, decode_choices=False)
        except (CanDatabaseError, ValueError) as exc:
            return {"name": frame.name, "error": str(exc)}
        if any(isinstance(v, float) and not math.isfinite(v) for v in values.values()):
            return {"name": frame.name, "error": "Frame contains a non-finite signal value"}
        return {"name": frame.name, "signals": {
            s: {"value": v, "unit": frame.get_signal_by_name(s).unit or ""} for s, v in values.items()
        }}


@lru_cache(maxsize=2)
def _compiled(raw: bytes, digest: str) -> ImportedDb:
    """Reuse the immutable codecs for recent previews and capture connections."""
    return ImportedDb(raw, digest)


class DbcStore:
    def __init__(self, directory: Path | None = None) -> None:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.directory = directory if directory is not None else base / "ecu-simulator" / "dbc"

    def import_file(self, filename: str, raw: bytes) -> dict[str, Any]:
        if len(raw) > MAX_DBC_BYTES:
            raise ValueError("DBC exceeds the 5 MiB import limit")
        digest = hashlib.sha256(raw).hexdigest()
        db = _compiled(raw, digest)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=self.directory, delete_on_close=False) as staging:
            staging.write(raw)
            staging.close()
            os.replace(staging.name, self.directory / f"{digest}.dbc")
        return db.catalog(filename)

    def load(self, sha256: str) -> ImportedDb:
        if not isinstance(sha256, str) or not re.fullmatch("[0-9a-f]{64}", sha256):
            raise ValueError("Expected a lowercase SHA-256 DBC digest")
        try:
            raw = (self.directory / f"{sha256}.dbc").read_bytes()
        except FileNotFoundError as exc:
            bundled = bundled_dbc_path(sha256)
            if bundled is None and sha256 in _BUNDLED_DATABASES:
                bundled = Path(__file__).with_name("_opendbc") / _BUNDLED_DATABASES[sha256]
            if bundled is None:
                raise ValueError(f"Missing DBC artifact {sha256}. Re-import the original DBC.") from exc
            raw = bundled.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise ValueError("Stored DBC hash mismatch. Re-import the original DBC.")
        return _compiled(raw, sha256)
