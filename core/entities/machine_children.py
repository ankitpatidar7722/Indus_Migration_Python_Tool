"""
Machine child settings.

  * Item Sub-Group Allocation : Machine_Material_Group_Allocation -> MachineItemSubGroupAllocationMaster
  (Machine Slab + Machine Online Coating Rates are already migrated elsewhere.)

Resolution:
  * MachineID      <- MachineMaster.RefMachineID = desktop Machine_ID
  * ItemSubGroupID <- resolved BY NAME: desktop Material_Group_ID ->
                      Material_Group_Master.Material_Group_Name ->
                      web ItemSubGroupMaster.ItemSubGroupName (ids don't pass through).
  * GroupAllocationIDs : CSV of all resolved sub-group ids for that machine,
                         repeated on every row of the machine's set (web convention).

This is a CHILD entity of Machine — migrates automatically with it. (The source
table is empty in some DBs; the mapping still works wherever rows exist.)
"""

from __future__ import annotations

from core import db
from core.mapping import MappedEntity, RefMap, resolve_subgroup_id


class MachineItemSubGroupAllocationMigration(MappedEntity):
    name = "MachineItemSubGroupAllocationMaster"
    target_table = "MachineItemSubGroupAllocationMaster"
    target_identity = "MachineSubGroupAllocationID"
    source_table = "Machine_Material_Group_Allocation"
    name_field_source = "Material_Group_ID"
    column_map = {}                     # built in build_parent
    extra_source_cols = ["Machine_ID", "Material_Group_ID"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self._machine = RefMap("MachineMaster", "RefMachineID", "MachineId",
                               company_id=self.company_id)
        self._machine_csv: dict = {}    # web MachineID -> CSV of its sub-group ids

    def read_source(self):
        rows = db.query_desktop(
            "SELECT Machine_ID, Material_Group_ID FROM Machine_Material_Group_Allocation")
        # Pre-build the GroupAllocationIDs CSV per (web) machine.
        per_machine: dict = {}
        for r in rows:
            mid = self._machine.resolve(r.get("Machine_ID"), required=False)
            sid = resolve_subgroup_id(r.get("Material_Group_ID"))
            if mid is not None and sid:
                per_machine.setdefault(mid, []).append(str(sid))
        self._machine_csv = {m: ",".join(dict.fromkeys(ids))
                             for m, ids in per_machine.items()}
        return rows

    def resolve_refs(self, row):
        mid = self._machine.resolve(row.get("Machine_ID"), required=True,
                                    label="MachineID")
        sid = resolve_subgroup_id(row.get("Material_Group_ID"))
        if not sid:
            raise ValueError(
                f"ItemSubGroupID: desktop group {row.get('Material_Group_ID')} "
                f"has no matching web ItemSubGroupMaster name")
        return {"MachineID": mid, "ItemSubGroupID": sid}

    def build_parent(self, row, refs):
        cols = ["MachineID", "ItemSubGroupID", "GroupAllocationIDs"]
        vals = [refs["MachineID"], refs["ItemSubGroupID"],
                self._machine_csv.get(refs["MachineID"], str(refs["ItemSubGroupID"]))]
        for c, v in self.context_columns().items():
            if c not in cols:
                cols.append(c); vals.append(v)
        return cols, vals

    def already_migrated(self, row):
        mid = self._machine.resolve(row.get("Machine_ID"), required=False)
        sid = resolve_subgroup_id(row.get("Material_Group_ID"))
        if mid is None or not sid:
            return False
        rows = db.query_web(
            "SELECT 1 c FROM MachineItemSubGroupAllocationMaster "
            "WHERE MachineID=? AND ItemSubGroupID=? AND ISNULL(IsDeletedTransaction,0)=0",
            [mid, sid])
        return bool(rows)
