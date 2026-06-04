# =============================================================================
# add_license_header.ps1
# -----------------------------------------------------------------------------
# Prepends the Apache 2.0 license header to every source file under src/,
# python/, and docs/user-manual/, skipping files that already contain a
# copyright notice. Mirrors the spody-core script of the same name; this
# one also covers .py and .spec (PyInstaller) files in the GUI tree.
#
# USAGE (from the repo root, in PowerShell):
#   .\add_license_header.ps1
#
# If PowerShell refuses to run the script due to execution policy:
#   powershell -ExecutionPolicy Bypass -File .\add_license_header.ps1
# =============================================================================

$cHeader = @"
/*
 * Copyright 2026 ValeEng
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

"@

$pyHeader = @"
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

"@

# (folder, glob filters, header) triples. Each glob may match either
# a name (forwarded to -Include) or .spec (forwarded to -Filter).
$targets = @(
    @{ folder = "src";                        filters = @("*.c", "*.h"); header = $cHeader  },
    @{ folder = "python";                     filters = @("*.py");       header = $pyHeader },
    @{ folder = "python";                     filters = @("*.spec");     header = $pyHeader },
    @{ folder = "docs/user-manual";           filters = @("*.py");       header = $pyHeader }
)

$modified = 0
$skipped  = 0

foreach ($t in $targets) {
    $folder  = $t.folder
    $filters = $t.filters
    $header  = $t.header
    if (-not (Test-Path $folder)) {
        Write-Host "Folder '$folder' not found, skipping." -ForegroundColor Yellow
        continue
    }

    Get-ChildItem -Path $folder -Recurse -Include $filters | Where-Object {
        # Don't recurse into virtualenvs, PyInstaller build dirs, or
        # __pycache__ -- those are not source.
        $_.FullName -notmatch '\\(\.venv|venv|build|dist|__pycache__)\\'
    } | ForEach-Object {
        $file = $_.FullName
        $content = Get-Content -Path $file -Raw -Encoding UTF8

        if ($content -match "(?i)copyright") {
            Write-Host "SKIP  $file" -ForegroundColor DarkGray
            $script:skipped++
            return
        }

        Set-Content -Path $file -Value ($header + $content) -Encoding UTF8 -NoNewline
        Write-Host "OK    $file" -ForegroundColor Green
        $script:modified++
    }
}

Write-Host ""
Write-Host "Done. Modified: $modified, Skipped: $skipped." -ForegroundColor Cyan
