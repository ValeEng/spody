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

"""Qt table model backing the Tables tab (raw record view)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from spody_io import (
    EVENT_KIND_ALT_CROSSING,
    EVENT_KIND_ECLIPSE,
    EVENT_KIND_IMPACT,
)


# ----------------------------------------------------------------------
# Per-field display name maps for the events kinds, applied by
# NumpyTableModel.data when the cell value is an integer code we
# want to surface as a label (instead of the raw enum int).
_EVENT_KIND_LABEL = {
    EVENT_KIND_IMPACT:       "IMPACT",
    EVENT_KIND_ECLIPSE:      "ECLIPSE",
    EVENT_KIND_ALT_CROSSING: "ALT_CROSSING",
}


# Display-name overrides for fields whose on-disk name is misleading
# in human display. Keyed by kind ("events" / "events_batch") so the
# rename only kicks in where it makes semantic sense.
#
# distance_km is the EventRecord's "trigger metric" slot: it carries
# whatever quantity tripped the predicate (distance in km for IMPACT,
# eclipse fraction in [0, 1] for ECLIPSE, etc.). The on-disk name is
# kept for backward compat but the table header surfaces the generic
# meaning.
FIELD_DISPLAY_RENAME: dict[str, dict[str, str]] = {
    "events":       {"distance_km": "trigger_value"},
    "events_batch": {"distance_km": "trigger_value"},
}


def _expand_columns(arr: np.ndarray,
                    rename: dict[str, str] | None = None
                    ) -> list[tuple[str, str, int | None]]:
    """Flatten a structured numpy dtype into a list of display columns.
    Each tuple is `(display_name, field_name, sub_index)`:
    - field_name is the dtype field; sub_index is None for scalar
      fields or 0..N-1 for the components of a nested array field.
    - Fields whose name starts with an underscore (e.g. the `_pad`
      padding byte in BATCH_EVENT_DTYPE) are skipped so they don't
      clutter the view.
    - `rename` swaps the display name for fields whose on-disk name is
      misleading (see FIELD_DISPLAY_RENAME)."""
    rename = rename or {}
    cols: list[tuple[str, str, int | None]] = []
    if arr.dtype.names is None:
        # Plain ndarray: one column per component.
        n = 1 if arr.ndim == 1 else arr.shape[1]
        for i in range(n):
            cols.append((f"col{i}", "", i))
        return cols
    for name in arr.dtype.names:
        if name.startswith("_"):
            continue
        display = rename.get(name, name)
        sub_dtype, _ = arr.dtype.fields[name]
        if sub_dtype.subdtype is not None:
            # Nested array, e.g. y[6] in EventRecord -> y0..y5
            length = sub_dtype.subdtype[1][0]
            for i in range(length):
                cols.append((f"{display}{i}", name, i))
        else:
            cols.append((display, name, None))
    return cols


def _format_cell(value, field_name: str) -> str:
    """Stringify one cell value for QTableView display. Floats get 12
    significant digits (round-trips a typical km-scale state vector
    without surprises); integers stay raw; the `kind` field gets the
    IMPACT/ECLIPSE label instead of its enum int."""
    if field_name == "kind":
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return str(value)
        return _EVENT_KIND_LABEL.get(iv, str(iv))
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.12g}"
    return str(value)


class NumpyTableModel(QAbstractTableModel):
    """QAbstractTableModel over a 1-D numpy structured array (events,
    accel, trajectory). Nested array fields (e.g. EventRecord.y[6])
    are flattened into N columns; private fields (starting with '_')
    are hidden so dtype padding never leaks into the view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._arr: np.ndarray | None = None
        self._cols: list[tuple[str, str, int | None]] = []

    def set_array(self, arr: np.ndarray | None,
                  rename: dict[str, str] | None = None) -> None:
        """Swap the backing array. `rename` is a map of dtype-field
        name -> display name, used to relabel columns whose on-disk
        name doesn't match how the value is interpreted (e.g.
        EventRecord.distance_km is really a 'trigger_value' jolly)."""
        self.beginResetModel()
        self._arr = arr
        self._cols = (_expand_columns(arr, rename)
                      if arr is not None else [])
        self.endResetModel()

    def rowCount(self, _parent=QModelIndex()) -> int:  # noqa: B008
        return 0 if self._arr is None else int(len(self._arr))

    def columnCount(self, _parent=QModelIndex()) -> int:  # noqa: B008
        return len(self._cols)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if self._arr is None or not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        display_name, field_name, sub_idx = self._cols[index.column()]
        row = self._arr[index.row()]
        if field_name == "":
            # Plain (non-structured) ndarray fallback.
            value = row if sub_idx is None else row[sub_idx]
        else:
            cell = row[field_name]
            value = cell if sub_idx is None else cell[sub_idx]
        return _format_cell(value, field_name)

    def headerData(self, section, orientation,
                   role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._cols[section][0]
        # Row header: 1-based index, easier to read off than 0-based.
        return str(section + 1)
