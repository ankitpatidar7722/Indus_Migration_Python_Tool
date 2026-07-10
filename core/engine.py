"""
Migration engine — the reusable core that every entity migration runs through.

Design goals (these directly fix the failings of the old VB/JS tool):

  * PARAMETERISED reads/writes — never string-concatenate values into SQL.
  * BATCH inserts via pyodbc fast_executemany — not one round-trip per row.
  * ATOMIC per-record transactions — a record's parent + child rows commit
    together or not at all (no orphaned parents).
  * REAL error reporting — every record's outcome (inserted / skipped /
    failed-with-reason) is captured; nothing is silently swallowed.
  * FOREIGN-KEY VALIDATION — references are resolved/checked against the
    target before insert; unresolved refs are reported, not written as 0.
  * IDEMPOTENT — re-running skips records already migrated (duplicate check),
    so a partial run can be safely resumed.

An individual entity is described by an `EntityMigration` subclass (see
core/entities/). The engine calls its hooks; the entity supplies the SQL and
the row-shaping logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import pyodbc

from core import db


class Outcome(str, Enum):
    INSERTED = "inserted"
    SKIPPED = "skipped"     # already present (idempotent re-run) or filtered out
    FAILED = "failed"


@dataclass
class RecordResult:
    """Outcome of migrating a single source record."""
    source_key: str            # human-readable identifier of the source row
    outcome: Outcome
    target_id: int | None = None
    message: str = ""


@dataclass
class MigrationResult:
    """Aggregate result of migrating one entity."""
    entity: str
    total: int = 0
    inserted: int = 0
    skipped: int = 0
    failed: int = 0
    records: list[RecordResult] = field(default_factory=list)

    def add(self, r: RecordResult):
        self.records.append(r)
        self.total += 1
        if r.outcome is Outcome.INSERTED:
            self.inserted += 1
        elif r.outcome is Outcome.SKIPPED:
            self.skipped += 1
        else:
            self.failed += 1

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        return (f"{self.entity}: {self.inserted} inserted, "
                f"{self.skipped} skipped, {self.failed} failed "
                f"(of {self.total})")


# Progress callback: (done, total, last_message)
ProgressFn = Callable[[int, int, str], None]


class EntityMigration:
    """
    Base class for one entity's migration. Subclasses override the hooks.

    The engine drives the flow:
        1. read_source()                  -> rows from the desktop DB
        2. for each row:
             a. source_key(row)           -> id for logging/idempotency
             b. already_migrated(row)?    -> skip if yes
             c. resolve_refs(row)         -> map/validate FKs (raise to fail row)
             d. build_parent(row)         -> (columns, values) for the parent insert
             e. build_children(row, pid)  -> list of (table, columns, rows)
             f. after_insert(row, pid, cur) (optional: e.g. call a calc SP)
           ...all inside ONE transaction on the web connection.
    """

    name: str = "entity"
    target_table: str = ""
    target_identity: str = ""   # identity column returned as the new parent id

    # ---- hooks (override) --------------------------------------------------
    def read_source(self) -> list[dict]:
        raise NotImplementedError

    def source_key(self, row: dict) -> str:
        return str(row)

    def already_migrated(self, row: dict) -> bool:
        return False

    def resolve_refs(self, row: dict) -> dict:
        """Return a dict of resolved foreign keys / derived values.
        Raise ValueError(reason) to fail this record with a clear message."""
        return {}

    def build_parent(self, row: dict, refs: dict) -> tuple[list[str], list]:
        """Return (column_names, values) for the parent INSERT."""
        raise NotImplementedError

    def build_children(self, row: dict, refs: dict, parent_id: int
                       ) -> list[tuple[str, list[str], list[list]]]:
        """Return [(table, column_names, [row_values, ...]), ...] for child inserts."""
        return []

    def after_insert(self, row: dict, refs: dict, parent_id: int,
                     cursor: pyodbc.Cursor) -> None:
        """Optional post-insert step within the same transaction
        (e.g. EXEC a calculation stored procedure)."""
        return None

    def before_import(self) -> None:
        """Optional one-off step run ONCE before importing this entity's rows
        (NOT during preview). Use for prerequisite migrations an entity depends
        on — e.g. Material migrates Material_Group_Master into ItemSubGroupMaster
        first so each material can resolve its sub-group."""
        return None

    def prepare_import(self) -> None:
        """Optional setup run on the IMPORT entity instance before its rows are
        inserted, so per-instance state that read_source() populated during
        preview is rebuilt on the (fresh) import entity. Hierarchical entities
        (ProductMaster) load their child-row maps here — otherwise the import
        worker's fresh entity has empty child maps and only the parent migrates."""
        return None

    # ---- clear-before-import support --------------------------------------
    # Child/detail tables to clear BEFORE the parent (FK order). Each is
    # (child_table, child_fk_col) — rows are removed where child_fk_col is in
    # the set of parent ids being cleared. Override per entity as needed.
    clear_child_tables: list[tuple[str, str]] = []

    def clear_scope(self) -> tuple[str, list]:
        """Return (where_clause, params) selecting the rows this entity 'owns'
        in its target table — company-scoped, plus a group filter for entities
        that share a table (Items by ItemGroupID, Ledger by LedgerGroupID).
        Used by `clear_entity` to wipe just this sub-group's rows. The base is
        company scope only; group-scoped entities override `clear_group_filter`."""
        conds, params = [], []
        from core.mapping import _has_column
        if _has_column(self.target_table, "CompanyID"):
            conds.append("CompanyID=?")
            params.append(getattr(self, "company_id", None))
        gf, gp = self.clear_group_filter()
        if gf:
            conds.append(gf)
            params.extend(gp)
        where = " AND ".join(conds) if conds else "1=1"
        return where, params

    def clear_group_filter(self) -> tuple[str, list]:
        """Override to scope a clear to one sub-group sharing the target table
        (e.g. 'ItemGroupID=?'). Default: no extra filter (whole table for the
        company)."""
        return "", []

    def clear_related(self, cursor, parent_ids: list, deleted: dict) -> None:
        """Optional: delete extra related rows keyed off the parent ids being
        cleared (same transaction as clear_entity). E.g. ProductMaster also removes
        its JobBooking / JobApprovedCost. Accumulate counts into `deleted`."""
        return None


