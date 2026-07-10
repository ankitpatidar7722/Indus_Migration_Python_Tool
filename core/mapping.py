"""
Declarative mapping layer that sits on top of the engine.

Most of the 22 entities follow the same shape: read a desktop source table,
rename columns to the web target columns, stamp context (CompanyID/UserID/
FYear/dates), optionally resolve a few foreign keys to already-migrated web
ids, insert the parent (+ an optional EAV "active" detail row), and skip rows
already present. `MappedEntity` captures that shape so each concrete entity is
just a small declaration (see core/entities/*). Anything unusual overrides a hook.

Two helpers do the heavy lifting:
  * RefMap          — desktop source-id  ->  web target-id, via a Ref* column.
  * MappedEntity    — column-map + config  ->  a full EntityMigration.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from core import db
from core.engine import EntityMigration


# ----------------------------------------------------------------------------
# ContentMaster resolver: desktop Orientation_Name -> web ContentMaster.ContentID.
# Desktop/web use different formatting (spaces, punctuation, case), so we match
# the FULL name after normalising to lowercase + alphanumeric-only (exact, NOT
# partial — so "Reverse Tuck In" and "Reverse Tuck And Tongue" stay distinct).
# A small ALIAS map hard-maps known equivalents (e.g. "6 Corner Box"->"SixCornerBox").
# Unmatched names resolve to ContentID 0 (valid; web allocation tables allow 0).
# ----------------------------------------------------------------------------
import re as _re

CONTENT_NAME_ALIASES = {
    # normalised desktop name -> normalised web ContentName
    "6cornerbox": "sixcornerbox",
    "6cornerbox1": "sixcornerbox",
}

_content_map_cache: dict | None = None


def _norm_name(s) -> str:
    return _re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resolve_content_id(orientation_name) -> int:
    """Return the web ContentID for a desktop orientation name (0 if no match)."""
    global _content_map_cache
    if _content_map_cache is None:
        rows = db.query_web(
            "SELECT ContentID, ContentName FROM ContentMaster "
            "WHERE ISNULL(ContentName,'')<>''")
        _content_map_cache = {_norm_name(r["ContentName"]): r["ContentID"] for r in rows}
    key = _norm_name(orientation_name)
    key = CONTENT_NAME_ALIASES.get(key, key)   # apply alias if any
    return _content_map_cache.get(key, 0)


_content_domain_cache: dict | None = None


def resolve_content_domain_type(orientation_name):
    """Match a desktop content type (Orientation / PlanContentType) to
    ContentMaster.ContentName and return its ContentDomainType (e.g. 'Label' ->
    'Flexo', most others -> 'Offset'). None if no match (caller applies a default)."""
    global _content_domain_cache
    if _content_domain_cache is None:
        rows = db.query_web(
            "SELECT ContentName, ContentDomainType FROM ContentMaster "
            "WHERE ISNULL(ContentName,'')<>''")
        _content_domain_cache = {_norm_name(r["ContentName"]): r["ContentDomainType"]
                                 for r in rows}
    key = _norm_name(orientation_name)
    key = CONTENT_NAME_ALIASES.get(key, key)
    return _content_domain_cache.get(key)


# ----------------------------------------------------------------------------
# ItemSubGroup resolver: desktop Material_Group_ID -> web ItemSubGroupID, BY NAME.
# Desktop has one self-referencing Material_Group_Master; web splits into
# ItemGroupMaster + ItemSubGroupMaster with independent ids and no Ref column.
# So we map the desktop group's NAME (normalized) to the web sub-group name.
# ----------------------------------------------------------------------------
_subgroup_name_cache: dict | None = None
_material_group_names: dict | None = None


def _load_subgroup_resolver():
    global _subgroup_name_cache, _material_group_names
    if _subgroup_name_cache is None:
        rows = db.query_web(
            "SELECT ItemSubGroupID, ItemSubGroupName FROM ItemSubGroupMaster "
            "WHERE ISNULL(IsDeletedTransaction,0)=0 AND ISNULL(ItemSubGroupName,'')<>''")
        _subgroup_name_cache = {_norm_name(r["ItemSubGroupName"]): r["ItemSubGroupID"]
                                for r in rows}
    if _material_group_names is None:
        rows = db.query_desktop(
            "SELECT Material_Group_ID, ISNULL(Material_Group_Name,'') AS nm "
            "FROM Material_Group_Master")
        _material_group_names = {r["Material_Group_ID"]: r["nm"] for r in rows}


def reset_subgroup_resolver():
    """Bust the sub-group name cache — call after migrating Material_Group_Master
    into ItemSubGroupMaster so newly-created sub-groups become resolvable."""
    global _subgroup_name_cache, _material_group_names
    _subgroup_name_cache = None
    _material_group_names = None


def resolve_subgroup_id(material_group_id) -> int:
    """desktop Material_Group_ID -> web ItemSubGroupID (0 if name not matched)."""
    _load_subgroup_resolver()
    name = _material_group_names.get(material_group_id)
    if not name:
        return 0
    return _subgroup_name_cache.get(_norm_name(name), 0)


# ----------------------------------------------------------------------------
# EAV detail rows driven by a *GroupFieldMaster (the web ERP's field template).
# The web app reads master values from the detail table as FieldName/FieldValue
# rows; the spec (MigrationIssue.txt) wants one detail row per defined field.
# ----------------------------------------------------------------------------
_field_master_cache: dict[tuple[str, int], list[str]] = {}


def group_field_names(field_master_table: str, group_col: str, group_id: int,
                      company_id: int | None = None) -> list[str]:
    """Field names defined for a group in a *GroupFieldMaster table, ordered by
    draw sequence — the set of detail rows the web ERP expects for that group."""
    key = (field_master_table.lower(), group_id)
    if key not in _field_master_cache:
        where = f"WHERE {group_col}=? AND ISNULL(IsDeletedTransaction,0)=0 " \
                f"AND ISNULL(FieldName,'')<>''"
        params: list = [group_id]
        rows = db.query_web(
            f"SELECT FieldName FROM [{field_master_table}] {where} "
            f"ORDER BY FieldDrawSequence", params)
        _field_master_cache[key] = [r["FieldName"] for r in rows]
    return _field_master_cache[key]


def _target_col_types(main_table: str) -> dict:
    """Return {col_lower: (sql_type_string, ui_field_type)} for a main table,
    used to fill a new field-master row's FieldDataType / FieldType."""
    rows = db.query_web(
        "SELECT c.name AS col, t.name AS dtype, c.max_length AS ml "
        "FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id "
        "WHERE c.object_id = OBJECT_ID(?)", [main_table])
    out = {}
    for r in rows:
        dt = (r["dtype"] or "").lower()
        ml = r["ml"]
        if dt in ("nvarchar", "nchar") and ml and ml > 0:
            sql_type = f"{dt}({ml // 2})"
        elif dt in ("varchar", "char") and ml and ml > 0:
            sql_type = f"{dt}({ml})"
        else:
            sql_type = dt
        if dt == "bit":
            ui = "checkbox"
        elif dt in ("int", "bigint", "smallint", "tinyint", "decimal",
                    "numeric", "float", "real", "money", "smallmoney"):
            ui = "number"
        else:
            ui = "text"
        out[r["col"].lower()] = (sql_type, ui)
    return out


