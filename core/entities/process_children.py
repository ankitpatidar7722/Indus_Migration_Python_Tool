"""
Process (Operation) child settings — migrate a process with ALL its settings.

  * Line Clearance   : Department_Wise_Line_Clearance_Parameters   -> ProcessLineClearanceParameters
  * Inspection Param : Department_Wise_Process_Inspection_Parameters -> ProcessInspectionParameterMaster
  * Tool Group Alloc : Operation_Tool_Group_Allocation             -> ProcessToolGroupAllocationMaster
  (Process Slabs / Process Allocated Machine / Client Process Cost are elsewhere.)

All link to a Process via Operation_ID, ALWAYS resolved through
ProcessMaster.RefProcessID regardless of sign — negative/zero Operation_IDs
(e.g. -53 "Printing + Aqua", -1 "Cutting") are real operations migrated to
ProcessMaster, which generates a new positive ProcessID; we use that. Required,
so a setting pointing at a non-migrated process is flagged (not orphaned).

These are CHILD entities of Process — they migrate automatically with it.
"""

from __future__ import annotations

from core.mapping import MappedEntity, RefMap


class _ProcessChild(MappedEntity):
    """Base: resolves ProcessID (required) from ProcessMaster.RefProcessID = Operation_ID."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._process = RefMap("ProcessMaster", "RefProcessID", "ProcessID",
                               company_id=self.company_id)

    def resolve_refs(self, row):
        return {"ProcessID": self._process.resolve(row.get("Operation_ID"),
                                                   required=True, label="ProcessID")}


class LineClearanceMigration(_ProcessChild):
    name = "ProcessLineClearanceParameters"
    target_table = "ProcessLineClearanceParameters"
    target_identity = "TransactionID"
    source_table = "Department_Wise_Line_Clearance_Parameters"
    name_field_source = "Parameter_Name"
    column_map = {
        "Parameter_Name": "ParameterName",
        "Standard_Value": "StandardValue",
        "Field_Type": "FieldType",
        "Default_Value": "DefaultValue",
    }
    extra_source_cols = ["Operation_ID"]



class InspectionParameterMigration(_ProcessChild):
    # QA: migrate into ProcessInspectionParameters (NOT ...Master). That table's
    # columns: ProcessID, ParameterName, StandardValue, FieldType, DefaultValue
    # (+ TestMethod/Observation, which the desktop doesn't have).
    name = "ProcessInspectionParameter"
    target_table = "ProcessInspectionParameters"
    target_identity = "TransactionID"
    source_table = "Department_Wise_Process_Inspection_Parameters"
    name_field_source = "Parameter_Name"
    column_map = {
        "Parameter_Name": "ParameterName",
        "Standard_Value": "StandardValue",
        "Field_Type": "FieldType",
        "Default_Value": "DefaultValue",
    }
    extra_source_cols = ["Operation_ID"]



class ToolGroupAllocationMigration(_ProcessChild):
    """Tool-group allocations are derived from the Process (Operation_Master)
    itself — ONE row per operation with Tool_Required=1. ProcessID resolves from
    the migrated ProcessMaster (RefProcessID=Operation_ID); ToolGroupID is matched
    from the desktop Tool_Category via ToolGroupMaster (the same value stamped onto
    ProcessMaster.ToolGroupID). Processes with Tool_Required=0 create no row."""
    name = "ProcessToolGroupAllocationMaster"
    target_table = "ProcessToolGroupAllocationMaster"
    target_identity = "ProcessToolGroupAllocationID"
    source_table = "Operation_Master"
    source_where = "ISNULL(Tool_Required,0)=1"       # only tool-requiring processes
    name_field_source = "Operation_Name"
    column_map = {}                                   # ProcessID/ToolGroupID via refs
    constant_columns = {"IsMandatory": 1}
    extra_source_cols = ["Operation_ID", "Tool_Category"]

    def resolve_refs(self, row):
        from core.entities.spare_tool import resolve_tool_group_id
        refs = {"ProcessID": self._process.resolve(
            row.get("Operation_ID"), required=True, label="ProcessID")}
        tgid = resolve_tool_group_id(self.company_id, row.get("Tool_Category"))
        if tgid is not None:
            refs["ToolGroupID"] = tgid
        return refs