# ----------------------------------------------------------------------------
# Target-schema cache: string column max-lengths, so we can safely fit values
# instead of failing a whole record on "string or binary data would be truncated".
# ----------------------------------------------------------------------------
_str_len_cache: dict[str, dict[str, int]] = {}
_coltype_cache: dict[str, dict[str, str]] = {}   # table -> {col_lower: 'num'|'date'|'str'|'bit'}

_NUMERIC = {"int", "bigint", "smallint", "tinyint", "decimal", "numeric",
            "float", "real", "money", "smallmoney"}
_DATE = {"datetime", "date", "smalldatetime", "datetime2", "time", "datetimeoffset"}


def _load_table_types(table: str):
    key = table.lower()
    if key in _str_len_cache:
        return
    rows = db.query_web(
        "SELECT c.name AS col, t.name AS dtype, c.max_length AS ml "
        "FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id "
        "WHERE c.object_id = OBJECT_ID(?)", [table]
    )
    lens: dict[str, int] = {}
    kinds: dict[str, str] = {}
    for r in rows:
        dt = (r["dtype"] or "").lower()
        col = r["col"].lower()
        ml = r["ml"]
        if dt in ("varchar", "char") and ml and ml > 0:
            lens[col] = ml
        elif dt in ("nvarchar", "nchar") and ml and ml > 0:
            lens[col] = ml // 2
        if dt in _NUMERIC:
            kinds[col] = "num"
        elif dt in _DATE:
            kinds[col] = "date"
        elif dt == "bit":
            kinds[col] = "bit"
    _str_len_cache[key] = lens
    _coltype_cache[key] = kinds


def _string_lengths(table: str) -> dict[str, int]:
    _load_table_types(table)
    return _str_len_cache[table.lower()]


