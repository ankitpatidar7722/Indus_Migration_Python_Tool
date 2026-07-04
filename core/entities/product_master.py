"""
ProductMaster — the hierarchical entity (a product + its contents + the
content-level children). This is the most complex migration: one desktop
Product_Master row becomes a ProductMaster parent plus, per content, a
ProductMasterContents row and its Process / Corrugation / MaterialRequirement
children — all inside ONE transaction per product.

Hierarchy:
    Product_Master                 -> ProductMaster                       (parent)
      Product_Master_Contents      -> ProductMasterContents               (per content)
        Product_Master_Operations  -> ProductMasterProcess                (per content)
        Product_Master_Corrugation -> ProductMasterCorrugation            (per content)
        Product_Master_Machine_Material_Setting -> ProductMasterProcessMaterialRequirement

Foreign keys resolved from already-migrated masters (via their Ref* columns):
    Ledger_ID / Sales_Employee_ID / Job_Coordinator_ID -> LedgerMaster.RefLedgerID -> LedgerID
    Catagory_ID        -> CategoryMaster.RefCategoryID    -> CategoryID
    Product_Group_ID   -> ProductHSNMaster.RefProductHSNID-> ProductHSNID
    Machine_ID         -> MachineMaster.RefMachineID      -> MachineId
    Operation_ID       -> ProcessMaster.RefProcessID      -> ProcessID
    Paper_ID/Item_ID/Material_ID -> ItemMaster.RefItemID  -> ItemID

So LedgerMaster, CategoryMaster, ProductHSNMaster, ItemMaster, ProcessMaster and
MachineMaster must be migrated first. A product referencing an unmigrated master
is flagged in preview (required FK that can't resolve raises, row shown red).
"""

from __future__ import annotations

import datetime as _dt
import re as _re

from core import db, engine
from core.engine import EntityMigration
from core.mapping import RefMap, _has_column, resolve_content_domain_type


def _norm(s):
    return _re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Desktop columns that must NEVER be auto-copied as a raw value: identity/PK
# surrogates, columns resolved via FK RefMaps elsewhere, and context columns the
# migration stamps itself. Auto-mapping fills EVERYTHING ELSE that name-matches.
_AUTO_MAP_EXCLUDE = {
    # FK sources (resolved to web ids via RefMaps, not raw-copied)
    "machine_id", "operation_id", "material_id", "item_id", "paper_id",
    "ledger_id", "client_id", "catagory_id", "sales_employee_id",
    "job_coordinator_id", "product_group_id", "tool_id", "tool_group_id",
    # desktop link/identity ids (not meaningful in web, or set explicitly)
    "content_id", "product_content_id", "estimate_id",
    "product_master_id", "product_master_code",
    # context / audit (stamped by _context)
    "company_id", "user_id", "modify_date", "f_year",
    # columns the migration sets explicitly (counter / desktop-id backpointer)
    "max_product_master_code", "desktopproductmasterid", "is_hidden",
}


def _auto_column_map(desktop_table, web_table, explicit_map):
    """Return explicit_map PLUS every desktop column whose normalized name matches
    a web column (and isn't in _AUTO_MAP_EXCLUDE / already mapped). This fills all
    the fields the hand-written maps missed — the legacy tool copies them all."""
    dcols = [r["name"] for r in db.query_desktop(
        "SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?)", [desktop_table])]
    wby_norm = {_norm(r["name"]): r["name"] for r in db.query_web(
        "SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?)", [web_table])}
    merged = dict(explicit_map)
    used_src = set(explicit_map)
    used_tgt = set(explicit_map.values())
    for c in dcols:
        if _norm(c) in _AUTO_MAP_EXCLUDE or c in used_src:
            continue
        wt = wby_norm.get(_norm(c))
        if wt and wt not in used_tgt:
            merged[c] = wt
            used_src.add(c); used_tgt.add(wt)
    return merged


# --- parent column map: Product_Master -> ProductMaster ----------------------
PM_MAP = {
    "Product_Master_ID": "RefProductMasterID",
    "Product_Master_Code": "ProductMasterCode",
    "Job_Name": "JobName",
    "Product_Code": "ProductCode",
    "Order_Quantity": "OrderQuantity",
    "Job_Type": "JobType",
    "Job_Reference": "JobReference",
    "Job_Priority": "JobPriority",
    "Approval_No": "ApprovalNo",
    "Ref_Product_Master_Code": "RefProductMasterCode",
    "Final_Cost": "FinalCost",
    "Remark": "Remark",
    "Old_Product_Code": "OldProductCode",
    "Is_Blocked": "IsBlocked",
    "Is_Hidden": "IsHidden",
    "Branch_ID": "BranchID",
    "Location": "Location",
    "File_No": "FileNo",
    "Rate_Type": "RateType",
}

# --- contents map: Product_Master_Contents -> ProductMasterContents -----------
CONTENT_MAP = {
    "Content_Name": "PlanContName",
    "Orientation": "PlanContentType",
    "Quantity": "PlanContQty",
    "Machine_Name": "MachineName",
    "Machine_Colors": "MachineColors",
    "Paper_Size": "PaperSize",
    "Cut_L": "CutL",
    "Cut_W": "CutW",
    # QA: UpsL/UpsW are swapped — desktop Ups_W -> UpsL, desktop Ups_L -> UpsW.
    "Ups_W": "UpsL",
    "Ups_L": "UpsW",
    "Ups_Total": "TotalUps",
    "Paper_Wastage_In_Kg": "WastageKg",
    "Plate_Quantity": "PlateQty",
    "Plate_Rate": "PlateRate",
    "Plate_Amount": "PlateAmount",
    "Total_Paper_In_Kg": "TotalPaperWeightInKg",
    "Paper_Rate": "PaperRate",
    "Paper_Amount": "PaperAmount",
    "Printing_Rate": "PrintingRate",
    "Printing_Amount": "PrintingAmount",
    "Make_Ready_Rate": "MakeReadyRate",
    "Make_Ready_Amount": "MakeReadyAmount",
    "Final_Quantity": "FinalQuantity",
    "Total_Amount": "TotalAmount",
    "Printing_Sheet_Size": "CutSize",
    "Balance_Piece": "BalPiece",
    "Balance_Piece_Side": "BalSide",
    "Impression_To_Be_Charged": "ImpressionsToBeCharged",
    "Make_Readies": "TotalMakeReadies",
    "Main_Paper_Name": "MainPaperName",
    "Plan_Type": "PlanType",
    "Coating_Amount": "CoatingAmount",
    "Die_Cut_Size": "DieCutSize",
    "Paper_Quality": "PaperGroup",
    "Paper_Mill": "PaperMill",
    "Paper_Face_GSM": "PaperFaceGSM",
    "Paper_Release_GSM": "PaperReleaseGSM",
    "Paper_Adhesive_GSM": "PaperAdhesiveGSM",
    "Machine_Type": "MachineType",
    "Machine_Per_Hour_Cost": "MachinePerHourRate",
    # ---- explicit overrides from the mapping sheet (Sheet 1) ----
    "Waste_In_Percentage": "WastePerc",
    "Grain_Direction_Main": "GrainDirection",
    "Total_Colors_All": "TotalColors",
    "Printing_Style": "PrintingStyle",
    "Paper_Rate_Type": "PaperRateType",
    "Total_Cost": "GrantAmount",
    "Job_Type": "JobType",
    "Job_Reference": "JobReference",
    "Job_Priority": "JobPriority",
    "Plate_Type": "PlateType",
    "QC_Instruction": "SpecialInstructions",
    "Print_Cyl_ID": "CylinderToolID",
    "Print_Cyl_Code": "CylinderToolCode",
    "Cutting_Cylinder_Circumference": "CylinderCircumferenceMM",
    "Print_Cyl_Width": "CylinderWidth",
    "No_Of_Teeth": "CylinderNoOfTeeth",
    "Gap_In_Column": "AcrossGap",
    "Gap_In_Row": "AroundGap",
    "Wastage_Strip": "WastageStrip",
    "Required_Running_Meter": "RequiredRunningMeter",
    "Make_Ready_Wastage_Running_Meter": "MakeReadyWastageRunningMeter",
    "Avg_Break_Down_Wastage_Meter": "AvgBreakDownRunningMeter",
    "Wastage_Running_Meter": "WastageRunningMeter",
    "Scrap_Square_Meter": "ScrapSquareMeter",
    "Total_Running_Meter": "TotalRequiredRunningMeter",
    "Total_Square_Meter": "TotalRequiredSquareMeter",
    "Total_GSM": "PaperTotalGSM",
    "Actual_Required_Paper_In_KG": "RequiredPaperWeightKg",
    "Roll_Change_Wastage_Meter": "RollChangeWastageMeter",
    "Label_Type": "LabelType",
    # FinalQuantityInPcs also = Final_Quantity — added as a derived col in
    # _content_cols (Final_Quantity is already mapped to FinalQuantity above).
    # CylinderCircumferenceInch = Cutting_Cylinder_Circumference / 25.4 — derived.
}