def _display_name(field: str) -> str:
    """'PurchaseOrderQuantity' -> 'Purchase Order Quantity'."""
    import re
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", field)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return s.strip()


def ensure_int_identity_pk(table: str, id_col: str):
    """Some web field-master tables ship with a tinyint IDENTITY primary key
    that maxes out at 255 — once full, NO new fields can be added. Widen it to
    int (idempotent: only acts while still tinyint). Safe because these surrogate
    keys aren't referenced by foreign keys."""
    try:
        is_tiny = db.query_web(
            "SELECT 1 c FROM sys.columns c JOIN sys.types t ON c.user_type_id=t.user_type_id "
            "WHERE c.object_id=OBJECT_ID(?) AND c.name=? AND t.name='tinyint'",
            [table, id_col])
        if not is_tiny:
            return
        cur = db.get_web().cursor()
        cur.execute(f"""
            DECLARE @pk SYSNAME = (SELECT name FROM sys.key_constraints
                WHERE parent_object_id=OBJECT_ID('{table}') AND type='PK');
            IF @pk IS NOT NULL EXEC('ALTER TABLE [{table}] DROP CONSTRAINT [' + @pk + ']');
            ALTER TABLE [{table}] DROP COLUMN [{id_col}];
            ALTER TABLE [{table}] ADD [{id_col}] INT IDENTITY(256,1) NOT NULL;
            ALTER TABLE [{table}] ADD CONSTRAINT [PK_{table}_w] PRIMARY KEY ([{id_col}]);
        """)
        db.get_web().commit()
    except Exception:
        db.get_web().rollback()


