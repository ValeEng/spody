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

"""Building blocks behind the TOML form (Run tab).

    catalog     declarative tables (enums, tooltips, validators,
                units, output naming) mirroring the engine schema
    widgets     field factories (+ layout helpers, _AssetCombo)
    sections    one builder per TOML section
    visibility  XOR groups + dynamic batch table
    roundtrip   dict <-> widgets (+ dotted-key helpers)
    handlers    bottom-bar actions

`TomlForm` (toml_form.py) composes the five mixins over QWidget and
keeps only state, signals and modification tracking. Adding an engine
feature: catalog row(s) + section builder + visibility hook if
conditional.
"""

from .handlers import HandlersMixin
from .roundtrip import RoundTripMixin
from .sections import SectionBuildersMixin
from .visibility import VisibilityMixin
from .widgets import WidgetFactoriesMixin

__all__ = [
    "HandlersMixin",
    "RoundTripMixin",
    "SectionBuildersMixin",
    "VisibilityMixin",
    "WidgetFactoriesMixin",
]
