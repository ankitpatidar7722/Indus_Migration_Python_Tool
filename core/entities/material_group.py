"""
Material Group Master  ->  ItemSubGroupMaster.

The desktop keeps a single self-referencing `Material_Group_Master` hierarchy
(INKS -> Convensional Ink, FOIL ROLL -> Silver Foils, PLATES -> CTP Plates, ...).
The web ERP splits items into ItemGroupMaster (top groups: PAPER/REEL/INK/...)
plus a finer `ItemSubGroupMaster`. Items reference a sub-group by its
`ItemSubGroupID` (a non-identity logical id), e.g. Printing Plates=27.

Migrating the whole desktop group tree into ItemSubGroupMaster means every
material can then be placed in its proper sub-group (Chemicals, Spare parts,
Printing Plates, UV Inks, ...) instead of all landing flat in "Other Material".
Material migration resolves each material's desktop group -> the web
ItemSubGroupID created here (via core.mapping.resolve_subgroup_id, by name).

Rules:
  * Dedup by NAME: a desktop group whose (normalised) name already exists as a
    web sub-group is REUSED, not duplicated.
  * New groups get a fresh ItemSubGroupID = MAX(ItemSubGroupID)+1.
  * Hierarchy: UnderSubGroupID points at the parent desktop group's migrated web
    ItemSubGroupID when known (else 1 = the root 'Primary' sub-group).
"""

from __future__ import annotations

import datetime as _dt

from core import db
from core.engine import EntityMigration
from core.mapping import _norm_name, strip_quotes


class MaterialGroupMigration(EntityMigration):
    name = "MaterialGroupMaster"
    target_table = "ItemSubGroupMaster"
    target_identity = "ItemSubGroupUniqueID"      # identity PK (engine returns it)
    name_field_source = "Material_Group_Name"

    def __init__(self, company_id: int = 2, user_id: int = 1, fyear: str = ""):
        self.company_id = company_id
        self.user_id = user_id
        self.fyear = fyear
        self._existing_names: dict[str, int] = {}   # norm(name) -> ItemSubGroupID
        self._next_id = 1                           # running ItemSubGroupID
        # desktop Material_Group_ID -> its web ItemSubGroupID (filled as we go),
        # so children can resolve their parent for UnderSubGroupID.
        self._desktop_to_web: dict = {}

    # ---- setup -------------------------------------------------------------
    def _load(self):
        rows = db.query_web(
            "SELECT ItemSubGroupID, ItemSubGroupName FROM ItemSubGroupMaster "
            "WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0 "
            "AND ISNULL(ItemSubGroupName,'')<>''", [self.company_id])
        for r in rows:
            self._existing_names[_norm_name(r["ItemSubGroupName"])] = r["ItemSubGroupID"]
        mx = db.query_web(
            "SELECT ISNULL(MAX(ItemSubGroupID),0) m FROM ItemSubGroupMaster "
            "WHERE CompanyID=?", [self.company_id])
        self._next_id = int(mx[0]["m"]) + 1

    # ---- engine hooks ------------------------------------------------------
    def read_source(self):
        self._load()
        # Order parents before children (by Group_Level) so a child's parent has
        # already been migrated when we resolve UnderSubGroupID.
        return db.query_desktop(
            "SELECT Material_Group_ID, ISNULL(Material_Group_Name,'') AS nm, "
            "ISNULL(Display_Name,'') AS dn, ISNULL(Under_Group_ID,0) AS parent, "
            "ISNULL(Group_Level,0) AS lvl "
            "FROM Material_Group_Master "
            "WHERE ISNULL(Material_Group_Name,'') <> '' "
            "ORDER BY Group_Level, Material_Group_ID")

    def source_key(self, row):
        return strip_quotes((row.get("nm") or "?").strip())

    def already_migrated(self, row):
        """Reuse (skip) a group whose name already exists as a web sub-group, but
        record its web id so children can still link to it as a parent."""
        key = _norm_name(row.get("nm"))
        if key in self._existing_names:
            self._desktop_to_web[row.get("Material_Group_ID")] = self._existing_names[key]
            return True
        return False

    def resolve_refs(self, row):
        return {}

    def build_parent(self, row, refs):
        name = strip_quotes((row.get("nm") or "").strip())
        display = strip_quotes((row.get("dn") or name).strip()) or name
        sub_id = self._next_id
        self._next_id += 1
        # parent sub-group: the desktop parent's migrated web id, else root (1).
        parent_web = self._desktop_to_web.get(row.get("parent"), 1)
        level = row.get("lvl") or 1
        now = _dt.datetime.now()
        cols = ["ItemSubGroupID", "ItemSubGroupName", "ItemSubGroupDisplayName",
                "UnderSubGroupID", "ItemSubGroupLevel", "CompanyID", "UserID",
                "FYear", "CreatedBy", "CreatedDate", "ModifiedBy", "ModifiedDate",
                "IsDeleted", "IsBlocked", "IsDeletedTransaction", "QCItemSubGroupID"]
        vals = [sub_id, name, display, parent_web, level, self.company_id,
                self.user_id, self.fyear, self.user_id, now, self.user_id, now,
                0, 0, 0, 0]
        # remember this group's web id so its own children resolve their parent.
        self._desktop_to_web[row.get("Material_Group_ID")] = sub_id
        # also record the name so a later same-name desktop group reuses it.
        self._existing_names[_norm_name(name)] = sub_id
        return cols, vals

    def build_children(self, row, refs, parent_id):
        return []
