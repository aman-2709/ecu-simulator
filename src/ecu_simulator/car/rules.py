"""Declarative integrity-rule files: which signal of a message is the counter or checksum, and how to compute it.

An engineer with their own network's DBC uploads a small YAML file::

    version: 1
    rules:
      - messages: [Gearbox]            # or: glob: "Gear*"
        counter: {signal: ALIVE, max: 9}
        checksum:
          signal: CRC
          algorithm: crc8              # xor, sum8, nibble_sum, crc8 or crc16
          poly: 0x1D
          init: 0xFF
          xorout: 0xFF
          covers: [0, 1, 2, 3, 4, 5, 6]
          data_id: 0x5A

The file is data only; nothing in it is executed. Because the GUI server can be reached from a bench network, a
hostile or malformed file is refused with the dotted path of the problem (``rules.0.checksum.poly``) and never partly
applied: ``parse_rule_text`` checks everything that depends on the file alone, and ``RuleFile.resolve`` then checks
every rule against the messages of the DBC before a single frame is stamped.

``algorithms.py`` computes the checksums and ``integrity.py`` stamps them; ``plan_message(..., user=...)`` takes the
``UserRule`` that ``rule_for`` or ``resolve`` returns.
"""

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from ecu_simulator.car.algorithms import ALGORITHM_BITS, CRC_ALGORITHMS, Algorithm, build_checksum
from ecu_simulator.car.bits import BitField
from ecu_simulator.car.integrity import ChecksumField, CounterField
from ecu_simulator.config.errors import ConfigError
from ecu_simulator.config.loader import read_yaml

MAX_RULE_BYTES = 256 * 1024
MAX_RULES = 1000
MAX_GLOB_CHARS = 64
# A CAN FD frame has at most 64 bytes, so no honest ``covers`` list is longer.
MAX_COVERS = 64


class RuleError(ValueError):
    """A rule file is refused. ``path`` is the dotted path of the problem, empty when it is the whole file."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}" if path else reason)
        self.path = path
        self.reason = reason


def _refuse(reason: str) -> PydanticCustomError:
    return PydanticCustomError("rule_file", reason)


def _fit(value: int, algorithm: Algorithm) -> int:
    """``value`` if it fits the result width of ``algorithm``, which bounds its ``poly``, ``init`` and ``xorout``."""
    bits = ALGORITHM_BITS[algorithm]
    if not 0 <= value < 1 << bits:
        noun = "CRC" if algorithm in CRC_ALGORITHMS else algorithm
        raise _refuse(f"value {value} does not fit {'an' if bits == 8 else 'a'} {bits}-bit {noun}")
    return value


class _Model(BaseModel):
    """Unknown keys are refused, and ``strict`` means a number is a number: not ``true``, ``7.0`` or ``"7"``."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


_Name = Annotated[str, Field(min_length=1)]


class CounterSpec(_Model):
    signal: _Name
    max: Annotated[int, Field(ge=0)] | None = None


class ChecksumSpec(_Model):
    signal: _Name
    algorithm: Algorithm
    poly: int | None = Field(default=None, validate_default=True)
    init: int = 0
    xorout: int = 0
    reflect: bool = False
    covers: Annotated[list[Annotated[int, Field(ge=0)]], Field(max_length=MAX_COVERS)] | None = None
    data_id: Annotated[int, Field(ge=0, le=255)] | None = None

    @field_validator("poly")
    @classmethod
    def _poly_belongs_to_a_crc(cls, value: int | None, info: ValidationInfo) -> int | None:
        algorithm: Algorithm | None = info.data.get("algorithm")
        if algorithm is None:  # the algorithm itself was refused, and that is the error reported
            return value
        if algorithm not in CRC_ALGORITHMS:
            if value is not None:
                raise _refuse(f"poly applies only to crc8 and crc16, not {algorithm}")
            return None
        if value is None:
            raise _refuse(f"a {algorithm} needs a poly")
        if value == 0:
            raise _refuse("a poly of 0 is not a CRC")
        return _fit(value, algorithm)

    @field_validator("init", "xorout")
    @classmethod
    def _fits_the_algorithm(cls, value: int, info: ValidationInfo) -> int:
        algorithm: Algorithm | None = info.data.get("algorithm")
        return value if algorithm is None else _fit(value, algorithm)

    @field_validator("reflect")
    @classmethod
    def _reflect_is_for_a_crc(cls, value: bool, info: ValidationInfo) -> bool:
        algorithm: Algorithm | None = info.data.get("algorithm")
        if value and algorithm is not None and algorithm not in CRC_ALGORITHMS:
            raise _refuse(f"reflect applies only to crc8 and crc16, not {algorithm}")
        return value