def ensure_group_fields(field_master_table: str, group_col: str, group_id: int,
                        main_table: str, fields: list[str], company_id: int,
                        user_id: int, fyear: str):
    """Make sure every field in `fields` exists in the group's field-master.
    Missing ones are INSERTed (idempotent) so the web ERP knows the field and
    the migration writes a detail row for it. Metadata inferred from the main
    table column's SQL type. Returns the list of fields actually added."""
    existing_rows = db.query_web(
        f"SELECT FieldName FROM [{field_master_table}] WHERE {group_col}=? "
        f"AND ISNULL(IsDeletedTransaction,0)=0 AND ISNULL(FieldName,'')<>''",
        [group_id])
    existing = {(r["FieldName"] or "").strip().lower() for r in existing_rows}
    types = _target_col_types(main_table)

    # next draw sequence (FieldDrawSequence is tinyint, 0..255)
    seqrow = db.query_web(
        f"SELECT ISNULL(MAX(FieldDrawSequence),0) AS mx FROM [{field_master_table}] "
        f"WHERE {group_col}=?", [group_id])
    seq = min(int(seqrow[0]["mx"]) if seqrow and seqrow[0]["mx"] is not None else 0, 255)

    to_add = [f for f in fields if f.strip().lower() not in existing]
    if not to_add:
        return []

    # Widen a tinyint-maxed identity PK first, or the inserts below overflow.
    id_col = {"LedgerGroupFieldMaster": "LedgerGroupFieldID",
              "ItemGroupFieldMaster": "ItemGroupFieldID",
              "ToolGroupFieldMaster": "ToolGroupFieldID"}.get(field_master_table)
    if id_col:
        ensure_int_identity_pk(field_master_table, id_col)

    cur = db.get_web().cursor()
    added = []
    for f in to_add:
        # FieldDrawSequence / FieldTabIndex are tinyint (0..255). Stay in range;
        # once at the ceiling, reuse 255 (ordering past that doesn't matter).
        seq = seq + 1 if seq < 255 else 255
        sql_type, ui = types.get(f.lower(), ("nvarchar(512)", "text"))
        cols = [group_col, "FieldName", "FieldDataType", "FieldDisplayName",
                "FieldType", "IsDisplay", "IsCalculated", "IsActive", "IsDeleted",
                "FieldDrawSequence", "FieldTabIndex", "CompanyID", "UserID",
                "FYear", "CreatedBy", "ModifiedBy", "IsDeletedTransaction"]
        vals = [group_id, f, sql_type, _display_name(f), ui, 1, 0, 1, 0,
                seq, seq, company_id, user_id, fyear, user_id, user_id, 0]
        placeholders = ", ".join("?" for _ in cols)
        collist = ", ".join(f"[{c}]" for c in cols)
        cur.execute(
            f"INSERT INTO [{field_master_table}] ({collist}) VALUES ({placeholders})",
            vals)
        added.append(f)
    db.get_web().commit()
    # bust the field-name cache so the detail builder sees the new fields
    _field_master_cache.pop((field_master_table.lower(), group_id), None)
    return added


