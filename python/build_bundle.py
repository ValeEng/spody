"""Build the SpOdy desktop bundle (one-folder PyInstaller artifact).

Run from the `python/` directory:

    python build_bundle.py
    python build_bundle.py --spody-exe ../build/Release/spody.exe
    python build_bundle.py --clean-only

What it does:

  1. Wipes the previous `build/` and `dist/` so stale hooks / orphaned
     binaries don't leak across iterations.
  2. Runs PyInstaller against `spody_gui.spec` (the spec controls
     hidden imports, data files, and the windowed/one-folder layout).
  3. Copies the C runner `spody.exe` next to `spody-gui.exe` so the
     two parts of the application ship as one unit.
  4. Creates an empty `data/` folder next to the exes so the wizard's
     "next to executable" default points somewhere writable from the
     first launch.
  5. Prints a final summary (total size, layout).

The output bundle lives at `dist/spody-gui/`. Zip that folder and
ship it -- the user extracts anywhere and double-clicks
`spody-gui.exe`.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE        = Path(__file__).resolve().parent             # python/
REPO_ROOT   = HERE.parent                                  # spody/
DEFAULT_C   = REPO_ROOT / "build" / "Release" / "spody.exe"
SPEC_FILE   = HERE / "spody_gui.spec"
BUILD_DIR   = HERE / "build"
DIST_DIR    = HERE / "dist"
BUNDLE_DIR  = DIST_DIR / "spody-gui"


def main() -> int:
    args = _parse_args()
    _wipe(BUILD_DIR)
    _wipe(DIST_DIR)
    if args.clean_only:
        print("Cleaned build/ and dist/.")
        return 0

    spody_exe = Path(args.spody_exe).resolve()
    if not spody_exe.is_file():
        sys.stderr.write(
            f"\n[error] spody.exe not found at {spody_exe}\n"
            "Build the C side first (cmake --build <build> --config Release "
            "--target spody) or pass --spody-exe.\n"
        )
        return 2

    rc = _run_pyinstaller()
    if rc != 0:
        return rc

    _copy_runtime_pieces(spody_exe)
    _summary()
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build the SpOdy desktop bundle.")
    p.add_argument("--spody-exe", default=str(DEFAULT_C),
                   help=f"Path to the C runner spody.exe "
                        f"(default: {DEFAULT_C}).")
    p.add_argument("--clean-only", action="store_true",
                   help="Wipe build/ and dist/ then exit (no build).")
    return p.parse_args()


def _wipe(path: Path) -> None:
    """Best-effort recursive delete; ignores 'already gone'."""
    if not path.exists():
        return
    print(f"  rm -rf {path.relative_to(HERE)}/")
    shutil.rmtree(path, ignore_errors=True)


def _run_pyinstaller() -> int:
    """Shell out to PyInstaller's CLI driver. We use the current
    interpreter's pyinstaller (via `python -m PyInstaller`) so the
    bundle stays in lockstep with the venv the user already set up
    for the GUI."""
    print(f"\n>>> pyinstaller {SPEC_FILE.name}")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC_FILE)]
    return subprocess.call(cmd, cwd=str(HERE))


def _copy_runtime_pieces(spody_exe: Path) -> None:
    """Drop `spody.exe` next to `spody-gui.exe` and create an empty
    `data/` so the wizard's portable defaults Just Work."""
    print(f"\n>>> populating {BUNDLE_DIR.relative_to(HERE)}/")
    dst_exe = BUNDLE_DIR / spody_exe.name
    shutil.copy2(spody_exe, dst_exe)
    print(f"  cp {spody_exe.name}  ({_pretty_size(dst_exe)})")

    data_dir = BUNDLE_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / ".gitkeep").touch()
    print("  mkdir data/  (empty; wizard fills on first launch)")


def _summary() -> None:
    """Print the final bundle layout + total size as a sanity check."""
    if not BUNDLE_DIR.exists():
        print("[!] no bundle produced", file=sys.stderr)
        return
    total = sum(p.stat().st_size for p in BUNDLE_DIR.rglob("*") if p.is_file())
    print(f"\n>>> bundle: {BUNDLE_DIR}")
    print(f"    size  : {total / (1024*1024):.1f} MB "
          f"({sum(1 for _ in BUNDLE_DIR.rglob('*')):,} files)")
    print("    layout:")
    for p in sorted(BUNDLE_DIR.iterdir()):
        suffix = "/" if p.is_dir() else ""
        print(f"      {p.name}{suffix}")
    print("\nReady. Zip dist/spody-gui/ and ship.")


def _pretty_size(p: Path) -> str:
    n = p.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


if __name__ == "__main__":
    raise SystemExit(main())
