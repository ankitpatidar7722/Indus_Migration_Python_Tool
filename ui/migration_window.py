"""
Migration screen — Load → Preview → Import.

Opened from the connection window once both DBs are live. Flow:

  1. Pick an entity (module) + the target context (Company / User / FYear).
  2. Load Data  → reads the desktop source, maps it to the TARGET columns,
                  resolves foreign keys, fits values — and SHOWS it in a grid.
                  NOTHING is written yet.
  3. Review the grid (read-only). Each row has a checkbox; rows that would be
     imported are ticked. Already-migrated rows (amber) and rows with issues
     (red, with the reason) are shown but not ticked.
  4. Import Selected → writes only the ticked rows to the web (target) DB.

Loading and importing both run in a QThread so the UI stays responsive.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QPushButton, QLabel, QGroupBox, QProgressBar, QStatusBar,
    QMessageBox, QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QCheckBox, QApplication
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from core import db, engine, entities
from core.engine import Outcome
from ui import widgets

# fixed leading columns in the preview grid
LEAD = ["✓", "#", "Record", "Status", "Note"]


class _LoadWorker(QThread):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(object)       # PreviewResult
    failed = pyqtSignal(str)

    def __init__(self, entity_name, context):
        super().__init__()
        self.entity_name = entity_name
        self.context = context
        self._stop = False

    def stop(self): self._stop = True

    def run(self):
        try:
            ent = entities.create(self.entity_name, **self.context)
            pv = engine.preview_entity(
                ent, progress=lambda d, t, m: self.progress.emit(d, t, m),
                stop_flag=lambda: self._stop)
            self.done.emit(pv)
        except Exception as e:
            self.failed.emit(str(e))


class _ImportWorker(QThread):
    progress = pyqtSignal(int, int, str)
    record = pyqtSignal(str, str, object, str)
    done = pyqtSignal(object)       # MigrationResult
    failed = pyqtSignal(str)

    def __init__(self, entity_name, context, preview, selected):
        super().__init__()
        self.entity_name = entity_name
        self.context = context
        self.preview = preview
        self.selected = selected
        self._stop = False

    def stop(self): self._stop = True

    def run(self):
        try:
            ent = entities.create(self.entity_name, **self.context)
            result = engine.MigrationResult(entity=self.entity_name)
            orig = result.add

            def add_emit(r):
                orig(r)
                self.record.emit(r.source_key, r.outcome.value, r.target_id, r.message)
            result.add = add_emit  # type: ignore

            # Pass our streaming result so each record emits to the live grid
            # (turns failed rows red with the real error in the Note column).
            res = engine.import_preview(
                ent, self.preview, selected_indexes=self.selected,
                progress=lambda d, t, m: self.progress.emit(d, t, m),
                stop_flag=lambda: self._stop, _result=result)
            self.done.emit(res)
        except Exception as e:
            self.failed.emit(str(e))


class _ChainWorker(QThread):
    """Import the selected master (its ticked rows) then auto-migrate each
    dependent CHILD master fully, in order. Reports per-entity results."""
    entity_progress = pyqtSignal(str, int, int)     # entity, done, total
    entity_done = pyqtSignal(str, object)           # entity, MigrationResult
    all_done = pyqtSignal(list)                      # [(entity, summary), ...]
    failed = pyqtSignal(str)

    def __init__(self, entity_name, context, preview, selected, children):
        super().__init__()
        self.entity_name = entity_name
        self.context = context
        self.preview = preview
        self.selected = selected
        self.children = children
        self._stop = False

    def stop(self): self._stop = True

    def run(self):
        results = []
        try:
            # 1) the selected master — only its ticked rows
            ent = entities.create(self.entity_name, **self.context)
            res = engine.import_preview(
                ent, self.preview, selected_indexes=self.selected,
                progress=lambda d, t, m, n=self.entity_name: self.entity_progress.emit(n, d, t),
                stop_flag=lambda: self._stop)
            self.entity_done.emit(self.entity_name, res)
            results.append((self.entity_name, res.summary()))

            # 2) each child master — full preview + import all importable rows
            for child in self.children:
                if self._stop:
                    break
                cent = entities.create(child, **self.context)
                cpv = engine.preview_entity(
                    cent, progress=lambda d, t, m, n=child: self.entity_progress.emit(n, d, t),
                    stop_flag=lambda: self._stop)
                cres = engine.import_preview(
                    cent, cpv, selected_indexes=None,  # all importable
                    progress=lambda d, t, m, n=child: self.entity_progress.emit(n, d, t),
                    stop_flag=lambda: self._stop)
                self.entity_done.emit(child, cres)
                results.append((child, cres.summary()))
            self.all_done.emit(results)
        except Exception as e:
            self.failed.emit(str(e))


class MigrationWindow(QMainWindow):
    def __init__(self, home_window=None):
        super().__init__()
        self.home_window = home_window
        self.setWindowTitle("Indus Migration — Load, Preview & Import")
        self.resize(1320, 860)
        self.setMinimumSize(1080, 680)
        self._load_worker = None
        self._import_worker = None
        self._chain_worker = None
        self._preview = None            # current PreviewResult
        self._build_ui()
        self._on_module_changed()    # populate sub-module dropdown for first module
        self._load_context()

    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        # Header
        hdr = QFrame()
        hdr.setStyleSheet("background-color: #1565c0; border-radius: 6px;")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 10, 16, 10)
        title = QLabel("Load → Preview → Import")
        title.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        title.setStyleSheet("color:#ffffff;background:transparent;")
        hl.addWidget(title)
        hl.addStretch()
        _, dd = db.get_info(db.DESKTOP)
        _, wd = db.get_info(db.WEB)
        hl.addWidget(self._tag(f"{dd}  →  {wd}"))
        root.addWidget(hdr)

        # Setup row
        setup = QGroupBox("Step 1 — choose what to load")
        sl = QHBoxLayout(setup)
        sl.setSpacing(20)

        f1 = QFormLayout()
        # Module dropdown (top level).
        self.cmb_module = QComboBox()
        self.cmb_module.setMinimumWidth(240)
        self.cmb_module.setFont(QFont("Segoe UI", 11))
        for m in entities.modules():
            self.cmb_module.addItem(m["label"], m)
        f1.addRow("Module:", self.cmb_module)

        # Sub-module dropdown — shown only for modules that have sub-groups.
        self.lbl_sub = QLabel("Sub-module:")
        self.cmb_submodule = QComboBox()
        self.cmb_submodule.setMinimumWidth(240)
        self.cmb_submodule.setFont(QFont("Segoe UI", 11))
        f1.addRow(self.lbl_sub, self.cmb_submodule)

        self.lbl_flow = QLabel("")
        self.lbl_flow.setStyleSheet("color:#546e7a;")
        f1.addRow("", self.lbl_flow)

        self.cmb_module.currentIndexChanged.connect(self._on_module_changed)
        self.cmb_submodule.currentIndexChanged.connect(self._on_entity_changed)
        sl.addLayout(f1)

        # Companies on ONE row: Desktop (source) and Web (target).
        # User / FYear are resolved automatically from the web DB (kept as
        # hidden combos so the rest of the code reading them is unchanged).
        comp_box = QVBoxLayout()
        comp_row = QHBoxLayout(); comp_row.setSpacing(24)

        dcol = QFormLayout()
        self.cmb_desktop_company = QComboBox()
        self.cmb_desktop_company.setMinimumWidth(220)
        self.cmb_desktop_company.setFont(QFont("Segoe UI", 11))
        dcol.addRow("Desktop Company:", self.cmb_desktop_company)
        comp_row.addLayout(dcol)

        wcol = QFormLayout()
        self.cmb_company = QComboBox()
        self.cmb_company.setMinimumWidth(220)
        self.cmb_company.setFont(QFont("Segoe UI", 11))
        self.cmb_company.currentIndexChanged.connect(self._on_company_changed)
        wcol.addRow("Web Company:", self.cmb_company)
        comp_row.addLayout(wcol)
        comp_box.addLayout(comp_row)

        self.lbl_ctx = QLabel("")
        self.lbl_ctx.setStyleSheet("color:#546e7a;")
        comp_box.addWidget(self.lbl_ctx)
        sl.addLayout(comp_box)
        sl.addStretch()

        # User / FYear are auto-selected (not shown); kept as members the rest
        # of the window already reads via _context().
        self.cmb_user = QComboBox()
        self.cmb_fyear = QComboBox(); self.cmb_fyear.setEditable(True)
        self.cmb_user.hide(); self.cmb_fyear.hide()

        self.btn_load = QPushButton("⟳  Load Data (preview only)")
        self.btn_load.setObjectName("btn_warning")
        self.btn_load.setMinimumHeight(44)
        self.btn_load.setMinimumWidth(210)
        self.btn_load.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.btn_load.clicked.connect(self._on_load)
        sl.addWidget(self.btn_load)
        root.addWidget(setup)

        # Preview grid
        grid_box = QGroupBox("Step 2 — review the data that WILL be written (nothing saved yet)")
        gl = QVBoxLayout(grid_box)
        bar = QHBoxLayout()
        self.lbl_summary = QLabel("No data loaded.")
        self.lbl_summary.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        bar.addWidget(self.lbl_summary)
        bar.addStretch()
        self.btn_check_all = QPushButton("Tick importable")
        self.btn_check_all.setStyleSheet("background:#607d8b;")
        self.btn_check_all.clicked.connect(lambda: self._tick_importable(True))
        self.btn_uncheck_all = QPushButton("Untick all")
        self.btn_uncheck_all.setStyleSheet("background:#607d8b;")
        self.btn_uncheck_all.clicked.connect(lambda: self._tick_importable(False))
        bar.addWidget(self.btn_check_all); bar.addWidget(self.btn_uncheck_all)
        gl.addLayout(bar)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        gl.addWidget(self.table)
        root.addWidget(grid_box, 1)

        # Import row
        imp = QGroupBox("Step 3 — import")
        il = QHBoxLayout(imp)
        self.progress = QProgressBar(); self.progress.setValue(0)
        il.addWidget(self.progress, 1)
        self.btn_import = QPushButton("⬇  Import Ticked Rows")
        self.btn_import.setObjectName("btn_success")
        self.btn_import.setMinimumHeight(42)
        self.btn_import.setMinimumWidth(200)
        self.btn_import.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.btn_import.setEnabled(False)
        self.btn_import.clicked.connect(self._on_import)
        il.addWidget(self.btn_import)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("btn_danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        il.addWidget(self.btn_stop)
        root.addWidget(imp)

        # Bottom
        bottom = QHBoxLayout()
        self.btn_back = QPushButton("← Back to Connections")
        self.btn_back.clicked.connect(self._on_back)
        bottom.addWidget(self.btn_back)
        bottom.addStretch()
        root.addLayout(bottom)

        self.status = QStatusBar(); self.setStatusBar(self.status)
        self.status.showMessage("Pick a module and click Load Data to preview.")
        self._on_entity_changed()

    def _tag(self, text):
        l = QLabel(text)
        l.setStyleSheet("color:#bbdefb;background:transparent;")
        return l

    # ------------------------------------------------------------------
    def _on_module_changed(self):
        """Populate the Sub-module dropdown for the selected module, or hide it
        when the module has no sub-groups."""
        m = self.cmb_module.currentData()
        if not m:
            return
        self.cmb_submodule.blockSignals(True)
        self.cmb_submodule.clear()
        has_sub = m["has_submodules"]
        if has_sub:
            for label, name in m["submodules"]:
                self.cmb_submodule.addItem(label, name)
        else:
            # single entity — hidden combo still carries the entity name
            self.cmb_submodule.addItem("", m["submodules"][0][1])
        self.cmb_submodule.blockSignals(False)
        self.lbl_sub.setVisible(has_sub)
        self.cmb_submodule.setVisible(has_sub)
        self._on_entity_changed()

    def _current_entity_name(self):
        return self.cmb_submodule.currentData()

    def _on_entity_changed(self):
        name = self._current_entity_name()
        if not name:
            return
        src, tgt = entities.labels(name)
        self.lbl_flow.setText(f"{src}  →  {tgt}")

    def _load_context(self):
        # --- Web (target) companies ---
        try:
            companies = db.query_web(
                "SELECT CompanyID, CompanyName FROM CompanyMaster ORDER BY CompanyID")
        except Exception as e:
            QMessageBox.warning(self, "Load failed",
                                f"Could not read companies:\n{e}")
            companies = []
        self.cmb_company.clear()
        for c in companies:
            self.cmb_company.addItem(f"{c['CompanyName']} (#{c['CompanyID']})", c["CompanyID"])

        # --- Desktop (source) companies: from C_Company_Master (the real company
        #     master) if it has rows, else fall back to the connected DB name. ---
        self.cmb_desktop_company.clear()
        try:
            dcos = db.query_desktop(
                "SELECT Company_ID, Company_Name FROM C_Company_Master ORDER BY Company_ID")
        except Exception:
            dcos = []
        if dcos:
            for c in dcos:
                self.cmb_desktop_company.addItem(
                    f"{(c['Company_Name'] or '').strip()} (#{c['Company_ID']})",
                    c["Company_ID"])
        else:
            _, ddb = db.get_info(db.DESKTOP)
            self.cmb_desktop_company.addItem(ddb or "Desktop", None)

        if companies:
            self._on_company_changed()

    def _on_company_changed(self):
        cid = self.cmb_company.currentData()
        if cid is None:
            return
        try:
            users = db.query_web(
                "SELECT UserID, UserName, FYear FROM UserMaster WHERE CompanyID=? "
                "ORDER BY UserID", [cid])
        except Exception:
            users = []
        self.cmb_user.clear()
        for u in users:
            self.cmb_user.addItem(f"{u['UserName']} (#{u['UserID']})", u["UserID"])
        fyears = sorted({(u["FYear"] or "").strip() for u in users if u["FYear"]}, reverse=True)
        self.cmb_fyear.clear(); self.cmb_fyear.addItems(fyears)
        if fyears:
            self.cmb_fyear.setCurrentText(fyears[0])
        # Show what was auto-selected (User/FYear), since they're no longer pickers.
        uname = self.cmb_user.currentText() or "—"
        fy = self.cmb_fyear.currentText() or "—"
        self.lbl_ctx.setText(f"Imports stamped as  User: {uname}   ·   F.Year: {fy}")

    def _context(self):
        return {
            "company_id": self.cmb_company.currentData() or 0,
            "user_id": self.cmb_user.currentData() or 0,
            "fyear": self.cmb_fyear.currentText().strip(),
        }

    # ---------------- Load (preview) -----------------------------------
    def _on_load(self):
        ctx = self._context()
        if not ctx["company_id"]:
            QMessageBox.warning(self, "Missing", "Select a target company first.")
            return
        name = self._current_entity_name()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self._preview = None
        self.btn_import.setEnabled(False)
        self.progress.setRange(0, 0)        # busy
        self._set_busy(True)
        self.status.showMessage(f"Loading {name} (preview)…")
        self._load_worker = _LoadWorker(name, ctx)
        self._load_worker.progress.connect(
            lambda d, t, m: self.status.showMessage(m))
        self._load_worker.done.connect(self._on_loaded)
        self._load_worker.failed.connect(self._on_load_failed)
        self._load_worker.start()

    def _on_load_failed(self, err):
        self.progress.setRange(0, 100); self.progress.setValue(0)
        self._set_busy(False)
        self.status.showMessage("Load failed: " + err)
        QMessageBox.critical(self, "Load failed", err)

    def _on_loaded(self, preview):
        self.progress.setRange(0, 100); self.progress.setValue(0)
        self._set_busy(False)
        self._preview = preview
        self._render_preview(preview)
        self.btn_import.setEnabled(preview.would_insert > 0)
        summary = f"{preview.summary()}   —   ticked rows will be imported."
        # Show any dependent children that can auto-migrate with this master.
        chain = entities.dependent_chain(self._current_entity_name())
        if chain:
            summary += ("\nWill also auto-migrate with this: " + ", ".join(chain))
        self.lbl_summary.setText(summary)
        self.status.showMessage("Loaded. Review, then Import Ticked Rows.")

    def _render_preview(self, preview):
        cols = LEAD + list(preview.columns)
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(preview.rows))
        col_index = {c: i for i, c in enumerate(preview.columns)}
        for r, prow in enumerate(preview.rows):
            # checkbox
            chk = QCheckBox(); chk.setChecked(prow.outcome is Outcome.INSERTED)
            chk.setEnabled(prow.outcome is Outcome.INSERTED)
            cell = QWidget(); lay = QHBoxLayout(cell)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(chk)
            self.table.setCellWidget(r, 0, cell)
            self._set(r, 1, str(prow.index))
            self._set(r, 2, prow.source_key)
            status = {Outcome.INSERTED: "will import",
                      Outcome.SKIPPED: "already migrated",
                      Outcome.FAILED: "issue"}[prow.outcome]
            self._set(r, 3, status)
            self._set(r, 4, prow.message)
            # mapped target values
            vals_by_col = dict(zip(prow.columns, prow.values))
            for c, idx in col_index.items():
                v = vals_by_col.get(c, "")
                self._set(r, len(LEAD) + idx, "" if v is None else str(v))
            color = {Outcome.INSERTED: widgets.COLOR_SUCCESS,
                     Outcome.SKIPPED: widgets.COLOR_SKIPPED,
                     Outcome.FAILED: widgets.COLOR_ERROR}[prow.outcome]
            widgets.set_row_color(self.table, r, color)
        self.table.resizeColumnsToContents()
        # keep Record column readable
        if self.table.columnWidth(2) > 320:
            self.table.setColumnWidth(2, 320)

    def _set(self, r, c, text):
        it = QTableWidgetItem(str(text))
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, c, it)

    def _tick_importable(self, val):
        if not self._preview:
            return
        for r, prow in enumerate(self._preview.rows):
            if prow.outcome is Outcome.INSERTED:
                cb = self.table.cellWidget(r, 0).findChild(QCheckBox)
                cb.setChecked(val)

    def _selected_indexes(self):
        sel = set()
        for r, prow in enumerate(self._preview.rows):
            if prow.outcome is Outcome.INSERTED:
                cb = self.table.cellWidget(r, 0).findChild(QCheckBox)
                if cb.isChecked():
                    sel.add(prow.index)
        return sel

    # ---------------- Import -------------------------------------------
    def _on_import(self):
        if not self._preview:
            return
        sel = self._selected_indexes()
        if not sel:
            QMessageBox.warning(self, "Nothing ticked",
                                "Tick at least one row to import.")
            return
        name = self._current_entity_name()
        chain = entities.dependent_chain(name)

        # Children always come along with their parent — no separate choice.
        extra = ("\n\nIts dependent settings will also migrate automatically:\n  • "
                 + "\n  • ".join(chain)) if chain else ""
        confirm = QMessageBox.question(
            self, "Confirm import",
            f"Import {len(sel)} ticked row(s) of {name} into "
            f"{self.cmb_company.currentText()}?{extra}\n\nThis writes to the web DB.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Optional clear-before-import: wipe this entity's existing rows (and its
        # dependent children) from the web DB first. Default is No (safer).
        # Returns "proceed" / "cleared" / "cancel".
        outcome = self._maybe_clear_before_import(name, chain)
        if outcome == "cancel":
            return
        if outcome == "cleared":
            # The clear wiped the existing rows; rebuild the preview against the
            # emptied target so previously-migrated rows flip to "will import" —
            # but KEEP the user's exact selection so only the ticked rows import
            # (never every row). This is the fix for "clear unchecked my selection
            # and then migrated everything".
            self._rebuild_preview_after_clear(sel)
            sel = self._selected_indexes()
            if not sel:
                QMessageBox.information(
                    self, "Nothing to import",
                    "After clearing, no rows remain to import.")
                return

        if chain:
            self._start_chain(name, sel, chain)
            return

        self.progress.setRange(0, len(sel)); self.progress.setValue(0)
        self._set_busy(True, importing=True)
        self.status.showMessage(f"Importing {len(sel)} rows…")
        self._import_worker = _ImportWorker(name, self._context(), self._preview, sel)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.record.connect(self._on_import_record)
        self._import_worker.done.connect(self._on_import_done)
        self._import_worker.failed.connect(self._on_import_failed)
        self._import_worker.start()

    def _rebuild_preview_after_clear(self, keep_indexes=None):
        """Re-run the preview against the (now cleared) target so rows that were
        'already migrated' become 'will import'. If keep_indexes is given, restore
        exactly that selection (selection-scoped clear); otherwise auto-tick all
        importable rows (classic whole-group clear)."""
        name = self._current_entity_name()
        try:
            ent = entities.create(name, **self._context())
            pv = engine.preview_entity(ent)
        except Exception as e:
            QMessageBox.warning(self, "Refresh failed",
                                f"Cleared, but could not refresh the preview:\n{e}")
            return
        self._preview = pv
        self._render_preview(pv)
        if keep_indexes is None:
            self._tick_importable(True)       # auto-tick all importable (cleared) rows
            note = "ticked rows will import."
        else:
            self._restore_selection(keep_indexes)   # keep the user's exact selection
            note = "your selection was kept; only ticked rows will import."
        self.btn_import.setEnabled(pv.would_insert > 0)
        self.lbl_summary.setText(f"{pv.summary()}   —   refreshed after clear; {note}")

    def _restore_selection(self, keep_indexes):
        """Tick ONLY the importable rows whose source index is in keep_indexes;
        untick the rest. Used after a selection-scoped clear so the user's choice
        survives the preview rebuild (rather than every row being re-ticked)."""
        keep = set(keep_indexes or ())
        for r, prow in enumerate(self._preview.rows):
            if prow.outcome is Outcome.INSERTED:
                cb = self.table.cellWidget(r, 0).findChild(QCheckBox)
                cb.setChecked(prow.index in keep)

    def _maybe_clear_before_import(self, name, chain):
        """Ask whether to clear this entity's existing web-DB rows before import.
        Returns 'proceed' (No / import without clearing), 'cleared' (rows were
        deleted — caller must refresh the preview), or 'cancel'. Default No."""
        targets = [name] + list(chain)
        ask = QMessageBox(self)
        ask.setIcon(QMessageBox.Icon.Question)
        ask.setWindowTitle("Clear existing data?")
        kids = ("\n\nThis also clears its dependent settings:\n  • "
                + "\n  • ".join(chain)) if chain else ""
        ask.setText(
            f"Do you want to CLEAR the existing {name} data from "
            f"{self.cmb_company.currentText()} before importing?{kids}\n\n"
            "• Yes — delete the existing rows (for this group) first, then import.\n"
            "• No — keep existing data and just add/insert the new rows.\n\n"
            "Only your ticked rows are re-imported after clearing.")
        yes = ask.addButton("Yes, clear first", QMessageBox.ButtonRole.DestructiveRole)
        no = ask.addButton("No, just insert", QMessageBox.ButtonRole.AcceptRole)
        cancel = ask.addButton(QMessageBox.StandardButton.Cancel)
        ask.setDefaultButton(no)          # default = No (safer)
        ask.exec()
        clicked = ask.clickedButton()
        if clicked is cancel:
            return "cancel"
        if clicked is not yes:
            return "proceed"              # No → proceed without clearing

        # Final, explicit confirmation — this is destructive.
        confirm = QMessageBox.warning(
            self, "Confirm clear",
            f"This will permanently DELETE the existing {name} rows "
            f"(and dependents) for {self.cmb_company.currentText()} from the web "
            "database, then import.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Yes:
            return "cancel"

        try:
            self.status.showMessage("Clearing existing data…")
            QApplication.processEvents()
            totals = {}
            for tname in targets:
                ent = entities.create(tname, **self._context())
                deleted = engine.clear_entity(ent)
                for tbl, n in deleted.items():
                    totals[tbl] = totals.get(tbl, 0) + n
            summary = ", ".join(f"{t}: {n}" for t, n in totals.items() if n) or "nothing to clear"
            self.status.showMessage(f"Cleared — {summary}")
        except Exception as e:
            QMessageBox.critical(self, "Clear failed",
                                 f"Could not clear existing data:\n{e}")
            return "cancel"
        return "cleared"

    def _start_chain(self, name, sel, chain):
        self._chain_results = []
        self.progress.setRange(0, 0)        # busy/indeterminate across chain
        self._set_busy(True, importing=True)
        self.status.showMessage(f"Migrating {name} + {len(chain)} dependent(s)…")
        self._chain_worker = _ChainWorker(
            name, self._context(), self._preview, sel, chain)
        self._chain_worker.entity_progress.connect(
            lambda e, d, t: self.status.showMessage(f"{e}: {d}/{t}"))
        self._chain_worker.entity_done.connect(
            lambda e, res: self.status.showMessage(f"{e} done — {res.summary()}"))
        self._chain_worker.all_done.connect(self._on_chain_done)
        self._chain_worker.failed.connect(self._on_import_failed)
        self._chain_worker.start()

    def _on_chain_done(self, results):
        self.progress.setRange(0, 100); self.progress.setValue(100)
        self._set_busy(False)
        lines = "\n".join(f"  • {e}: {s}" for e, s in results)
        self.status.showMessage("Chain migration finished.")
        QMessageBox.information(self, "Migration complete",
                                "Migrated master + dependents:\n\n" + lines)

    def _on_import_progress(self, done, total, msg):
        self.progress.setMaximum(total or 1)
        self.progress.setValue(done)
        self.status.showMessage(msg)

    def _on_import_record(self, key, outcome, target_id, message):
        # mark the matching grid row as imported (green, status updated)
        for r, prow in enumerate(self._preview.rows):
            if prow.source_key == key and prow.outcome is Outcome.INSERTED:
                self._set(r, 3, "imported ✓" if outcome == "inserted" else "import failed")
                if outcome != "inserted":
                    self._set(r, 4, message)
                    widgets.set_row_color(self.table, r, widgets.COLOR_ERROR)
                break

    def _on_import_done(self, result):
        self._set_busy(False)
        self.status.showMessage("Import finished — " + result.summary())
        msg = result.summary()
        log_path = self._write_failure_log(result)
        if log_path:
            msg += f"\n\nFailure details written to:\n{log_path}"
        QMessageBox.information(self, "Import complete", msg)

    def _write_failure_log(self, result):
        """On any failures, dump the per-record error messages to a log file next
        to the exe / project root so the exact reason is recoverable (the grid
        shows it per row, but a file is easier to share). Returns the path or ''."""
        fails = [r for r in result.records if r.outcome is Outcome.FAILED]
        if not fails:
            return ""
        try:
            from core import db as _db
            import os
            path = os.path.join(_db._get_settings_dir(), "migration_errors.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"{result.summary()}\n\n")
                # distinct messages first (usually all failures share one cause)
                seen = {}
                for r in fails:
                    seen.setdefault(r.message, []).append(r.source_key)
                f.write(f"=== {len(seen)} distinct error(s) ===\n")
                for m, keys in seen.items():
                    f.write(f"\n[{len(keys)}x] {m}\n    e.g. {keys[:10]}\n")
                f.write("\n=== every failed record ===\n")
                for r in fails:
                    f.write(f"{r.source_key}\t{r.message}\n")
            return path
        except Exception:
            return ""

    def _on_import_failed(self, err):
        self._set_busy(False)
        self.status.showMessage("Import failed: " + err)
        QMessageBox.critical(self, "Import failed", err)

    def _on_stop(self):
        for w in (self._load_worker, self._import_worker, self._chain_worker):
            if w and w.isRunning():
                w.stop()
        self.btn_stop.setEnabled(False)
        self.status.showMessage("Stopping…")

    # ------------------------------------------------------------------
    def _set_busy(self, busy, importing=False):
        for w in (self.btn_load, self.cmb_module, self.cmb_submodule, self.cmb_company,
                  self.cmb_desktop_company, self.btn_back, self.btn_check_all,
                  self.btn_uncheck_all):
            w.setEnabled(not busy)
        self.btn_import.setEnabled(not busy and bool(self._preview)
                                   and self._preview.would_insert > 0)
        self.btn_stop.setEnabled(busy)

    def _on_back(self):
        self.close()

    def closeEvent(self, event):
        for w in (self._load_worker, self._import_worker, self._chain_worker):
            if w and w.isRunning():
                w.stop(); w.wait(3000)
        if self.home_window is not None:
            self.home_window.show()
        super().closeEvent(event)