class Rule(_Model):
    messages: Annotated[list[_Name], Field(min_length=1)] | None = None
    glob: str | None = Field(default=None, min_length=1, max_length=MAX_GLOB_CHARS)
    counter: CounterSpec | None = None
    checksum: ChecksumSpec | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.messages is None) == (self.glob is None):
            raise _refuse("give exactly one of messages and glob")
        if self.counter is None and self.checksum is None:
            raise _refuse("a rule needs a counter, a checksum or both")
        if self.counter is not None and self.checksum is not None and self.counter.signal == self.checksum.signal:
            raise _refuse(f"the counter and the checksum are the same signal, {self.counter.signal!r}")
        return self


@dataclass(frozen=True, slots=True)
class UserRule:
    """The counter and the checksum a rule file defines for one message, built from that message's own signals."""

    message: str
    counters: tuple[CounterField, ...]
    checksums: tuple[ChecksumField, ...]


@dataclass(frozen=True, slots=True)
class _Index:
    """Where a message name leads: the first rule that lists it, and the glob rules in file order."""

    exact: Mapping[str, int]
    globs: tuple[tuple[int, str], ...]


class RuleFile(_Model):
    version: Literal[1]
    rules: list[Rule]

    _index: _Index = PrivateAttr()

    @field_validator("version", mode="before")
    @classmethod
    def _version_is_one(cls, value: object) -> object:
        # Literal[1] alone would take true and 1.0 for 1.
        if type(value) is not int:
            raise _refuse("version must be the integer 1")
        if value != 1:
            raise _refuse(f"unsupported version {value}; this reader reads only version 1")
        return value

    @field_validator("rules", mode="before")
    @classmethod
    def _not_too_many(cls, value: object) -> object:
        if isinstance(value, list) and len(value) > MAX_RULES:
            raise _refuse(f"{len(value)} rules; at most {MAX_RULES} are accepted")
        return value

    def model_post_init(self, context: Any, /) -> None:
        exact: dict[str, int] = {}
        globs: list[tuple[int, str]] = []
        for position, rule in enumerate(self.rules):
            for name in rule.messages or ():
                exact.setdefault(name, position)
            if rule.glob is not None:
                globs.append((position, rule.glob))
        self._index = _Index(MappingProxyType(exact), tuple(globs))

    def _first_match(self, name: str) -> int | None:
        """The position of the first rule in file order that matches ``name``; globs are case-sensitive fnmatch."""
        exact = self._index.exact.get(name)
        for position, pattern in self._index.globs:
            if exact is not None and position > exact:
                break
            if fnmatchcase(name, pattern):
                return position
        return exact

    def rule_for(self, message: Mapping[str, Any]) -> UserRule | None:
        """The ``UserRule`` of the first rule that matches ``message``, or ``None``.

        A message with a ``layout_issue`` has no usable signal list and is never matched. A matching rule that does
        not fit the message (an unknown signal, a field outside the frame, a ``covers`` index past its end) raises
        ``RuleError`` with the path of the rule's own key.
        """
        if message["layout_issue"]:
            return None
        position = self._first_match(str(message["name"]))
        return None if position is None else _bind(position, self.rules[position], message)

    def resolve(self, messages: Sequence[Mapping[str, Any]]) -> Mapping[str, UserRule]:
        """The ``UserRule`` of every message that a rule matches, by message name.

        Raises ``RuleError`` for a rule that does not fit a message it matches first, and for a rule that matches no
        message at all (a rule whose only matches have layout issues has matched something). Nothing is returned for
        a file that is refused.
        """
        resolved: dict[str, UserRule] = {}
        first_matches: set[int] = set()
        for message in messages:
            name = str(message["name"])
            position = self._first_match(name)
            if position is None:
                continue
            first_matches.add(position)
            if not message["layout_issue"]:
                resolved[name] = _bind(position, self.rules[position], message)
        names = {str(message["name"]) for message in messages}
        for position, rule in enumerate(self.rules):
            if position not in first_matches and not _matches_any(rule, names):
                key = "messages" if rule.messages is not None else "glob"
                raise RuleError(f"rules.{position}.{key}", "matches no message in this DBC")
        return MappingProxyType(resolved)


