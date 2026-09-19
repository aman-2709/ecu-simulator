"""Reading a profile from disk.

YAML is parsed with ``ruamel.yaml`` through its safe, pure-Python loader. That loader
refuses arbitrary object construction and, unlike the obvious alternative, rejects a
duplicate key instead of silently keeping the last occurrence, which matters for a file
whose whole purpose is to be validated (docs/decisions/0002-configuration-format-and-
validation.md). Nothing else in the project imports a YAML library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError

from ecu_simulator.config.errors import ConfigError


def _reader() -> YAML:
    # typ="safe" refuses python/object tags; pure=True keeps it off the optional C extension.
    yaml = YAML(typ="safe", pure=True)
    yaml.allow_duplicate_keys = False
    return yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Parse ``path`` into a mapping, or raise :class:`ConfigError` naming the file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigError(f"profile {path} does not exist") from error
    except OSError as error:
        raise ConfigError(f"profile {path} could not be read: {error}") from error
    try:
        document = _reader().load(text)
    except DuplicateKeyError as error:
        raise ConfigError(f"profile {path} has a duplicate key: {_first_line(error)}") from error
    except YAMLError as error:
        raise ConfigError(f"profile {path} is not valid YAML: {_first_line(error)}") from error
    if document is None:
        raise ConfigError(f"profile {path} is empty")
    if not isinstance(document, dict):
        raise ConfigError(f"profile {path} must be a mapping at the top level, found {type(document).__name__}")
    return document


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0] if str(error).strip() else error.__class__.__name__
