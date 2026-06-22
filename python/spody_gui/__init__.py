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
"""SpOdy desktop GUI.

A PySide6 frontend that edits TOML input files, launches the spody
executable as a subprocess, and streams its terminal output into an
embedded read-only pane. No C bindings -- the GUI talks to the
propagator entirely through the file-based interface (TOML in, binary
or CSV out).
"""

__version__ = "0.1.3-beta"
