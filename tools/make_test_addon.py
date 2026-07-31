"""Dev-only: package the native-core smoke test as an installable
.nvda-addon so it can be exercised inside real NVDA.

This builds a TEST addon, not the shipping synth driver: it carries the
globalPlugin from addon/globalPlugins/dtc01NativeTest.py, the emulator
package, and the architecture-matching DLL. It deliberately ships **no
ROMs** (DESIGN.md §0) -- the plugin reads them from a local path.

Usage:  python tools/make_test_addon.py [--arch x64]
Output: build/dtc01-nativetest-<arch>.nvda-addon
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addon"
BUILD = ROOT / "build"

MANIFEST = """name = dtc01NativeTest
summary = "DECtalk DTC-01 native core smoke test (DEV)"
description = \"\"\"Development-only smoke test. Loads the native DTC-01 emulator inside NVDA, boots the real firmware, synthesises a phrase, and reports the result to the log and via speech. Not a synth driver; ships no ROMs.\"\"\"
author = "dtc-01 project"
version = 0.0.1
minimumNVDAVersion = 2025.1
lastTestedNVDAVersion = 2026.1
"""

# Emulator package files to include (source of truth lives in addon/).
PKG_REL = Path("synthDrivers/dectalkDtc01")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="x64", choices=["x64", "x86"],
                    help="which DLL to bundle (NVDA 2026 is x64 -- see DESIGN.md §14)")
    args = ap.parse_args()

    dll = BUILD / f"dtc01_{args.arch}.dll"
    if not dll.is_file():
        print(f"ERROR: {dll} not found. Run tools\\build_native.bat {args.arch} first.")
        return 1

    stage = BUILD / f"_stage_testaddon_{args.arch}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    # manifest
    (stage / "manifest.ini").write_text(MANIFEST, encoding="utf-8")

    # globalPlugin
    gp_src = ADDON / "globalPlugins" / "dtc01NativeTest.py"
    gp_dst = stage / "globalPlugins"
    gp_dst.mkdir(parents=True)
    shutil.copy2(gp_src, gp_dst / gp_src.name)

    # emulator package (python only -- skip caches and any stray DLLs)
    src_pkg = ADDON / PKG_REL
    dst_pkg = stage / PKG_REL
    shutil.copytree(
        src_pkg, dst_pkg,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.dll", "*.lib", "*.exp"),
    )
    # synthDrivers must be a package for `from synthDrivers.dectalkDtc01...` to import
    init = stage / "synthDrivers" / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")

    # the architecture-matching DLL, next to native.py where _find_dll looks first
    shutil.copy2(dll, dst_pkg / "emu" / dll.name)

    out = BUILD / f"dtc01-nativetest-{args.arch}.nvda-addon"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(stage).as_posix())

    # sanity: make sure we didn't accidentally bundle ROM data
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    bad = [n for n in names if n.lower().endswith((".bin", ".rom")) or "roms_extracted" in n.lower()]
    if bad:
        print("ERROR: refusing to ship, ROM-like files found in package:", bad)
        out.unlink()
        return 1

    print(f"built {out}  ({out.stat().st_size/1024:.0f} KB, {len(names)} files)")
    print("contents:")
    for n in names:
        print("   ", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
