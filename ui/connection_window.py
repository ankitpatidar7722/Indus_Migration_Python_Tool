"""
Dual Connection screen — the landing window of IndusMigration.

Shows TWO connection panels side by side:

    ┌─ Desktop ERP (Source) ─┐   ┌─ Web ERP (Target) ─┐
    │ server / db / creds    │   │ server / db / creds │
    │ Fetch · Connect        │   │ Fetch · Connect     │
    └────────────────────────┘   └─────────────────────┘
              [ Open Migration ]   (enabled only when both connected)

This is the first thing the user asked for: "add two connections for DBs —
one desktop, one web — and once the connection is built successfully, do the
migration." The migration window is launched only after both are live.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QPushButton, QLabel, QGroupBox, QMessageBox,
    QFrame, QCheckBox, QStatusBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from core import db


class _FetchDatabasesWorker(QThread):
    """Background thread: list databases on a server without freezing the UI."""
    finished = pyqtSignal(bool, list, str)  # success, db_list, error_msg

    def __init__(self, server: str, username: str, password: str):
        super().__init__()
        self.server = server
        self.username = username
        self.password = password

    def run(self):
        try:
            dbs = db.list_databases(self.server, self.username, self.password)
            self.finished.emit(True, dbs, "")
        except Exception as e:
            self.finished.emit(False, [], str(e))


class _ConnectWorker(QThread):
    """Background thread: open a connection for one role."""
    finished = pyqtSignal(bool, str)  # success, error_msg

    def __init__(self, role, server, database, username, password):
        super().__init__()
        self.role = role
        self.server = server
        self.database = database
        self.username = username
        self.password = password

    def run(self):
        try:
            db.connect(self.role, self.server, self.database,
                       self.username, self.password)
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))


class ConnectionPanel(QGroupBox):
    """One side of the screen — connects a single DB role (desktop or web)."""

    connection_changed = pyqtSignal()  # emitted when connect/disconnect succeeds

    def __init__(self, role: str, title: str, accent: str, parent=None):
        super().__init__(title, parent)
        self.role = role
        self.accent = accent
        self._fetch_worker = None
        self._connect_worker = None
        self.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._build_ui()
        self._load_saved()

    # ------------------------------------------------------------------
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 18, 14, 14)
        outer.setSpacing(10)

        form = QFormLayout()
        form.setVerticalSpacing(8)

        self.cmb_server = QComboBox()
        self.cmb_server.setEditable(True)
        self.cmb_server.lineEdit().setPlaceholderText(
            "e.g. 192.168.1.100,1433  or  MYPC\\SQLEXPRESS"
        )
        self.cmb_server.setMinimumHeight(34)
        self.cmb_server.setFont(QFont("Segoe UI", 11))
        form.addRow("Server / IP:", self.cmb_server)

        self.txt_username = QLineEdit("INDUS")
        self.txt_username.setMinimumHeight(34)
        self.txt_username.setFont(QFont("Segoe UI", 11))
        form.addRow("Username:", self.txt_username)

        self.txt_password = QLineEdit("Param@99811")
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setMinimumHeight(34)
        self.txt_password.setFont(QFont("Segoe UI", 11))
        form.addRow("Password:", self.txt_password)

        self.chk_show_pass = QCheckBox("Show password")
        self.chk_show_pass.toggled.connect(self._toggle_password)
        form.addRow("", self.chk_show_pass)

        # Database row: dropdown + fetch button
        db_row = QHBoxLayout()
        self.cmb_database = QComboBox()
        self.cmb_database.setEditable(True)
        self.cmb_database.setMinimumHeight(34)
        self.cmb_database.setFont(QFont("Segoe UI", 11))
        self.cmb_database.setMaxVisibleItems(15)
        self.cmb_database.lineEdit().setPlaceholderText("Type or select a database")
        db_row.addWidget(self.cmb_database, 1)

        self.btn_fetch = QPushButton("↻ Fetch")
        self.btn_fetch.setObjectName("btn_warning")
        self.btn_fetch.clicked.connect(self._on_fetch)
        db_row.addWidget(self.btn_fetch)
        form.addRow("Database:", db_row)

        outer.addLayout(form)

        # Status line
        self.lbl_status = QLabel("Not connected")
        self.lbl_status.setStyleSheet("color: #546e7a; font-style: italic;")
        outer.addWidget(self.lbl_status)

        # Connect / disconnect buttons
        btn_row = QHBoxLayout()
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setObjectName("btn_success")
        self.btn_connect.clicked.connect(self._on_connect)
        btn_row.addWidget(self.btn_connect)

        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.setObjectName("btn_danger")
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        btn_row.addWidget(self.btn_disconnect)
        outer.addLayout(btn_row)

    def _load_saved(self):
        saved = db.load_saved_settings()
        history = saved.get("server_history", [])
        mine = saved.get(self.role, {})
        last_server = mine.get("server", "")
        if last_server and last_server not in history:
            history.insert(0, last_server)
        self.cmb_server.clear()
        self.cmb_server.addItems(history)
        if last_server:
            self.cmb_server.setCurrentText(last_server)
        if mine.get("database"):
            self.cmb_database.setCurrentText(mine["database"])
        if mine.get("username"):
            self.txt_username.setText(mine["username"])
        if mine.get("password"):
            self.txt_password.setText(mine["password"])

    def _toggle_password(self, checked: bool):
        self.txt_password.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    # ------------------------------------------------------------------
    # Fetch databases
    # ------------------------------------------------------------------
    def _on_fetch(self):
        server = self.cmb_server.currentText().strip()
        if not server:
            QMessageBox.warning(self, "Missing", "Enter a server address first.")
            return
        self.btn_fetch.setEnabled(False)
        self._set_status("Connecting to server…", "#1565c0")
        self._fetch_worker = _FetchDatabasesWorker(
            server, self.txt_username.text().strip(), self.txt_password.text()
        )
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.start()

    def _on_fetch_done(self, ok: bool, dbs: list, error: str):
        self.btn_fetch.setEnabled(True)
        if ok:
            current = self.cmb_database.currentText()
            self.cmb_database.clear()
            self.cmb_database.addItems(dbs)
            if current:
                self.cmb_database.setCurrentText(current)
            self._set_status(f"{len(dbs)} databases found.", "#2e7d32")
        else:
            self._set_status(f"Failed: {error}", "#c62828")

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------
    def _on_connect(self):
        server = self.cmb_server.currentText().strip()
        database = self.cmb_database.currentText().strip()
        username = self.txt_username.text().strip()
        password = self.txt_password.text()
        if not server:
            QMessageBox.warning(self, "Missing", "Enter a server address.")
            return
        if not database:
            QMessageBox.warning(self, "Missing", "Enter or select a database.")
            return

        self.btn_connect.setEnabled(False)
        self._set_status("Connecting…", "#1565c0")
        self._connect_worker = _ConnectWorker(
            self.role, server, database, username, password
        )
        self._connect_worker.finished.connect(self._on_connect_done)
        self._connect_worker.start()

    def _on_connect_done(self, ok: bool, error: str):
        if ok:
            server, database = db.get_info(self.role)
            self._set_status(f"✓ Connected to {database}", "#2e7d32")
            self._set_inputs_enabled(False)
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
        else:
            self.btn_connect.setEnabled(True)
            self._set_status(f"Failed: {error}", "#c62828")
        self.connection_changed.emit()

    def _on_disconnect(self):
        db.close(self.role)
        self._set_status("Not connected", "#546e7a")
        self._set_inputs_enabled(True)
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.connection_changed.emit()

    def _set_inputs_enabled(self, enabled: bool):
        for w in (self.cmb_server, self.cmb_database, self.txt_username,
                  self.txt_password, self.btn_fetch, self.chk_show_pass):
            w.setEnabled(enabled)

    def _set_status(self, text: str, color: str):
        weight = "bold" if text.startswith("✓") else "normal"
        style = f"color: {color}; font-style: italic; font-weight: {weight};"
        self.lbl_status.setStyleSheet(style)
        self.lbl_status.setText(text)


class ConnectionWindow(QMainWindow):
    """Main window: two connection panels + gateway to the migration screen."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Indus Migration Tool")
        self.resize(1180, 720)
        self.setMinimumSize(980, 620)
        self._child = None
        self._build_ui()
        self._update_migration_button()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        hdr = QFrame()
        hdr.setStyleSheet("background-color: #1565c0; border-radius: 8px;")
        hl = QVBoxLayout(hdr)
        hl.setContentsMargins(16, 12, 16, 12)
        lbl_title = QLabel("Indus Migration Tool")
        lbl_title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        lbl_title.setStyleSheet("color: #ffffff; background: transparent;")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl.addWidget(lbl_title)
        lbl_sub = QLabel("Desktop ERP  →  Web ERP   ·   Data Migration")
        lbl_sub.setFont(QFont("Segoe UI", 11))
        lbl_sub.setStyleSheet("color: #bbdefb; background: transparent;")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl.addWidget(lbl_sub)
        root.addWidget(hdr)

        # Two panels
        panels = QHBoxLayout()
        panels.setSpacing(16)
        self.panel_desktop = ConnectionPanel(
            db.DESKTOP, "①  Desktop ERP  (Source)", "#6a1b9a"
        )
        self.panel_web = ConnectionPanel(
            db.WEB, "②  Web ERP  (Target)", "#2e7d32"
        )
        self.panel_desktop.connection_changed.connect(self._update_migration_button)
        self.panel_web.connection_changed.connect(self._update_migration_button)
        panels.addWidget(self.panel_desktop)
        panels.addWidget(self.panel_web)
        root.addLayout(panels)

        # Open Migration button
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.btn_migration = QPushButton("Open Migration  →")
        self.btn_migration.setMinimumSize(220, 46)
        self.btn_migration.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self.btn_migration.clicked.connect(self._on_open_migration)
        bottom.addWidget(self.btn_migration)
        bottom.addStretch()
        root.addLayout(bottom)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Connect both databases to begin.")

    def _update_migration_button(self):
        ready = db.both_connected()
        self.btn_migration.setEnabled(ready)
        if ready:
            self.status.showMessage("Both databases connected — ready to migrate.")
        elif db.is_connected(db.DESKTOP):
            self.status.showMessage("Desktop connected. Now connect the Web (target) database.")
        elif db.is_connected(db.WEB):
            self.status.showMessage("Web connected. Now connect the Desktop (source) database.")
        else:
            self.status.showMessage("Connect both databases to begin.")

    def _on_open_migration(self):
        if not db.both_connected():
            QMessageBox.warning(
                self, "Not ready",
                "Connect both the Desktop (source) and Web (target) databases first."
            )
            return
        # Migration window arrives with the engine task; placeholder until then.
        try:
            from ui.migration_window import MigrationWindow
        except ImportError:
            QMessageBox.information(
                self, "Coming next",
                "Both connections are live.\n\n"
                "The migration screen is the next build step."
            )
            return
        self.status.showMessage("Opening migration…")
        win = MigrationWindow(home_window=self)
        win.show()
        self.hide()
        self._child = win

    def closeEvent(self, event):
        db.close_all()
        super().closeEvent(event)
