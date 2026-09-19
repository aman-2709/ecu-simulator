"""One error type for everything wrong with a profile, carrying the path to the problem."""

from __future__ import annotations


class ConfigError(ValueError):
    """A profile could not be read or does not satisfy the schema.

    The message names the profile, where one was read from disk, and the dotted path of
    each offending key. It is raised before any socket is opened.
    """
