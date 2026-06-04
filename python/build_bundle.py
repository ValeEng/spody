# Copyright 2026 ValeEng
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
SPEC_FILE   = HERE / "spody_gui.spec"
BUILD_DIR   = HERE / "build"
DIST_DIR    = HERE / "dist"
BUNDLE_DIR  = DIST_DIR / "spody-gui"
MANUAL_DIR  = REPO_ROOT / "docs" / "user-manual"
MANUAL_PDF  = MANUAL_DIR / "spody-user-manual.pdf"

# Where the C engine lands after a cmake build. CMake's multi-config
# generators (MSVC) put the binary under <build>/<Config>/<name>;
# single-config generators (Makefiles, Ninja) put it under
# <build>/<name>. We try both and the platform suffix on the name.
import platform as _platform
_EXE_SUFFIX = ".exe" if _platform.system() == "Windows" else ""
_DEFAULT_C_CANDIDATES = [
    REPO_ROOT / "build" / "Release" / f"spody{_EXE_SUFFIX}",  # MSVC
    REPO_ROOT / "build" / f"spody{_EXE_SUFFIX}",              # Make/Ninja
]
DEFAULT_C = next((p for p in _DEFAULT_C_CANDIDATES if p.is_file()),
                 _DEFAULT_C_CANDIDATES[0])


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

    # Rebuild the user manual PDF if we can; non-fatal on failure so
    # bundling proceeds even when (say) Edge headless or the python
    # markdown libs are unavailable. The spec file then sees no PDF
    # and the bundle ships without the manual; Help > User manual
    # surfaces a clear message in that case.
    _rebuild_manual()

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


def _rebuild_manual() -> None:
    """Try to (re)build the user-manual PDF. Best-effort: a failure
    here is logged but does not abort the bundle build."""
    builder = MANUAL_DIR / "build_pdf.py"
    if not builder.is_file():
        print("[warn] no docs/user-manual/build_pdf.py -- "
              "bundle will ship without the manual")
        return
    print(f"\n>>> rebuilding {MANUAL_PDF.relative_to(REPO_ROOT)}")
    rc = subprocess.call([sys.executable, str(builder)],
                         cwd=str(MANUAL_DIR))
    if rc != 0 or not MANUAL_PDF.is_file():
        print("[warn] manual PDF build failed -- bundle will ship "
              "without the manual (Help > User manual will report "
              "'not found')")


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
    # OS-specific advice for packaging the bundle into a release
    # artifact. The GitHub Actions workflow does this automatically;
    # for local dev the commands below are convenient.
    if _platform.system() == "Windows":
        print("\nReady. Pack with:\n  Compress-Archive -Path "
              f"{BUNDLE_DIR} -DestinationPath spody-gui-<version>-win64.zip")
    elif _platform.system() == "Darwin":
        print("\nReady. Pack with:\n  ditto -c -k --sequesterRsrc "
              f"{BUNDLE_DIR} spody-gui-<version>-macos-arm64.zip")
    else:
        print("\nReady. Pack with:\n  tar -C dist -czf "
              "spody-gui-<version>-linux-x86_64.tar.gz spody-gui")


def _pretty_size(p: Path) -> str:
    n = p.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


if __name__ == "__main__":
    raise SystemExit(main())
