"""
Item masters: Paper / Reel / Roll / Other-Material.

All four migrate different desktop source tables into the SAME web ItemMaster
(+ an ItemMasterDetails "IsActive" EAV row), each scoped to its ItemGroupID.
They resolve ProductHSNID from the already-migrated ProductHSNMaster (by the
desktop Product_Group_ID preserved in RefProductHSNID).

Item group ids (web ItemGroupMaster): PAPER=14, REEL=2, ROLL=13, OTHER MATERIAL=8.
"""

from __future__ import annotations

from core.mapping import MappedEntity, RefMap, ChildEAV


ITEM_GROUP = {"PAPER": 14, "REEL": 2, "ROLL": 13, "OTHER MATERIAL": 8}


class _ItemBase(MappedEntity):
    target_table = "ItemMaster"
    target_identity = "ItemID"
    name_field_target = "ItemName"
    item_group_name = ""        # set per subclass
    # extra_source_cols is a property below (pulls the per-type rate/size columns)
    child_eav = None            # detail rows built from ItemGroupFieldMaster below
    clear_child_tables = [("ItemMasterDetails", "ItemID")]

    def clear_group_filter(self):
        # Clear only this item group's rows (Paper=14 / Reel=2 / Roll=13 / 8 ...).
        return "ItemGroupID=?", [self.group_id]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.group_id = ITEM_GROUP[self.item_group_name]
        self._hsn = RefMap("ProductHSNMaster", "RefProductHSNID", "ProductHSNID",
                           company_id=self.company_id)
        # Per web ItemGroupID: the group's code prefix and a running MaxitemNo.
        # The visible ItemCode stays the desktop code (per business decision), but
        # the ERP grids + next-number generation read ItemCodePrefix/MaxitemNo, so
        # we still populate those (mirrors legacy SaveDataItem, line 1029-1044).
        self._grp_prefix: dict[int, str] = {}
        self._grp_max_no: dict[int, int] = {}

    def _load_code_counters(self):
        """Seed each web group's ItemGroupPrefix and current MAX(MaxitemNo) so
        generated counters continue from the target without colliding."""
        from core import db
        if self._grp_prefix:
            return
        for g in db.query_web(
                "SELECT ItemGroupID, ISNULL(ItemGroupPrefix,'') AS Prefix "
                "FROM ItemGroupMaster WHERE ISNULL(IsDeletedTransaction,0)=0"):
            self._grp_prefix[g["ItemGroupID"]] = g["Prefix"]
        for m in db.query_web(
                "SELECT ItemGroupID, ISNULL(MAX(ISNULL(MaxitemNo,0)),0) AS MaxNo "
                "FROM ItemMaster WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0 "
                "GROUP BY ItemGroupID", [self.company_id]):
            self._grp_max_no[m["ItemGroupID"]] = int(m["MaxNo"])

    def _next_code_counter(self, gid: int) -> tuple[str, int]:
        """Return (ItemCodePrefix, next MaxitemNo) for a web ItemGroupID,
        reserving the number for this run."""
        self._load_code_counters()
        nxt = self._grp_max_no.get(gid, 0) + 1
        self._grp_max_no[gid] = nxt
        return self._grp_prefix.get(gid, ""), nxt

    # ItemMaster has CompanyID but dup check must also be within this item group.
    def _load_existing(self):
        rows = self._query_existing()
        self._existing = {(r["n"] or "").strip().lower() for r in rows}

    def _query_existing(self):
        from core import db
        return db.query_web(
            "SELECT ItemName AS n FROM ItemMaster "
            "WHERE CompanyID=? AND ItemGroupID=? AND ISNULL(IsDeletedTransaction,0)=0",
            [self.company_id, self.group_id])

    def resolve_refs(self, row):
        hsn = self._hsn.resolve(row.get("Product_Group_ID"), required=False)
        return {"ProductHSNID": hsn} if hsn is not None else {}

    # ----- per-item-type source columns for the estimation/size/packing fields
    #   item_type_value      : the web ItemType label for this group
    #   est_unit_source_col  : desktop column -> EstimationUnit  (the "rate type")
    #   est_rate_source_col  : desktop column -> EstimationRate  ("rate to charge")
    #   standard_source_col  : desktop column -> IsStandardItem  (Paper only)
    #   packing_source_col   : desktop column -> PackingType
    #   size_w_col/size_l_col: desktop size columns for the "W x L" ItemSize string
    item_type_value: str = ""
    est_unit_source_col: str = ""
    est_rate_source_col: str = ""
    standard_source_col: str = ""
    not_regular_source_col: str = ""        # desktop "Is_Not_Regular_*" flag
    packing_source_col: str = ""
    size_w_col: str = "Size_W"
    size_l_col: str = "Size_L"
    # Boolean flags that are written 1/0 to the ItemMaster column AND emitted as
    # True/False rows in ItemMasterDetails (QA: IsStandardItem / IsRegularItem).
    bool_detail_fields: list = []

    # subclasses can add more source columns they need in hooks (e.g. material
    # classification needs Under_Group_ID) without re-implementing the property.
    extra_source_cols_extra: list = []

    @property
    def extra_source_cols(self):
        # pull every per-type source column the corrections() below need
        base = ["Product_Group_ID"]
        for c in (self.est_unit_source_col, self.est_rate_source_col,
                  self.standard_source_col, self.not_regular_source_col,
                  self.packing_source_col, self.size_w_col, self.size_l_col,
                  *self.extra_source_cols_extra):
            if c and c not in base:
                base.append(c)
        return base

    @staticmethod
    def _truthy(v):
        return v in (1, True, "1", "True", "true", "Y", "y")

    def _item_size(self, row):
        """Build the 'W x L' ItemSize string from the desktop size columns.
        Returns None when there's no real size (both 0/blank), and just 'W' when
        only one dimension is present (e.g. roll width)."""
        def fmt(col):
            if not col:
                return ""
            x = row.get(col)
            if x is None or str(x).strip() == "":
                return ""
            try:
                f = float(x)
                if f == 0:
                    return ""                       # 0 is "no size"
                return str(int(f)) if f.is_integer() else str(f)
            except (TypeError, ValueError):
                return str(x).strip()
        ws, ls = fmt(self.size_w_col), fmt(self.size_l_col)
        if ws and ls:
            return f"{ws} x {ls}"
        if ws:
            return ws                               # single dimension (roll width)
        if ls:
            return ls
        return None                                 # no real size

    # ----- Bucket-C correction hook (applied at insert time, shown in preview)
    def corrections(self, row) -> dict:
        """Per-item target-column overrides. Common to all items:
          * ISItemActive = 0 when desktop Is_Blocked = 1, else 1
          * ItemCode     = desktop item code
          * ItemType     = the group's type label
          * EstimationUnit / EstimationRate from the per-type desktop columns
          * IsStandardItem from the per-type standard flag (Paper)
          * ItemSize = "W x L"; PackingType = desktop packing
        """
        out = {}
        blocked = row.get("Is_Blocked")
        out["ISItemActive"] = 0 if (blocked in (1, True, "1", "True")) else 1
        code = self._source_code(row)
        if code:
            out["ItemCode"] = code
        # ItemType per the selected group (Paper/Reel/Roll/...).
        if self.item_type_value:
            out["ItemType"] = self.item_type_value
        # EstimationUnit <- desktop "rate type"; EstimationRate <- "rate to charge".
        if self.est_unit_source_col and row.get(self.est_unit_source_col) is not None:
            out["EstimationUnit"] = row.get(self.est_unit_source_col)
        if self.est_rate_source_col and row.get(self.est_rate_source_col) is not None:
            out["EstimationRate"] = row.get(self.est_rate_source_col)
        # IsStandardItem / IsRegularItem flags (1/0 in the ItemMaster column;
        # also emitted as True/False detail rows via bool_detail_fields). Paper
        # has the source flags; others default (standard=0, regular=1).
        if "IsStandardItem" in self.bool_detail_fields:
            out["IsStandardItem"] = (
                1 if (self.standard_source_col and
                      self._truthy(row.get(self.standard_source_col))) else 0)
        elif self.standard_source_col:
            out["IsStandardItem"] = 1 if self._truthy(row.get(self.standard_source_col)) else 0
        if "IsRegularItem" in self.bool_detail_fields:
            # regular unless the desktop says "not regular".
            if self.not_regular_source_col:
                out["IsRegularItem"] = 0 if self._truthy(row.get(self.not_regular_source_col)) else 1
            else:
                out["IsRegularItem"] = 1
        # ItemSize "W x L" and PackingType.
        size = self._item_size(row)
        if size is not None:
            out["ItemSize"] = size
        if self.packing_source_col and row.get(self.packing_source_col) is not None:
            out["PackingType"] = row.get(self.packing_source_col)
        return out

    # subclasses set the desktop code column (Paper_Code / Reel_Code / ...)
    code_source_col: str = ""

    def _source_code(self, row):
        return row.get(self.code_source_col) if self.code_source_col else None

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        # Scope every item to its group.
        if "ItemGroupID" not in cols:
            cols.append("ItemGroupID"); vals.append(self.group_id)
        # Apply corrections (override existing cols or append new ones).
        for c, v in self.corrections(row).items():
            if c in cols:
                vals[cols.index(c)] = v
            else:
                cols.append(c); vals.append(v)
        # Fill the ERP code counters (visible ItemCode stays the desktop code).
        # The final group id may have been reclassified by corrections() above.
        gid = vals[cols.index("ItemGroupID")]
        prefix, next_no = self._next_code_counter(gid)
        for c, v in (("ItemCodePrefix", prefix), ("MaxitemNo", next_no)):
            if c in cols:
                vals[cols.index(c)] = v
            else:
                cols.append(c); vals.append(v)
        # Desktop item codes are always present, but guard against a blank one:
        # generate <ItemGroupPrefix><MaxitemNo> so the grid never shows an empty
        # code (legacy SaveDataItem always generated one).
        code_i = cols.index("ItemCode") if "ItemCode" in cols else None
        cur_code = vals[code_i] if code_i is not None else None
        if cur_code is None or str(cur_code).strip() == "":
            gen = f"{prefix}{next_no:05d}"
            if code_i is not None:
                vals[code_i] = gen
            else:
                cols.append("ItemCode"); vals.append(gen)
        return cols, vals
    # NOTE: legacy SaveDataItem (line 1070) also EXEC'd UpdateLedgerMasterValues
    # with the ItemID, but that SP ONLY updates LedgerMaster (filters on
    # LM.LedgerID) — for an item it is a no-op at best and could clobber a
    # ledger whose LedgerID happens to equal the ItemID. Deliberately NOT ported.

    # Self-extend the field-master ONCE up front (outside the per-record
    # transaction), based on which mapped fields actually have data in the source.
    def read_source(self):
        rows = super().read_source()
        self._extend_field_master(rows)
        return rows

    # QA: these must NOT become ItemGroupFieldMaster fields or ItemMasterDetails
    # rows — IsBlocked is the active-flag pair, ItemName/RefItemID are core item
    # identity, not UI EAV fields.
    _NON_FIELD_TARGETS = {"IsBlocked", "ItemName", "RefItemID"}
    # Extra EAV fields written as details with a default/derived value. Subclasses
    # override per type (e.g. Paper adds PaperGroup/CertificationType).
    eav_defaults: dict = {}
    # QA: when True, do NOT add any dynamic field to ItemGroupFieldMaster and emit
    # only the ISItemActive detail row (used by the material groups).
    skip_field_master: bool = False

    def _eav_default_values(self, row, refs, parent_map) -> dict:
        """EAV detail values common to all items: ProductHSNName = the resolved
        ProductHSNID (QA), plus any per-type eav_defaults."""
        out = dict(self.eav_defaults)
        hsn = parent_map.get("ProductHSNID")
        if hsn is None:
            hsn = refs.get("ProductHSNID")
        if hsn is not None:
            out["ProductHSNName"] = hsn          # QA: ProductHSNName = ProductHSNId
        return out

    def _extend_field_master(self, rows):
        if self.skip_field_master:
            return                          # QA: materials add no dynamic fields
        from core.mapping import ensure_group_fields
        # group present mapped target fields by the item group they'd land in
        by_group: dict = {}
        for row in rows:
            try:
                gid = self._row_group_id(row)
            except Exception:
                gid = self.group_id
            present = by_group.setdefault(gid, set())
            for src, tgt in self.column_map.items():
                if tgt in self._NON_FIELD_TARGETS:
                    continue                     # QA: exclude IsBlocked/ItemName/RefItemID
                v = row.get(src)
                if v is not None and str(v).strip() != "":
                    present.add(tgt)
            present.update(self.eav_defaults.keys())
            present.add("ProductHSNName")
        for gid, fields in by_group.items():
            ensure_group_fields("ItemGroupFieldMaster", "ItemGroupID", gid,
                                "ItemMaster", sorted(fields), self.company_id,
                                self.user_id, self.fyear)

    def _row_group_id(self, row):
        """The item group a row will land in (default = entity's fixed group;
        OtherMaterial overrides via classification)."""
        return self.group_id

    def _material_detail_fields(self, gid):
        """Return the exact allow-list of ItemMasterDetails fields for a material
        group, or None to use the default behaviour (non-materials)."""
        return None

    def build_children(self, row, refs, parent_id):
        """ItemMasterDetails EAV rows from ItemGroupFieldMaster for this item's
        group (Bucket D). The field-master was already extended in read_source,
        so the group's field set now includes every mapped field with data."""
        from core.mapping import group_field_names, build_eav_detail_rows
        pcols, pvals = self.build_parent(row, refs)
        parent_map = dict(zip(pcols, pvals))
        gid = parent_map.get("ItemGroupID", self.group_id)
        # Inject EAV default/derived values (ProductHSNName=ProductHSNID, and
        # per-type defaults like PaperGroup/CertificationType).
        for k, v in self._eav_default_values(row, refs, parent_map).items():
            parent_map[k] = v
        # The ERP item grid reads the active state from an EAV row named
        # 'ISItemActive' whose value is the boolean string 'True'/'False'
        # (verified against native rows; legacy SaveDataItem line 1065). Writing
        # 'IsActive'/'1' (as we did before) left the grid unable to see the item.
        is_active = parent_map.get("ISItemActive", 1) in (1, True, "1")
        active = "True" if is_active else "False"
        allow = self._material_detail_fields(gid)
        if allow is not None:
            # QA: materials write ONLY the allow-listed fields to ItemMasterDetails
            # (an exact per-group set). IsStandardItem/IsRegularItem come out as
            # True/False rows via bool_detail_fields, so drop them from here to
            # avoid duplicates. ProductHSNID's value is the resolved id.
            hsn = parent_map.get("ProductHSNID")
            if hsn is None:
                hsn = refs.get("ProductHSNID")
            parent_map["ProductHSNID"] = hsn
            fields = [f for f in allow if f not in self.bool_detail_fields]
        elif self.skip_field_master:
            # (materials with no explicit allow-list) write mapped fields w/ data.
            fields = []
            for src, tgt in self.column_map.items():
                if tgt in self._NON_FIELD_TARGETS or tgt in fields:
                    continue
                v = parent_map.get(tgt, row.get(src))
                if v is not None and str(v).strip() != "":
                    fields.append(tgt)
            for f in list(self.eav_defaults) + ["ProductHSNName"]:
                if f not in fields and parent_map.get(f) not in (None, ""):
                    fields.append(f)
        else:
            fields = group_field_names("ItemGroupFieldMaster", "ItemGroupID", gid)
            # QA: never emit detail rows for IsBlocked/ItemName/RefItemID, but DO
            # emit the EAV-default fields even if missing from the field-master.
            fields = [f for f in fields if f not in self._NON_FIELD_TARGETS]
            for f in list(self.eav_defaults) + ["ProductHSNName"]:
                if f not in fields:
                    fields = fields + [f]
        # Never let a bool_detail_field (IsStandardItem/IsRegularItem) come through
        # as a plain EAV row (value 0/1) — for Paper/Reel/Roll the group-field list
        # includes them, which previously produced a duplicate '0' row alongside the
        # 'True'/'False' one below. They are emitted ONCE, as True/False, right after.
        fields = [f for f in fields if f not in self.bool_detail_fields]
        cols, rows = build_eav_detail_rows(
            "ItemMasterDetails", "ItemID", "ItemGroupID", parent_id, gid,
            parent_map, fields, self.company_id, self.user_id, self.fyear,
            "ISItemActive", active)
        # QA: IsStandardItem/IsRegularItem also as True/False detail rows.
        for fld in self.bool_detail_fields:
            sval = "True" if parent_map.get(fld) in (1, True, "1") else "False"
            rows.append(self._extra_detail_row(cols, parent_id, gid, fld, sval,
                                               len(rows) + 1))
        return [("ItemMasterDetails", cols, rows)]

    def _extra_detail_row(self, cols, parent_id, gid, fname, fval, seq):
        """Build one ItemMasterDetails row matching the column order produced by
        build_eav_detail_rows (so we can append flag rows like IsStandardItem)."""
        base = {"ItemID": parent_id, "ItemGroupID": gid, "FieldName": fname,
                "FieldValue": fval, "ParentFieldName": fname, "ParentFieldValue": fval,
                "CompanyID": self.company_id, "UserID": self.user_id,
                "FYear": self.fyear, "CreatedBy": self.user_id,
                "ModifiedBy": self.user_id, "SequenceNo": seq, "IsActive": 1,
                "IsDeletedTransaction": 0}
        return [base.get(c) for c in cols]