def _column_kinds(table: str) -> dict[str, str]:
    _load_table_types(table)
    return _coltype_cache[table.lower()]


def _coerce_for_type(kind: str, v):
    """Make a source value safe for a numeric/date/bit target column.
    Empty strings and non-convertible junk become None; numeric-looking strings
    become numbers; bit accepts 0/1/true/false."""
    if v is None:
        return None
    if kind == "num":
        if isinstance(v, (int, float)):
            return v
        s = str(v).strip()
        if s == "":
            return None
        try:
            f = float(s)
            return int(f) if f.is_integer() else f
        except ValueError:
            return None
    if kind == "date":
        if isinstance(v, str) and v.strip() == "":
            return None
        return v
    if kind == "bit":
        if isinstance(v, bool):
            return 1 if v else 0
        # Numeric flags (incl. desktop 'real'/'float' columns like Variable_CutOff,
        # which arrive as 1.0/0.0): any non-zero -> 1, zero -> 0.
        if isinstance(v, (int, float)):
            return 1 if v != 0 else 0
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "y"):
            return 1
        if s in ("0", "false", "no", "n", ""):
            return 0
        # numeric-looking strings ('1.0', '2', '0.0', ...)
        try:
            return 1 if float(s) != 0 else 0
        except ValueError:
            return None
    return v


def _fit_values(table: str, columns: list[str], values: list) -> tuple[list, list[str]]:
    """Make source values safe for their target columns:
      * coerce empty-string / junk into numeric / date / bit columns to None,
      * truncate over-long strings to the column width.
    Returns (fitted_values, notes). This fixes the old tool's habit of pushing
    quoted empty strings into numeric columns (which pyodbc rejects)."""
    limits = _string_lengths(table)
    kinds = _column_kinds(table)
    fitted = list(values)
    notes: list[str] = []
    for i, col in enumerate(columns):
        v = fitted[i]
        kind = kinds.get(col.lower())
        if kind in ("num", "date", "bit"):
            fitted[i] = _coerce_for_type(kind, v)
            continue
        if isinstance(v, str):
            limit = limits.get(col.lower())
            if limit is not None and len(v) > limit:
                fitted[i] = v[:limit]
                notes.append(f"{col} truncated {len(v)}→{limit}")
    return fitted, notes


# ----------------------------------------------------------------------------
# Insert helpers (parameterised + identity capture)
# ----------------------------------------------------------------------------
def _insert_parent(cursor: pyodbc.Cursor, table: str, identity: str,
                   columns: list[str], values: list) -> tuple[int, list[str]]:
    """Parameterised INSERT returning (new_identity, truncation_notes).

    OUTPUT returns the id as the statement's own resultset, which pyodbc reads
    reliably — unlike a trailing `SELECT SCOPE_IDENTITY()`, whose extra
    resultset ordering is fragile across drivers. Over-long string values are
    fit to the target column width rather than failing the record.
    """
    values, notes = _fit_values(table, columns, values)
    placeholders = ", ".join("?" for _ in columns)
    collist = ", ".join(f"[{c}]" for c in columns)
    # If the entity supplies the PK value itself (non-identity key, e.g.
    # FluteMaster.FluteID), do a plain insert and return that value — the
    # OUTPUT INSERTED path only works for true identity columns.
    if identity in columns:
        cursor.fast_executemany = False
        cursor.execute(f"INSERT INTO [{table}] ({collist}) VALUES ({placeholders})",
                       values)
        return int(values[columns.index(identity)]), notes
    sql = (f"INSERT INTO [{table}] ({collist}) "
           f"OUTPUT INSERTED.[{identity}] "
           f"VALUES ({placeholders})")
    cursor.execute(sql, values)
    new_id = cursor.fetchone()[0]
    while cursor.nextset():
        pass
    return int(new_id), notes


