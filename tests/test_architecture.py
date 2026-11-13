"""The rules that keep the four packages independent.

These are not style checks: the server image must not carry the client's
dependencies, the frozen client must not drag in the server's, and neither
can be refactored freely if they import each other. The direction is
``client -> contracts <- server -> pipeline``: the server is the protocol, the
pipeline everything that makes a CV, and the pipeline knows nothing of HTTP
or of the wire format.
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
    "client": REPO_ROOT / "client" / "src" / "cv_client",
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


def test_the_client_never_imports_the_server():
    assert "cv_server" not in imports_of(PACKAGES["client"])


def test_the_server_never_imports_the_client():
    assert "cv_client" not in imports_of(PACKAGES["server"])


def test_the_client_never_imports_the_pipeline():
    assert "cv_pipeline" not in imports_of(PACKAGES["client"])


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


def test_the_client_carries_no_server_side_dependency():
    """No LaTeX, no model SDK, no templating in the shipped executable."""
    forbidden = {"openai", "jinja2", "pypdf", "fastapi", "uvicorn", "subprocess",
                 "langchain_core", "langchain_openai", "langchain_deepseek", "langgraph"}
    assert not (imports_of(PACKAGES["client"]) & forbidden - {"subprocess"})
    # subprocess is allowed in exactly one place: probing the clipboard helper.
    users = [p.name for p in PACKAGES["client"].rglob("*.py")
             if "import subprocess" in p.read_text(encoding="utf-8")]
    assert users == ["clipboard.py"]


def test_the_server_carries_no_client_side_dependency():
    """The image has no clipboard and no spreadsheet."""
    assert not (imports_of(PACKAGES["server"]) & {"pyperclip", "openpyxl"})


def client_modules_used_by(subpackage: Path) -> Set[str]:
    """Every ``cv_client.<module>`` a client subpackage imports, however spelled."""
    found: Set[str] = set()
    for path in subpackage.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".") for alias in node.names]
                found.update(parts[1] for parts in names
                             if parts[0] == "cv_client" and len(parts) > 1)
            elif isinstance(node, ast.ImportFrom):
                parts = (node.module or "").split(".")
                if node.level == 0 and parts[0] == "cv_client" and len(parts) > 1:
                    found.add(parts[1])
                elif node.level == 2 and node.module:  # from ..module import x
                    found.add(parts[0])
                elif node.level == 2:  # from .. import module
                    found.update(alias.name for alias in node.names)
    return found


def test_the_stages_never_reach_back_into_the_modes_or_the_sources():
    """``stages/`` is the one pipeline every mode builds on. It uses the shared
    modules (the API, the workspace, the spreadsheet) but never a mode, and
    never where a posting comes from: a new source is a new ``JDSource``,
    handed in by its mode, and no change here."""
    used = client_modules_used_by(PACKAGES["client"] / "stages")
    assert "workspace" in used, "the check must see the imports it is checking"
    assert not used & {"modes", "sources"}