class PaperMigration(_ItemBase):
    name = "PaperMaster"
    source_table = "Paper_Master"
    name_field_source = "Paper_Name"
    item_group_name = "PAPER"
    column_map = {
        "Paper_ID": "RefItemID",
        "Paper_Name": "ItemName",
        "Quality": "Quality",
        "GSM": "GSM",
        "Mill": "Manufecturer",
        "Unit_Per_Packing": "UnitPerPacking",
        "Wt_Per_Packing": "WtPerPacking",
        "Finish": "Finish",
        "Size_W": "SizeW",
        "Size_L": "SizeL",
        "Caliper": "Caliper",
        "Purchase_Quantity": "PurchaseOrderQuantity",
        "Minimum_Stock_Level": "MinimumStockQty",
        "Unit_Symbol": "StockUnit",
        "Paper_Code": "StockRefCode",
        "Tally_Paper_Name": "TallyItemName",
        "Purchase_Unit": "PurchaseUnit",
        "Paper_Group": "PaperGroup",
        "Purchase_Rate": "PurchaseRate",
        "Tax_Percentage": "GSTPercentage",
        "Is_Blocked": "IsBlocked",
        "Valid_Upto_Days": "ShelfLife",
    }
    code_source_col = "Paper_Code"          # ItemCode = desktop Paper_Code
    item_type_value = "PAPER"
    est_unit_source_col = "Rate_Type"           # EstimationUnit
    est_rate_source_col = "Rate_To_Charge"      # EstimationRate ("rate to charge")
    standard_source_col = "Is_Standard_Paper"   # IsStandardItem
    not_regular_source_col = "Is_Not_Regular_Paper"   # -> IsRegularItem (inverted)
    bool_detail_fields = ["IsStandardItem", "IsRegularItem"]
    packing_source_col = "Packing"              # PackingType
    # QA EAV defaults for paper: PaperGroup='Paper', CertificationType='None'.
    eav_defaults = {"PaperGroup": "Paper", "CertificationType": "None"}

    def corrections(self, row):
        out = super().corrections(row)
        out["StockUnit"] = "Sheet"          # paper StockUnit must be 'Sheet' (line 361)
        out["PaperGroup"] = "Paper"         # QA: PaperGroup = 'Paper' (constant)
        return out


