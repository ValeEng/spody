# Build a standalone Windows .exe for the SpOdy GUI using PyInstaller.
#
# Run from inside the python/ directory, with the dev venv activated:
#   cd python
#   .venv\Scripts\Activate.ps1
#   pip install -e .[dev]    # ensures PyInstaller is present
#   .\build_exe.ps1
#
# Output: dist\spody-gui.exe  (one file, ~200 MB once bundled with Qt).
# Ship it alongside spody.exe + data files; no Python install on the
# target machine.

$ErrorActionPreference = 'Stop'

# --windowed   : no console window on launch (uses pythonw under the hood)
# --onefile    : single .exe (extracts to a temp dir at run-time)
# --name       : output filename
# --noconfirm  : overwrite previous dist\ without prompting
pyinstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name spody-gui `
    spody_gui\__main__.py

Write-Host ""
Write-Host "Built: $((Resolve-Path dist\spody-gui.exe).Path)"
Write-Host "Size : $([math]::Round((Get-Item dist\spody-gui.exe).Length / 1MB, 1)) MB"
