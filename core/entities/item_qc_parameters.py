"""
RM / Item QC Parameter Setting.

Four desktop sources all migrate into ONE web table `ItemQCParameterSetting`,
distinguished by ItemGroupID:

  * Paper_QC_Parameter           -> ItemQCParameterSetting (ItemGroupID 14)
  * Reel_QC_Parameter            -> ItemQCParameterSetting (ItemGroupID 2)
  * Roll_QC_Parameter            -> ItemQCParameterSetting (ItemGroupID 13)
  * Material_Group_QC_Parameter  -> ItemQCParameterSetting (ItemGroupID resolved
                                    by name from Material_Group_ID)

Column mapping follows the QA reference SQL (MigrationIssue.txt lines 280-302):
Characterstics->Characteristics, Method_Of_Inspection->MethodOfInspection,
Specification->MeasuringEquipment, Measurement_Unit->UOM, Field_Type->FieldType,
Standard_Value->StandardValue, Acceptance_Criteria->AcceptanceCriteria,
Default_Value->AcceptanceStatus, Sample_Size->SampleSize, <type>_Quality->Quality.

These are hidden CHILD entities of their item type (Paper/Reel/Roll/Material).
"""

from __future__ import annotations

from core.mapping import MappedEntity, resolve_subgroup_id

# Web ItemGroupID per item type.
ITEM_GROUP = {"Paper": 14, "Reel": 2, "Roll": 13}

# Common source->target map shared by all QC param tables.
_COMMON_MAP = {
    "Characterstics": "Characteristics",
    "Method_Of_Inspection": "MethodOfInspection",
    "Specification": "MeasuringEquipment",
    "Measurement_Unit": "UOM",
    "Field_Type": "FieldType",
    "Standard_Value": "StandardValue",
    "Acceptance_Criteria": "AcceptanceCriteria",
    "Default_Value": "AcceptanceStatus",
    "Sample_Size": "SampleSize",
    "Master_Field_Type": "MasterFieldType",
}


class _ItemTypeQC(MappedEntity):
    """Paper/Reel/Roll QC: fixed ItemGroupID + Quality from a per-type column."""
    target_table = "ItemQCParameterSetting"
    target_identity = "ItemQCID"
    name_field_source = "Characterstics"
    item_type = ""              # "Paper" / "Reel" / "Roll"
    quality_source_col = ""     # Paper_Quality / Reel_Quality / Roll_Quality
    column_map = dict(_COMMON_MAP)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.group_id = ITEM_GROUP[self.item_type]

    def clear_group_filter(self):
        # Clear only this item type's QC rows (Paper=14 / Reel=2 / Roll=13).
        return "ItemGroupID=?", [self.group_id]

    @property
    def extra_source_cols(self):
        return [self.quality_source_col]

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        for c, v in (("ItemGroupID", self.group_id),
                     ("Quality", row.get(self.quality_source_col)),
                     # QA: ItemSubGroupUniqueID defaults to 0 when there's no
                     # sub-group (Paper/Reel/Roll QC have none).
                     ("ItemSubGroupUniqueID", 0)):
            if c not in cols:
                cols.append(c); vals.append(v)
        return cols, vals


class PaperQCMigration(_ItemTypeQC):
    name = "PaperQCParameter"
    source_table = "Paper_QC_Parameter"
    item_type = "Paper"
    quality_source_col = "Paper_Quality"


class ReelQCMigration(_ItemTypeQC):
    name = "ReelQCParameter"
    source_table = "Reel_QC_Parameter"
    item_type = "Reel"
    quality_source_col = "Reel_Quality"


class RollQCMigration(_ItemTypeQC):
    name = "RollQCParameter"
    source_table = "Roll_QC_Parameter"
    item_type = "Roll"
    quality_source_col = "Roll_Quality"


class MaterialGroupQCMigration(MappedEntity):
    """Material_Group_QC_Parameter: ItemGroupID resolved by NAME from the
    desktop Material_Group_ID (via Material_Group_Master -> web group), like the
    machine item-sub-group allocation. Uses Parameter_Name where present."""
    name = "MaterialGroupQCParameter"
    target_table = "ItemQCParameterSetting"
    target_identity = "ItemQCID"
    source_table = "Material_Group_QC_Parameter"
    name_field_source = "Parameter_Name"
    column_map = dict(_COMMON_MAP)
    extra_source_cols = ["Material_Group_ID", "Parameter_Name"]

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        gid = resolve_subgroup_id(row.get("Material_Group_ID"))
        if "ItemSubGroupUniqueID" not in cols:
            cols.append("ItemSubGroupUniqueID"); vals.append(gid)
        # Characterstics may be empty; fall back to Parameter_Name for the QC name.
        if "Characteristics" in cols:
            i = cols.index("Characteristics")
            if not vals[i]:
                vals[i] = row.get("Parameter_Name")
        return cols, vals
