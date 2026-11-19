import ast
import pathlib
import sys

OBSERVE = pathlib.Path(__file__).resolve().parents[3] / "src" / "ecu_simulator" / "observe"


def test_observe_imports_only_stdlib_and_the_package():
    # 0010 §4.1: observe has no third-party dependency, so ordinary CI proves it on every run.
    offenders = []
    for path in sorted(OBSERVE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            )
            for name in names:
                top = name.split(".")[0]
                if top != "ecu_simulator" and top not in sys.stdlib_module_names and top != "__future__":
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