class ReelMigration(_ItemBase):
    name = "ReelMaster"
    source_table = "Reel_Master"
    name_field_source = "Reel_Name"
    item_group_name = "REEL"
    column_map = {
        "Reel_ID": "RefItemID",
        "Reel_Name": "ItemName",
        "Quality": "Quality",
        "GSM": "GSM",
        "Caliper": "Caliper",
        "Finish": "Finish",
        "Mill": "Manufecturer",
        "Size_W": "SizeW",
        "Size_L": "SizeL",
        "Paper_Group": "PaperGroup",
        "Purchase_Quantity": "PurchaseOrderQuantity",
        "Minimum_Stock_Level": "MinimumStockQty",
        "Reel_Code": "StockRefCode",
        "Is_Blocked": "IsBlocked",
        "BF": "BF",
        "Purchase_Rate": "PurchaseRate",
        "Tax_Percentage": "GSTPercentage",
        "Reel_Catagory": "StockCategory",
        "Tally_Reel_Name": "TallyItemName",
        "OldReel_Id": "ExItemID",
        "Valid_Upto_Days": "ShelfLife",
    }
    code_source_col = "Reel_Code"           # ItemCode = desktop Reel_Code
    item_type_value = "REEL"
    est_unit_source_col = "PO_Rate_Type"        # EstimationUnit (reel uses PO_Rate_Type)
    est_rate_source_col = "Rate_To_Charge"      # EstimationRate
    # Reel has no desktop "standard" flag, so IsStandardItem defaults to False —
    # emitted as ONE True/False detail row (never a numeric 0/1), like Roll.
    bool_detail_fields = ["IsStandardItem"]


