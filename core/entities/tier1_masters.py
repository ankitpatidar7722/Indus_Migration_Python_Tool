"""
Tier 1 — independent masters (no dependency on other migrated entities).

Each is a small MappedEntity declaration. Column maps are grounded in the live
schemas of IndusDesktop (source) and IndusWeb (target). The original desktop id
is preserved in the target's Ref<thing>ID column so later tiers can resolve FKs.
"""

from __future__ import annotations

from core.mapping import MappedEntity


class CategoryMigration(MappedEntity):
    name = "CategoryMaster"
    target_table = "CategoryMaster"
    target_identity = "CategoryID"
    source_table = "Catagory_Master"
    name_field_source = "Catagory_Name"
    name_field_target = "CategoryName"
    # If a category already exists by name, stamp RefCategoryID on it so its
    # children (content/process alloc, FG QC, COA) can resolve it.
    backfill_ref = ("RefCategoryID", "Catagory_ID", "CategoryName")
    column_map = {
        "Catagory_ID": "RefCategoryID",
        "Catagory_Name": "CategoryName",
        "Orientation": "Orientation",
        "Is_Blocked": "IsBlocked",
        "Tax": "Tax",
        "Tariff_No": "TariffNo",
        "Notification_Sr_No": "NotificationSrNo",
        "HSN_Code": "HSNCode",
        "GST": "GST",
        "Minimum_Around_Gap": "MinimumAroundGap",
        "Maximum_Around_Gap": "MaximumAroundGap",
        "Default_Around_Gap": "DefaultAroundGap",
        "Minimum_Across_Gap": "MinimumAcrossGap",
        "Maximum_Across_Gap": "MaximumAcrossGap",
        "Default_Across_Gap": "DefaultAcrossGap",
        "Minimum_Plate_Bearer": "MinimumPlateBearer",
        "Maximum_Plate_Bearer": "MaximumPlateBearer",
        "Default_Bearer": "DefaultPlateBearer",      # QA: DefaultPlateBearer = Default_Bearer
        "Minimum_Side_Strip": "MinimumSideStrip",
        "Maximum_Side_Strip": "MaximumSideStrip",
        "Default_Side_Strip": "DefaultSideStrip",
        "Printing_Margin_T": "DefaultPrintingMarginTop",
        "Printing_Margin_B": "DefaultPrintingMarginBottom",
        "Printing_Margin_L": "DefaultPrintingMarginLeft",
        "Printing_Margin_R": "DefaultPrintingMarginRight",
        "Strippping_Margin_T": "DefaultStrippingMarginTop",
        "Strippping_Margin_B": "DefaultStrippingMarginBottom",
        "Strippping_Margin_L": "DefaultStrippingMarginLeft",
        "Strippping_Margin_R": "DefaultStrippingMarginRight",
    }

    def __init__(self, **kw):
        super().__init__(**kw)
        self._segment_id: int | None = None

    def _default_segment(self) -> int:
        """The web 'Division' shown on the category screen is SegmentID; native
        categories carry one (=1 here). Default to the company's first segment so
        the Division never shows empty (QA)."""
        if self._segment_id is None:
            from core import db
            r = db.query_web(
                "SELECT MIN(SegmentID) AS s FROM SegmentMaster "
                "WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0",
                [self.company_id])
            self._segment_id = (r[0]["s"] if r and r[0]["s"] is not None else 1)
        return self._segment_id

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        # QA: Division (SegmentID) must not be empty.
        seg = self._default_segment()
        if "SegmentID" in cols:
            if vals[cols.index("SegmentID")] in (None, "", 0):
                vals[cols.index("SegmentID")] = seg
        else:
            cols.append("SegmentID"); vals.append(seg)
        return cols, vals