def _fmt_detail_value(v):
    """Stringify an EAV FieldValue WITHOUT tacking a trailing '.0' onto a whole
    number. The desktop stores GSM (and similar) as int OR real; pyodbc hands the
    real ones back as Python float, so a plain str() turned 80 into '80.0'. Here a
    whole numeric -> '80', a genuine decimal -> '80.5' (preserved), and bools /
    text pass through str() unchanged (True/False, 'ART PAPER', …)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    # Decimal or other numeric-like: drop a trailing '.0' only when whole.
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return str(v)


def build_eav_detail_rows(detail_table: str, fk_col: str, group_col: str,
                          parent_id: int, group_id: int, parent_values: dict,
                          field_names: list[str], company_id: int, user_id: int,
                          fyear: str, active_field: str, active_value: str):
    """Return (columns, rows) for the EAV detail insert: one row per field_name,
    FieldValue pulled from parent_values (the main row we just built), plus the
    active flag first. Generic across LedgerMasterDetails / ItemMasterDetails /
    ToolMasterDetails."""
    # Full column set, but keep only those the detail table actually has
    # (e.g. ToolMasterDetails has no FYear). Values are positional, so drop the
    # value for any column we skip.
    all_cols = [fk_col, group_col, "FieldName", "FieldValue", "ParentFieldName",
                "ParentFieldValue", "CompanyID", "UserID", "FYear", "CreatedBy",
                "ModifiedBy", "SequenceNo", "IsActive", "IsDeletedTransaction"]
    keep = [i for i, c in enumerate(all_cols) if _has_column(detail_table, c)]
    cols = [all_cols[i] for i in keep]
    rows = []
    seq = 1

    def add(fname, fval):
        nonlocal seq
        sval = _fmt_detail_value(fval)
        full = [parent_id, group_id, fname, sval, fname, sval, company_id,
                user_id, fyear, user_id, user_id, seq, 1, 0]
        rows.append([full[i] for i in keep])
        seq += 1

    add(active_field, active_value)
    for fname in field_names:
        if fname == active_field:
            continue
        add(fname, parent_values.get(fname))
    return cols, rows


# ----------------------------------------------------------------------------
# RefMap: resolve a desktop source id to the web id of the already-migrated row.
# Mirrors the legacy JS getdataForFilter RefID mapping: every migrated master
# carries a Ref<thing>ID column holding its original desktop id, so dependent
# entities look the web id up by that.
# ----------------------------------------------------------------------------
class RefMap:
    def __init__(self, table: str, ref_col: str, id_col: str,
                 company_id: int | None = None,
                 group_col: str | None = None, group_ids=None):
        self.table = table
        self.ref_col = ref_col
        self.id_col = id_col
        self.company_id = company_id
        # Optional group restriction (e.g. ItemMaster ItemGroupID IN (2,13,14) so a
        # desktop Paper_ID only ever resolves to a substrate item, never a colliding
        # RefItemID from another group).
        self.group_col = group_col
        self.group_ids = list(group_ids) if group_ids else None
        self._map: dict | None = None

    def _load(self):
        where = "WHERE ISNULL(IsDeletedTransaction,0)=0"
        params: list = []
        # Only filter by company if the table actually has that column.
        if self.company_id is not None and _has_column(self.table, "CompanyID"):
            where += " AND CompanyID=?"
            params.append(self.company_id)
        if self.group_col and self.group_ids and _has_column(self.table, self.group_col):
            ph = ",".join("?" for _ in self.group_ids)
            where += f" AND [{self.group_col}] IN ({ph})"
            params.extend(self.group_ids)
        rows = db.query_web(
            f"SELECT [{self.ref_col}] AS r, [{self.id_col}] AS i "
            f"FROM [{self.table}] {where} AND [{self.ref_col}] IS NOT NULL",
            params
        )
        self._map = {}
        for row in rows:
            self._map[_norm_key(row["r"])] = row["i"]

    def resolve(self, source_id, required: bool = True, label: str = ""):
        if self._map is None:
            self._load()
        key = _norm_key(source_id)
        val = self._map.get(key)
        if val is None and required:
            raise ValueError(
                f"{label or self.table}: no migrated row for source id "
                f"{source_id!r} (migrate {self.table.replace('Master','')} first)")
        return val


def _norm_key(v):
    """Normalise an id for dict lookup (ints, decimals, strings compare equal)."""
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    try:
        return int(v)
    except (TypeError, ValueError):
        return str(v).strip()


_col_cache: dict[str, set[str]] = {}


def _has_column(table: str, col: str) -> bool:
    key = table.lower()
    if key not in _col_cache:
        rows = db.query_web(
            "SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?)", [table])
        _col_cache[key] = {r["name"].lower() for r in rows}
    return col.lower() in _col_cache[key]


_desktop_col_cache: dict[str, set[str]] = {}


def _desktop_columns(table: str) -> set[str]:
    """Lower-cased column names that actually exist on a DESKTOP source table.
    Lets read_source skip mapped/extra columns a given desktop schema lacks
    (schemas vary across customer DBs) instead of failing the whole entity."""
    key = table.lower()
    if key not in _desktop_col_cache:
        rows = db.query_desktop(
            "SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?)", [table])
        _desktop_col_cache[key] = {r["name"].lower() for r in rows}
    return _desktop_col_cache[key]


def strip_quotes(v):
    """G2: remove single (') and double (") quotes from a text value. Curly
    quotes are stripped too. Non-strings pass through unchanged."""
    if not isinstance(v, str):
        return v
    for q in ("'", '"', "‘", "’", "“", "”", "`"):
        v = v.replace(q, "")
    return v


def to_sql_value(v):
    """Coerce a pyodbc-read value into something safe to re-insert.
    G2: text values have quote characters removed before insert."""
    if isinstance(v, _dt.datetime):
        return v
    if isinstance(v, str):
        return strip_quotes(v)
    return v


# ----------------------------------------------------------------------------
# CountryStateMaster resolver (G3): match a desktop Country/State value to the
# canonical value in the web CountryStateMaster (case-insensitive), returning the
# master's spelling (e.g. "india" -> "India"). Unmatched -> "" (blank).
# ----------------------------------------------------------------------------
_country_set: dict | None = None      # norm(country) -> canonical Country
_state_set: dict | None = None        # norm(state)   -> canonical State


def _load_country_state(company_id):
    global _country_set, _state_set
    if _country_set is not None:
        return
    _country_set, _state_set = {}, {}
    rows = db.query_web(
        "SELECT DISTINCT Country, State FROM CountryStateMaster "
        "WHERE ISNULL(IsDeletedTransaction,0)=0")
    for r in rows:
        c = (r["Country"] or "").strip()
        s = (r["State"] or "").strip()
        if c:
            _country_set.setdefault(_norm_name(c), c)
        if s:
            _state_set.setdefault(_norm_name(s), s)


_branch_id_cache: dict = {}


def resolve_branch_id(company_id, desktop_branch_id=None) -> int | None:
    """Resolve a web BranchID from BranchMaster (QA: 'find from Branch Master').
    The desktop Branch_ID is empty/0 in practice and the desktop Branch_Master is
    empty, so we map to the company's web branch — preferring a matching desktop
    id, else the company's single/first BranchMaster row."""
    if company_id not in _branch_id_cache:
        rows = db.query_web(
            "SELECT BranchID FROM BranchMaster WHERE CompanyID=? "
            "AND ISNULL(IsDeletedTransaction,0)=0 ORDER BY BranchID", [company_id])
        _branch_id_cache[company_id] = [r["BranchID"] for r in rows]
    ids = _branch_id_cache[company_id]
    if not ids:
        return None
    try:
        d = int(desktop_branch_id) if desktop_branch_id is not None else 0
    except (TypeError, ValueError):
        d = 0
    return d if d in ids else ids[0]


def resolve_country(value, company_id) -> str:
    """Canonical Country from CountryStateMaster (case-insensitive). '' if no match."""
    _load_country_state(company_id)
    return _country_set.get(_norm_name(value), "")


def resolve_state(value, company_id) -> str:
    """Canonical State from CountryStateMaster (case-insensitive). '' if no match."""
    _load_country_state(company_id)
    return _state_set.get(_norm_name(value), "")


# ----------------------------------------------------------------------------
# MappedEntity: a declarative EntityMigration.
# ----------------------------------------------------------------------------
@dataclass
class ChildEAV:
    """An EAV 'active flag' detail row (LedgerMasterDetails / ItemMasterDetails style)."""
    table: str
    fk_col: str               # e.g. "LedgerID" / "ItemID"
    field_name: str           # e.g. "ISLedgerActive" / "IsActive"
    field_value: str = "1"
    group_col: str | None = None   # e.g. "LedgerGroupID" / "ItemGroupID"


class MappedEntity(EntityMigration):
    # ---- declarative config (override per entity) --------------------------
    source_table: str = ""
    source_where: str = ""                  # extra WHERE (no leading AND)
    source_params: list = []
    column_map: dict[str, str] = {}         # source_col -> target_col
    name_field_source: str = ""             # source col used for the log key + dup check
    name_field_target: str = ""             # target col holding the name (dup check)
    ref_resolvers: dict = {}                # target_col -> (RefMap, source_col, required)
    constant_columns: dict = {}             # target_col -> constant value
    child_eav: ChildEAV | None = None
    group_id: int | None = None             # for entities scoped to a group
    extra_source_cols: list = []            # source cols needed beyond the map (e.g. for refs/group)
    # Global rule: any entity that carries a DepartmentID *reference* normalises
    # it — a real department reference collapses to the default department 200,
    # while a desktop 0 maps to the default-department 100 (mirrors the 0->100
    # rule applied inside DepartmentMaster itself). DepartmentMaster, whose
    # DepartmentID is its OWN id (not a reference), opts out via this flag.
    normalize_department_id: bool = True
    # Machine/Process opt out of the ->200 collapse (QA: keep the real department
    # id when non-zero) but STILL map a desktop 0 to the default department 100.
    # Set normalize_department_id=False AND department_zero_to_100_only=True.
    department_zero_to_100_only: bool = False

    def __init__(self, company_id: int = 2, user_id: int = 1, fyear: str = ""):
        self.company_id = company_id
        self.user_id = user_id
        self.fyear = fyear
        self._existing: set = set()

    # ---- context columns stamped on every parent row -----------------------
    def context_columns(self) -> dict:
        ctx = {}
        now = _dt.datetime.now()
        for col, val in [
            ("CompanyID", self.company_id), ("UserID", self.user_id),
            ("FYear", self.fyear), ("CreatedBy", self.user_id),
            ("ModifiedBy", self.user_id), ("IsDeletedTransaction", 0),
            # G1: always stamp the destination CreatedDate/ModifiedDate with the
            # current system date/time (the columns have no DB default).
            ("CreatedDate", now), ("ModifiedDate", now),
        ]:
            # never stamp a context value onto the table's identity PK
            # (e.g. UserMaster's identity IS 'UserID').
            if col == getattr(self, "target_identity", None):
                continue
            if _has_column(self.target_table, col):
                ctx[col] = val
        return ctx

    # ---- pre-flight validation --------------------------------------------
    def validate_mapping(self):
        """Fail fast if any mapped/constant/ref/context target column does not
        exist on the target table. Catches mapping typos before a run instead of
        failing every row with a SQL error."""
        bad = [t for t in self.column_map.values() if not _has_column(self.target_table, t)]
        bad += [t for t in self.constant_columns if not _has_column(self.target_table, t)]
        bad += [t for t in self.ref_resolvers if not _has_column(self.target_table, t)]
        if bad:
            raise ValueError(
                f"{self.name}: target {self.target_table} is missing mapped "
                f"column(s): {', '.join(sorted(set(bad)))}")

    # ---- engine hooks ------------------------------------------------------
    def read_source(self) -> list[dict]:
        self.validate_mapping()
        self._load_existing()
        cols = list(self.column_map.keys())
        # also pull any source columns needed by ref resolvers
        for _tgt, (_rm, scol, _req) in self.ref_resolvers.items():
            if scol not in cols:
                cols.append(scol)
        # extra source columns an entity's hooks need (refs / group id, etc.)
        for c in self.extra_source_cols:
            if c not in cols:
                cols.append(c)
        if self.name_field_source and self.name_field_source not in cols:
            cols.append(self.name_field_source)
        # Drop any source column this desktop schema doesn't actually have, so a
        # schema variation (e.g. Tool_Master without Product_Group_ID) doesn't
        # fail the whole entity. Missing columns then read as None via row.get.
        have = _desktop_columns(self.source_table)
        cols = [c for c in dict.fromkeys(cols) if c.lower() in have]
        sel = ", ".join(f"[{c}]" for c in cols)
        where = ""
        if self.source_where:
            where = "WHERE " + self.source_where
        sql = f"SELECT {sel} FROM [{self.source_table}] {where}"
        return db.query_desktop(sql, list(self.source_params))

    def _load_existing(self):
        if not (self.name_field_target and _has_column(self.target_table,
                                                        self.name_field_target)):
            return
        conds = []
        params: list = []
        # Some target tables use IsDeletedTransaction, others don't (e.g.
        # UserMaster uses IsDeletedUser) — only filter on it when present.
        if _has_column(self.target_table, "IsDeletedTransaction"):
            conds.append("ISNULL(IsDeletedTransaction,0)=0")
        if _has_column(self.target_table, "CompanyID"):
            conds.append("CompanyID=?")
            params.append(self.company_id)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = db.query_web(
            f"SELECT [{self.name_field_target}] AS n FROM [{self.target_table}] {where}",
            params)
        self._existing = {(r["n"] or "").strip().lower() for r in rows}

    def source_key(self, row: dict) -> str:
        if self.name_field_source:
            return str(row.get(self.name_field_source) or "?").strip()
        return str(row)

    # When a master skips a record because a same-named row already exists in the
    # target, that existing row won't have the Ref* backpointer — so its children
    # can't resolve it. Declare (ref_col, source_id_col, name_col) and we'll stamp
    # the Ref* on the existing row so children link correctly.
    backfill_ref: tuple | None = None   # (ref_col, source_id_col, target_name_col)

    def already_migrated(self, row: dict) -> bool:
        if not self._existing:
            return False
        hit = self.source_key(row).strip().lower() in self._existing
        if hit and self.backfill_ref:
            self._backfill_ref_on_existing(row)
        return hit

    def _backfill_ref_on_existing(self, row: dict):
        """Stamp Ref* = desktop id onto the existing target row (where currently
        null) so dependent children can resolve it."""
        ref_col, src_id_col, name_col = self.backfill_ref
        src_id = row.get(src_id_col)
        name = self.source_key(row)
        if src_id is None:
            return
        try:
            cur = db.get_web().cursor()
            params = [src_id, name]
            where_company = ""
            if _has_column(self.target_table, "CompanyID"):
                where_company = " AND CompanyID=?"
                params.append(self.company_id)
            cur.execute(
                f"UPDATE [{self.target_table}] SET [{ref_col}]=? "
                f"WHERE [{name_col}]=? AND ISNULL([{ref_col}],0)=0{where_company} "
                f"AND ISNULL(IsDeletedTransaction,0)=0", params)
            db.get_web().commit()
        except Exception:
            db.get_web().rollback()

    def resolve_refs(self, row: dict) -> dict:
        refs = {}
        for tgt_col, (rm, scol, required) in self.ref_resolvers.items():
            refs[tgt_col] = rm.resolve(row.get(scol), required=required, label=tgt_col)
        return refs

    def build_parent(self, row: dict, refs: dict):
        cols: list[str] = []
        vals: list = []
        # mapped business columns
        for src, tgt in self.column_map.items():
            cols.append(tgt)
            vals.append(to_sql_value(row.get(src)))
        # resolved foreign keys
        for tgt_col, val in refs.items():
            cols.append(tgt_col)
            vals.append(val)
        # constants
        for tgt_col, val in self.constant_columns.items():
            cols.append(tgt_col)
            vals.append(val)
        # context
        for tgt_col, val in self.context_columns().items():
            if tgt_col not in cols:
                cols.append(tgt_col)
                vals.append(val)
        self._normalize_department(cols, vals)
        return cols, vals

    def _normalize_department(self, cols: list, vals: list):
        """Normalise a DepartmentID reference. Default rule: desktop 0 -> 100
        (default dept), any other present value -> 200 (default reference).
        Entities with department_zero_to_100_only map 0 -> 100 but KEEP a real
        non-zero id (QA: Machine/Process). DepartmentMaster opts out entirely."""
        if "DepartmentID" not in cols:
            return
        if not self.normalize_department_id and not self.department_zero_to_100_only:
            return
        i = cols.index("DepartmentID")
        raw = vals[i]
        try:
            is_zero = int(raw or 0) == 0
        except (TypeError, ValueError):
            is_zero = True   # blank / non-numeric -> treat as the 0 (default) case
        if is_zero:
            vals[i] = 100
        elif self.normalize_department_id:
            vals[i] = 200
        # else (zero-to-100-only, non-zero): leave the real department id

    def build_children(self, row: dict, refs: dict, parent_id: int):
        if not self.child_eav:
            return []
        e = self.child_eav
        cols = [e.fk_col, "FieldName", "FieldValue", "ParentFieldName",
                "ParentFieldValue"]
        vals = [parent_id, e.field_name, e.field_value, e.field_name, e.field_value]
        if e.group_col and self.group_id is not None:
            cols.append(e.group_col); vals.append(self.group_id)
        for c, v in self.context_columns().items():
            if _has_column(e.table, c) and c not in cols:
                cols.append(c); vals.append(v)
        if _has_column(e.table, "IsActive") and "IsActive" not in cols:
            cols.append("IsActive"); vals.append(1)
        return [(e.table, cols, [vals])]

    def after_insert(self, row: dict, refs: dict, parent_id: int, cursor):
        # remember within-run so a same-session repeat won't duplicate
        if self.name_field_target:
            self._existing.add(self.source_key(row).strip().lower())