class RollMigration(_ItemBase):
    name = "RollMaster"
    source_table = "Roll_Master"
    name_field_source = "Roll_Name"
    item_group_name = "ROLL"
    column_map = {
        "Roll_ID": "RefItemID",
        "Roll_Name": "ItemName",
        "Roll_Code": "StockRefCode",
        "Quality": "Quality",
        "Thickness": "Thickness",
        "GSM_Release_Paper": "ReleaseGSM",
        "GSM_Adhesive": "AdhesiveGSM",
        "Width": "SizeW",
        "Minimum_Stock": "MinimumStockQty",
        "Valid_Upto_days": "ShelfLife",
        "Density": "Density",
        # QA (roll): PurchaseRate ← Basic_Rate; Manufacturer ← Mfg_by;
        # ManufecturerItemCode ← Item_Code. (EstimationRate ← Rate_To_Be_Charged
        # is set via est_rate_source_col below.)
        "Basic_Rate": "PurchaseRate",
        "Mfg_by": "Manufecturer",
        "Item_Code": "ManufecturerItemCode",
        "Is_Blocked": "IsBlocked",
        "Paper_Group": "PaperGroup",
        "Total_GSM": "TotalGSM",
        "Tally_Item_Name": "TallyItemName",
        # QA (roll): GSM ← GSM_Face_Paper; PurchaseOrderQuantity ← Purchase_Quantity.
        "GSM_Face_Paper": "GSM",
        "Purchase_Quantity": "PurchaseOrderQuantity",
    }
    code_source_col = "Roll_Code"
    # QA: ItemType comes from the desktop Roll_Type_Name (not a fixed label).
    item_type_value = ""
    est_unit_source_col = "Rate_Type"           # EstimationUnit
    est_rate_source_col = "Rate_To_Be_Charged"  # EstimationRate (roll spelling)
    size_w_col = "Width"                         # roll has only a width
    size_l_col = ""
    # Roll_Master has no standard/regular source flag -> defaults (standard 0,
    # regular 1); still emit the True/False detail rows (QA).
    bool_detail_fields = ["IsStandardItem", "IsRegularItem"]
    extra_source_cols_extra = ["Roll_Type_Name"]

    def corrections(self, row):
        out = super().corrections(row)
        # QA: ItemType from Roll_Type_Name, with mapping corrections:
        # 'Paper Roll' -> 'Paper', 'Film Roll' -> 'Film'; any other value kept as-is.
        rtn = row.get("Roll_Type_Name")
        if rtn:
            out["ItemType"] = {
                "paper roll": "Paper",
                "film roll": "Film",
            }.get(str(rtn).strip().lower(), rtn)
        # QA: round PurchaseRate and EstimationRate to 2 decimals.
        for fld, src in (("PurchaseRate", "Basic_Rate"),
                         ("EstimationRate", "Rate_To_Be_Charged")):
            v = row.get(src)
            try:
                if v is not None and str(v).strip() != "":
                    out[fld] = round(float(v), 2)
            except (TypeError, ValueError):
                pass
        # QA: round Density to 3 decimals (applies to both ItemMaster.Density and
        # its ItemMasterDetails EAV row, which build from this corrected value).
        dv = row.get("Density")
        try:
            if dv is not None and str(dv).strip() != "":
                out["Density"] = round(float(dv), 3)
        except (TypeError, ValueError):
            pass
        # QA: if PurchaseUnit/StockUnit empty, fall back to EstimationUnit.
        # Roll_Master has no purchase/stock unit column, so these are always
        # filled from the estimation unit (the desktop "rate type").
        est_unit = out.get("EstimationUnit") or row.get(self.est_unit_source_col)
        if est_unit:
            for unit_col in ("PurchaseUnit", "StockUnit"):
                cur = out.get(unit_col)
                if cur is None or str(cur).strip() == "":
                    out[unit_col] = est_unit
        return out