def clear_entity(entity: EntityMigration) -> dict[str, int]:
    """Delete this entity's existing rows from the web DB (clear-before-import).

    Scope is the entity's `clear_scope()` — company + optional sub-group filter —
    so clearing 'Paper' removes only ItemGroupID=14 rows, leaving Reel/Roll and
    any other group alone. Child/detail tables (`clear_child_tables`) are removed
    first (FK order). Everything runs in ONE transaction; on any error nothing is
    deleted. Returns {table: rows_deleted}.
    """
    web = db.get_web()
    table = entity.target_table
    identity = entity.target_identity
    where, params = entity.clear_scope()
    deleted: dict[str, int] = {}
    cur = web.cursor()
    try:
        # Collect the parent ids in scope so child deletes can target them.
        ids = [r[0] for r in cur.execute(
            f"SELECT [{identity}] FROM [{table}] WHERE {where}", params).fetchall()]
        if ids:
            # Delete children first, chunking the id list to stay within the
            # SQL Server parameter limit (~2100).
            for child_table, fk in entity.clear_child_tables:
                n = 0
                for i in range(0, len(ids), 1000):
                    chunk = ids[i:i + 1000]
                    ph = ",".join("?" for _ in chunk)
                    cur.execute(
                        f"DELETE FROM [{child_table}] WHERE [{fk}] IN ({ph})", chunk)
                    n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                deleted[child_table] = n
            # Entity-specific related deletes (e.g. ProductMaster -> JobBooking /
            # JobApprovedCost by BookingID), same transaction.
            entity.clear_related(cur, ids, deleted)
        # Delete the parents in scope.
        cur.execute(f"DELETE FROM [{table}] WHERE {where}", params)
        deleted[table] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        web.commit()
    except Exception:
        web.rollback()
        raise
    return deleted


# Cache of {table.lower(): set(lowercased MAX-width column names)}. The schema
# doesn't change during a migration, so we look it up from sys.columns ONCE per
# table instead of on every batch insert. Without this, _insert_children fired a
# fresh sys.columns query for EVERY child batch — hundreds of thousands of extra
# schema round-trips over a full run, invisible on a local DB but the dominant
# cost on a real/networked SQL Server. Cleared by reset_schema_caches().
_max_col_cache: dict[str, set[str]] = {}


def _table_max_columns(table: str) -> set[str]:
    key = table.lower()
    cached = _max_col_cache.get(key)
    if cached is not None:
        return cached
    rows = db.query_web(
        "SELECT c.name AS col, c.max_length AS ml, t.name AS dtype "
        "FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id "
        "WHERE c.object_id = OBJECT_ID(?)", [table])
    maxset = {r["col"].lower() for r in rows
              if r["ml"] == -1 and (r["dtype"] or "").lower() in
              ("nvarchar", "varchar", "varbinary")}
    _max_col_cache[key] = maxset
    return maxset


def _has_max_column(table: str, columns: list[str]) -> bool:
    """True if any inserted column is an unbounded (MAX) string/binary type.
    fast_executemany pre-allocates a buffer at the column's declared max width,
    which for MAX columns is ~2GB/cell → MemoryError. So we must NOT use the
    fast path when a MAX column is present. Column set is cached per table."""
    maxset = _table_max_columns(table)
    return any(c.lower() in maxset for c in columns)


def reset_schema_caches() -> None:
    """Drop cached table-schema info. Call after any ALTER TABLE done at runtime
    (e.g. adding RefToolId / DesktopProductMasterID) so the new column is seen."""
    _max_col_cache.clear()


def _insert_children(cursor: pyodbc.Cursor, table: str, columns: list[str],
                     rows: list[list]):
    """Batch parameterised INSERT of many child rows. Uses fast_executemany
    unless the target has a MAX-width column (which would blow memory)."""
    if not rows:
        return
    rows = [_fit_values(table, columns, r)[0] for r in rows]
    placeholders = ", ".join("?" for _ in columns)
    collist = ", ".join(f"[{c}]" for c in columns)
    sql = f"INSERT INTO [{table}] ({collist}) VALUES ({placeholders})"
    try:
        cursor.fast_executemany = not _has_max_column(table, columns)
    except Exception:
        cursor.fast_executemany = False
    cursor.executemany(sql, rows)


