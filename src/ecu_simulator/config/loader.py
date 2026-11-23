"""Reading a profile from disk.

YAML is parsed with ``ruamel.yaml`` through its safe, pure-Python loader. That loader
refuses arbitrary object construction and, unlike the obvious alternative, rejects a
duplicate key instead of silently keeping the last occurrence, which matters for a file
whose whole purpose is to be validated (docs/decisions/0002-configuration-format-and-
validation.md). Nothing else in the project imports a YAML library.

A file that arrives from outside, such as an uploaded rule file, is read with ``untrusted=True``: it may not use
anchors, aliases, merge keys or explicit tags (an alias can expand a few bytes into an enormous structure, and a tag
can make the constructor build something else or fail with an exception that is not a ``YAMLError``) or nest deeper
than ``MAX_UNTRUSTED_DEPTH``, and a value the YAML constructor cannot build is a refusal rather than an escaping
exception.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError
from ruamel.yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, NodeEvent, ScalarEvent

from ecu_simulator.config.errors import ConfigError

# The deepest legitimate rule file has five levels; the composer recurses once per level, so the bound also keeps a
# hostile file from exhausting the stack.
MAX_UNTRUSTED_DEPTH = 16


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
    return read_yaml(text, str(path))


def read_yaml(text: str, source: str, *, kind: str = "profile", untrusted: bool = False) -> dict[str, Any]:
    """Parse ``text`` into a mapping, or raise :class:`ConfigError` that names ``kind`` and ``source``.

    ``kind`` words the errors (``profile`` unless the caller reads something else, such as a rule file); an empty
    ``source`` leaves it out.

    ``untrusted`` is for text that came from outside: it refuses anchors, aliases, merge keys, explicit tags and
    nesting deeper than ``MAX_UNTRUSTED_DEPTH`` from the parser's events before anything is built, and turns an
    exception the constructor raises for a value it cannot build into a ``ConfigError``. It reads the text twice,
    once to scan the events and once to load it, and the parser is pure Python and holds the GIL. Measured on the
    development machine, a dense file at the 256 KiB rule-file limit (about 131,000 one-byte scalars) took 7.7 to
    10.5 s, and a realistic 1000-rule file (170 KB) about 3 s. A caller on an async server must run this in a
    worker thread and serialise uploads, so that one hostile file cannot occupy every worker.
    """
    subject = f"{kind} {source}" if source else kind
    try:
        if untrusted:
            _refuse_expanding_structure(text, subject, kind)
        document = _reader().load(text)
    except DuplicateKeyError as error:
        raise ConfigError(f"{subject} has a duplicate key: {_first_line(error)}") from error
    except YAMLError as error:
        raise ConfigError(f"{subject} is not valid YAML: {_first_line(error)}") from error
    except ConfigError:
        raise  # a refusal by the scan above
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
        # Defence in depth: the safe constructor raises these, not a YAMLError, for a value it cannot build.
        if not untrusted:
            raise
        raise ConfigError(f"{subject} has a value that cannot be read{_unreadable(error)}") from error
    if document is None:
        raise ConfigError(f"{subject} is empty")
    if not isinstance(document, dict):
        raise ConfigError(f"{subject} must be a mapping at the top level, found {type(document).__name__}")
    return document


def _unreadable(error: Exception) -> str:
    """Why the constructor failed, said without echoing the interpreter's own text, which can be long or internal."""
    if isinstance(error, ValueError) and "integer string conversion" in str(error):
        return ": an integer in it is too large"
    return ""


def _refuse_expanding_structure(text: str, subject: str, kind: str) -> None:
    """Refuse anchors, aliases, tags, merge keys and deep nesting, from the parser's events before anything is built.

    The parser works from a state stack, so a deeply nested file is counted here rather than recursed into, and no
    alias has been expanded yet. A syntax error propagates as the parser's own ``YAMLError``.
    """
    depth = 0
    for event in _reader().parse(text):
        line = event.start_mark.line + 1 if event.start_mark is not None else 0
        if isinstance(event, AliasEvent):
            raise ConfigError(f"{subject} uses a YAML alias at line {line}, which it may not")
        if isinstance(event, NodeEvent) and event.anchor is not None:
            raise ConfigError(f"{subject} uses a YAML anchor at line {line}, which it may not")
        if isinstance(event, ScalarEvent | CollectionStartEvent) and event.tag is not None:
            raise ConfigError(f"{subject} uses a YAML tag at line {line}; tags are not allowed in a {kind}")
        if isinstance(event, ScalarEvent) and _is_merge_key(event):
            raise ConfigError(f"{subject} uses a YAML merge key (<<) at line {line}, which it may not")
        if isinstance(event, CollectionStartEvent):
            depth += 1
            if depth > MAX_UNTRUSTED_DEPTH:
                raise ConfigError(f"{subject} is nested more than {MAX_UNTRUSTED_DEPTH} levels deep at line {line}")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1


def _is_merge_key(event: ScalarEvent) -> bool:
    # A bare << is the merge key; a quoted '<<' is an ordinary string. (An explicit !!merge tag is refused as a tag.)
    return event.value == "<<" and not event.style


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0] if str(error).strip() else error.__class__.__name__