class ProductHSNMigration(MappedEntity):
    name = "ProductHSNMaster"
    target_table = "ProductHSNMaster"
    target_identity = "ProductHSNID"
    source_table = "Product_Group_Master"
    name_field_source = "Product_Group_Name"
    name_field_target = "ProductHSNName"
    column_map = {
        "Product_Group_ID": "RefProductHSNID",
        "Product_Group_Name": "ProductHSNName",
        "HSN_Code": "HSNCode",
        "Group_Level": "GroupLevel",
        "Display_Name": "DisplayName",
        "Tariff_No": "TariffNo",
        "Product_Catagory": "ProductCategory",
        "GST_Tax_Percentage": "GSTTaxPercentage",
        "CGST_Tax_Percentage": "CGSTTaxPercentage",
        "SGST_Tax_Percentage": "SGSTTaxPercentage",
        "IGST_Tax_Percentage": "IGSTTaxPercentage",
        "Tally_Group_Name": "TallyGroupName",
        "Tally_GUID": "TallyGUID",
    }
    extra_source_cols = ["Product_Catagory"]

    # HSN name keyword -> web ItemGroupID (checked first, most specific).
    _NAME_TO_GROUP = [
        ("lamination", 5), ("ink", 3), ("varnish", 4), ("coating", 4),
        ("foil", 6), ("reel", 2), ("roll", 13), ("paper", 14),
    ]
    # Desktop Product_Catagory -> web ItemGroupID (fallback when name doesn't match).
    _CATEGORY_TO_GROUP = {"paper": 14, "reel": 2, "roll": 13, "material": 8}

    # QA: web ProductCategory must be exactly one of
    # (Raw Material, Finish Goods, Spare Parts, Service, Tool). Map the desktop
    # Product_Catagory values onto that set; paper/reel/roll/material are all
    # raw material; anything unrecognised defaults to Raw Material.
    _PRODUCT_CATEGORY_MAP = {
        "paper": "Raw Material", "reel": "Raw Material", "roll": "Raw Material",
        "material": "Raw Material", "raw material": "Raw Material",
        "spare": "Spare Parts", "spare parts": "Spare Parts",
        "finish goods": "Finish Goods", "finished goods": "Finish Goods",
        "die/block": "Tool", "die / block": "Tool", "tool": "Tool",
        "maintenance service": "Service", "service": "Service",
    }

    def _product_category(self, row) -> str:
        """Normalise the desktop Product_Catagory onto the allowed web set."""
        cat = (row.get("Product_Catagory") or "").strip().lower()
        # paper/reel/roll item names are raw material regardless of the desktop
        # category text (QA: "paper reel roll ... product category Raw Material").
        name = (row.get("Product_Group_Name") or "").lower()
        if any(k in name for k in ("paper", "reel", "roll")):
            return "Raw Material"
        return self._PRODUCT_CATEGORY_MAP.get(cat, "Raw Material")

    def _item_group_id(self, row) -> int:
        """Derive ItemGroupID so an HSN links to the right item group (e.g. a
        paper HSN -> 14). By NAME first, then by Product_Catagory; non-raw-material
        HSNs (Finish Goods / Spare / Die-Block / Maintenance) -> 0."""
        name = (row.get("Product_Group_Name") or "").lower()
        for key, gid in self._NAME_TO_GROUP:
            if key in name:
                return gid
        cat = (row.get("Product_Catagory") or "").strip().lower()
        return self._CATEGORY_TO_GROUP.get(cat, 0)

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        gid = self._item_group_id(row)
        if "ItemGroupID" in cols:
            vals[cols.index("ItemGroupID")] = gid
        else:
            cols.append("ItemGroupID"); vals.append(gid)
        # Normalise ProductCategory onto the allowed web set (QA).
        cat = self._product_category(row)
        if "ProductCategory" in cols:
            vals[cols.index("ProductCategory")] = cat
        else:
            cols.append("ProductCategory"); vals.append(cat)
        return cols, vals


class DepartmentMigration(MappedEntity):
    name = "DepartmentMaster"
    target_table = "DepartmentMaster"
    target_identity = "ID"           # identity is ID; DepartmentID holds the desktop id
    # DepartmentID here is this department's OWN id (not a reference), so the
    # global 200/100 reference-normalisation must not apply — only its own
    # 0->100 default rule (in build_parent below) does.
    normalize_department_id = False
    source_table = "Department_Master"
    name_field_source = "Department_Name"
    name_field_target = "DepartmentName"
    column_map = {
        "Department_ID": "DepartmentID",
        "Department_Name": "DepartmentName",
        "Press": "Press",
        "Is_Blocked": "IsBlocked",
        "Is_Show": "IsShow",
        "Branch_ID": "BranchID",
        "Sequence_No": "SequenceNo",
        "Department_Picture": "DepartmentPicture",
    }

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        # Rule: a desktop Department_ID of 0 becomes the default department 100.
        if "DepartmentID" in cols:
            i = cols.index("DepartmentID")
            try:
                if int(vals[i] or 0) == 0:
                    vals[i] = 100
            except (TypeError, ValueError):
                vals[i] = 100
        # QA: the web Press field spells pre-press as 'Pree' (desktop uses 'Pre').
        # Post/Stock are unchanged.
        if "Press" in cols:
            pi = cols.index("Press")
            if (vals[pi] or "").strip().lower() == "pre":
                vals[pi] = "Pree"
        return cols, vals


class UnitMigration(MappedEntity):
    name = "UnitMaster"
    target_table = "UnitMaster"
    target_identity = "UnitID"
    source_table = "Unit_Master"
    name_field_source = "Unit_Name"
    name_field_target = "UnitName"
    column_map = {
        "Unit_Symbol": "UnitSymbol",
        "Unit_Name": "UnitName",
        "Type": "Type",
        "Conversion_Value": "ConversionValue",
        "Decimal_Place": "DecimalPlace",
        "Under_Unit": "UnderUnit",
        "Branch_ID": "BranchID",
    }


class ProductionUnitMigration(MappedEntity):
    name = "ProductionUnitMaster"
    target_table = "ProductionUnitMaster"
    target_identity = "ProductionUnitID"
    source_table = "Production_Unit_Master"
    name_field_source = "Production_Unit_Name"
    name_field_target = "ProductionUnitName"
    column_map = {
        "Production_Unit_Name": "ProductionUnitName",
        "Address": "Address",
        "City": "City",
        "State": "State",
        "Pin_Zip_Code": "Pincode",
        "Country": "Country",
        "GSTIN": "GSTNo",
        "PAN": "PAN",
        "Branch_ID": "BranchID",
    }
    # Production_Unit_ID -> RefProductionUnitCode? target has RefProductionUnitName,
    # not a numeric ref id. Keep the desktop name as the ref handle for later FK
    # resolution by name where needed.
    constant_columns = {}

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        # Preserve desktop name + id as the resolvable handle.
        if "RefProductionUnitName" not in cols:
            cols.append("RefProductionUnitName")
            vals.append(row.get("Production_Unit_Name"))
        return cols, vals