# Public helpers for hierarchical entities to do their own nested inserts
# using the live transaction cursor.
def insert_parent_row(cursor, table, identity, columns, values) -> int:
    new_id, _notes = _insert_parent(cursor, table, identity, columns, values)
    return new_id


def insert_child_rows(cursor, table, columns, rows):
    _insert_children(cursor, table, columns, rows)


def insert_one_row(cursor, table, columns, values):
    """Insert a single row with a plain (non-fast) execute. Used by hierarchical
    entities for nested child rows, where fast_executemany's strict per-column
    type binding is brittle with mixed/None values across heterogeneous rows."""
    values, _notes = _fit_values(table, columns, values)
    placeholders = ", ".join("?" for _ in columns)
    collist = ", ".join(f"[{c}]" for c in columns)
    cursor.fast_executemany = False
    cursor.execute(
        f"INSERT INTO [{table}] ({collist}) VALUES ({placeholders})", values)


# ----------------------------------------------------------------------------
# The driver
# ----------------------------------------------------------------------------
def run_entity(entity: EntityMigration,
               progress: ProgressFn | None = None,
               stop_flag: Callable[[], bool] | None = None,
               _result: MigrationResult | None = None) -> MigrationResult:
    """
    Migrate one entity. Each source record is committed in its own transaction
    so a failure on one record never rolls back already-migrated records and
    never leaves a half-written record behind.

    `_result` lets a caller pass its own MigrationResult (e.g. one whose `add`
    is wrapped to stream each record to a live UI); if omitted, a fresh one is
    created.
    """
    result = _result if _result is not None else MigrationResult(entity=entity.name)
    web = db.get_web()

    entity.before_import()          # prerequisite migrations
    rows = entity.read_source()
    total = len(rows)
    if progress:
        progress(0, total, f"Read {total} source rows for {entity.name}")

    # Phase 1: skip already-migrated rows and resolve refs up front; a ref
    # failure fails only that row (as before). What survives is the insert todo.
    todo = []
    for row in rows:
        if stop_flag and stop_flag():
            break
        key = entity.source_key(row)
        if entity.already_migrated(row):
            result.add(RecordResult(key, Outcome.SKIPPED, message="already migrated"))
            continue
        try:
            refs = entity.resolve_refs(row)             # may raise ValueError
        except Exception as ex:
            result.add(RecordResult(key, Outcome.FAILED, message=str(ex)))
            continue
        todo.append(_RunItem(entity, row, refs, key))

    # Phase 2: insert them, committing in batches (large speed-up), with the
    # same per-record error isolation via row-by-row retry on a batch failure.
    def do_one(it, cursor):
        cols, vals = entity.build_parent(it._row, it._refs)
        parent_id, _notes = _insert_parent(
            cursor, entity.target_table, entity.target_identity, cols, vals)
        for table, ccols, crows in entity.build_children(it._row, it._refs, parent_id):
            _insert_children(cursor, table, ccols, crows)
        if hasattr(entity, "insert_children"):
            entity.insert_children(it._row, it._refs, parent_id, cursor)
        entity.after_insert(it._row, it._refs, parent_id, cursor)
        return parent_id

    _commit_in_batches(web, todo, do_one, result, progress, stop_flag, total)
    if progress:
        progress(total, total, result.summary())
    return result


class _RunItem:
    """A resolved row ready to insert (mirrors PreviewRow's shape for _commit_in_batches)."""
    __slots__ = ("_row", "_refs", "source_key", "message")

    def __init__(self, entity, row, refs, key):
        self._row = row
        self._refs = refs
        self.source_key = key
        self.message = ""


# ----------------------------------------------------------------------------
# Preview (build rows, do NOT insert)  +  import-selected
# ----------------------------------------------------------------------------
@dataclass
class PreviewRow:
    """One previewed record: the exact target columns/values that WOULD be
    written, plus its status — so the UI can show it and let the user choose."""
    index: int
    source_key: str
    outcome: Outcome                 # INSERTED here means "would insert"
    columns: list                    # target column names (parent)
    values: list                     # fitted values aligned to columns
    message: str = ""
    _row: dict = field(default_factory=dict)   # original source row (for import)
    _refs: dict = field(default_factory=dict)  # resolved refs (for import)


