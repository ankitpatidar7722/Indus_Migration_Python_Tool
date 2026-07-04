"""
Flute Master — a standalone master (corrugation flute definitions).

Desktop `Flute_Master` -> web `FluteMaster`. Note the web FluteID is NOT an
identity column, so we generate it ourselves as MAX(FluteID)+1 (continuing from
whatever is already there, so re-runs don't collide).
"""

from __future__ import annotations

from core import db
from core.engine import EntityMigration


class FluteMigration(EntityMigration):
    name = "FluteMaster"
    target_table = "FluteMaster"
    target_identity = "FluteID"     # NOT an identity — generated below
    name_field_source = "Flute_Name"

    COLUMN_MAP = {
        "Flute_Name": "FluteName",
        "Take_Up_Factor": "TakeupFactor",
        "Caliper": "Caliper",
    }

    def __init__(self, company_id: int = 2, user_id: int = 1, fyear: str = ""):
        self.company_id = company_id
        self.user_id = user_id
        self.fyear = fyear
        self._next_id = 1
        self._existing: set = set()

    def read_source(self):
        mx = db.query_web("SELECT ISNULL(MAX(FluteID),0) AS mx FROM FluteMaster")
        self._next_id = int(mx[0]["mx"]) + 1
        ex = db.query_web(
            "SELECT FluteName FROM FluteMaster WHERE ISNULL(IsDeleted,0)=0")
        self._existing = {(r["FluteName"] or "").strip().lower() for r in ex}
        cols = ", ".join(f"[{c}]" for c in self.COLUMN_MAP)
        return db.query_desktop(
            f"SELECT {cols} FROM Flute_Master WHERE ISNULL(Flute_Name,'')<>''")

    def source_key(self, row):
        return (row.get("Flute_Name") or "?").strip()

    def already_migrated(self, row):
        return self.source_key(row).strip().lower() in self._existing

    def resolve_refs(self, row):
        return {}

    def build_parent(self, row, refs):
        # FluteID is a plain (non-identity) PK — generate sequentially.
        fid = self._next_id
        self._next_id += 1
        cols = ["FluteID"]
        vals = [fid]
        # QA: TakeupFactor and Caliper rounded to 2 decimals.
        round2 = {"TakeupFactor", "Caliper"}
        for s, t in self.COLUMN_MAP.items():
            v = row.get(s)
            if t in round2 and v is not None and str(v).strip() != "":
                try:
                    v = round(float(v), 2)
                except (TypeError, ValueError):
                    pass
            cols.append(t); vals.append(v)
        cols += ["CompanyID", "UserID", "FYear"]
        vals += [self.company_id, self.user_id, self.fyear]
        # Stamp the flags/dates the ERP expects, but only those that exist on
        # this web schema (defensive: keeps the row shaped like a native flute).
        from core.mapping import _has_column
        import datetime as _dt
        now = _dt.datetime.now()
        for c, v in (("IsDeleted", 0), ("IsBlocked", 0),
                     ("SaveDate", now), ("ModifyDate", now)):
            if _has_column("FluteMaster", c) and c not in cols:
                cols.append(c); vals.append(v)
        return cols, vals

    def build_children(self, row, refs, parent_id):
        return []

    def after_insert(self, row, refs, parent_id, cursor):
        self._existing.add(self.source_key(row).strip().lower())
