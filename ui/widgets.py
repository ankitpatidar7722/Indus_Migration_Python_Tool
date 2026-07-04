"""
Reusable widget helpers used across all windows.
"""

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor


# Row background colours (mirrors VB6 vbGreen / vbRed on execution grids)
COLOR_SUCCESS = QColor("#c8e6c9")   # light green
COLOR_ERROR   = QColor("#ffcdd2")   # light red
COLOR_SKIPPED = QColor("#fff9c4")   # light amber (duplicate / skipped)
COLOR_PENDING = QColor("#ffffff")


def make_table(headers: list[str], read_only: bool = True) -> QTableWidget:
    """Create a styled QTableWidget with given column headers."""
    t = QTableWidget()
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setShowGrid(True)
    t.setSortingEnabled(False)
    if read_only:
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    return t


def fill_table(table: QTableWidget, rows: list[list], clear: bool = True):
    """Populate a QTableWidget from a list of row lists."""
    if clear:
        table.setRowCount(0)
    for row_data in rows:
        r = table.rowCount()
        table.insertRow(r)
        for c, val in enumerate(row_data):
            item = QTableWidgetItem(str(val) if val is not None else "")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(r, c, item)


def set_row_color(table: QTableWidget, row: int, color: QColor):
    for c in range(table.columnCount()):
        item = table.item(row, c)
        if item:
            item.setBackground(color)


def get_cell(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    return item.text().strip() if item else ""


def find_row(table: QTableWidget, value: str, col: int = 0,
             case_sensitive: bool = True) -> int:
    """Return first row index where column `col` matches value, else -1."""
    for r in range(table.rowCount()):
        cell = get_cell(table, r, col)
        if case_sensitive:
            if cell == value:
                return r
        else:
            if cell.lower() == value.lower():
                return r
    return -1