# Web ItemGroupID for the material sub-types that have their own group.
# Everything else falls through to OTHER MATERIAL (8).
MATERIAL_GROUP_BY_ROOT = {
    "ink": 3,           # INK & ADDITIVES
    "varnish": 4,       # VARNISHES & COATINGS
    "coating": 4,
    "lamination": 5,    # LAMINATION FILM
    "foil": 6,          # FOIL
}
# Web ItemType per item group (line 191 "Set ItemType as per the Group").
ITEM_TYPE_BY_GROUP = {3: "Ink", 4: "Varnish", 5: "Lamination", 6: "Foil", 8: "Other"}

# Material sub-type label -> the web ItemGroupID it classifies into. Used for
# selective migration (one dropdown entry per material sub-type).
MATERIAL_SUBGROUP_TO_GID = {
    "Ink": 3, "Varnish": 4, "Lamination": 5, "Foil": 6, "Other Material": 8,
}

# QA: EXACT set of ItemMasterDetails fields to write per material group (allow-
# list). ISItemActive is always written by the detail builder; ProductHSNID here
# means a detail row carrying the resolved ProductHSNID value.
MATERIAL_DETAIL_FIELDS = {
    # Ink (QA): EXACT ItemMasterDetails field set. ISItemActive is always written
    # by the detail builder; IsStandardItem/IsRegularItem come out as True/False
    # rows via bool_detail_fields. No field outside this list is inserted.
    3: ["EstimationRate", "EstimationUnit", "InkColour", "IsRegularItem",
        "IsStandardItem", "ItemSubGroupID", "ItemType", "LeadTime", "Manufecturer",
        "ManufecturerItemCode", "MinimumStockQty", "PantoneCode", "ProductHSNID",
        "ProductHSNName", "PurchaseOrderQuantity", "PurchaseRate", "PurchaseUnit",
        "ShelfLife", "StockRefCode", "StockType", "StockUnit"],
    # Varnish (QA): EXACT ItemMasterDetails field set. ISItemActive is always
    # written by the builder; IsStandardItem/IsRegularItem come out as True/False
    # rows. No field outside this list is inserted.
    4: ["ConsumptionUnit", "ConversionFactor", "Density", "EstimationRate",
        "EstimationUnit", "IsRegularItem", "IsStandardItem", "ItemSubGroupID",
        "ItemType", "LeadTime", "Manufecturer", "ManufecturerItemCode",
        "MinimumStockQty", "ProductHSNID", "ProductHSNName", "PurchaseOrderQuantity",
        "PurchaseRate", "PurchaseUnit", "Quality", "ShelfLife", "StockRefCode",
        "StockType", "StockUnit"],
    # Lamination (QA): EXACT ItemMasterDetails field set. ISItemActive is always
    # written by the builder; IsStandardItem/IsRegularItem come out as True/False
    # rows. No field outside this list is inserted.
    5: ["ConversionFactor", "Density", "EstimationRate", "EstimationUnit",
        "IsRegularItem", "IsStandardItem", "ItemSubGroupID", "Manufecturer",
        "ManufecturerItemCode", "MinimumStockQty", "ProductHSNID", "ProductHSNName",
        "PurchaseOrderQuantity", "PurchaseRate", "PurchaseUnit", "Quality",
        "ShelfLife", "SizeW", "StockRefCode", "StockType", "StockUnit", "Thickness"],
    # Foil (QA): EXACT ItemMasterDetails field set. ISItemActive is always written
    # by the builder; IsStandardItem/IsRegularItem come out as True/False rows.
    # No field outside this list is inserted.
    6: ["ConversionFactor", "Density", "EstimationRate", "EstimationUnit",
        "IsRegularItem", "IsStandardItem", "ItemSubGroupID", "Manufecturer",
        "ManufecturerItemCode", "MinimumStockQty", "ProductHSNID", "ProductHSNName",
        "PurchaseOrderQuantity", "PurchaseRate", "PurchaseUnit", "Quality",
        "ShelfLife", "SizeL", "SizeW", "StockRefCode", "StockType", "StockUnit",
        "Thickness"],
    # Other Material (QA): EXACT ItemMasterDetails field set. ISItemActive is
    # always written by the builder; IsStandardItem/IsRegularItem come out as
    # True/False rows. No field outside this list is inserted.
    8: ["ConversionFactor", "EstimationRate", "EstimationUnit", "IsRegularItem",
        "IsStandardItem", "ItemSubGroupID", "LeadTime", "Manufecturer",
        "ManufecturerItemCode", "MinimumStockQty", "ProductHSNID", "ProductHSNName",
        "PurchaseOrderQuantity", "PurchaseRate", "PurchaseUnit", "Quality",
        "ShelfLife", "StockRefCode", "StockType", "StockUnit"],
}