@dataclass
class PreviewResult:
    entity: str
    columns: list = field(default_factory=list)   # union of target columns (display order)
    rows: list = field(default_factory=list)       # list[PreviewRow]
    would_insert: int = 0
    would_skip: int = 0
    would_fail: int = 0

    def summary(self) -> str:
        return (f"{self.entity}: {self.would_insert} to import, "
                f"{self.would_skip} already migrated, {self.would_fail} with issues "
                f"(of {len(self.rows)})")


def preview_entity(entity: EntityMigration,
                   progress: ProgressFn | None = None,
                   stop_flag=None) -> PreviewResult:
    """Build every row exactly as it would be written — FKs resolved, values
    fit to target width — but DO NOT touch the database. Returns a PreviewResult
    the UI can render; importing later uses import_preview()."""
    pr = PreviewResult(entity=entity.name)
    rows = entity.read_source()
    total = len(rows)
    if progress:
        progress(0, total, f"Loaded {total} source rows for {entity.name}")

    col_order: list[str] = []
    for i, row in enumerate(rows, start=1):
        if stop_flag and stop_flag():
            break
        key = entity.source_key(row)
        try:
            if entity.already_migrated(row):
                pr.rows.append(PreviewRow(i, key, Outcome.SKIPPED, [], [],
                                          "already migrated", _row=row))
                pr.would_skip += 1
            else:
                refs = entity.resolve_refs(row)            # may raise ValueError
                cols, vals = entity.build_parent(row, refs)
                vals, notes = _fit_values(entity.target_table, cols, vals)
                for c in cols:
                    if c not in col_order:
                        col_order.append(c)
                pr.rows.append(PreviewRow(i, key, Outcome.INSERTED, cols, vals,
                                          "; ".join(notes), _row=row, _refs=refs))
                pr.would_insert += 1
        except ValueError as ve:
            pr.rows.append(PreviewRow(i, key, Outcome.FAILED, [], [], str(ve), _row=row))
            pr.would_fail += 1
        except Exception as ex:
            pr.rows.append(PreviewRow(i, key, Outcome.FAILED, [], [], str(ex), _row=row))
            pr.would_fail += 1
        if progress and (i % 50 == 0 or i == total):
            progress(i, total, f"Prepared {i}/{total}")

    pr.columns = col_order
    if progress:
        progress(total, total, pr.summary())
    return pr


def import_preview(entity: EntityMigration, preview: PreviewResult,
                   selected_indexes: set[int] | None = None,
                   progress: ProgressFn | None = None,
                   stop_flag=None,
                   _result: MigrationResult | None = None) -> MigrationResult:
    """Insert only the chosen previewed rows (those with outcome INSERTED and,
    if given, whose index is in selected_indexes). Each row is its own
    transaction (parent + children atomic), exactly like run_entity.

    `_result` lets a caller pass its own MigrationResult (e.g. one whose `add`
    streams each record to a live UI) — mirrors run_entity. If omitted, a fresh
    one is created."""
    result = _result if _result is not None else MigrationResult(entity=entity.name)
    web = db.get_web()
    entity.before_import()          # prerequisite migrations (real import only)
    entity.prepare_import()         # rebuild per-instance child maps on this entity
    todo = [r for r in preview.rows
            if r.outcome is Outcome.INSERTED
            and (selected_indexes is None or r.index in selected_indexes)]
    total = len(todo)
    if progress:
        progress(0, total, f"Importing {total} selected rows for {entity.name}")

    def do_one(prow, cursor):
        parent_id, _notes = _insert_parent(
            cursor, entity.target_table, entity.target_identity,
            prow.columns, prow.values)
        for table, ccols, crows in entity.build_children(
                prow._row, prow._refs, parent_id):
            _insert_children(cursor, table, ccols, crows)
        if hasattr(entity, "insert_children"):
            entity.insert_children(prow._row, prow._refs, parent_id, cursor)
        entity.after_insert(prow._row, prow._refs, parent_id, cursor)
        return parent_id

    _commit_in_batches(web, todo, do_one, result, progress, stop_flag, total)
    if progress:
        progress(total, total, result.summary())
    return result