# --- ContentSizeValues: the ERP's content-size filter string for a content.
# Format: Key1=Value1AndOrKey2=Value2AndOr... (the literal separator is "AndOr",
# which the ERP later replaces with "&" to parse as a query string). The field
# ORDER + names match the legacy builder (BulkProductMasterSalesOrderPWO.js #154).
# Each entry is (Key, desktop_content_column_or_None, default_when_missing).
CONTENT_SIZE_FIELDS = [
    ("SizeHeight", "Job_Height", "0"),
    ("SizeLength", "Job_Length", "0"),
    ("SizeWidth", "Job_Width", "0"),
    ("SizeOpenflap", "Open_Flap", "0"),
    ("SizePastingflap", "Overlap_Flap", "0"),
    ("SizeBottomflap", "Bottom_Flap", "0"),
    ("JobNoOfPages", "Pages", "0"),
    ("JobUps", "Ups_Total", "0"),
    ("JobFlapHeight", "Flap_Height", "0"),
    ("JobTongHeight", "Tongue_Height", "0"),
    ("JobFoldedH", "Fold_H", "0"),
    ("JobFoldedL", "Fold_L", "0"),
    ("PlanContentType", "Orientation", ""),
    ("PlanFColor", "Front_Color", "0"),
    ("PlanBColor", "Back_Color", "0"),
    ("PlanColorStrip", "Color_Strip", "0"),
    ("PlanGripper", "Gripper", "0"),
    ("PlanPrintingStyle", "Printing_Style", "Choose Best"),
    ("PlanWastageValue", "Wastage_Percent_Sheets", "0"),
    ("Trimmingleft", "Job_Trimming_L", "0"),
    ("Trimmingright", "Job_Trimming_R", "0"),
    ("Trimmingtop", "Job_Trimming_T", "0"),
    ("Trimmingbottom", "Job_Trimming_B", "0"),
    ("Stripingleft", None, "0"),         # derived: Stripping_L + Printing_Margin_L
    ("Stripingright", None, "0"),        # derived: Stripping_R + Printing_Margin_R
    ("Stripingtop", None, "0"),          # derived: Stripping_T + Printing_Margin_T
    ("Stripingbottom", None, "0"),       # derived: Stripping_B + Printing_Margin_B
    ("PlanPrintingGrain", "Grain_Direction", "Both"),
    ("ItemPlanQuality", "Paper_Quality", ""),
    ("ItemPlanGsm", "Paper_Face_GSM", ""),
    ("ItemPlanMill", "Paper_Mill", ""),
    ("PlanPlateType", "Plate_Type", "CTP Plate"),
    ("PlanWastageType", "Wastage_Type", "Machine Default"),
    ("PlanContQty", "Quantity", "0"),
    ("PlanSpeFColor", "Special_Front_Color", "0"),
    ("PlanSpeBColor", "Special_Back_Color", "0"),
    ("PlanContName", "Content_Name", ""),
    ("ItemPlanFinish", "Finish_Type", ""),
    ("OperId", None, ""),                # derived: CSV of resolved web ProcessIDs
    ("JobBottomPerc", None, "0"),
    ("JobPrePlan", None, ""),            # derived: "H:<Job_Height>,W:<Job_Width>"
    ("ChkPlanInSpecialSizePaper", "Plan_On_Special_Size", "false"),   # bool
    ("ChkPlanInStandardSizePaper", None, "false"),                    # bool
    ("MachineId", "Machine_ID", ""),
    ("PlanOnlineCoating", "Online_Coating", ""),
    ("PaperTrimleft", "Paper_Trimming_L", "0"),
    ("PaperTrimright", "Paper_Trimming_R", "0"),
    ("PaperTrimtop", "Paper_Trimming_T", "0"),
    ("PaperTrimbottom", "Paper_Trimming_B", "0"),
    ("ChkPaperByClient", None, "false"),                              # bool
    ("JobFoldInL", None, "1"),
    ("JobFoldInH", None, "1"),
    ("ChkPlanInAvailableStock", None, "false"),                       # bool
    ("PlanPlateBearer", None, "0"),
    ("PlanStandardARGap", "Gap_In_Row", "0"),
    ("PlanStandardACGap", "Gap_In_Column", "0"),
    ("PlanContDomainType", "Module_Type", "Offset"),
    ("Planlabeltype", "Label_Type", "null"),
    ("Planwindingdirection", "Winding_Direction", ""),
    ("Planfinishedformat", None, "null"),
    ("Plandietype", None, ""),
    ("PlanPcsPerRoll", "Finish_Qty_Per_Roll", "0"),
    ("PlanCoreInnerDia", None, "0"),
    ("PlanCoreOuterDia", None, "0"),
    # ---- keys added to match the current web ContentSizeValues format ----
    ("EstimationQuantityUnit", None, "PCS"),
    ("ItemPlanThickness", None, "0"),
    ("SizeCenterSeal", None, "0"),
    ("SizeSideSeal", None, "0"),
    ("SizeTopSeal", None, "0"),
    ("SizeBottomGusset", None, "0"),
    ("PlanMakeReadyWastage", None, "0"),
    ("CategoryID", None, "0"),           # derived: resolved web CategoryID
    ("BookSpine", None, "0"),
    ("BookHinge", None, "0"),
    ("BookCoverTurnIn", None, "0"),
    ("BookExtension", None, "0"),
    ("BookLoops", None, "0"),
    ("PlanOtherMaterialGSM", None, "0"),
    ("PlanOtherMaterialGSMSettingJSON", None, ""),
    ("MaterialWetGSMConfigJSON", None, ""),
    ("PlanPunchingType", None, "null"),
    ("ChkBackToBackPastingRequired", "Back2Back_Pasting", "false"),   # bool
    ("JobAcrossUps", "Ups_W", "0"),
    ("JobAroundUps", "Ups_L", "0"),
    ("SizeBottomflapPer", None, "0"),    # derived: (Bottom_Flap*100)/Job_Width
    ("SizeZipperLength", None, "0"),
    ("ZipperWeightPerMeter", None, "0"),
    ("JobSizeInputUnit", None, "MM"),
    ("LedgerID", None, "0"),
    ("ShowPlanUptoWastePercent", None, "10"),
]

# Keys whose value is a boolean flag (emitted as lowercase true/false).
# (ChkPaperByClient and ChkPlanInSpecialSizePaper are derived specially instead.)
CONTENT_SIZE_BOOL_KEYS = {
    "ChkPlanInStandardSizePaper",
    "ChkPlanInAvailableStock", "ChkBackToBackPastingRequired",
}


# --- process map: Product_Master_Operations -> ProductMasterProcess -----------
PROCESS_MAP = {
    # Desktop Quantity is empty; the real per-operation qty is Operation_Quantity.
    "Operation_Quantity": "Quantity",
    "Content_Name": "PlanContName",
    "Rate": "Rate",
    "Trans_ID": "TransId",
    "Is_Display": "IsDisplay",
    "Size_L": "SizeL",
    "Size_W": "SizeW",
    "Amount": "Amount",
    # Web ProductMasterProcess has only 'Remarks'; the meaningful desktop text is
    # in the singular 'Remark' column, so map Remark -> Remarks.
    "Remark": "Remarks",
    "Avg_Machine_Speed": "MachineSpeed",
    "Make_Ready_Time_Hr": "MakeReadyTime",
    "Machine_Hour_Cost": "MachinePerHourCost",
    "Paper_Consumption_Required": "PaperConsumptionRequired",
}

# --- corrugation map: Product_Master_Corrugation -> ProductMasterCorrugation --
CORR_MAP = {
    "Content_Name": "PlanContName",
    "Quantity": "PlanContQty",
    "Ply_No": "PlyNo",
    "Flute_Type": "FluteName",
    "Weight": "Weight",
    "Rate": "Rate",
    "Amount": "Amount",
    "Width": "Width",
    "Gsm": "GSM",
    "BF": "BF",
    "BS": "BS",
    "Sheet": "Sheets",
    "Box_Weight": "BoxWeight",
    "Conversion_Kg": "ConversionPerKG",
    "Conversion_Amount": "ConversionAmount",
    "Total_Amount": "TotalAmount",
    "Grand_Total": "GrandTotal",
    "Corrugation_Weight": "CorrugationWeight",
}

# --- material req map: Product_Master_Machine_Material_Setting -> ...Requirement
MATREQ_MAP = {
    "Content_Name": "PlanContName",
    "Required_Quantity": "RequiredQty",
    "Rate": "Rate",
    "Total_Amount": "Amount",
}

# --- tool allocation map: Product_Master_Operation_Tool_Allocation
#     -> ProductMasterProcessToolAllocation
TOOLALLOC_MAP = {
    "Content_Name": "PlanContName",
    "Quantity": "PlanContQty",
}

# --- material parameter map: Product_Master_Material_Costing_Property_Details
#     -> ProductMasterProcessMaterialParameterDetail
#  FieldName (the parameter identifier the ERP/formula engine uses) comes from the
#  desktop Field_Description — it was previously left NULL, making params unusable.
MATPARAM_MAP = {
    "Content_Name": "PlanContName",
    "Trans_ID": "TransID",
    "Field_Description": "FieldName",
    "Field_Display_Name": "FieldDisplayName",
    "Master_Field_Name": "ItemMasterFieldName",
    "Variable_Name": "AppVariableName",
    "Calculation_Formula": "CalculationFormula",
    "Field_Value": "FieldValue",
}

# --- shade selection map: Color_Details_Product -> JobBookingColorDetails
#     (product-linked; BookingID left empty as bookings aren't migrated here)
SHADE_MAP = {
    "Material_Name": "ItemName",
    "Color_Specification": "ColorSpecification",
    "Material_Panton_Number": "ItemPantoneCode",
    "Coverage_Area_Percent": "CoverageAreaPercent",
    "Solid": "Solid",
    "Midtone": "Midtone",
    "Highlight": "Highlight",
    "Quartertone": "Quartertone",
    "Content_Name": "PlanContName",
    "Form_No": "FormNo",
    "Form_Side": "FormSide",
    "Booking_No": "JobBookingNo",
    "Order_Booking_No": "OrderBookingNo",
}

# Desktop Color_Specification label -> web ColorSpecification shorthand. The web
# ERP stores only "Front"/"Back"/"Sp. Front"/"Sp. Back" (ProductCatalog.js#167);
# the desktop stores the long form ("Front Colour", "Back Colour", ...). Keys are
# normalised (lower-cased, American 'color' -> 'colour'). Anything not listed
# (e.g. the desktop junk value "0") maps to None => nothing written (QA: only fill
# ColorSpecification when a recognised desktop value is present).
COLOR_SPEC_MAP = {
    "front colour": "Front",
    "back colour": "Back",
    "front special colour": "Sp. Front",
    "back special colour": "Sp. Back",
}


# --- specification map: Product_Master_Contents -> ProductMasterContentsSpecification
# (per the user's mapping sheet). Direct desktop->web pairs; the derived/sum ones
# (Striping* = Stripping_* + Printing_Margin_*, SizeBottomflapPer) are computed in
# _spec_cols(), not here.
SPEC_MAP = {
    "Quantity": "PlanContQty",
    "Content_Name": "PlanContName",
    "Orientation": "PlanContentType",
    "Job_Height": "SizeHeight",
    "Job_Length": "SizeLength",
    "Job_Width": "SizeWidth",
    "Big_Size_H": "BigSizeHeight",
    "Big_Size_L": "BigSizeLength",
    "Big_H": "BigUpsAcross",
    "Open_Flap": "SizeOpenflap",
    "Overlap_Flap": "SizePastingflap",
    "Bottom_Flap": "SizeBottomflap",
    "Pages": "JobNoOfPages",
    "Ups_Total": "JobUps",
    "Flap_Height": "JobFlapHeight",
    "Tongue_Height": "JobTongHeight",
    "Fold_H": "JobFoldedH",
    "Fold_L": "JobFoldedL",
    "Front_Color": "PlanFColor",
    "Back_Color": "PlanBColor",
    "Special_Front_Color": "PlanSpeFColor",
    "Special_Back_Color": "PlanSpeBColor",
    "Color_Strip": "PlanColorStrip",       # sheet said Side_Strip (typo) -> Color_Strip
    "Gripper": "PlanGripper",
    "Printing_Style": "PlanPrintingStyle",
    "Wastage_Percent_Sheets": "PlanWastageValue",
    "Job_Trimming_L": "Trimmingleft",
    "Job_Trimming_R": "Trimmingright",
    "Job_Trimming_T": "Trimmingtop",
    "Job_Trimming_B": "Trimmingbottom",
    "Grain_Direction": "PlanPrintingGrain",
    "Paper_Quality": "ItemPlanQuality",
    "Paper_Face_GSM": "ItemPlanGsm",
    "Paper_Mill": "ItemPlanMill",
    "Plate_Type": "PlanPlateType",
    "Wastage_Type": "PlanWastageType",
    "Machine_ID": "MachineId",
    "Online_Coating": "PlanOnlineCoating",
    "Paper_Trimming_L": "PaperTrimleft",
    "Paper_Trimming_R": "PaperTrimright",
    "Paper_Trimming_T": "PaperTrimtop",
    "Paper_Trimming_B": "PaperTrimbottom",
    "Paper_By": "ChkPaperByClient",
    "Label_Type": "Planlabeltype",
    "Winding_Direction": "Planwindingdirection",
    "Gap_In_Row": "PlanStandardARGap",
    "Gap_In_Column": "PlanStandardACGap",
    "Ups_W": "JobAcrossUps",
    "Ups_L": "JobAroundUps",
}