class OtherMaterialMigration(_ItemBase):
    name = "OtherMaterialMaster"
    source_table = "Material_Master"
    name_field_source = "Material_Name"
    item_group_name = "OTHER MATERIAL"      # default; reclassified per row below
    # QA: materials get an AUTO-GENERATED ItemCode (I/V/L/F/RM + counter), not the
    # desktop code — so code_source_col is empty (desktop code still -> StockRefCode).
    code_source_col = ""
    # QA: materials must NOT add dynamic fields to ItemGroupFieldMaster.
    skip_field_master = True
    # ItemType is set per classified group (Ink/Varnish/...) in corrections.
    extra_source_cols_extra = ["Under_Group_ID", "Material_Panton_Number",
                               "Material_Thickness", "Unit_Symbol", "Rate"]
    column_map = {
        "Material_ID": "RefItemID",
        "Material_Name": "ItemName",
        "Unit_Symbol": "StockUnit",
        "Purchase_Quantity": "PurchaseOrderQuantity",
        "Minimum_Stock_Level": "MinimumStockQty",
        "Material_Code": "StockRefCode",
        "Tally_Material_Name": "TallyItemName",
        "Is_Blocked": "IsBlocked",
        "Size_L": "SizeL",
        "Size_W": "SizeW",
        "Size_H": "SizeH",
        "Manufacturing_Company": "Manufecturer",
        "No_Of_Ply": "NoOfPly",
        "GSM": "GSM",
        "BF": "BF",
        "Density": "Density",
        "Material_Quality": "Quality",
        "Material_Catagory": "StockCategory",
        "Material_Type": "StockType",
        "Purchase_Rate": "PurchaseRate",
        "Tax_Percentage": "GSTPercentage",
        "Purchase_Unit": "PurchaseUnit",
        "OldMaterial_ID": "ExItemID",
        "Rate": "EstimationRate",
        "Valid_Upto_Days": "ShelfLife",
    }

    def __init__(self, subgroup: str | None = None, **kw):
        super().__init__(**kw)
        # subgroup = one of MATERIAL_SUBGROUP_TO_GID (selective), or None = all materials.
        self.subgroup = subgroup if subgroup in MATERIAL_SUBGROUP_TO_GID else None
        self._only_gid = MATERIAL_SUBGROUP_TO_GID.get(self.subgroup) if self.subgroup else None
        self._group_root: dict = {}   # Material_Group_ID -> root group name (lowercased)

    def clear_group_filter(self):
        # Materials span several groups (Ink/Varnish/Lamination/Foil/Other).
        # Clear only the selected sub-type's group, or all material groups when
        # migrating all materials.
        gids = [self._only_gid] if self._only_gid is not None \
            else list(MATERIAL_SUBGROUP_TO_GID.values())
        ph = ",".join("?" for _ in gids)
        return f"ItemGroupID IN ({ph})", list(gids)

    def _load_group_tree(self):
        """Build, for each material group, the chain of ancestor names (self
        first, up to the root). A material is classified by the NEAREST ancestor
        whose name matches a known sub-type (Ink/Varnish/Lamination/Foil)."""
        from core import db
        rows = db.query_desktop(
            "SELECT Material_Group_ID AS gid, ISNULL(Material_Group_Name,'') AS gname, "
            "ISNULL(Under_Group_ID,0) AS parent FROM Material_Group_Master")
        by_id = {r["gid"]: r for r in rows}

        def chain(gid, depth=0):
            r = by_id.get(gid)
            if r is None or depth > 20:
                return []
            names = [(r["gname"] or "").lower()]
            if r["parent"] and r["parent"] != gid and r["parent"] in by_id:
                names += chain(r["parent"], depth + 1)
            return names

        for gid in by_id:
            self._group_root[gid] = chain(gid)   # nearest-first ancestor names

    def before_import(self):
        """Migrate the desktop Material_Group_Master tree into ItemSubGroupMaster
        first (idempotent — existing sub-groups are skipped), so every material
        can resolve its proper ItemSubGroupID. Runs on real import only."""
        from core import engine
        from core.entities.material_group import MaterialGroupMigration
        from core.mapping import reset_subgroup_resolver
        engine.run_entity(MaterialGroupMigration(
            company_id=self.company_id, user_id=self.user_id, fyear=self.fyear))
        reset_subgroup_resolver()

    def prepare_import(self):
        # The import worker uses a FRESH entity (no read_source). Rebuild the
        # material group tree so classification (Ink/Varnish/...) and sub-group
        # resolution work on import, not just preview.
        if not self._group_root:
            self._load_group_tree()

    def read_source(self):
        self._load_group_tree()
        rows = super().read_source()
        if self._only_gid is not None:
            # Keep only materials that classify into the selected sub-type.
            rows = [r for r in rows if self._classify_group(r) == self._only_gid]
        return rows

    def _query_existing(self):
        # De-dup against the material group(s) this run targets, not just group 8.
        from core import db
        gids = [self._only_gid] if self._only_gid is not None \
            else list(MATERIAL_SUBGROUP_TO_GID.values())
        ph = ",".join("?" for _ in gids)
        return db.query_web(
            f"SELECT ItemName AS n FROM ItemMaster "
            f"WHERE CompanyID=? AND ItemGroupID IN ({ph}) "
            f"AND ISNULL(IsDeletedTransaction,0)=0",
            [self.company_id, *gids])

    # Materials write IsStandardItem/IsRegularItem as True/False detail rows.
    bool_detail_fields = ["IsStandardItem", "IsRegularItem"]

    def _material_detail_fields(self, gid):
        return MATERIAL_DETAIL_FIELDS.get(gid)

    def _row_group_id(self, row):
        # Materials reclassify into Ink/Varnish/Lamination/Foil/Other.
        return self._classify_group(row)

    def _classify_group(self, row) -> int:
        """Return the web ItemGroupID by scanning the material's ancestor group
        names nearest-first; first known sub-type wins, else OTHER MATERIAL (8)."""
        names = self._group_root.get(row.get("Under_Group_ID"), [])
        for name in names:                        # nearest ancestor first
            for key, gid in MATERIAL_GROUP_BY_ROOT.items():
                if key in name:
                    return gid
        return ITEM_GROUP["OTHER MATERIAL"]

    def corrections(self, row):
        from core.mapping import resolve_subgroup_id
        out = super().corrections(row)
        gid = self._classify_group(row)
        out["ItemGroupID"] = gid                       # route to correct group
        out["ItemType"] = ITEM_TYPE_BY_GROUP.get(gid, "Other")
        # Place the material in its proper sub-group: resolve its desktop group
        # (Under_Group_ID) to the web ItemSubGroupID by name. The desktop group
        # tree is migrated into ItemSubGroupMaster first, so this matches for all
        # material types (Convensional Ink, Chemicals, Printing Plates, ...).
        out["ItemSubGroupID"] = resolve_subgroup_id(row.get("Under_Group_ID"))

        # ---- per-group field rules (QA) ------------------------------------
        name = row.get("Material_Name")
        out["StockType"] = "JOB CONSUMABLES"           # all material groups
        out["EstimationUnit"] = row.get("Unit_Symbol")

        def r2(v):
            try:
                return round(float(v), 2) if v not in (None, "") else v
            except (TypeError, ValueError):
                return v

        if gid == 3:                                   # INK & ADDITIVES
            out["InkColour"] = name
            out["PantoneCode"] = row.get("Material_Panton_Number")
        elif gid == 4:                                 # VARNISHES & COATINGS
            out["Quality"] = name
            out["EstimationRate"] = row.get("Rate")
        elif gid == 5:                                 # LAMINATION FILM
            out["Thickness"] = row.get("Material_Thickness")
            out["EstimationRate"] = r2(row.get("Rate"))  # QA: round 2 decimals
            if name:
                out["Quality"] = name                  # keep prior lamination rule
        elif gid == 6:                                 # FOIL
            out["Quality"] = name
            out["EstimationRate"] = row.get("Rate")
        else:                                          # OTHER MATERIAL (8)
            out["Quality"] = name
            out["EstimationRate"] = row.get("Rate")
        return out
