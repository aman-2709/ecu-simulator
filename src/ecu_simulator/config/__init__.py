"""Profile configuration: YAML on disk, validated into typed models before anything runs."""

from pathlib import Path

from ecu_simulator.config.errors import ConfigError
from ecu_simulator.config.loader import load_yaml, read_yaml
from ecu_simulator.config.schema import (
    EcuConfig,
    EndpointConfigModel,
    Profile,
    TransportConfig,
    UdsConfig,
    UdsSessionConfig,
    VehicleConfig,
    parse_profile,
)

DEFAULT_PROFILE = "src/ecu_simulator/profiles/ice_default.yaml"


def load_profile(path: str | Path) -> Profile:
    """Read and validate a profile; raises :class:`ConfigError` naming the file."""
    return parse_profile(load_yaml(path), source=str(path))


def load_profile_text(text: str, source: str = "working copy") -> Profile:
    return parse_profile(read_yaml(text, source), source=source)


__all__ = [
    "DEFAULT_PROFILE",
    "ConfigError",
    "EcuConfig",
    "EndpointConfigModel",
    "Profile",
    "TransportConfig",
    "UdsConfig",
    "UdsSessionConfig",
    "VehicleConfig",
    "load_profile",
    "load_profile_text",
    "load_yaml",
    "parse_profile",
]
