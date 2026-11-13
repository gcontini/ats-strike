"""The rules that keep the three packages independent.

These are not style checks: the server image must not carry anything a client
needs, and neither half can be refactored freely if they import each other.
The direction is ``contracts <- server -> pipeline``: the server is the
protocol, the pipeline everything that makes a CV, and the pipeline knows
nothing of HTTP or of the wire format. The client is on main; it depends on
the contracts alone, which is why they are copied here byte for byte.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Set

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "contracts": REPO_ROOT / "contracts" / "src" / "cv_contracts",
    "pipeline": REPO_ROOT / "pipeline" / "src" / "cv_pipeline",
    "server": REPO_ROOT / "server" / "src" / "cv_server",
}


def imports_of(package: Path) -> Set[str]:
    """Every top-level module imported anywhere in a package."""
    found: Set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def test_the_server_never_imports_the_client():
    assert "cv_client" not in imports_of(PACKAGES["server"])


def test_the_pipeline_imports_none_of_the_others_and_knows_no_http():
    """It is a library: a server calls it, and it could be called by anything."""
    assert not imports_of(PACKAGES["pipeline"]) & {
        "cv_contracts", "cv_server", "cv_client", "fastapi", "starlette", "uvicorn",
    }


def test_the_server_carries_no_model_latex_or_templating_library():
    """Those are the pipeline's; the server is the protocol."""
    used = imports_of(PACKAGES["server"])
    assert "cv_pipeline" in used, "the check must see the imports it is checking"
    assert not used & {"openai", "langgraph", "jinja2", "pypdf"}
    assert not [name for name in used if name.startswith("langchain")]


def test_the_server_uses_only_the_pipelines_top_level():
    """``from cv_pipeline import ...``, never a submodule: the facade is the
    whole interface, so a pipeline of another version can sit behind it."""
    reached = []
    for path in PACKAGES["server"].rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                reached += [a.name for a in node.names if a.name.startswith("cv_pipeline.")]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith("cv_pipeline."):
                    reached.append(node.module)
    assert reached == []


def test_the_contracts_depend_on_pydantic_and_nothing_else():
    """It is imported by a web server and by a frozen binary; it has to stay
    cheap."""
    heavy = {"fastapi", "httpx", "openai", "jinja2", "openpyxl", "uvicorn", "pypdf",
             "starlette", "pyperclip", "langchain_core", "langchain_openai",
             "langchain_deepseek", "langgraph"}
    assert not (imports_of(PACKAGES["contracts"]) & heavy)


def test_the_server_carries_no_client_side_dependency():
    """The image has no clipboard and no spreadsheet."""
    assert not (imports_of(PACKAGES["server"]) & {"pyperclip", "openpyxl"})
