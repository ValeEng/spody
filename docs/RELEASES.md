# Releasing SpOdy

End-to-end notes for cutting a new SpOdy release. The pipeline is
automated through GitHub Actions; this document explains the parts
the maintainer touches by hand.

## Versioning

Semantic versioning, with a pre-release suffix on anything that is
not yet a stable v1.0:

- `vMAJOR.MINOR.PATCH-alpha`  &mdash; early development, expect
  breaking changes.
- `vMAJOR.MINOR.PATCH-rc1`    &mdash; release candidate, expect
  only bug fixes before the final.
- `vMAJOR.MINOR.PATCH`        &mdash; stable release.

A tag matching any of the above triggers the
[`.github/workflows/release.yml`](../.github/workflows/release.yml)
pipeline. Tags whose suffix is `-alpha` / `-beta` / `-rc` are
automatically marked as **pre-release** in GitHub's UI so they do
not promote past the "Latest" badge.

The runtime version label lives in **four code files** that must be
touched in lockstep before tagging:

| File                                           | Constant            |
|------------------------------------------------|---------------------|
| `src/main.c`                                   | `SPODY_APP_VERSION` |
| `python/spody_gui/__init__.py`                 | `__version__`       |
| `python/pyproject.toml`                        | `version`           |
| `docs/user-manual/build_pdf.py`                | `APP_VERSION`       |

`pyproject.toml` follows PEP 440 syntax (`0.1.1b0`, `0.1.1rc1`),
the others follow plain semver (`0.1.1-beta`, `0.1.1-rc1`).

Two further docs carry the version as cosmetic text and should be
refreshed in the same commit so the published artifacts agree:

| File                                            | Where                |
|-------------------------------------------------|----------------------|
| `README.md`                                     | `spody info` example |
| `python/spody_gui/about_dialog.py`              | layout-sketch docstring |

The actual About dialog reads `__version__` at runtime; the
docstring is for code-reading humans only.

## Cutting a release

1. **Update the version constants** in the four files above; commit:

   ```sh
   git commit -am "release: bump to v0.1.1-beta"
   ```

2. **Tag the commit** with an annotated tag matching the same
   version:

   ```sh
   git tag -a v0.1.1-beta -m "v0.1.1-beta: first public alpha"
   git push origin main
   git push origin v0.1.1-beta
   ```

3. **Wait for CI** &mdash; the `release` workflow fires on the tag
   push. The three build jobs (Windows / Linux / macOS arm64) run
   in parallel, taking ~10 minutes per OS. Watch the run from
   `https://github.com/<owner>/<repo>/actions`.

4. **Review the draft release**. When all jobs are green, the
   `release` job opens a **draft** release at the tag URL with all
   six assets attached:

   ```
   spody-gui-v0.1.1-beta-win64.zip          + .sha256
   spody-gui-v0.1.1-beta-linux-x86_64.tar.gz + .sha256
   spody-gui-v0.1.1-beta-macos-arm64.zip    + .sha256
   ```

5. **Polish the release notes**. The draft has GitHub's
   auto-generated notes (PR/commit summary since the previous tag).
   Trim them, group related items, and pin the headline change at
   the top.

6. **Click Publish release**. The badge flips to **pre-release** for
   `-alpha` / `-beta` / `-rc` tags or **Latest** for a stable tag.
   The release page becomes the canonical download URL:
   `github.com/<owner>/<repo>/releases/tag/v0.1.1-beta`.

## What the workflow builds

Each OS runner runs the same steps:

1. Check out the repository with `submodules: recursive` so
   `spody-core` is present.
2. Install a C toolchain:
   - **Windows**: MinGW-w64 gcc via the
     [setup-mingw](https://github.com/egor-tensin/setup-mingw)
     action, plus cmake via chocolatey.
   - **Linux**: gcc + cmake are preinstalled on `ubuntu-latest`.
   - **macOS**: clang (system default) + cmake (preinstalled).
3. Install a Chromium-class browser for the user manual PDF step:
   - **Windows**: Microsoft Edge (ships with the OS).
   - **Linux**: `chromium-browser` via apt.
   - **macOS**: Chromium via Homebrew.
4. Configure cmake with `-DCMAKE_BUILD_TYPE=Release
   -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=ON
   -DSPODY_ENABLE_OMP_SIMD=ON`. The LTO + OMP-SIMD flags together
   give the engine the most aggressive optimisation profile its
   source tree supports today.
5. Build `spody` via `cmake --build`.
6. Set up a Python 3.9 venv and install the GUI package
   (`pip install -e ./python[dev]`). 3.9 is pinned because the
   PyInstaller bootloader's apiset-resolution interaction with
   `python311.dll` triggers a `LoadLibrary: access to invalid
   memory location` on some end-user Windows 10 builds. Bump to
   3.10+ once the runtime hook workaround lands.
7. Run `python python/build_bundle.py`. This auto-rebuilds the
   manual PDF (via the freshly-installed browser) and invokes
   PyInstaller to produce `python/dist/spody-gui/`.
8. Pack the bundle:
   - Windows: `Compress-Archive` &rArr; `.zip`.
   - macOS:   `ditto -c -k --sequesterRsrc` &rArr; `.zip`
     (preserves Apple resource forks).
   - Linux:   `tar -czf` &rArr; `.tar.gz`.
9. Compute the SHA-256 hash into a `<archive>.sha256` sidecar.
10. Upload `<archive>` + `<archive>.sha256` as workflow artifacts.

The `release` job then downloads every build's artifacts and
attaches them to the draft GitHub release.

## Re-running a release

If a release fails partway through (e.g. the macOS runner had a
transient brew error), delete the tag and recreate it:

```sh
git push --delete origin v0.1.1-beta
git tag -d v0.1.1-beta
git tag -a v0.1.1-beta -m "..."
git push origin v0.1.1-beta
```

The workflow's `concurrency` group cancels any in-flight run for
the same tag, so a quick re-tag does not collide.

## Local sanity check before tagging

You can dry-run the bundle pipeline locally (Windows-only today,
since the dev environment is set up there):

```powershell
cd python
.\.venv\Scripts\Activate.ps1
python build_bundle.py
```

Then unzip `python/dist/spody-gui/` into a separate folder and run
`spody-gui.exe` from there. Verify the wizard pops, the manual
opens from Help, and a simple LRO propagation completes. If
everything is green, push the tag.

## Manual dispatch (debugging the workflow)

The release workflow has `workflow_dispatch:` enabled, so you can
trigger it from the Actions tab without pushing a tag. In that mode
the build jobs run normally (and produce artifacts) but the
`release` job is gated on `startsWith(github.ref, 'refs/tags/v')`
and so does **not** run &mdash; useful when you want to validate
a workflow change without producing a phantom release.
