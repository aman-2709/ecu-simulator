"""Offline codec for the pinned wheel-speed restbus profile."""

import hashlib
import math
import re
from importlib.resources import files

from ecu_simulator._opendbc import Signal, pack

_REVISION = "79ddcc7bb72d557fc5b20d038c0fad9c9d423446"
_DBC = "toyota_nodsu_pt_generated.dbc"
_SHA256 = "29ee5c92017b46b8db2fe7566e6f765fe52efcddfb3b426dff929c879d70e066"
_WHEELS = ("FR", "FL", "RR", "RL")
_SIGNAL = re.compile(r' SG_ (\w+) : (\d+)\|(\d+)@0\+ \(([\d.]+),([-\d.]+)\) \[[^\]]+\] "([^"]*)" .+')


class WheelCodec:
    """Resolve and validate the packaged database before encoding or decoding."""

    def __init__(self) -> None:
        content = files("ecu_simulator._opendbc").joinpath(_DBC).read_bytes()
        self.sha256 = hashlib.sha256(content).hexdigest()
        if self.sha256 != _SHA256:
            raise ValueError(f"{_DBC}: unexpected SHA-256 {self.sha256}")
        lines = content.decode("utf-8").splitlines()
        start = lines.index("BO_ 170 WHEEL_SPEEDS: 8 XXX")
        self._signals: dict[str, Signal] = {}
        for line in lines[start + 1 :]:
            if not line.startswith(" SG_ "):
                break
            match = _SIGNAL.fullmatch(line)
            if match is None:
                raise ValueError(f"Unsupported WHEEL_SPEEDS signal: {line}")
            name, start_bit, width, factor, offset, unit = match.groups()
            # Motorola DBC start bits use LSB-zero numbering within each byte.
            msb, size = int(start_bit), int(width)
            end_position = (msb ^ 7) + size - 1
            if end_position >= 64:
                raise ValueError(f"{name} extends beyond WHEEL_SPEEDS")
            if unit != ("" if name.endswith("_FAULT") else "km/h"):
                raise ValueError(f"Unexpected unit for {name}")
            self._signals[name] = Signal(
                name, msb, msb, end_position ^ 7, size, False, float(factor), float(offset), False
            )
        expected = {f"WHEEL_SPEED_{wheel}{suffix}" for wheel in _WHEELS for suffix in ("", "_FAULT")}
        if self._signals.keys() != expected:
            raise ValueError("Incomplete WHEEL_SPEEDS signal mapping")

    def metadata(self) -> dict[str, str | int]:
        return {
            "dbc": _DBC,
            "revision": _REVISION,
            "sha256": self.sha256,
            "message": "WHEEL_SPEEDS",
            "can_id": 0xAA,
            "period_ms": 20,
        }

    def encode(self, speed: float) -> bytes:
        if not math.isfinite(speed) or not -67.67 <= speed <= 260:
            raise ValueError(f"Wheel speed outside the representable range: {speed}")
        return pack(8, self._signals, {f"WHEEL_SPEED_{wheel}": speed for wheel in _WHEELS})

    def decode(self, data: bytes) -> dict[str, float]:
        if len(data) != 8:
            raise ValueError("WHEEL_SPEEDS requires 8 bytes")
        speeds = {}
        for wheel in _WHEELS:
            signal = self._signals[f"WHEEL_SPEED_{wheel}"]
            speeds[wheel] = round(signal.get_raw_value(data) * signal.factor + signal.offset, 2)
        return speeds
