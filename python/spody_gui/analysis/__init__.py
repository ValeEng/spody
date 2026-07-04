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

"""Analysis machinery behind the Analysis tab.

Layout (acyclic, bottom-up):

    spec         PlotSpec + plot-fn signatures
    context      PlotContext resolved from the run-folder snapshot
    scene3d      shared VTK decoration (triads, third bodies, PA anim)
    plots_*      one module per view family, each exporting SPECS
    overlays     N-file overlay lifters
    registry     per-kind dispatch tables assembled from the SPECS
    info         Info-tab row builders
    table_model  Qt model for the Tables tab

Adding a view: write the function + append a PlotSpec to the SPECS
list of the matching plots module. Adding a file kind: see
registry.py's docstring.
"""

from .context import CR3BPPrimary, PlotContext, resolve_run_context
from .registry import KIND_LABEL, PLOTS, READERS, detect_kind
from .spec import PlotSpec
from .table_model import NumpyTableModel

__all__ = [
    "CR3BPPrimary",
    "KIND_LABEL",
    "NumpyTableModel",
    "PLOTS",
    "PlotContext",
    "PlotSpec",
    "READERS",
    "detect_kind",
    "resolve_run_context",
]
