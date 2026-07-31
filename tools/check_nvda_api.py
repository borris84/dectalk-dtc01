"""Dev-only: verify every NVDA symbol the addon imports actually exists.

The offline harness stubs NVDA's modules, so a wrong import path passes
there and only fails when NVDA tries to load the driver -- which is how
`from driverHandler import NumericDriverSetting` shipped despite a green
test run (it lives in autoSettingsUtils.driverSetting on NVDA 2019.3+).

This checks the addon's imports against the real NVDA library.zip instead
of against hand-written stubs. Run it before packaging.

Usage: python tools/check_nvda_api.py [path-to-NVDA]
"""

from __future__ import annotations

import ast
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addon"
DEFAULT_NVDA = Path(r"C:\Program Files\NVDA")

# Modules the addon provides itself or that are stdlib -- not NVDA's problem.
SKIP_PREFIXES = ("synthDrivers.dectalkDtc01", ".", "__future__")


def _stdlib_names():
    return set(sys.stdlib_module_names)


def load_nvda_index(nvdaDir: Path):
    """Map module name -> compiled bytes, from NVDA's library.zip and its
    top-level .pyd/.dll-backed modules."""
    lib = nvdaDir / "library.zip"
    if not lib.is_file():
        raise SystemExit(f"NVDA library.zip not found at {lib}")
    index = {}
    with zipfile.ZipFile(lib) as z:
        for name in z.namelist():
            if not name.endswith(".pyc"):
                continue
            mod = name[:-4].replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            index[mod] = z.read(name)
    # extension modules sitting next to nvda.exe
    for p in nvdaDir.glob("*.pyd"):
        index.setdefault(p.stem.split(".")[0], b"")
    return index


def collect_imports(path: Path):
    """(module, symbol|None, lineno, file) for every import in the addon."""
    out = []
    for py in sorted(path.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            out.append(("<syntax error>", str(e), e.lineno or 0, py))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    out.append((a.name, None, node.lineno, py))
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import, ours
                    continue
                for a in node.names:
                    out.append((node.module or "", a.name, node.lineno, py))
    return out


def guarded_lines(py: Path):
    """Line numbers inside a try/except ImportError, which are allowed to
    reference symbols that may not exist."""
    safe = set()
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return safe
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
            (h.type is None
             or (isinstance(h.type, ast.Name)
                 and ("Error" in h.type.id or h.type.id.endswith("Exception"))))
            for h in node.handlers
        ):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    safe.add(child.lineno)
    return safe


def main() -> int:
    nvdaDir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NVDA
    index = load_nvda_index(nvdaDir)
    stdlib = _stdlib_names()
    print(f"NVDA at {nvdaDir}: {len(index)} modules indexed")

    problems = []
    guardedCache = {}
    for module, symbol, lineno, py in collect_imports(ADDON):
        if module.startswith(SKIP_PREFIXES) or not module:
            continue
        top = module.split(".")[0]
        if top in stdlib:
            continue
        if py not in guardedCache:
            guardedCache[py] = guarded_lines(py)
        isGuarded = lineno in guardedCache[py]

        if module not in index:
            if not isGuarded:
                problems.append(
                    f"{py.relative_to(ROOT)}:{lineno}: module '{module}' not in NVDA")
            continue
        if symbol and symbol != "*":
            blob = index[module]
            # "from pkg import mod" imports a submodule, which won't appear
            # as a name inside the package's own bytecode.
            if f"{module}.{symbol}" in index:
                continue
            if blob and symbol.encode() not in blob:
                where = "guarded" if isGuarded else "UNGUARDED"
                msg = (f"{py.relative_to(ROOT)}:{lineno}: '{symbol}' not found in "
                       f"'{module}' ({where})")
                if not isGuarded:
                    problems.append(msg)
                else:
                    print("  note:", msg)

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  ", p)
        print(f"\n{len(problems)} problem(s) -- these would fail at NVDA load time")
        return 1
    print("all NVDA imports resolve against the real NVDA install")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
