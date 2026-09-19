"""Profile configuration: YAML on disk, validated into typed models before anything runs."""

from pathlib import Path

from ecu_simulator.config.errors import ConfigError
from ecu_simulator.config.loader import load_yaml
from ecu_simulator.config.schema import (
    EcuConfig,
    EndpointConfigModel,
    Profile,
    TransportConfig,
    VehicleConfig,
    parse_profile,
)

DEFAULT_PROFILE = "profiles/ice_default.yaml"


def load_profile(path: str | Path) -> Profile:
    """Read and validate a profile; raises :class:`ConfigError` naming the file."""
    return parse_profile(load_yaml(path), source=str(path))


__all__ = [
    "DEFAULT_PROFILE",
    "ConfigError",
    "EcuConfig",
    "EndpointConfigModel",
    "Profile",
    "TransportConfig",
    "VehicleConfig",
    "load_profile",
    "load_yaml",
    "parse_profile",
]
