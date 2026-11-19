"""Working copies and create-only user profiles for the local GUI."""

import json
import os
import tempfile
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode, ScalarNode

from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import ConfigError, load_profile_text
from ecu_simulator.config.loader import read_yaml


def change_interface(text: str, interface: str) -> str:
    """Replace one YAML scalar without reformatting comments or the rest of the draft."""
    data = read_yaml(text, "working copy")
    root = YAML(typ="safe", pure=True).compose(text)
    node = root
    for key in ("transport", "interface"):
        if isinstance(node, MappingNode):
            node = next((v for k, v in node.value if k.value == key), None)
        else:
            node = None
    if not isinstance(node, ScalarNode):
        raise ConfigError("Enter transport.interface in the working copy first")
    replacement = json.dumps(interface)
    result = text[:node.start_mark.index] + replacement + text[node.end_mark.index:]
    # An alias can share this scalar with another field. Refuse any edit that
    # would unexpectedly change other profile settings.
    expected = {**data, "transport": {**data["transport"], "interface": interface}}
    if read_yaml(result, "working copy") != expected:
        raise ConfigError("Edit the shared YAML alias directly in the working copy")
    return result


class Profiles:
    def __init__(self, directory: Path | None = None) -> None:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.user = directory if directory is not None else base / "ecu-simulator" / "profiles"
        self.shipped = default_profile_path().parent

    def names(self) -> dict[str, list[str]]:
        return {source: sorted(p.stem for p in directory.glob("*.yaml") if p.is_file() and not p.is_symlink())
                for source, directory in (("shipped", self.shipped), ("user", self.user))}

    def _path(self, source: str, name: str) -> Path:
        if not name.strip() or name.startswith(".") or Path(name).name != name or "\x00" in name:
            raise ValueError("Choose a profile name without path components or a leading dot")
        if source not in ("user", "shipped"):
            raise ValueError("Unknown profile group")
        return (self.user if source == "user" else self.shipped) / f"{name}.yaml"

    def read(self, source: str, name: str) -> str:
        path = self._path(source, name)
        if path.is_symlink():
            raise ValueError("Choose a regular profile file")
        return path.read_text(encoding="utf-8")

    def save(self, name: str, text: str) -> None:
        path = self._path("user", name)
        load_profile_text(text)
        self.user.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.user) as staging:
            staging.write(text)
            staging.flush()
            try:
                os.link(staging.name, path)
            except FileExistsError:
                raise FileExistsError(f"A profile named {name!r} already exists") from None