class ProductMasterMigration(EntityMigration):
    name = "ProductMaster"
    target_table = "ProductMaster"
    target_identity = "ProductMasterID"
    # Clearing a product also clears every child table it owns (by ProductMasterID).
    clear_child_tables = [
        ("ProductMasterProcess", "ProductMasterID"),
        ("ProductMasterCorrugation", "ProductMasterID"),
        ("ProductMasterProcessMaterialRequirement", "ProductMasterID"),
        ("ProductMasterProcessToolAllocation", "ProductMasterID"),
        ("ProductMasterProcessMaterialParameterDetail", "ProductMasterID"),
        ("JobBookingColorDetails", "ProductMasterID"),
        ("ProductMasterContentsSpecification", "ProductMasterID"),
        ("ProductMasterContents", "ProductMasterID"),
    ]

    def clear_related(self, cursor, parent_ids, deleted):
        """When clearing products, also remove their JobBooking + JobApprovedCost:
        fetch each product's BookingID, delete JobApprovedCost then JobBooking by it
        (so no orphan booking/cost rows remain). Same transaction as the clear."""
        if not parent_ids or not _has_column("JobBooking", "BookingID"):
            return
        for i in range(0, len(parent_ids), 1000):
            chunk = parent_ids[i:i + 1000]
            ph = ",".join("?" for _ in chunk)
            bids = [r[0] for r in cursor.execute(
                f"SELECT BookingID FROM JobBooking WHERE ProductMasterID IN ({ph})",
                chunk).fetchall() if r[0] is not None]
            for j in range(0, len(bids), 1000):
                bch = bids[j:j + 1000]
                bph = ",".join("?" for _ in bch)
                if _has_column("JobApprovedCost", "BookingID"):
                    cursor.execute(
                        f"DELETE FROM JobApprovedCost WHERE BookingID IN ({bph})", bch)
                    deleted["JobApprovedCost"] = deleted.get("JobApprovedCost", 0) + \
                        (cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0)
                cursor.execute(f"DELETE FROM JobBooking WHERE BookingID IN ({bph})", bch)
                deleted["JobBooking"] = deleted.get("JobBooking", 0) + \
                    (cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0)

    def __init__(self, company_id: int = 2, user_id: int = 1, fyear: str = ""):
        self.company_id = company_id
        self.user_id = user_id
        self.fyear = fyear
        self._existing: set = set()
        # Timestamp stamped into every table's CreatedDate at migration time.
        self._now = _dt.datetime.now()
        # Job_Coordinator_ID -> web Job Coordinator LedgerID (built lazily).
        self._coordinator_map: dict | None = None
        # ERPParameterSetting caches: {ParameterName: {norm(value): canonical}} and
        # the default Job Priority value (built lazily).
        self._erp_params: dict | None = None
        self._priority_default: str | None = None
        # JobBooking creation: running MaxBookingNo counter, desktop Job_Estimation
        # cost map, and the list of product tables carrying a BookingID (all lazy).
        self._max_booking_no: int | None = None
        self._job_estimation: dict | None = None
        self._bk_tables: list | None = None
        cid = company_id
        self._ledger = RefMap("LedgerMaster", "RefLedgerID", "LedgerID", company_id=cid)
        self._category = RefMap("CategoryMaster", "RefCategoryID", "CategoryID", company_id=cid)
        self._hsn = RefMap("ProductHSNMaster", "RefProductHSNID", "ProductHSNID", company_id=cid)
        self._machine = RefMap("MachineMaster", "RefMachineID", "MachineId", company_id=cid)
        self._process = RefMap("ProcessMaster", "RefProcessID", "ProcessID", company_id=cid)
        self._item = RefMap("ItemMaster", "RefItemID", "ItemID", company_id=cid)
        # Desktop Material_ID -> the colour item's NON-substrate ItemMaster row as
        # (ItemID, ItemGroupID), excluding groups 2/13/14 (Reel/Roll/Paper) — those
        # share desktop id ranges with the Ink/Material items a colour row
        # references. Built lazily below.
        self._color_item_map: dict | None = None
        # Desktop Paper_ID -> the paper item's ItemMaster row as (ItemID, ItemGroupID,
        # PurchaseUnit, EstimationUnit), EXCLUDING the ink/material groups 3-8 so a
        # paper resolves to its substrate item, not a colliding ink. Built lazily.
        self._paper_item_map: dict | None = None
        # Tool allocation resolves the desktop Tool_ID via ToolMaster.RefToolId
        # (the tool migration stores the desktop Tool_ID there) -> web ToolID and
        # the tool's ToolGroupID.
        self._tool = RefMap("ToolMaster", "RefToolId", "ToolID", company_id=cid)
        self._tool_group = RefMap("ToolMaster", "RefToolId", "ToolGroupID", company_id=cid)
        # child rows grouped by parent id, loaded once in read_source()
        self._contents: dict = {}
        self._ops: dict = {}
        self._corr: dict = {}
        self._matreq: dict = {}
        self._toolalloc: dict = {}      # tool allocation, by Product_Content_ID
        self._matparam: dict = {}       # material parameter details, by Product_Content_ID
        self._shade: dict = {}          # shade selection (color details), by Product_Content_ID
        # effective (auto-expanded) maps — built once against the live schema so
        # EVERY name-matched field migrates, not just the hand-picked ones.
        self._maps_built = False
        self._pm_map = PM_MAP
        self._content_map = CONTENT_MAP
        self._process_map = PROCESS_MAP
        self._corr_map = CORR_MAP
        self._matreq_map = MATREQ_MAP
        self._toolalloc_map = TOOLALLOC_MAP
        self._matparam_map = MATPARAM_MAP
        self._shade_map = SHADE_MAP

    def _build_full_maps(self):
        """Expand each hand-written map with every name-matched desktop->web
        column (the legacy tool copies them all). Built once against the schema."""
        if self._maps_built:
            return
        self._pm_map = _auto_column_map("Product_Master", "ProductMaster", PM_MAP)
        self._content_map = _auto_column_map(
            "Product_Master_Contents", "ProductMasterContents", CONTENT_MAP)
        self._process_map = _auto_column_map(
            "Product_Master_Operations", "ProductMasterProcess", PROCESS_MAP)
        self._corr_map = _auto_column_map(
            "Product_Master_Corrugation", "ProductMasterCorrugation", CORR_MAP)
        self._matreq_map = _auto_column_map(
            "Product_Master_Machine_Material_Setting",
            "ProductMasterProcessMaterialRequirement", MATREQ_MAP)
        self._toolalloc_map = _auto_column_map(
            "Product_Master_Operation_Tool_Allocation",
            "ProductMasterProcessToolAllocation", TOOLALLOC_MAP)
        self._matparam_map = _auto_column_map(
            "Product_Master_Material_Costing_Property_Details",
            "ProductMasterProcessMaterialParameterDetail", MATPARAM_MAP)
        self._shade_map = _auto_column_map(
            "Color_Details_Product", "JobBookingColorDetails", SHADE_MAP)
        self._maps_built = True

    # ---- read + bulk-load children ----------------------------------------
    def _ensure_desktop_id_column(self):
        """The team's reference SQL joins on ProductMaster.DesktopProductMasterID.
        Add it (idempotently) so those queries work; we populate it alongside
        RefProductMasterID at insert time."""
        try:
            exists = db.query_web(
                "SELECT 1 c FROM sys.columns WHERE object_id=OBJECT_ID('ProductMaster') "
                "AND name='DesktopProductMasterID'")
            if not exists:
                cur = db.get_web().cursor()
                cur.execute("ALTER TABLE ProductMaster ADD DesktopProductMasterID BIGINT NULL")
                db.get_web().commit()
                engine.reset_schema_caches()   # new column now visible to inserts
        except Exception:
            db.get_web().rollback()

    def read_source(self):
        self._ensure_desktop_id_column()
        self._build_full_maps()
        # existing products (idempotency by RefProductMasterID)
        rows = db.query_web(
            "SELECT RefProductMasterID FROM ProductMaster "
            "WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0 "
            "AND RefProductMasterID IS NOT NULL", [self.company_id])
        self._existing = {self._k(r["RefProductMasterID"]) for r in rows}

        parents = db.query_desktop(
            "SELECT " + ", ".join(f"[{c}]" for c in dict.fromkeys(
                list(self._pm_map) + ["Product_Master_ID", "Product_Master_Code",
                                "Ledger_ID", "Sales_Employee_ID", "Job_Coordinator_ID",
                                "Catagory_ID", "Product_Group_ID", "Estimate_ID"])) +
            " FROM Product_Master WHERE ISNULL(Is_Hidden,0)=0")
        self._parents = parents
        self._load_children([p["Product_Master_ID"] for p in parents])
        return parents

    def _load_children(self, ids):
        """Bulk-load the per-content child rows, grouped for fast lookup."""
        self._build_full_maps()
        # source columns the ContentSizeValues string + Specification row need
        size_src = [col for _k, col, _d in CONTENT_SIZE_FIELDS if col]
        spec_src = list(SPEC_MAP) + [
            "Stripping_L", "Stripping_R", "Stripping_T", "Stripping_B",
            "Printing_Margin_L", "Printing_Margin_R", "Printing_Margin_T",
            "Printing_Margin_B", "Final_Quantity", "Cutting_Cylinder_Circumference"]
        self._contents = self._group(self._read_children(
            "Product_Master_Contents",
            list(self._content_map) + size_src + spec_src + [
                "Product_Master_ID", "Content_ID", "Product_Content_ID",
                "Machine_ID", "Paper_ID", "Is_Planned", "Actual_Sheets",
                "Paper_Rate", "Gripper_Main", "Paper_Search_String",
                "Finish_Type"], ids, "Product_Master_ID"),
            "Product_Master_ID")
        self._ops = self._group(self._read_children(
            "Product_Master_Operations", list(self._process_map) + [
                "Content_ID", "Product_Content_ID", "Machine_ID", "Operation_ID",
                "Tool_ID"], ids, "Product_Master_ID"), "Product_Content_ID")
        self._corr = self._group(self._read_children(
            "Product_Master_Corrugation", list(self._corr_map) + [
                "Content_ID", "Product_Content_ID", "Item_ID"], ids,
            "Product_Master_ID"), "Product_Content_ID")
        self._matreq = self._group(self._read_children(
            "Product_Master_Machine_Material_Setting", list(self._matreq_map) + [
                "Content_ID", "Product_Content_ID", "Machine_ID", "Operation_ID",
                "Material_ID", "Order_Quantity", "Trans_ID"], ids,
            "Product_Master_ID"), "Product_Content_ID")
        # Tool allocation — keyed by Product_Content_ID (skip products w/o rows).
        self._toolalloc = self._group(self._read_children(
            "Product_Master_Operation_Tool_Allocation", list(self._toolalloc_map) + [
                "Product_Content_ID", "Operation_ID", "Tool_ID", "Tool_Group_ID",
                "Tool_Type"], ids, "Product_Master_ID"), "Product_Content_ID")
        # Material parameter details (the big per-material parameter/formula table).
        self._matparam = self._group(self._read_children(
            "Product_Master_Material_Costing_Property_Details", list(self._matparam_map) + [
                "Product_Content_ID", "Operation_ID", "Material_ID"],
            ids, "Product_Master_ID"), "Product_Content_ID")
        # Shade selection (color details) — product-linked colour/coverage rows.
        self._shade = self._group(self._read_children(
            "Color_Details_Product", list(self._shade_map) + [
                "Product_Content_ID", "Material_ID"],
            ids, "Product_Master_ID"), "Product_Content_ID")

    def prepare_import(self):
        """Import-time setup: the import worker creates a FRESH entity and calls
        import_preview WITHOUT read_source(), so the child maps would be empty and
        only parents would migrate. Reload the child maps here so all child tables
        migrate. Idempotent — safe to call again."""
        self._build_full_maps()
        if self._contents:
            return                      # already loaded (e.g. run_entity path)
        ids = [p["Product_Master_ID"] for p in db.query_desktop(
            "SELECT Product_Master_ID FROM Product_Master WHERE ISNULL(Is_Hidden,0)=0")]
        self._load_children(ids)

    def _read_children(self, table, cols, parent_ids, parent_col):
        if not parent_ids:
            return []
        # de-dupe requested columns; only those that exist
        existing = {c["name"].lower() for c in db.query_desktop(
            "SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?)", [table])}
        sel = [c for c in dict.fromkeys(cols) if c.lower() in existing]
        # chunk IN-list to stay under parameter limits
        out = []
        CH = 1000
        for i in range(0, len(parent_ids), CH):
            chunk = parent_ids[i:i + CH]
            ph = ",".join("?" for _ in chunk)
            out += db.query_desktop(
                f"SELECT {', '.join('['+c+']' for c in sel)} FROM [{table}] "
                f"WHERE [{parent_col}] IN ({ph})", chunk)
        return out

    @staticmethod
    def _group(rows, key):
        g: dict = {}
        for r in rows:
            g.setdefault(r.get(key), []).append(r)
        return g

    @staticmethod
    def _k(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    # ---- identity / idempotency -------------------------------------------
    def source_key(self, row):
        return str(row.get("Product_Master_Code") or row.get("Job_Name") or
                   row.get("Product_Master_ID") or "?").strip()

    def already_migrated(self, row):
        return self._k(row.get("Product_Master_ID")) in self._existing

    # ---- FK resolution (parent level) -------------------------------------
    def resolve_refs(self, row):
        refs = {}
        led = self._ledger.resolve(row.get("Ledger_ID"), required=False)
        if led is not None:
            refs["LedgerID"] = led
        for tgt, src in (("SalesEmployeeID", "Sales_Employee_ID"),
                         ("JobCoordinatorID", "Job_Coordinator_ID")):
            v = self._ledger.resolve(row.get(src), required=False)
            if v is not None:
                refs[tgt] = v
        # CoordinatorLedgerID: resolve the desktop Job_Coordinator_ID to the web
        # LedgerID of the matching Job Coordinator ledger (LedgerGroupID=3,
        # Designation='Job Coordinator', RefLedgerID=Job_Coordinator_ID) — never the
        # raw desktop id.
        coord = self._resolve_coordinator(row.get("Job_Coordinator_ID"))
        if coord is not None:
            refs["CoordinatorLedgerID"] = coord
        cat = self._category.resolve(row.get("Catagory_ID"), required=False)
        if cat is not None:
            refs["CategoryID"] = cat
        hsn = self._hsn.resolve(row.get("Product_Group_ID"), required=False)
        if hsn is not None:
            refs["ProductHSNID"] = hsn
        return refs

    def _resolve_coordinator(self, jc_id):
        """Desktop Job_Coordinator_ID -> web LedgerMaster.LedgerID of the matching
        Job Coordinator ledger (LedgerGroupID=3, Designation='Job Coordinator',
        RefLedgerID=Job_Coordinator_ID). None if no such ledger exists."""
        if self._coordinator_map is None:
            rows = db.query_web(
                "SELECT RefLedgerID AS r, LedgerID AS i FROM LedgerMaster "
                "WHERE CompanyID=? AND LedgerGroupID=3 "
                "AND Designation='Job Coordinator' "
                "AND ISNULL(IsDeletedTransaction,0)=0 AND RefLedgerID IS NOT NULL",
                [self.company_id])
            self._coordinator_map = {self._k(row["r"]): row["i"] for row in rows}
        return self._coordinator_map.get(self._k(jc_id))

    # ---- ERPParameterSetting (JobType / JobReference / JobPriority) --------
    def _load_erp_param_map(self, param_name):
        """Cache {lower(value): canonical ParameterValue} of the ACTIVE values for a
        ParameterName in ERPParameterSetting (company-scoped)."""
        if self._erp_params is None:
            self._erp_params = {}
        if param_name not in self._erp_params:
            rows = db.query_web(
                "SELECT ParameterValue AS v FROM ERPParameterSetting "
                "WHERE ParameterName=? AND CompanyID=? "
                "AND ISNULL(IsDeletedTransaction,0)=0 AND ISNULL(ParameterValue,'')<>''",
                [param_name, self.company_id])
            self._erp_params[param_name] = {
                (r["v"] or "").strip().lower(): (r["v"] or "").strip() for r in rows}
        return self._erp_params[param_name]

    def _ensure_erp_value(self, param_name, value):
        """Get-or-create a ParameterValue under a ParameterName in ERPParameterSetting.
        Returns the canonical value (existing match, or the value just inserted).
        Blank/None passes through unchanged."""
        if value is None or str(value).strip() == "":
            return value
        val = str(value).strip()
        pmap = self._load_erp_param_map(param_name)
        hit = pmap.get(val.lower())
        if hit is not None:
            return hit
        try:
            cur = db.get_web().cursor()
            cur.execute(
                "INSERT INTO ERPParameterSetting (ParameterName, ParameterType, "
                "ParameterValue, CompanyID, CreatedBy, CreatedDate, IsDeletedTransaction) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                [param_name, param_name, val, self.company_id, self.user_id, self._now])
            db.get_web().commit()
            pmap[val.lower()] = val
        except Exception:
            db.get_web().rollback()
        return val

    def _erp_priority_default(self):
        """Default Job Priority = the first active 'Job Priority' ParameterValue."""
        if self._priority_default is None:
            rows = db.query_web(
                "SELECT TOP 1 ParameterValue AS v FROM ERPParameterSetting "
                "WHERE ParameterName='Job Priority' AND CompanyID=? "
                "AND ISNULL(IsDeletedTransaction,0)=0 AND ISNULL(ParameterValue,'')<>'' "
                "ORDER BY ParameterID", [self.company_id])
            self._priority_default = (rows[0]["v"].strip() if rows and rows[0]["v"] else "")
        return self._priority_default

    def _job_priority_value(self, value):
        """JobPriority: the ERP-defaulted value when the desktop is blank, else the
        get-or-created ParameterValue."""
        if value is None or str(value).strip() == "":
            return self._erp_priority_default()
        return self._ensure_erp_value("Job Priority", value)

    # ---- parent build ------------------------------------------------------
    def _context(self, table):
        ctx = {}
        for col, val in (("CompanyID", self.company_id), ("UserID", self.user_id),
                         ("FYear", self.fyear), ("CreatedBy", self.user_id),
                         ("CreatedDate", self._now), ("IsDeletedTransaction", 0)):
            if _has_column(table, col):
                ctx[col] = val
        return ctx

    _max_pm_code: int | None = None

    def _next_pm_code(self) -> int:
        """Next MaxProductMasterCode, seeded from the target (company-wide series,
        prefix 'PC' — legacy SaveDataProductMaster line 469). ProductMasterCode
        itself stays the desktop code; this counter feeds the ERP grid/next-no."""
        if self._max_pm_code is None:
            r = db.query_web(
                "SELECT ISNULL(MAX(ISNULL(MaxProductMasterCode,0)),0) AS MaxNo "
                "FROM ProductMaster WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0",
                [self.company_id])
            self._max_pm_code = int(r[0]["MaxNo"]) if r else 0
        self._max_pm_code += 1
        return self._max_pm_code

    def build_parent(self, row, refs):
        from core.mapping import to_sql_value
        cols, vals = [], []

        def put(col, val):
            if col in cols:
                vals[cols.index(col)] = val      # never duplicate a column
            else:
                cols.append(col); vals.append(val)

        for s, t in self._pm_map.items():
            put(t, to_sql_value(row.get(s)))     # G2: strip quotes
        # JobType / JobReference / JobPriority go through ERPParameterSetting
        # (get-or-create) so the stored value is one the ERP knows; a blank
        # JobPriority falls back to the ERP default. Values are quote-stripped (G2)
        # before lookup so e.g. 'As Client"s Approved Artwork' matches the existing
        # 'As Clients Approved Artwork'.
        put("JobType", self._ensure_erp_value("Job Type", to_sql_value(row.get("Job_Type"))))
        put("JobReference",
            self._ensure_erp_value("Job Reference", to_sql_value(row.get("Job_Reference"))))
        put("JobPriority", self._job_priority_value(to_sql_value(row.get("Job_Priority"))))
        # Also store the desktop id in DesktopProductMasterID (team SQL joins on it).
        put("DesktopProductMasterID", row.get("Product_Master_ID"))
        # Fill the ERP MaxProductMasterCode counter (visible code stays desktop).
        if _has_column("ProductMaster", "MaxProductMasterCode"):
            put("MaxProductMasterCode", self._next_pm_code())
        for t, v in refs.items():
            put(t, v)
        for c, v in self._context(self.target_table).items():
            put(c, v)
        return cols, vals

    def build_children(self, row, refs, parent_id):
        return []   # handled by insert_children (needs cursor for nested ids)

    # ---- nested child inserts (within the product's transaction) ----------
    def insert_children(self, row, refs, product_id, cursor):
        pm_code = row.get("Product_Master_Code")
        pm_id = self._k(row.get("Product_Master_ID"))
        contents = self._contents.get(pm_id, [])
        for idx, c in enumerate(contents, start=1):
            ccols, cvals = self._content_cols(row, c, product_id, pm_code, idx, len(contents), refs)
            cvals, _ = _fit(self, "ProductMasterContents", ccols, cvals)
            content_id = engine.insert_parent_row(
                cursor, "ProductMasterContents", "ProductMasterContentsID", ccols, cvals)
            # One ProductMasterContentsSpecification row per content (mapping sheet).
            scols, svals = self._spec_cols(c, product_id, content_id)
            svals, _ = _fit(self, "ProductMasterContentsSpecification", scols, svals)
            engine.insert_one_row(
                cursor, "ProductMasterContentsSpecification", scols, svals)
            # link grandchildren by the source Product_Content_ID
            src_content = self._k(c.get("Product_Content_ID"))
            self._insert_grandchildren(cursor, product_id, content_id, src_content, c)

    @staticmethod
    def _batch_insert(cursor, table, built_rows):
        """Insert many child rows for one table in a SINGLE round-trip. Each row
        is a (cols, vals) pair from _simple_child; rows can have different column
        sets (FK cols appear only when resolved), so we union the columns and pad
        missing ones with None, then executemany. This replaces one INSERT per row
        (the migration's main slowness) with one INSERT per table per content."""
        rows = [rv for rv in built_rows if rv[0]]
        if not rows:
            return
        # union of all columns, preserving first-seen order
        cols = []
        seen = set()
        for c, _v in rows:
            for name in c:
                if name not in seen:
                    seen.add(name); cols.append(name)
        # pad each row to the union column order
        padded = []
        for c, v in rows:
            m = dict(zip(c, v))
            padded.append([m.get(name) for name in cols])
        engine.insert_child_rows(cursor, table, cols, padded)

    def _insert_grandchildren(self, cursor, product_id, content_id, src_content,
                              content_row=None):
        links_base = {"ProductMasterID": product_id,
                      "ProductMasterContentsID": content_id}
        # QA: stamp the parent content's PlanContentType (desktop Orientation) onto
        # its children so ProductMasterProcess (and any child table that has the
        # column) carries the content type it belongs to, keyed by ProductMasterContentsID.
        if content_row is not None:
            ctype = content_row.get("Orientation")
            if ctype is not None and str(ctype).strip() != "":
                links_base["PlanContentType"] = ctype
        # Process. PlanContQty comes from the parent content (ProductMasterContents,
        # matched by ProductMasterContentsID); Rate is rounded to 4 decimals.
        content_qty = content_row.get("Quantity") if content_row is not None else None
        process_rows = []
        for op in self._ops.get(src_content, []):
            cols, vals = self._simple_child(
                self._process_map, op, "ProductMasterProcess", links_base,
                {"ProcessID": (self._process, "Operation_ID"),
                 "MachineID": (self._machine, "Machine_ID")})
            if content_row is not None and _has_column("ProductMasterProcess", "PlanContQty"):
                if "PlanContQty" in cols:
                    vals[cols.index("PlanContQty")] = content_qty
                else:
                    cols.append("PlanContQty"); vals.append(content_qty)
            if "Rate" in cols:
                vals[cols.index("Rate")] = self._round4(vals[cols.index("Rate")])
            process_rows.append((cols, vals))
        self._batch_insert(cursor, "ProductMasterProcess", process_rows)
        # Corrugation
        self._batch_insert(cursor, "ProductMasterCorrugation", [
            self._simple_child(self._corr_map, cr, "ProductMasterCorrugation", links_base,
                {"ItemID": (self._item, "Item_ID")})
            for cr in self._corr.get(src_content, [])])
        # Material requirement — mirrors the desktop material builder: ONE row for
        # the content's own paper/main material (ProcessID=0, ItemID from Paper_ID),
        # then one row per Product_Master_Machine_Material_Setting entry.
        T = "ProductMasterProcessMaterialRequirement"
        # PlanContentType = content Orientation with all whitespace removed (the
        # material-setting source has no Orientation of its own).
        ctype_ns = _re.sub(r"\s+", "", str(content_row.get("Orientation") or "")) \
            if content_row is not None else ""
        matreq_rows = []
        if content_row is not None:
            # Paper row: ItemID is the PAPER item for Paper_ID (ItemMaster row whose
            # group is NOT an ink/material group 3-8); ItemGroupID/PurchaseUnit/
            # EstimationUnit come from that same item. MachineID is the content's
            # machine; ProcessID is the paper-consumption process for this content.
            paper = self._resolve_paper_item(content_row.get("Paper_ID"))
            p_item, p_grp, p_pu, p_eu = paper if paper else (0, 0, None, None)
            matreq_rows.append(self._matreq_row(T, {
                "MachineID": self._machine.resolve(content_row.get("Machine_ID"), required=False) or 0,
                "ProcessID": self._paper_consumption_process_id(src_content),
                "ItemID": p_item or 0,
                "ItemGroupID": p_grp or 0,
                "SequenceNo": 0,
                "PlanContentType": ctype_ns,
                "PlanContName": content_row.get("Content_Name"),
                "PlanContQty": content_row.get("Quantity"),
                "RequiredQty": content_row.get("Actual_Sheets"),
                "Rate": content_row.get("Paper_Rate"),
                "Amount": content_row.get("Total_Amount"),
                "IsPlannedItem": content_row.get("Is_Planned"),
                "PurchaseUnit": p_pu,
                "EstimationUnit": p_eu,
            }, product_id, content_id))
        for mr in self._matreq.get(src_content, []):
            # Material row: ItemID is the NON-substrate item for Material_ID (group
            # not in 2/13/14 — Reel/Roll/Paper), matching the colour-item rule.
            matreq_rows.append(self._matreq_row(T, {
                "MachineID": self._machine.resolve(mr.get("Machine_ID"), required=False) or 0,
                "ProcessID": self._process.resolve(mr.get("Operation_ID"), required=False) or 0,
                "ItemID": self._resolve_color_item_id(mr.get("Material_ID")) or 0,
                "ItemGroupID": 0,
                "SequenceNo": mr.get("Trans_ID"),
                "PlanContentType": ctype_ns,
                "PlanContName": mr.get("Content_Name"),
                "PlanContQty": mr.get("Order_Quantity"),
                "RequiredQty": mr.get("Required_Quantity"),
                "Rate": mr.get("Rate"),
                "Amount": mr.get("Total_Amount"),
                "IsPlannedItem": 0,
            }, product_id, content_id))
        self._batch_insert(cursor, T, matreq_rows)
        # Tool allocation: one row per OPERATION that carries a tool (desktop
        # Product_Master_Operations.Tool_ID > 0), built with the SAME mapping/links
        # as ProductMasterProcess. ToolID and ToolGroupID resolve from the desktop
        # Tool_ID via ToolMaster.RefToolId; PlanContQty comes from the content.
        ta_rows = []
        for op in self._ops.get(src_content, []):
            try:
                if int(op.get("Tool_ID") or 0) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            cols, vals = self._simple_child(
                self._process_map, op, "ProductMasterProcessToolAllocation", links_base,
                {"ProcessID": (self._process, "Operation_ID"),
                 "ToolID": (self._tool, "Tool_ID"),
                 "ToolGroupID": (self._tool_group, "Tool_ID")})
            if content_row is not None and _has_column("ProductMasterProcessToolAllocation", "PlanContQty"):
                if "PlanContQty" in cols:
                    vals[cols.index("PlanContQty")] = content_qty
                else:
                    cols.append("PlanContQty"); vals.append(content_qty)
            ta_rows.append((cols, vals))
        self._batch_insert(cursor, "ProductMasterProcessToolAllocation", ta_rows)
        # Material parameter details (the big per-material parameter/formula table)
        mp_rows = []
        for mp in self._matparam.get(src_content, []):
            links = dict(links_base)
            links["FieldDescription"] = mp.get("Field_Description")
            mp_rows.append(self._simple_child(
                self._matparam_map, mp, "ProductMasterProcessMaterialParameterDetail", links,
                {"ProcessID": (self._process, "Operation_ID"),
                 "ItemID": (self._item, "Material_ID")}))
        self._batch_insert(cursor, "ProductMasterProcessMaterialParameterDetail", mp_rows)
        # Shade selection (colour details). ColorSpecification is normalised from
        # the desktop Color_Specification label to the web shorthand (Front / Back /
        # Sp. Front / Sp. Back); an unrecognised desktop value (e.g. "0") writes
        # NULL rather than the raw text.
        shade_rows = []
        for sh in self._shade.get(src_content, []):
            # ItemID/ItemGroupID are resolved below (non-substrate row), so no ItemID
            # RefMap in the refspec here.
            cols, vals = self._simple_child(
                self._shade_map, sh, "JobBookingColorDetails", links_base, {})
            spec = self._map_color_spec(sh.get("Color_Specification"))
            if "ColorSpecification" in cols:
                vals[cols.index("ColorSpecification")] = spec
            elif _has_column("JobBookingColorDetails", "ColorSpecification"):
                cols.append("ColorSpecification"); vals.append(spec)
            # ItemID + ItemGroupID: resolve the material to its NON-substrate
            # ItemMaster row (group not in 2/13/14) so a colour row never points at
            # the Paper/Reel/Roll item that shares the same desktop id. Both come
            # from the same row.
            mid = sh.get("Material_ID")
            for tcol, tval in (("ItemID", self._resolve_color_item_id(mid)),
                               ("ItemGroupID", self._resolve_color_item_group(mid))):
                if _has_column("JobBookingColorDetails", tcol):
                    if tcol in cols:
                        vals[cols.index(tcol)] = tval
                    else:
                        cols.append(tcol); vals.append(tval)
            # PlanContQty: from the parent content (ProductMasterContents), matched
            # by ProductMasterContentsID — i.e. the content's own quantity.
            if content_row is not None and _has_column("JobBookingColorDetails", "PlanContQty"):
                qty = content_row.get("Quantity")
                if "PlanContQty" in cols:
                    vals[cols.index("PlanContQty")] = qty
                else:
                    cols.append("PlanContQty"); vals.append(qty)
            shade_rows.append((cols, vals))
        self._batch_insert(cursor, "JobBookingColorDetails", shade_rows)

    _tool_group_cache: dict | None = None
    _desktop_tool_type_cache: dict | None = None

    def _desktop_tool_type(self, desktop_group_id):
        """Desktop Tool_Group_ID -> its Tool_Type name (e.g. 4 -> 'Plate/Block')."""
        if self._desktop_tool_type_cache is None:
            self._desktop_tool_type_cache = {
                r["Tool_Group_ID"]: r["Tool_Type"] for r in db.query_desktop(
                    "SELECT DISTINCT Tool_Group_ID, Tool_Type FROM Tool_Group_Master "
                    "WHERE Tool_Type IS NOT NULL")}
        return self._desktop_tool_type_cache.get(desktop_group_id)

    def _tool_group_id(self, tool_type):
        """Resolve a desktop tool type/group name -> web ToolGroupID by name
        (same keyword matching as the Tool Master migration). None if no match."""
        from core.mapping import _norm_name
        from core.entities.spare_tool import TOOL_GROUP_KEYWORDS
        if self._tool_group_cache is None:
            self._tool_group_cache = {}
            for g in db.query_web(
                    "SELECT ToolGroupID, ToolGroupName FROM ToolGroupMaster "
                    "WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0",
                    [self.company_id]):
                self._tool_group_cache[_norm_name(g["ToolGroupName"])] = g["ToolGroupID"]
        tt = (tool_type or "").strip()
        if not tt:
            return None
        key = _norm_name(tt)
        if key in self._tool_group_cache:
            return self._tool_group_cache[key]
        low = tt.lower()
        for kw, gname in TOOL_GROUP_KEYWORDS:
            if kw in low:
                gid = self._tool_group_cache.get(_norm_name(gname))
                if gid is not None:
                    return gid
        return None

    @staticmethod
    def _truthy(v):
        return str(v).strip().lower() in ("1", "true", "yes", "y")

    @staticmethod
    def _fmt_size_value(v):
        """Format a value for the ContentSizeValues string the way the web builder
        does: bools -> true/false, whole numbers -> no decimals, decimals trimmed
        (3.00 -> 3, 1.50 -> 1.5). None/blank -> None (caller applies the default)."""
        from decimal import Decimal
        if v is None:
            return None
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, Decimal):
            s = format(v, "f")
            return (s.rstrip("0").rstrip(".") if "." in s else s)
        if isinstance(v, float):
            v = round(v, 4)          # drop SQL 'real' precision noise (4.40000009 -> 4.4)
            if v.is_integer():
                return str(int(v))
            return ("%.4f" % v).rstrip("0").rstrip(".")
        s = str(v).strip()
        return s

    @staticmethod
    def _is_label(c) -> bool:
        """True when the content's PlanContentType (desktop Orientation) is 'Label'."""
        return str(c.get("Orientation") or "").strip().lower() == "label"

    @staticmethod
    def _is_rectangular(c) -> bool:
        """True when the content's PlanContentType (desktop Orientation) is 'Rectangular'."""
        return str(c.get("Orientation") or "").strip().lower() == "rectangular"

    @staticmethod
    def _plan_plate_bearer(c):
        """PlanPlateBearer = desktop Color_Strip — the SINGLE source used for both
        ProductMasterContentsSpecification.PlanPlateBearer and the ContentSizeValues
        string, so the two never diverge."""
        return c.get("Color_Strip")

    @staticmethod
    def _plan_color_strip(c):
        """PlanColorStrip = desktop Wastage_Strip — the SINGLE source used for both
        ProductMasterContentsSpecification.PlanColorStrip and the ContentSizeValues
        string, so the two never diverge."""
        return c.get("Wastage_Strip")

    @staticmethod
    def _plan_gripper(c):
        """PlanGripper = desktop Gripper_Main — the SINGLE source used for both
        ProductMasterContentsSpecification.PlanGripper and the ContentSizeValues
        string, so the two never diverge."""
        return c.get("Gripper_Main")

    @staticmethod
    def _parse_paper_search(s):
        """Parse Quality/GSM/Mill/Finish from a Paper_Search_String like
        "Quality = 'ART PAPER GLOSS' AND GSM = 90 AND Mill = 'BGPPL IMPERIAL'
        AND Finish = 'GLOSS'". Returns a dict of the keys found."""
        out = {}
        if not s:
            return out
        for key in ("Quality", "GSM", "Mill", "Finish"):
            m = _re.search(rf"{key}\s*=\s*(?:'([^']*)'|([^\s]+))", str(s), _re.IGNORECASE)
            if m:
                out[key] = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
        return out

    def _paper_plan_fields(self, c):
        """(ItemPlanQuality, ItemPlanGsm, ItemPlanMill, ItemPlanFinish) values.
        Uses the desktop paper columns; if Paper_Quality OR Paper_Mill is blank,
        falls back to parsing Paper_Search_String. SINGLE source for both the spec
        row and the ContentSizeValues string."""
        def blank(v):
            return v is None or str(v).strip() == ""
        quality = c.get("Paper_Quality")
        gsm = c.get("Paper_Face_GSM")
        mill = c.get("Paper_Mill")
        finish = c.get("Finish_Type")
        if blank(quality) or blank(mill):
            p = self._parse_paper_search(c.get("Paper_Search_String"))
            quality = p.get("Quality") or quality
            gsm = p.get("GSM") if p.get("GSM") not in (None, "") else gsm
            mill = p.get("Mill") or mill
            finish = p.get("Finish") or finish
        return quality, gsm, mill, finish

    @staticmethod
    def _paper_by(chk):
        """ChkPaperByClient (desktop paperBy()): 'false' when Paper_By is blank/null
        or 'Self' (self supplies the paper); 'true' otherwise (client supplies it)."""
        s = "" if chk is None else str(chk).strip()
        if s == "" or s.lower() in ("null", "undefined"):
            return "false"
        return "false" if s.upper() == "SELF" else "true"

    @staticmethod
    def _label_lw(s):
        """For Label content, the dimension identifier 'L' becomes 'W' in the
        JobSize/JobCloseSize strings (e.g. 'L=275; H=95' -> 'W=275; H=95')."""
        if s is None:
            return s
        return _re.sub(r"L(\s*[=:])", r"W\1", str(s))

    def _content_size_values(self, c, refs, oper_ids) -> str:
        """Build the ContentSizeValues filter string in the exact key order the web
        ERP expects: Key=Value AndOr Key=Value ... (the ERP swaps 'AndOr' -> '&' to
        parse it as a query string). Values come from the desktop content columns
        with the shown defaults; a handful of keys are derived (OperId, JobPrePlan,
        Striping*, SizeBottomflapPer, CategoryID), and the Chk* keys are booleans."""
        derived = {
            "OperId": oper_ids or "",
            # AndOrJobPrePlan must match ProductMasterContentsSpecification.JobPrePlan
            # exactly (derived from the same builder), for consistency between tables.
            "JobPrePlan": self._spec_job_preplan(c),
            "Stripingleft": self._sum(c.get("Stripping_L"), c.get("Printing_Margin_L")),
            "Stripingright": self._sum(c.get("Stripping_R"), c.get("Printing_Margin_R")),
            "Stripingtop": self._sum(c.get("Stripping_T"), c.get("Printing_Margin_T")),
            "Stripingbottom": self._sum(c.get("Stripping_B"), c.get("Printing_Margin_B")),
            "SizeBottomflapPer": self._div(self._num(c.get("Bottom_Flap")) * 100,
                                           c.get("Job_Width")),
            "CategoryID": (refs or {}).get("CategoryID", 0),
            # MachineId inside the filter is the resolved WEB machine id (the web
            # UI preselects the machine from it), not the raw desktop Machine_ID.
            "MachineId": self._machine.resolve(c.get("Machine_ID"), required=False),
            # PlanContDomainType = ContentMaster.ContentDomainType matched by the
            # content type (PlanContentType) -> ContentName (None -> default below).
            "PlanContDomainType": resolve_content_domain_type(c.get("Orientation")),
            # PlanPlateBearer (=Color_Strip) and PlanColorStrip (=Wastage_Strip) must
            # match ProductMasterContentsSpecification (same values), for consistency.
            "PlanPlateBearer": self._plan_plate_bearer(c),
            "PlanColorStrip": self._plan_color_strip(c),
            # ChkPaperByClient per the desktop paperBy(Paper_By) rule.
            "ChkPaperByClient": self._paper_by(c.get("Paper_By")),
            # ChkPlanInSpecialSizePaper = Plan_On_Special_Size (0 when null), per desktop.
            "ChkPlanInSpecialSizePaper": (0 if c.get("Plan_On_Special_Size") is None
                                          else c.get("Plan_On_Special_Size")),
            # PlanGripper must match ProductMasterContentsSpecification.PlanGripper
            # (= Gripper_Main), for consistency.
            "PlanGripper": self._plan_gripper(c),
        }
        # ItemPlan* paper fields — desktop columns, or parsed from
        # Paper_Search_String when Paper_Quality/Mill is blank. Same values used in
        # ProductMasterContentsSpecification.
        _q, _g, _m, _f = self._paper_plan_fields(c)
        derived["ItemPlanQuality"] = _q
        derived["ItemPlanGsm"] = _g
        derived["ItemPlanMill"] = _m
        derived["ItemPlanFinish"] = _f
        if self._is_label(c):
            # Label: SizeWidth uses Job_Length; SizeLength is not populated (0).
            derived["SizeWidth"] = c.get("Job_Length")
            derived["SizeLength"] = 0
        elif self._is_rectangular(c):
            # Rectangular: SizeHeight=0, SizeLength=Job_Length, SizeWidth=Job_Height.
            derived["SizeHeight"] = 0
            derived["SizeLength"] = c.get("Job_Length")
            derived["SizeWidth"] = c.get("Job_Height")
        parts = []
        for key, col, default in CONTENT_SIZE_FIELDS:
            if key in derived:
                v = self._fmt_size_value(derived[key])
                if v is None or v == "":
                    v = default
            elif key in CONTENT_SIZE_BOOL_KEYS:
                raw = c.get(col) if col else None
                if raw is None or str(raw).strip() == "":
                    v = default
                else:
                    v = "true" if self._truthy(raw) else "false"
            else:
                v = self._fmt_size_value(c.get(col) if col else None)
                if v is None or v == "":
                    v = default
            parts.append(f"{key}={v}")
        return "AndOr".join(parts)

    def _content_cols(self, parent_row, c, product_id, pm_code, idx, total, refs=None):
        cols, vals = [], []

        def put(col, val):
            if col in cols:
                vals[cols.index(col)] = val      # never duplicate a column
            else:
                cols.append(col); vals.append(val)

        for s, t in self._content_map.items():
            put(t, c.get(s))
        # WastageSquareMeter / ScrapSquareMeter / TotalAmount rounded to 2 decimals.
        put("WastageSquareMeter", self._round2(c.get("Wastage_Square_Meter")))
        put("ScrapSquareMeter", self._round2(c.get("Scrap_Square_Meter")))
        put("TotalAmount", self._round2(c.get("Total_Amount")))
        # TotalRequired*/WastePerc/WastageKg rounded to 3 decimals.
        put("TotalRequiredSquareMeter", self._round3(c.get("Total_Square_Meter")))
        put("TotalRequiredRunningMeter", self._round3(c.get("Total_Running_Meter")))
        put("WastePerc", self._round3(c.get("Waste_In_Percentage")))
        put("WastageKg", self._round3(c.get("Paper_Wastage_In_Kg")))
        # Label: in JobSize/JobCloseSize the dimension identifier 'L' becomes 'W'.
        if self._is_label(c):
            put("JobSize", self._label_lw(c.get("Job_Size")))
            put("JobCloseSize", self._label_lw(c.get("Job_Close_Size")))
        # links + generated content no
        put("ProductMasterID", product_id)
        put("ProductMasterCode", pm_code)
        put("ProductMasterContentNo", f"{pm_code or ''}[{idx}_{total}]")
        # ContentSizeValues filter string (the ERP's content-size filter). OperId
        # inside it is the CSV of the content's resolved web ProcessIDs.
        oper_ids = self._content_oper_ids(self._k(c.get("Product_Content_ID")))
        put("ContentSizeValues", self._content_size_values(c, refs, oper_ids))
        # machine ref (optional)
        mid = self._machine.resolve(c.get("Machine_ID"), required=False)
        if mid is not None:
            put("MachineID", mid)
        pid = self._item.resolve(c.get("Paper_ID"), required=False)
        if pid is not None:
            put("PaperID", pid)
        # UnitPerPacking / Packing — ONLY when the content's domain type is OFFSET
        # (same PlanContDomainType stored in ProductMasterContentsSpecification).
        # Pull from the resolved paper's web ItemMaster row (ItemMaster.ItemID = PaperID):
        # UnitPerPacking <- ItemMaster.UnitPerPacking, Packing <- ItemMaster.PackingType.
        # For any other domain type these columns are left untouched.
        domain = resolve_content_domain_type(c.get("Orientation")) or "Offset"
        if pid is not None and str(domain).strip().upper() == "OFFSET":
            upp, ptype = self._packing_for_item(pid)
            put("UnitPerPacking", upp)
            put("Packing", ptype)
        # ---- derived Contents fields from the mapping sheet ----
        # FinalQuantityInPcs = Final_Quantity (a second web col fed by the same src)
        put("FinalQuantityInPcs", c.get("Final_Quantity"))
        # CylinderCircumferenceInch = Cutting_Cylinder_Circumference / 25.4
        put("CylinderCircumferenceInch", self._div(c.get("Cutting_Cylinder_Circumference"), 25.4))
        for col, v in self._context("ProductMasterContents").items():
            put(col, v)
        return cols, vals

    @staticmethod
    def _num(v):
        try:
            return float(v) if v not in (None, "") and str(v).strip() != "" else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _round4(v):
        """Round a numeric value to 4 decimals; pass through blanks/non-numerics."""
        if v is None or str(v).strip() == "":
            return v
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            return v

    @staticmethod
    def _round2(v):
        """Round a numeric value to 2 decimals; pass through blanks/non-numerics."""
        if v is None or str(v).strip() == "":
            return v
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return v

    @staticmethod
    def _round3(v):
        """Round a numeric value to 3 decimals; pass through blanks/non-numerics."""
        if v is None or str(v).strip() == "":
            return v
        try:
            return round(float(v), 3)
        except (TypeError, ValueError):
            return v

    @classmethod
    def _div(cls, a, b):
        b = cls._num(b)
        return round(cls._num(a) / b, 4) if b else None

    @classmethod
    def _sum(cls, *vals):
        return sum(cls._num(v) for v in vals)

    def _spec_job_preplan(self, c) -> str:
        """JobPrePlan string for ProductMasterContentsSpecification, matching the
        desktop app's jobpreplan(): base dimensions depend on the content type,
        then OF/BF/PF flap parts are appended only when > 0.
          Label       -> H:<Job_Height>;W:<Job_Length>;
          Rectangular -> L:<Job_Length>;W:<Job_Height>;
          other       -> H:<Job_Height>;L:<Job_Length>;W:<Job_Width>;
        then + OF:<Open_Flap>; BF:<Bottom_Flap>; PF:<Overlap_Flap>;  (each if > 0)."""
        def n(col):
            v = self._fmt_size_value(c.get(col))
            return v if (v is not None and v != "") else "0"
        # Format is driven by PlanContentType (desktop Orientation), NOT Content_Name
        # — so 'Front Label'/'Top Label'/... (all Orientation='Label') use the Label
        # form (W instead of L).
        name = (c.get("Orientation") or "").strip().lower()
        h, l, w = n("Job_Height"), n("Job_Length"), n("Job_Width")
        if name == "label":
            s = f"H:{h};W:{l};"
        elif name == "rectangular":
            s = f"L:{l};W:{h};"
        else:
            s = f"H:{h};L:{l};W:{w};"
        for prefix, col in (("OF", "Open_Flap"), ("BF", "Bottom_Flap"),
                            ("PF", "Overlap_Flap"), ("FH", "Flap_Height"),
                            ("TH", "Tongue_Height")):
            if self._num(c.get(col)) > 0:
                s += f"{prefix}:{n(col)};"
        return s

    def _spec_cols(self, c, product_id, content_id):
        """Build one ProductMasterContentsSpecification row for a content (per the
        mapping sheet): direct SPEC_MAP fields + sum/derived ones + links."""
        cols, vals = [], []

        def put(col, val):
            if not _has_column("ProductMasterContentsSpecification", col):
                return
            if col in cols:
                vals[cols.index(col)] = val
            else:
                cols.append(col); vals.append(val)

        for s, t in SPEC_MAP.items():
            put(t, c.get(s))
        # Label: SizeWidth uses Job_Length; SizeLength is not populated (NULL).
        if self._is_label(c):
            put("SizeWidth", c.get("Job_Length"))
            put("SizeLength", None)
        elif self._is_rectangular(c):
            # Rectangular: SizeHeight=0, SizeLength=Job_Length, SizeWidth=Job_Height.
            put("SizeHeight", 0)
            put("SizeLength", c.get("Job_Length"))
            put("SizeWidth", c.get("Job_Height"))
        # MachineId must be the WEB machine id, resolved from the desktop Machine_ID
        # via MachineMaster.RefMachineID — not the raw desktop id that SPEC_MAP copied.
        put("MachineId", self._machine.resolve(c.get("Machine_ID"), required=False))
        # JobPrePlan: desktop jobpreplan() formatted string (content-type aware + flaps).
        put("JobPrePlan", self._spec_job_preplan(c))
        # PlanContDomainType = ContentMaster.ContentDomainType matched by the content
        # type (PlanContentType/Orientation) -> ContentName (default 'Offset').
        put("PlanContDomainType", resolve_content_domain_type(c.get("Orientation")) or "Offset")
        # Striping* = Stripping_* + Printing_Margin_* (sum both, blanks = 0).
        put("Stripingleft", self._sum(c.get("Stripping_L"), c.get("Printing_Margin_L")))
        put("Stripingright", self._sum(c.get("Stripping_R"), c.get("Printing_Margin_R")))
        put("Stripingtop", self._sum(c.get("Stripping_T"), c.get("Printing_Margin_T")))
        put("Stripingbottom", self._sum(c.get("Stripping_B"), c.get("Printing_Margin_B")))
        # SizeBottomflapPer = (Bottom_Flap * 100) / Job_Width.
        put("SizeBottomflapPer", self._div(self._num(c.get("Bottom_Flap")) * 100,
                                           c.get("Job_Width")))
        # PlanPlateBearer = Color_Strip; PlanColorStrip = Wastage_Strip;
        # PlanGripper = Gripper_Main. Same values used in the ContentSizeValues
        # string (see _plan_plate_bearer / _plan_color_strip / _plan_gripper).
        put("PlanPlateBearer", self._plan_plate_bearer(c))
        put("PlanColorStrip", self._plan_color_strip(c))
        put("PlanGripper", self._plan_gripper(c))
        # ItemPlan* paper fields (desktop columns, or parsed from Paper_Search_String
        # when Paper_Quality/Mill is blank). Same values used in ContentSizeValues.
        _q, _g, _m, _f = self._paper_plan_fields(c)
        put("ItemPlanQuality", _q)
        put("ItemPlanGsm", _g)
        put("ItemPlanMill", _m)
        put("ItemPlanFinish", _f)
        # OperId: the process-id string for this content (resolved web ProcessIDs).
        put("OperId", self._content_oper_ids(self._k(c.get("Product_Content_ID"))))
        # links + context
        put("ProductMasterID", product_id)
        put("ProductMasterContentsID", content_id)
        for col, v in self._context("ProductMasterContentsSpecification").items():
            put(col, v)
        return cols, vals

    def _content_oper_ids(self, src_content):
        """Comma-separated web ProcessIDs for a content's operations (for OperId)."""
        ids = []
        for op in self._ops.get(src_content, []):
            pid = self._process.resolve(op.get("Operation_ID"), required=False)
            if pid is not None and str(pid) not in ids:
                ids.append(str(pid))
        return ",".join(ids)

    # Substrate item groups (Reel / Roll / Paper). A colour-detail item is never
    # one of these; when a desktop Material_ID collides with a substrate item that
    # shares the same RefItemID, we must pick the non-substrate row.
    _SUBSTRATE_ITEM_GROUPS = (2, 13, 14)

    def _load_color_item_map(self):
        """RefItemID -> (ItemID, ItemGroupID) for the item's NON-substrate ItemMaster
        row (group not in 2/13/14). One row per RefItemID (lowest ItemGroupID, then
        lowest ItemID) so ItemID and ItemGroupID always come from the same row."""
        if self._color_item_map is not None:
            return
        from core import db
        rows = db.query_web(
            "SELECT r, i, g FROM ("
            " SELECT RefItemID AS r, ItemID AS i, ItemGroupID AS g,"
            " ROW_NUMBER() OVER (PARTITION BY RefItemID ORDER BY ItemGroupID, ItemID) AS rn"
            " FROM ItemMaster"
            " WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0"
            " AND RefItemID IS NOT NULL AND ItemGroupID NOT IN (2,13,14)"
            ") x WHERE rn=1", [self.company_id])
        self._color_item_map = {self._k(row["r"]): (row["i"], row["g"]) for row in rows}

    def _resolve_color_item_id(self, material_id):
        """web ItemID of the colour item's non-substrate ItemMaster row (None if none)."""
        self._load_color_item_map()
        hit = self._color_item_map.get(self._k(material_id))
        return hit[0] if hit else None

    def _resolve_color_item_group(self, material_id):
        """web ItemGroupID of the colour item's non-substrate row (None if none)."""
        self._load_color_item_map()
        hit = self._color_item_map.get(self._k(material_id))
        return hit[1] if hit else None

    def _load_paper_item_map(self):
        """RefItemID -> (ItemID, ItemGroupID, PurchaseUnit, EstimationUnit) for the
        PAPER item's ItemMaster row, EXCLUDING the ink/material groups 3-8 (so a
        Paper_ID resolves to its substrate/paper item, not a colliding ink). One
        row per RefItemID (lowest ItemGroupID, then lowest ItemID)."""
        if self._paper_item_map is not None:
            return
        from core import db
        rows = db.query_web(
            "SELECT r, i, g, pu, eu FROM ("
            " SELECT RefItemID AS r, ItemID AS i, ItemGroupID AS g,"
            " PurchaseUnit AS pu, EstimationUnit AS eu,"
            " ROW_NUMBER() OVER (PARTITION BY RefItemID ORDER BY ItemGroupID, ItemID) AS rn"
            " FROM ItemMaster"
            " WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0"
            " AND RefItemID IS NOT NULL AND ItemGroupID NOT IN (3,4,5,6,7,8)"
            ") x WHERE rn=1", [self.company_id])
        self._paper_item_map = {
            self._k(row["r"]): (row["i"], row["g"], row["pu"], row["eu"]) for row in rows}

    def _resolve_paper_item(self, paper_id):
        """(ItemID, ItemGroupID, PurchaseUnit, EstimationUnit) for a paper item, or
        None. Group is never one of the ink/material groups 3-8."""
        self._load_paper_item_map()
        return self._paper_item_map.get(self._k(paper_id))

    def _packing_for_item(self, item_id):
        """(UnitPerPacking, PackingType) from the web ItemMaster for a resolved web
        ItemID; (None, None) if unknown. Cached per instance — one company-scoped
        query the first time it's needed."""
        cache = getattr(self, "_item_packing_cache", None)
        if cache is None:
            cache = {}
            for r in db.query_web(
                    "SELECT ItemID, UnitPerPacking, PackingType FROM ItemMaster "
                    "WHERE CompanyID=?", [self.company_id]):
                cache[self._k(r["ItemID"])] = (r.get("UnitPerPacking"),
                                               r.get("PackingType"))
            self._item_packing_cache = cache
        return cache.get(self._k(item_id), (None, None))

    def _paper_consumption_process_id(self, src_content):
        """ProcessID of this content's operation flagged Paper_Consumption_Required
        (the process the paper is consumed on); 0 if none."""
        for op in self._ops.get(src_content, []):
            if self._truthy(op.get("Paper_Consumption_Required")):
                pid = self._process.resolve(op.get("Operation_ID"), required=False)
                if pid:
                    return pid
        return 0

    @staticmethod
    def _map_color_spec(value):
        """Desktop Color_Specification label -> web ColorSpecification shorthand
        (Front/Back/Sp. Front/Sp. Back). Returns None for null/unrecognised values
        (e.g. "0") so nothing is written for those rows."""
        if value is None:
            return None
        key = _re.sub(r"\s+", " ", str(value).strip().lower()).replace("color", "colour")
        return COLOR_SPEC_MAP.get(key)

    def _matreq_row(self, table, fields, product_id, content_id):
        """Build a (cols, vals) ProductMasterProcessMaterialRequirement row from an
        explicit field dict, plus the product/content links and context; keeps only
        columns the target table actually has, and fits values to their width."""
        cols, vals = [], []

        def put(col, val):
            if not _has_column(table, col):
                return
            if col in cols:
                vals[cols.index(col)] = val
            else:
                cols.append(col); vals.append(val)

        for col, val in fields.items():
            put(col, val)
        put("ProductMasterID", product_id)
        put("ProductMasterContentsID", content_id)
        for col, v in self._context(table).items():
            put(col, v)
        vals, _ = _fit(self, table, cols, vals)
        return cols, vals

    def _simple_child(self, cmap, srcrow, table, links, refspec):
        cols, vals = [], []

        def put(col, val):
            if col in cols:
                vals[cols.index(col)] = val      # never duplicate a column
            else:
                cols.append(col); vals.append(val)

        for s, t in cmap.items():
            if _has_column(table, t):
                put(t, srcrow.get(s))
        # links/refspec take precedence over auto-mapped values (resolved FKs win)
        for t, v in links.items():
            if _has_column(table, t):
                put(t, v)
        for t, (rm, scol) in refspec.items():
            if _has_column(table, t):
                rid = rm.resolve(srcrow.get(scol), required=False)
                if rid is not None:
                    put(t, rid)
        for col, v in self._context(table).items():
            put(col, v)
        vals, _ = _fit(self, table, cols, vals)
        return cols, vals

    def after_insert(self, row, refs, product_id, cursor):
        self._existing.add(self._k(row.get("Product_Master_ID")))
        # After the product + all its children are inserted (same transaction),
        # create its JobBooking + JobApprovedCost and link everything by BookingID.
        self._create_job_booking(row, refs, product_id, cursor)

    # ---- JobBooking / JobApprovedCost -------------------------------------
    def _next_booking_no(self) -> int:
        """Next MaxBookingNo (company-wide series); BookingNo is '<n>.0'."""
        if self._max_booking_no is None:
            r = db.query_web(
                "SELECT ISNULL(MAX(ISNULL(MaxBookingNo,0)),0) AS m FROM JobBooking "
                "WHERE CompanyID=?", [self.company_id])
            self._max_booking_no = int(r[0]["m"]) if r else 0
        self._max_booking_no += 1
        return self._max_booking_no

    def _est_costs(self, estimate_id):
        """(Final_Cost, Type_Of_Cost) from the desktop Job_Estimation for an
        Estimate_ID; (None, None) if not found."""
        if self._job_estimation is None:
            self._job_estimation = {}
            try:
                for r in db.query_desktop(
                        "SELECT Estimate_ID AS e, Final_Cost AS fc, Type_Of_Cost AS tc "
                        "FROM Job_Estimation"):
                    self._job_estimation[self._k(r["e"])] = (r["fc"], r["tc"])
            except Exception:
                pass
        return self._job_estimation.get(self._k(estimate_id), (None, None))

    def _booking_id_tables(self):
        """Product-master tables (parent + children) that carry BOTH a BookingID and
        a ProductMasterID, so BookingID can be stamped on this product's rows."""
        if self._bk_tables is None:
            candidates = [
                "ProductMaster", "ProductMasterContents",
                "ProductMasterContentsSpecification", "ProductMasterProcess",
                "ProductMasterCorrugation", "ProductMasterProcessMaterialRequirement",
                "ProductMasterProcessMaterialParameterDetail",
                "ProductMasterProcessToolAllocation", "JobBookingColorDetails",
                "ProductMasterContentBookForms", "ProductMasterContentsLayerDetail",
                "ProductMasterOneTimeCharges"]
            self._bk_tables = [t for t in candidates
                               if _has_column(t, "BookingID") and _has_column(t, "ProductMasterID")]
        return self._bk_tables

    @staticmethod
    def _filtered_cols(table, fields):
        """(cols, vals) keeping only the fields that exist as columns on `table`."""
        cols, vals = [], []
        for col, val in fields.items():
            if _has_column(table, col):
                cols.append(col); vals.append(val)
        return cols, vals

    def _create_job_booking(self, row, refs, product_id, cursor):
        n = self._next_booking_no()
        booking_no = f"{n}.0"
        final_cost, type_of_cost = self._est_costs(row.get("Estimate_ID"))
        now = self._now

        # 1) JobBooking (auto BookingID). Copy the product's applicable fields.
        jb = {
            "MaxBookingNo": n, "BookingNo": booking_no,
            "RevisionNo": row.get("Revision_No") or 0,
            "JobName": row.get("Job_Name"),
            "LedgerID": refs.get("LedgerID"), "CompanyID": self.company_id,
            "CategoryID": refs.get("CategoryID"),
            "OrderQuantity": row.get("Order_Quantity"),
            "TypeOfCost": type_of_cost, "FinalCost": final_cost,
            "ProductCode": row.get("Product_Code"),
            "RefProductMasterCode": row.get("Ref_Product_Master_Code"),
            "ProductHSNID": refs.get("ProductHSNID"),
            "SalesEmployeeID": refs.get("SalesEmployeeID"),
            "ProductMasterID": product_id, "FYear": self.fyear,
            "CreatedBy": self.user_id, "CreatedDate": now,
            "ModifiedBy": self.user_id, "ModifiedDate": now,
            "BookingDate": now, "IsProductCatalog": 1, "IsBooked": 1,
            "IsApproved": 1, "IsDeletedTransaction": 0,
        }
        cols, vals = self._filtered_cols("JobBooking", jb)
        booking_id = engine.insert_parent_row(cursor, "JobBooking", "BookingID", cols, vals)

        # 2) JobApprovedCost — copy the applicable JobBooking data + costs. (This
        # table has no MaxBookingNo column; MaxApprovalNo holds the same number.)
        ja = {
            "BookingID": booking_id, "BookingNo": booking_no, "MaxApprovalNo": n,
            "JobName": row.get("Job_Name"), "OrderQuantity": row.get("Order_Quantity"),
            "FinalCost": final_cost, "UnitCost": final_cost, "QuotedFinalCost": final_cost,
            "TypeOfCost": type_of_cost, "LedgerID": refs.get("LedgerID"),
            "CategoryID": refs.get("CategoryID"), "CompanyID": self.company_id,
            "FYear": self.fyear, "UserID": self.user_id, "ModifiedDate": now,
            "ProductCode": row.get("Product_Code"), "IsProductMaster": 1,
            "IsDeletedTransaction": 0,
        }
        cols, vals = self._filtered_cols("JobApprovedCost", ja)
        engine.insert_one_row(cursor, "JobApprovedCost", cols, vals)

        # 3) Stamp the new BookingID on the product + all its related tables.
        for t in self._booking_id_tables():
            cursor.execute(
                f"UPDATE [{t}] SET BookingID=? WHERE ProductMasterID=?",
                [booking_id, product_id])


# value-fit helper that reuses the engine's truncation
def _fit(self, table, cols, vals):
    from core.engine import _fit_values
    return _fit_values(table, cols, vals)
