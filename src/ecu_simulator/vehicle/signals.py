"""Dotted signal paths over the composed vehicle state.

Every state component declares a namespace, and its dataclass fields become the signals
inside it: ``IceState`` under ``engine`` exposes ``engine.rpm``, ``engine.coolant_temp``
and so on. Encoders read physical values through these paths and never reach into the
state objects, so which signals exist is a property of the configured vehicle.

Reads have no side effects. That is the whole point of DEV-09: the legacy implementation
incremented vehicle speed on every read.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable


class UnknownSignalError(KeyError):
    """The requested signal does not exist on this vehicle."""

    def __init__(self, path: str, available: Iterable[str] = ()) -> None:
        known = sorted(available)
        message = f"unknown signal {path!r}"
        if known:
            message += f"; available: {', '.join(known)}"
        super().__init__(message)
        self.path = path

    def __str__(self) -> str:
        return str(self.args[0])


@runtime_checkable
class SignalSource(Protocol):
    """A dataclass contributing its fields as signals under one namespace."""

    namespace: str


def signal_paths(source: Any) -> dict[str, Any]:
    """``{"engine.rpm": 0, ...}`` for one state component."""
    namespace = source.namespace
    return {f"{namespace}.{field.name}": getattr(source, field.name) for field in dataclasses.fields(source)}


def split(path: str) -> tuple[str, str]:
    namespace, separator, name = path.partition(".")
    if not separator or not namespace or not name:
        raise UnknownSignalError(path)
    return namespace, name
