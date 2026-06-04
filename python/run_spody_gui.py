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
"""PyInstaller entry script.

Kept as a standalone module (rather than pointing PyInstaller at the
`__main__.py` inside the package) so the analysis step has a single
top-level script and the bundle name `spody-gui.exe` follows naturally
from this filename.

The actual application logic stays in `spody_gui.main` so `python -m
spody_gui` keeps working from a regular checkout.
"""
from spody_gui.main import main

if __name__ == "__main__":
    raise SystemExit(main())
