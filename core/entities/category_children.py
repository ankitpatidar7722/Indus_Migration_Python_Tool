"""
Category child settings — migrate a category with ALL its details:

  * Content Allocation : Catagory_Wise_Default_Orientations -> CategoryContentAllocationMaster
  * Process Allocation : Catagory_Wise_Default_Operations   -> CategoryWiseProcessAllocation
  * FG QC Sampling     : Finish_Goods_QC_Sampling_Plans     -> FinishGoodsQCSamplingPlan
  * FG QC Parameter    : Finish_Goods_QC_Parameter          -> FinishGoodsQCParameterSetting
  (COA is in coa_parameters.py)

All resolve CategoryID from the migrated CategoryMaster (RefCategoryID). The two
allocation tables also resolve:
  * ContentID via the orientation-name resolver (normalized full-name + aliases;
    unmatched -> 0, kept).
  * ProcessID (Process Allocation only): positive Operation_ID via ProcessMaster.
    RefProcessID; non-positive (system pseudo-ops -1,-5,0,...) pass through as-is.

These are CHILD entities of Category — they migrate automatically with it.
"""

from __future__ import annotations

from core import db
from core.mapping import MappedEntity, RefMap, resolve_content_id


class _CategoryChild(MappedEntity):
    """Base: resolves CategoryID (required) from CategoryMaster.RefCategoryID."""
    category_source_col = "Catagory_ID"

    def __init__(self, **kw):
        super().__init__(**kw)
        self._category = RefMap("CategoryMaster", "RefCategoryID", "CategoryID",
                                company_id=self.company_id)

    def _category_id(self, row, required=True):
        return self._category.resolve(row.get(self.category_source_col),
                                      required=required, label="CategoryID")


class ContentAllocationMigration(_CategoryChild):
    name = "CategoryContentAllocationMaster"
    target_table = "CategoryContentAllocationMaster"
    target_identity = "ProcessContentAllocationID"
    source_table = "Catagory_Wise_Default_Orientations"
    name_field_source = "Orientation_Name"
    column_map = {}                 # all columns built in build_parent
    extra_source_cols = ["Catagory_ID", "Orientation_Name"]

    def resolve_refs(self, row):
        return {"CategoryID": self._category_id(row, required=True)}

    def build_parent(self, row, refs):
        content_id = resolve_content_id(row.get("Orientation_Name"))
        cols = ["CategoryID", "ProcessID", "ContentID", "IsDefaultContent"]
        vals = [refs["CategoryID"], 0, content_id, 0]
        for c, v in self.context_columns().items():
            if c not in cols:
                cols.append(c); vals.append(v)
        return cols, vals

    def already_migrated(self, row):
        cat = self._category_id(row, required=False)
        if cat is None:
            return False
        cid = resolve_content_id(row.get("Orientation_Name"))
        rows = db.query_web(
            "SELECT 1 c FROM CategoryContentAllocationMaster "
            "WHERE CategoryID=? AND ContentID=? AND ISNULL(IsDeletedTransaction,0)=0",
            [cat, cid])
        return bool(rows)


class ProcessAllocationMigration(_CategoryChild):
    name = "CategoryWiseProcessAllocation"
    target_table = "CategoryWiseProcessAllocation"
    target_identity = "ID"
    source_table = "Catagory_Wise_Default_Operations"
    name_field_source = "Orientation_Name"
    column_map = {}
    extra_source_cols = ["Catagory_ID", "Operation_ID", "Orientation_Name"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self._process = RefMap("ProcessMaster", "RefProcessID", "ProcessID",
                               company_id=self.company_id)

    def resolve_refs(self, row):
        # Operation_ID -> ProcessID is ALWAYS resolved via ProcessMaster.RefProcessID,
        # regardless of sign: negative/zero Operation_IDs (e.g. -53 "Printing + Aqua",
        # -1 "Cutting") are real operations migrated to ProcessMaster, which stores
        # RefProcessID=that value and generates a new positive ProcessID. We must use
        # that new ProcessID, never the raw desktop id. Required — a process allocation
        # pointing at a non-migrated process would be an orphan.
        return {"CategoryID": self._category_id(row, required=True),
                "ProcessID": self._process.resolve(row.get("Operation_ID"),
                                                   required=True, label="ProcessID")}

    def build_parent(self, row, refs):
        content_id = resolve_content_id(row.get("Orientation_Name"))
        cols = ["CategoryID", "ContentID", "ProcessID"]
        vals = [refs["CategoryID"], content_id, refs["ProcessID"]]
        for c, v in self.context_columns().items():
            if c not in cols:
                cols.append(c); vals.append(v)
        return cols, vals

    def already_migrated(self, row):
        cat = self._category_id(row, required=False)
        pid = self._process.resolve(row.get("Operation_ID"), required=False)
        if cat is None or pid is None:
            return False
        cid = resolve_content_id(row.get("Orientation_Name"))
        rows = db.query_web(
            "SELECT 1 c FROM CategoryWiseProcessAllocation "
            "WHERE CategoryID=? AND ProcessID=? AND ISNULL(ContentID,0)=? "
            "AND ISNULL(IsDeletedTransaction,0)=0", [cat, pid, cid])
        return bool(rows)


class FGQCSamplingMigration(_CategoryChild):
    name = "FinishGoodsQCSamplingPlan"
    target_table = "FinishGoodsQCSamplingPlan"
    target_identity = "FGQCSamplingPlanID"
    source_table = "Finish_Goods_QC_Sampling_Plans"
    category_source_col = "Category_ID"          # this table uses 'Category_ID'
    name_field_source = "Sampling_Method_Type"
    column_map = {
        "Lot_Range_From": "LotRangeFrom",
        "Lot_Range_To": "LotRangeTo",
        "Sample_Size": "SampleSize",
        "Critical_Acceptance_Value": "CriticalAcceptanceValue",
        "Major_Acceptance_Value": "MajorAcceptanceValue",
        "Minor_Acceptance_Value": "MinorAcceptanceValue",
        "Total_Acceptance_Value": "TotalAcceptanceValue",
        "Sampling_Method_Type": "SamplingMethodType",
    }
    extra_source_cols = ["Category_ID"]

    def resolve_refs(self, row):
        return {"CategoryID": self._category_id(row, required=True)}


class FGQCParameterMigration(_CategoryChild):
    name = "FinishGoodsQCParameterSetting"
    target_table = "FinishGoodsQCParameterSetting"
    target_identity = "FGQCParameterSettingID"
    source_table = "Finish_Goods_QC_Parameter"
    category_source_col = "Category_ID"
    name_field_source = "Characterstics"
    column_map = {
        "Characterstics": "Characterstics",
        "Master_Field_Type": "MasterFieldType",
        "Method_Of_Inspection": "MethodOfInspection",
        "Measurement_Unit": "UOM",
        "Field_Type": "FieldType",
        "Critical_Criteria": "CriticalCriteria",
        "Major_Criteria": "MajorCriteria",
        "Minor_Criteria": "MinorCriteria",
        "Specification": "StandardValue",
    }
    extra_source_cols = ["Category_ID"]

    def resolve_refs(self, row):
        return {"CategoryID": self._category_id(row, required=True)}
