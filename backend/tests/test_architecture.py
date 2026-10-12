"""The code's layout, checked: every module belongs to one area, and an area
only imports from the areas beneath it.

CLAUDE.md describes the same map in words. This is the half an agent can't
miss: put code in the wrong place, or reach upwards from a low area into a
high one, and the suite fails with the rule it broke. Reads the source with
`ast` and imports nothing, so a module with an import-time side effect
can't skew it.

When this fails because a module is new, decide which area it belongs to,
add it to AREAS below, and update CLAUDE.md's map to match. When it fails
because of an import, move the code to the area it belongs in rather than
loosening the rule."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# Lowest first. An area may import from itself and from any area listed
# before it, never after. `api` and `cli` are the two entry points that
# sit on top of everything; neither imports the other.
LAYERS = [
    "core",
    "db",
    "catalogue",
    "qbittorrent",
    "matching",
    "updater",
    "box",
    "plex",
    "library",
    "downloads",
    "api",
    "cli",
]

# Pure logic: these import nothing from the app at all, so their rules can
# be tested without TMDB, Plex or a browser (see feed.py's docstring).
PURE = {"recommendations"}

# A module inside a package named for an area (app/<area>/...) belongs to
# it. Only a module outside one needs an entry here — today, just the CLI
# entry point, which stays at app/cli.py so `python -m app.cli` keeps
# working on every installed box.
AREAS = {
    "app.cli": "cli",
}

# Only the HTTP layer speaks HTTP. Everything else is plain Python that the
# API, the CLI and the worker all call.
WEB_FRAMEWORKS = {"fastapi", "starlette", "slowapi"}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(APP.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


MODULES = {_module_name(path): path for path in sorted(APP.rglob("*.py"))}


def _area(module: str) -> str | None:
    if module in AREAS:
        return AREAS[module]
    parts = module.split(".")
    if len(parts) >= 2 and (parts[1] in LAYERS or parts[1] in PURE):
        return parts[1]
    return None


def _is_type_checking_block(node: ast.AST) -> bool:
    return isinstance(node, ast.If) and (
        (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING")
        or (isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING")
    )


def _imports(module: str) -> list[tuple[int, str]]:
    """(line, imported module) for every import that runs, skipping
    `if TYPE_CHECKING:` blocks — those never execute, so they can't create
    the cycle the layering exists to prevent."""
    path = MODULES[module]
    tree = ast.parse(path.read_text(), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if _is_type_checking_block(child):
                for orelse in child.orelse:
                    visit(orelse)
                continue
            if isinstance(child, ast.Import):
                found.extend((child.lineno, alias.name) for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                base = child.module or ""
                if child.level:
                    anchor = package.split(".")
                    anchor = anchor[: len(anchor) - (child.level - 1)]
                    base = ".".join(anchor + ([base] if base else []))
                for alias in child.names:
                    submodule = f"{base}.{alias.name}"
                    found.append((child.lineno, submodule if submodule in MODULES else base))
            visit(child)

    visit(tree)
    return found


def _app_imports(module: str) -> list[tuple[int, str]]:
    return [(line, name) for line, name in _imports(module) if name == "app" or name.startswith("app.")]


def _where(module: str, line: int) -> str:
    return f"{MODULES[module].relative_to(APP.parent)}:{line}"


def test_every_module_belongs_to_an_area():
    unplaced = [m for m in MODULES if m not in ("app", "app.api") and _area(m) is None]
    assert not unplaced, (
        f"These modules belong to no area: {unplaced}. Add each to AREAS in "
        "tests/test_architecture.py, and to CLAUDE.md's map."
    )


def test_areas_only_import_from_beneath_themselves():
    rank = {area: i for i, area in enumerate(LAYERS)}
    broken = []
    for module in MODULES:
        area = _area(module)
        if area not in rank:
            continue
        for line, imported in _app_imports(module):
            target = _area(imported)
            if target is None or target == area or target in PURE:
                continue
            if rank[target] > rank[area] or {area, target} == {"api", "cli"}:
                broken.append(f"{_where(module, line)}: {area} imports {imported} ({target})")
    assert not broken, (
        "An area may only import from the areas listed before it in LAYERS:\n  " + "\n  ".join(broken)
    )


def test_pure_areas_import_nothing_from_the_app():
    broken = [
        f"{_where(module, line)}: imports {imported}"
        for module in MODULES
        if _area(module) in PURE
        for line, imported in _app_imports(module)
    ]
    assert not broken, "Pure-logic modules take every input as an argument:\n  " + "\n  ".join(broken)


def test_only_the_api_speaks_http():
    broken = [
        f"{_where(module, line)}: imports {imported}"
        for module in MODULES
        if _area(module) != "api"
        for line, imported in _imports(module)
        if imported.split(".")[0] in WEB_FRAMEWORKS
    ]
    assert not broken, "Only app/api/ may import a web framework:\n  " + "\n  ".join(broken)


def test_the_checker_sees_imports():
    # Guards the guard: if parsing silently found nothing, every test above
    # would pass on an empty list.
    assert (_app_imports("app.downloads.worker")) and any(name == "app.core.config" for _, name in _app_imports("app.downloads.worker"))
    assert any(name.split(".")[0] == "fastapi" for _, name in _imports("app.api.main"))