def _matches_any(rule: Rule, names: set[str]) -> bool:
    """Whether ``rule`` matches any of ``names``; the rules that win a message are settled before this is asked."""
    if rule.messages is not None:
        return not names.isdisjoint(rule.messages)
    assert rule.glob is not None
    return any(fnmatchcase(name, rule.glob) for name in names)


def _bind(position: int, rule: Rule, message: Mapping[str, Any]) -> UserRule:
    """``rule`` (at ``position`` in its file) over the signals of ``message``, or ``RuleError`` for what misfits."""
    name, size = str(message["name"]), int(message["dlc"])
    fields = {
        str(s["name"]): BitField(int(s["start"]), int(s["length"]), bool(s["little_endian"]))
        for s in message["signals"]
    }

    def locate(key: str, signal: str) -> BitField:
        field = fields.get(signal)
        if field is None:
            raise RuleError(f"rules.{position}.{key}.signal", f"message {name!r} has no signal named {signal!r}")
        if not field.fits(size):
            raise RuleError(
                f"rules.{position}.{key}.signal", f"signal {signal!r} reaches outside the {size}-byte frame"
            )
        return field

    counters: tuple[CounterField, ...] = ()
    if (counter := rule.counter) is not None:
        field = locate("counter", counter.signal)
        field_max = (1 << field.size) - 1
        if counter.max is not None and counter.max > field_max:
            raise RuleError(
                f"rules.{position}.counter.max",
                f"value {counter.max} does not fit the {field.size}-bit signal {counter.signal}",
            )
        counters = (CounterField(counter.signal, field, 0, field_max if counter.max is None else counter.max),)

    checksums: tuple[ChecksumField, ...] = ()
    if (checksum := rule.checksum) is not None:
        field = locate("checksum", checksum.signal)
        for k, index in enumerate(checksum.covers or ()):
            if index >= size:
                raise RuleError(
                    f"rules.{position}.checksum.covers.{k}", f"byte {index} is past the {size}-byte message"
                )
        checksums = (ChecksumField(checksum.signal, field, build_checksum(checksum, field.size)),)

    return UserRule(name, counters, checksums)


def parse_rule_text(text: str) -> RuleFile:
    """The validated ``RuleFile`` in ``text``, or ``RuleError`` for a file that is too large, not YAML, or not valid.

    The text is at most ``MAX_RULE_BYTES`` bytes of UTF-8 with at most ``MAX_RULES`` rules. It may not use YAML
    anchors, aliases or merge keys, and every key and number must be of the kind the rule schema names.
    """
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise RuleError("", "rule file is not valid UTF-8 text") from error
    if size > MAX_RULE_BYTES:
        raise RuleError("", f"rule file is {size} bytes, over the {MAX_RULE_BYTES}-byte (256 KiB) limit")
    try:
        document = read_yaml(text, "", kind="rule file", untrusted=True)
    except ConfigError as error:
        raise RuleError("", str(error)) from error
    try:
        return RuleFile.model_validate(document)
    except ValidationError as error:
        first = error.errors(include_url=False, include_context=False, include_input=False)[0]
        raise RuleError(".".join(str(part) for part in first["loc"]), first["msg"]) from error


class RuleStore:
    """Validated rule files kept by the SHA-256 of their text, as ``DbcStore`` keeps DBCs."""

    def __init__(self, directory: Path | None = None) -> None:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.directory = directory if directory is not None else base / "ecu-simulator" / "rules"

    def import_text(self, text: str) -> str:
        """Validate ``text``, store it as ``<sha256>.yaml``, and return the SHA-256 of its UTF-8 bytes.

        A file that is refused is never written, and the directory is not created for it.
        """
        parse_rule_text(text)
        raw = text.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=self.directory, delete_on_close=False) as staging:
            staging.write(raw)
            staging.close()
            os.replace(staging.name, self.directory / f"{digest}.yaml")
        return digest

    def load(self, sha256: str) -> RuleFile:
        if not isinstance(sha256, str) or not re.fullmatch("[0-9a-f]{64}", sha256):
            raise ValueError("Expected a lowercase SHA-256 rule file digest")
        try:
            raw = (self.directory / f"{sha256}.yaml").read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(f"Missing rule file {sha256}. Import the rule file again.") from exc
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise ValueError("Stored rule file hash mismatch. Import the original rule file again.")
        return parse_rule_text(raw.decode("utf-8"))
