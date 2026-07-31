"""Package the DECtalk DTC-01 synth driver as an installable .nvda-addon.

Ships **no ROM bytes** -- the DTC-01 firmware is DEC/Fonix copyright
(DESIGN.md §0). The packaged addon refuses to build if anything ROM-shaped
sneaks in. At runtime the driver looks for a user-supplied dump in, in
order: $DTC01_ROM_DIR, <NVDA config>/dectalkDtc01/roms, <addon>/roms.

Usage:  python tools/make_addon.py [--arch x64] [--version X.Y.Z]
Output: build/dectalkDtc01-<version>-<arch>.nvda-addon
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

NVDA_DIR = Path(r"C:\Program Files\NVDA")
VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def read_version():
	"""The single source of truth for the add-on version.

	The manifest, the package filename and the running add-on all have to
	agree: the auto-updater compares a GitHub release tag against the
	installed manifest, so a version living in two places means it either
	misses updates or offers the same one forever.
	"""
	if not VERSION_FILE.is_file():
		raise SystemExit(f"missing {VERSION_FILE} -- it holds the add-on version")
	return VERSION_FILE.read_text(encoding="utf-8").strip()


def git_tag():
	"""Latest git tag, so a mismatch with VERSION can be pointed out."""
	try:
		out = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
							 cwd=ROOT, capture_output=True, text=True)
		return out.stdout.strip() if out.returncode == 0 else ""
	except Exception:
		return ""

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addon"
BUILD = ROOT / "build"

PKG_REL = Path("synthDrivers/dectalkDtc01")

MANIFEST = """name = dectalkDtc01
summary = "DECtalk DTC-01"
description = \"\"\"Speech synthesizer driver for the 1984 DEC DECtalk DTC-01. The original 68000 and TMS32010 firmware runs in an emulator inside NVDA, so the voice is the real hardware's, not a recreation.

Requires your own dump of the DTC-01 firmware ROMs; none are included. Place the ROM files in the "dectalkDtc01\\\\roms" folder inside your NVDA user configuration directory.\"\"\"
author = "dtc-01 project"
version = {version}
minimumNVDAVersion = 2025.1
lastTestedNVDAVersion = 2026.1
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="x64", choices=["x64", "x86"],
                    help="DLL to bundle; NVDA 2026 is x64 (DESIGN.md §14)")
    ap.add_argument("--version", default=None,
                    help="override the VERSION file (not normally needed)")
    args = ap.parse_args()

    version = args.version or read_version()
    tag = git_tag()
    if tag and tag.lstrip("vV") != version:
        print(f"NOTE: VERSION is {version} but the latest git tag is {tag} -- "
              f"tag the release v{version} so the updater matches.")

    # Gate: every NVDA symbol we import must exist in the real NVDA install.
    # The offline harness uses stubs, so only this catches a wrong import
    # path -- which is exactly how a bad `driverHandler` import once shipped
    # and stopped the synth loading at all.
    # Only a missing NVDA install is tolerated. Anything else -- including a
    # bug in the gate itself -- must fail loudly: a check that silently
    # skips is worse than no check, and this one skipped on a NameError the
    # first time it ran.
    checker = ROOT / "tools" / "check_nvda_api.py"
    if not NVDA_DIR.is_dir():
        print(f"(NVDA API check skipped: no NVDA install at {NVDA_DIR})")
    else:
        rc = subprocess.call([sys.executable, str(checker)])
        if rc != 0:
            print("ERROR: NVDA API check failed; refusing to package.")
            return 1

    dll = BUILD / f"dtc01_{args.arch}.dll"
    if not dll.is_file():
        print(f"ERROR: {dll} not found. Run  tools\\build_native.bat {args.arch}")
        return 1

    stage = BUILD / f"_stage_addon_{args.arch}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    (stage / "manifest.ini").write_text(MANIFEST.format(version=version), encoding="utf-8")

    dst_pkg = stage / PKG_REL
    shutil.copytree(
        ADDON / PKG_REL, dst_pkg,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.dll", "*.lib",
                                      "*.exp", "roms"),
    )
    (stage / "synthDrivers" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(dll, dst_pkg / "emu" / dll.name)

    # The update checker ships; the native smoke test does not -- it is a
    # development tool that announces itself on every NVDA start.
    plugins = stage / "globalPlugins"
    plugins.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ADDON / "globalPlugins" / "dtc01Updater.py",
                 plugins / "dtc01Updater.py")

    out = BUILD / f"dectalkDtc01-{version}-{args.arch}.nvda-addon"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(stage).as_posix())

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        total = sum(i.file_size for i in z.infolist())

    # Hard guard: never ship firmware.
    suspect = [n for n in names
               if n.lower().endswith((".bin", ".rom", ".e1", ".e2", ".u21"))
               or "rom" in Path(n).parent.name.lower()]
    if suspect:
        print("ERROR: refusing to ship, ROM-like files present:", suspect)
        out.unlink()
        return 1

    print(f"built {out}")
    print(f"  {len(names)} files, {out.stat().st_size/1024:.0f} KB packed "
          f"/ {total/1024:.0f} KB unpacked")
    for n in names:
        print("   ", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
