"""
Shared stylesheet and palette for all windows.
Clean, professional look matching the sibling IndusDB Tool.
"""

APP_STYLE = """
QMainWindow, QDialog {
    background-color: #f0f2f5;
}

QWidget {
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
    color: #1a1a2e;
}

/* ── Title bar area ── */
QLabel#title_label {
    font-size: 18px;
    font-weight: bold;
    color: #0d47a1;
    padding: 6px 0px;
}

QLabel#subtitle_label {
    font-size: 12px;
    color: #546e7a;
}

/* ── Group boxes ── */
QGroupBox {
    border: 1px solid #cfd8dc;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 8px;
    background-color: #ffffff;
    font-weight: bold;
    color: #37474f;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 10px;
}

/* ── Tables ── */
QTableWidget {
    background-color: #ffffff;
    alternate-background-color: #f5f7fa;
    gridline-color: #e0e4ea;
    border: 1px solid #cfd8dc;
    border-radius: 4px;
    selection-background-color: #1565c0;
    selection-color: #ffffff;
}

QTableWidget::item {
    padding: 4px 8px;
}

QHeaderView::section {
    background-color: #1565c0;
    color: #ffffff;
    font-weight: bold;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #0d47a1;
}

/* ── Buttons ── */
QPushButton {
    background-color: #1565c0;
    color: #ffffff;
    border: none;
    border-radius: 5px;
    padding: 7px 18px;
    font-weight: bold;
    min-height: 32px;
}

QPushButton:hover {
    background-color: #1976d2;
}

QPushButton:pressed {
    background-color: #0d47a1;
}

QPushButton:disabled {
    background-color: #b0bec5;
    color: #ffffff;
}

QPushButton#btn_danger {
    background-color: #c62828;
}

QPushButton#btn_danger:hover {
    background-color: #d32f2f;
}

QPushButton#btn_success {
    background-color: #2e7d32;
}

QPushButton#btn_success:hover {
    background-color: #388e3c;
}

QPushButton#btn_warning {
    background-color: #e65100;
}

QPushButton#btn_warning:hover {
    background-color: #f57c00;
}

/* ── Status bar ── */
QStatusBar {
    background-color: #1565c0;
    color: #ffffff;
    font-size: 12px;
    padding: 2px 8px;
}

/* ── Line edits ── */
QLineEdit {
    border: 1px solid #b0bec5;
    border-radius: 4px;
    padding: 5px 8px;
    background-color: #ffffff;
}

QLineEdit:focus {
    border: 1px solid #1565c0;
}

/* ── Combo box ── */
QComboBox {
    border: 1px solid #b0bec5;
    border-radius: 4px;
    padding: 5px 8px;
    background-color: #ffffff;
    color: #1a1a2e;
    min-height: 28px;
}

QComboBox:focus {
    border: 1px solid #1565c0;
}

/* dropdown popup list — force white bg / dark text (otherwise Fusion shows it dark) */
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #1a1a2e;
    border: 1px solid #b0bec5;
    selection-background-color: #1565c0;
    selection-color: #ffffff;
    outline: none;
}

QComboBox QAbstractItemView::item {
    min-height: 26px;
    padding: 4px 8px;
    background-color: #ffffff;
    color: #1a1a2e;
}

QComboBox QAbstractItemView::item:hover {
    background-color: #e3f2fd;
    color: #1a1a2e;
}

QComboBox QAbstractItemView::item:selected {
    background-color: #1565c0;
    color: #ffffff;
}

/* ── Splitter handle ── */
QSplitter::handle {
    background-color: #cfd8dc;
    width: 4px;
    height: 4px;
}

/* ── Scroll bars ── */
QScrollBar:vertical {
    border: none;
    background: #f0f2f5;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #90a4ae;
    border-radius: 5px;
    min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    border: none;
    background: #f0f2f5;
    height: 10px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #90a4ae;
    border-radius: 5px;
    min-width: 20px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Tab widget ── */
QTabWidget::pane {
    border: 1px solid #cfd8dc;
    border-radius: 4px;
    background-color: #ffffff;
}

QTabBar::tab {
    background-color: #e8eaf6;
    color: #37474f;
    padding: 7px 18px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
    font-weight: bold;
}

QTabBar::tab:selected {
    background-color: #1565c0;
    color: #ffffff;
}

QTabBar::tab:hover:!selected {
    background-color: #c5cae9;
}

/* ── Progress bar ── */
QProgressBar {
    border: 1px solid #b0bec5;
    border-radius: 4px;
    text-align: center;
    background-color: #eceff1;
    height: 18px;
}

QProgressBar::chunk {
    background-color: #1565c0;
    border-radius: 3px;
}

/* ── Message box ── */
QMessageBox {
    background-color: #f0f2f5;
}
"""


def apply(app):
    """Call once at startup: Fusion style + our stylesheet."""
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
