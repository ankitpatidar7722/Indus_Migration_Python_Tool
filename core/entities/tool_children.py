"""
Tool child settings.

  * Tool QC Parameter : Tool_Group_QC_Parameter -> ToolQCParameterSetting

These QC parameters are defined per tool GROUP. Desktop Tool_Group_ID passes
through to web ToolGroupID (Tool migration uses the same group ids — Under_Group_ID
-> ToolGroupID). This is a CHILD entity of Tool — migrates automatically with it.
(The source table is empty in some DBs; the mapping works wherever rows exist.)
"""

from __future__ import annotations

from core.mapping import MappedEntity


class ToolQCParameterMigration(MappedEntity):
    name = "ToolQCParameterSetting"
    target_table = "ToolQCParameterSetting"
    target_identity = "ToolQCID"
    source_table = "Tool_Group_QC_Parameter"
    name_field_source = "Characterstics"
    column_map = {
        "Tool_Group_ID": "ToolGroupID",            # passthrough (same group ids)
        "Method_Of_Inspection": "MethodOfInspection",
        "Characterstics": "Characteristics",
        "Measurement_Unit": "UOM",
        "Field_Type": "FieldType",
        "Standard_Value": "StandardValue",
        "Acceptance_Criteria": "AcceptanceCriteria",
        "Default_Value": "AcceptanceStatus",
        "Sample_Size": "SampleSize",
        "Master_Field_Type": "MasterFieldType",
    }