# Records committed per transaction. Committing every record is the biggest
# avoidable cost (a network round-trip + log flush each time); batching many
# records into one commit is a large speed-up with no change to the data.
_COMMIT_BATCH = 50


# How many times to re-establish a dropped connection and retry before giving up.
_CONN_RETRIES = 4


def _is_connection_error(ex) -> bool:
    """True for errors that mean the physical DB connection dropped (so a reconnect
    + retry is worth it) rather than a bad record. Covers ODBC 08xxx SQLSTATEs and
    the 'Communication link failure' text over a flaky remote link."""
    state = ""
    args = getattr(ex, "args", None)
    if args:
        state = str(args[0])
    blob = (state + " " + str(ex))
    return any(m in blob for m in (
        "08S01", "08003", "08007", "08004", "08001", "08S02", "HYT00", "HY000",
        "Communication link failure", "Named Pipes Provider", "TCP Provider",
        "connection is closed", "Connection is busy"))


def _safe_rollback(conn):
    try:
        conn.rollback()
    except Exception:
        pass


def _reconnect_web(attempt: int):
    """Re-establish the dropped web connection with a short backoff."""
    time.sleep(min(2 ** attempt, 15))     # 1, 2, 4, 8, 15… seconds
    db.reconnect_web()


def _commit_in_batches(web, items, do_one, result, progress, stop_flag, total):
    """Insert `items` (each via do_one(item, cursor)), committing every
    _COMMIT_BATCH records. On a BAD record the batch is re-run one-per-commit so
    only the bad one fails. On a CONNECTION drop (remote link failure) the web
    connection is re-established and the batch is retried — so a flaky network no
    longer aborts the whole migration. The live connection is always fetched fresh
    from db.get_web() so it follows a reconnect."""
    i = 0
    n = len(items)
    while i < n:
        if stop_flag and stop_flag():
            break
        batch = items[i:i + _COMMIT_BATCH]
        _run_batch(batch, do_one, result, stop_flag)
        i += len(batch)
        if progress:
            progress(min(i, total), total, f"{min(i, total)}/{total}")


def _run_batch(batch, do_one, result, stop_flag):
    """Try the whole batch in one transaction, reconnecting+retrying on a dropped
    link. On a non-connection failure (a bad record) fall through to row-by-row."""
    for attempt in range(_CONN_RETRIES):
        web = db.get_web()
        cursor = web.cursor()
        done = []                          # (item, target_id) staged this batch
        try:
            for it in batch:
                done.append((it, do_one(it, cursor)))
            web.commit()
            for it, pid in done:
                result.add(RecordResult(it.source_key, Outcome.INSERTED,
                                        target_id=pid, message=it.message))
            return
        except Exception as ex:
            _safe_rollback(web)
            if _is_connection_error(ex) and attempt < _CONN_RETRIES - 1:
                _reconnect_web(attempt)
                continue                   # retry the batch on the fresh connection
            break                          # bad record (or out of retries) -> row-by-row
    for it in batch:
        if stop_flag and stop_flag():
            break
        _run_one(it, do_one, result)


def _run_one(it, do_one, result):
    """One record in its own transaction, reconnecting+retrying on a dropped link;
    a genuine bad record is marked FAILED and the run continues."""
    for attempt in range(_CONN_RETRIES):
        web = db.get_web()
        cursor = web.cursor()
        try:
            pid = do_one(it, cursor)
            web.commit()
            result.add(RecordResult(it.source_key, Outcome.INSERTED,
                                    target_id=pid, message=it.message))
            return
        except Exception as ex:
            _safe_rollback(web)
            if _is_connection_error(ex) and attempt < _CONN_RETRIES - 1:
                _reconnect_web(attempt)
                continue
            result.add(RecordResult(it.source_key, Outcome.FAILED, message=str(ex)))
            return
