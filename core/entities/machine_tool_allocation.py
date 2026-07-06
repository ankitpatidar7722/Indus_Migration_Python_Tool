"""
Cylinder_Machine_Allocation  ->  MachineToolAllocationMaster.

ONE target row per desktop record (row count is preserved — records are NOT
merged). For each desktop Cylinder_Machine_Allocation row:

    Cylinder_ID  -> ToolMaster.RefToolId   => (ToolID, ToolGroupID)
    Machine_ID   -> MachineMaster.RefMachineID => MachineID

The only aggregation is the ToolAllocatedIDString column: every row that shares
the same (MachineID, ToolGroupID) carries the SAME comma-separated list of all
those ToolIDs, while still keeping its own ToolID.

    desktop                          MachineToolAllocationMaster
    MachineId ToolGroupId ToolId     MachineID ToolGroupID ToolID ToolAllocatedIDString
    10        5           101   ==>   10        5           101    101,102,103
    10        5           102         10        5           102    101,102,103
    10        5           103         10        5           103    101,102,103

Depends on Tool AND Machine already being migrated: a source row whose cylinder
(tool) or machine isn't in the web DB yet is surfaced as an ISSUE row (FK
validation) rather than dropped silently.
"""

from __future__ import annotations

from core import db
from core.mapping import MappedEntity, RefMap, _norm_key


class MachineToolAllocationMigration(MappedEntity):
    name = "MachineToolAllocationMaster"
    target_table = "MachineToolAllocationMaster"
    target_identity = "MachineToolAllocationID"
    source_table = "Cylinder_Machine_Allocation"
    column_map = {}                       # custom build_parent

    def __init__(self, **kw):
        super().__init__(**kw)
        # Machine_ID -> web MachineID (same RefMap other entities use).
        self._machine = RefMap("MachineMaster", "RefMachineID", "MachineId",
                               company_id=self.company_id)
        self._tool_map: dict | None = None        # RefToolId -> (ToolID, ToolGroupID)
        self._existing: set | None = None          # {(MachineID, ToolGroupID, ToolID)}

    # ---- lookups ----------------------------------------------------------
    def _load_tool_map(self) -> dict:
        """web ToolMaster: RefToolId -> (ToolID, ToolGroupID), company-scoped."""
        if self._tool_map is None:
            self._tool_map = {}
            for r in db.query_web(
                    "SELECT RefToolId, ToolID, ToolGroupID FROM ToolMaster "
                    "WHERE CompanyID=? AND RefToolId IS NOT NULL "
                    "AND ISNULL(IsDeletedTransaction,0)=0", [self.company_id]):
                self._tool_map[_norm_key(r["RefToolId"])] = (r["ToolID"],
                                                             r["ToolGroupID"])
        return self._tool_map

    def _load_existing(self) -> set:
        """(MachineID, ToolGroupID, ToolID) triples already in the target, so a
        re-run skips exactly the rows it already inserted (idempotency)."""
        if self._existing is None:
            self._existing = set()
            for r in db.query_web(
                    "SELECT DISTINCT MachineID, ToolGroupID, ToolID "
                    "FROM MachineToolAllocationMaster "
                    "WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0",
                    [self.company_id]):
                self._existing.add((_norm_key(r["MachineID"]),
                                    _norm_key(r["ToolGroupID"]),
                                    _norm_key(r["ToolID"])))
        return self._existing

    # ---- read (one row per record; shared ToolAllocatedIDString) ----------
    def read_source(self) -> list[dict]:
        tool_map = self._load_tool_map()
        src = db.query_desktop(
            "SELECT Cylinder_ID, Machine_ID FROM Cylinder_Machine_Allocation "
            "WHERE Company_ID=? ORDER BY Machine_ID, Cylinder_ID",
            [self.company_id])

        resolved: list = []       # one entry per resolvable desktop record
        group_ids: dict = {}      # (MachineID, ToolGroupID) -> [ToolID, ...] distinct
        unresolved: list = []
        for r in src:
            cyl, mch = r.get("Cylinder_ID"), r.get("Machine_ID")
            tool = tool_map.get(_norm_key(cyl))
            machine_id = self._machine.resolve(mch, required=False)
            if tool is None or machine_id is None:
                if tool is None and machine_id is None:
                    reason = (f"Tool (Cylinder_ID={cyl}) and Machine "
                              f"(Machine_ID={mch}) not migrated")
                elif tool is None:
                    reason = f"Tool not migrated for Cylinder_ID={cyl} (migrate Tool first)"
                else:
                    reason = f"Machine not migrated for Machine_ID={mch} (migrate Machine first)"
                unresolved.append({"_unresolved": True, "Cylinder_ID": cyl,
                                   "Machine_ID": mch, "_reason": reason})
                continue
            tool_id, tgid = tool
            key = (_norm_key(machine_id), _norm_key(tgid))
            lst = group_ids.setdefault(key, [])
            if tool_id not in lst:               # distinct, first-seen (source) order
                lst.append(tool_id)
            resolved.append({"MachineID": machine_id, "ToolGroupID": tgid,
                             "ToolID": tool_id, "_key": key})

        rows: list = []
        for d in resolved:
            ids = group_ids[d["_key"]]
            rows.append({
                "MachineID": d["MachineID"],
                "ToolGroupID": d["ToolGroupID"],
                "ToolID": d["ToolID"],
                "ToolAllocatedIDString": ",".join(str(t) for t in ids),
            })
        rows.extend(unresolved)                  # show issues in the grid too
        return rows

    # ---- per-row hooks ----------------------------------------------------
    def source_key(self, row: dict) -> str:
        if row.get("_unresolved"):
            return f"Cylinder {row.get('Cylinder_ID')} / Machine {row.get('Machine_ID')}"
        return (f"Machine {row.get('MachineID')} / ToolGroup {row.get('ToolGroupID')}"
                f" / Tool {row.get('ToolID')}")

    def already_migrated(self, row: dict) -> bool:
        if row.get("_unresolved"):
            return False
        triple = (_norm_key(row.get("MachineID")), _norm_key(row.get("ToolGroupID")),
                  _norm_key(row.get("ToolID")))
        return triple in self._load_existing()

    def resolve_refs(self, row: dict) -> dict:
        if row.get("_unresolved"):
            raise ValueError(row.get("_reason") or "tool/machine not migrated")
        return {}

    def build_parent(self, row: dict, refs: dict):
        cols = ["MachineID", "ToolGroupID", "ToolID", "ToolAllocatedIDString"]
        vals = [row.get("MachineID"), row.get("ToolGroupID"), row.get("ToolID"),
                row.get("ToolAllocatedIDString")]
        for c, v in self.context_columns().items():
            if c not in cols:
                cols.append(c); vals.append(v)
        return cols, vals
