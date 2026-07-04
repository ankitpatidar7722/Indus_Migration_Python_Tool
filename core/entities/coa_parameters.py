"""
Category-wise COA Parameter Setting.

Migrates desktop `Category_Wise_COA_Parameters` -> web
`CategoryWiseCOAParameterSetting`. COA (Certificate of Analysis) parameters are
defined per category, so this depends on CategoryMaster being migrated first
(CategoryID is resolved from CategoryMaster.RefCategoryID). LedgerID is optional.

Column map follows the team's reference SQL (MigrationIssue.txt lines 1181-1186).
"""

from __future__ import annotations

from core.mapping import MappedEntity, RefMap


class COAParameterMigration(MappedEntity):
    name = "CategoryWiseCOAParameterSetting"
    target_table = "CategoryWiseCOAParameterSetting"
    target_identity = "ParameterID"
    source_table = "Category_Wise_COA_Parameters"
    name_field_source = "TEST_Parameter_Name"
    name_field_target = ""          # no idempotent name key (TransID+Category is the key)

    column_map = {
        "TEST_Parameter_Name": "TestParameterName",
        "Specification_Field_Type": "Specification",
        "Specification_Field_Table": "SpecificationFieldDataFromTable",
        "Specification_Field_Default_Value": "SpecificationFieldValue",
        "Specification_Field_Unit": "SpecificationFieldUnit",
        "Observation_Field_Type": "ResultDataFieldType",
        "Observation_Field_Default_Value": "Defaults",
        "Trans_ID": "TransID",
        "Parameter_Type": "ParameterCategory",
    }
    extra_source_cols = ["Catagory_ID", "Ledger_ID"]

    def __init__(self, **kw):
        super().__init__(**kw)
        cid = self.company_id
        self._category = RefMap("CategoryMaster", "RefCategoryID", "CategoryID", company_id=cid)
        self._ledger = RefMap("LedgerMaster", "RefLedgerID", "LedgerID", company_id=cid)

    def resolve_refs(self, row):
        # CategoryID is required — COA is scoped to a migrated category.
        cat = self._category.resolve(row.get("Catagory_ID"), required=True,
                                     label="CategoryID")
        refs = {"CategoryID": cat}
        # LedgerID optional (often 0 in source).
        led = self._ledger.resolve(row.get("Ledger_ID"), required=False)
        if led:
            refs["LedgerID"] = led
        return refs

    def already_migrated(self, row):
        # Re-runs are skipped by (CategoryID, TransID, TestParameterName) presence.
        from core import db
        cat = self._category.resolve(row.get("Catagory_ID"), required=False)
        if cat is None:
            return False
        rows = db.query_web(
            "SELECT 1 c FROM CategoryWiseCOAParameterSetting "
            "WHERE CategoryID=? AND ISNULL(TransID,0)=? AND ISNULL(TestParameterName,'')=? "
            "AND ISNULL(IsDeletedTransaction,0)=0",
            [cat, int(row.get("Trans_ID") or 0), (row.get("TEST_Parameter_Name") or "").strip()])
        return bool(rows)

    def source_key(self, row):
        return (row.get("TEST_Parameter_Name") or "?").strip()
