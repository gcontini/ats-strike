"""The rules that keep the packages independent.

These are not style checks: the contracts are imported by the client too (the
ats-strike-client repository, which has its own copy of these rules), so they
must stay cheap, and the pipeline must stay a library. The direction is
``contracts <- server -> pipeline_v1, pipeline_v2 -> common``: the server is
the protocol, a pipeline everything that makes a CV, and ``common`` what the
pipelines share; none of them knows HTTP or the wire format. Pipeline 2.0 sits
beside v1 under the same rules, and takes only ``common``, never v1.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Set

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "contracts": REPO_ROOT / "src" / "contracts" / "src" / "cv_contracts",
    "pipeline": REPO_ROOT / "src" / "pipeline_v1",
    "pipeline_v2": REPO_ROOT / "src" / "pipeline_v2",
    "common": REPO_ROOT / "src" / "common",
    "server": REPO_ROOT / "src" / "cv_server",
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
        "cv_contracts",
        "cv_server",
        "cv_client",
        "fastapi",
        "starlette",
        "uvicorn",
    }


def test_pipeline_v2_imports_none_of_the_others_and_knows_no_http():
    assert not imports_of(PACKAGES["pipeline_v2"]) & {
        "cv_contracts",
        "cv_server",
        "cv_client",
        "fastapi",
        "starlette",
        "uvicorn",
    }


def test_pipeline_v2_imports_nothing_from_pipeline_v1():
    """What the two share is in ``common``."""
    used = imports_of(PACKAGES["pipeline_v2"])
    assert "common" in used, "the check must see the imports it is checking"
    assert "pipeline_v1" not in used


def test_common_imports_no_pipeline_and_knows_no_http():
    """The pipelines' shared library: below them both, and a library as they are."""
    assert not imports_of(PACKAGES["common"]) & {
        "pipeline_v1",
        "pipeline_v2",
        "cv_server",
        "cv_contracts",
        "cv_client",
        "fastapi",
        "starlette",
        "uvicorn",
    }


def test_no_model_role_name_is_in_both_pipelines():
    """An ``ATS_<ROLE>_<FIELD>`` env override must reach one pipeline only."""
    from pipeline_v1.facade import MODEL_ROLES
    from pipeline_v2 import ROLES

    assert not set(MODEL_ROLES) & set(ROLES)


def test_pipeline_v1_never_imports_pipeline_v2():
    assert "pipeline_v2" not in imports_of(PACKAGES["pipeline"])


def test_the_server_serves_both_pipelines():
    assert {"pipeline_v1", "pipeline_v2"} <= imports_of(PACKAGES["server"])


def test_the_server_carries_no_model_latex_or_templating_library():
    """Those are the pipeline's; the server is the protocol."""
    used = imports_of(PACKAGES["server"])
    assert "pipeline_v1" in used, "the check must see the imports it is checking"
    assert not used & {"openai", "langgraph", "jinja2", "pypdf"}
    assert not [name for name in used if name.startswith("langchain")]


def test_the_server_uses_only_the_pipelines_top_level():
    """``from pipeline_v1 import ...``, never a submodule: the facade is the
    whole interface, so a pipeline of another version can sit behind it."""
    reached = []
    for path in PACKAGES["server"].rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                reached += [a.name for a in node.names if a.name.startswith(("pipeline_v1.", "pipeline_v2."))]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith(("pipeline_v1.", "pipeline_v2.")):
                    reached.append(node.module)
    assert reached == []


def test_the_contracts_depend_on_pydantic_and_nothing_else():
    """It is imported by a web server and by a frozen binary; it has to stay
    cheap."""
    heavy = {
        "fastapi",
        "httpx",
        "openai",
        "jinja2",
        "openpyxl",
        "uvicorn",
        "pypdf",
        "starlette",
        "pyperclip",
        "langchain_core",
        "langchain_openai",
        "langchain_deepseek",
        "langgraph",
    }
    assert not (imports_of(PACKAGES["contracts"]) & heavy)


def test_the_server_carries_no_client_side_dependency():
    """The image has no clipboard and no spreadsheet."""
    assert not (imports_of(PACKAGES["server"]) & {"pyperclip", "openpyxl"})
