"""
SparePartMaster and ToolMaster.

Both resolve ProductHSNID from the already-migrated ProductHSNMaster. Tool also
carries its desktop group id through to ToolGroupID and writes an "IsActive"
ToolMasterDetails EAV row. (Tool_Master may be empty in some desktop DBs; the
mapping is still defined so it works wherever data exists.)
"""

from __future__ import annotations

from core.mapping import MappedEntity, RefMap, ChildEAV


class SparePartMigration(MappedEntity):
    name = "SparePartMaster"
    target_table = "SparePartMaster"
    target_identity = "SparePartID"
    source_table = "Spare_Part_Master"
    name_field_source = "Spare_Name"
    name_field_target = "SparePartName"
    extra_source_cols = ["Product_Group_ID"]
    column_map = {
        "Spare_Name": "SparePartName",
        "Spare_Part_Code": "StockRefCode",
        "Spare_Group": "SparePartGroup",
        "Rate": "Rate",
        "Unit": "Unit",
        "Machine_ID": "MachineIDString",
        "Minimum_Stock_Level": "MinimumStockQty",
        "Purchase_Quantity": "PurchaseOrderQuantity",
        "Tally_Spare_Name": "TallyItemName",
        "HSN_Code": "HSNGroup",
    }

    def __init__(self, **kw):
        super().__init__(**kw)
        self._hsn = RefMap("ProductHSNMaster", "RefProductHSNID", "ProductHSNID",
                           company_id=self.company_id)
        self._max_no: int | None = None        # running MaxSparePartCode counter

    def _next_code_counter(self) -> int:
        """Next MaxSparePartCode, seeded from the target (prefix 'SP', single
        company-wide series — legacy ImportSparePartMasterData line 380)."""
        from core import db
        if self._max_no is None:
            r = db.query_web(
                "SELECT ISNULL(MAX(ISNULL(MaxSparePartCode,0)),0) AS MaxNo "
                "FROM SparePartMaster WHERE VoucherPrefix='SP' AND CompanyID=? "
                "AND ISNULL(IsDeletedTransaction,0)=0", [self.company_id])
            self._max_no = int(r[0]["MaxNo"]) if r else 0
        self._max_no += 1
        return self._max_no

    def resolve_refs(self, row):
        hsn = self._hsn.resolve(row.get("Product_Group_ID"), required=False)
        return {"ProductHSNID": hsn} if hsn is not None else {}

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        next_no = self._next_code_counter()
        # Fill the ERP code counters (legacy ImportSparePartMasterData line 380).
        for c, v in (("VoucherPrefix", "SP"), ("MaxSparePartCode", next_no)):
            if c in cols:
                vals[cols.index(c)] = v
            else:
                cols.append(c); vals.append(v)
        # The desktop spare code maps to StockRefCode, so the visible
        # SparePartCode is empty — generate SP##### so the grid shows it.
        code_i = cols.index("SparePartCode") if "SparePartCode" in cols else None
        cur_code = vals[code_i] if code_i is not None else None
        if cur_code is None or str(cur_code).strip() == "":
            gen = f"SP{next_no:05d}"
            if code_i is not None:
                vals[code_i] = gen
            else:
                cols.append("SparePartCode"); vals.append(gen)
        return cols, vals


# Map a desktop tool TYPE/group name to the web ToolGroupName it belongs in.
# Keyword-matched (nearest), since desktop "Offset Die" -> web "DIE", etc.
TOOL_GROUP_KEYWORDS = [
    ("flexo", "FLEXO DIE"),
    ("anilox", "ANILOX CYLINDER"),
    ("magnetic", "MAGNETIC CYLINDER"),
    ("emboss", "EMBOSS"),
    ("printing cylinder", "PRINTING CYLINDER"),
    ("die", "DIE"),
    ("plate", "PLATES"),
    ("block", "BLOCK"),
    ("cylinder", "PRINTING CYLINDER"),
]

# Cache of {company_id: {norm(ToolGroupName): ToolGroupID}} — ToolGroupMaster is a
# fixed reference table (9 groups), so it's loaded once per company.
_tool_group_id_cache: dict = {}


def resolve_tool_group_id(company_id, tool_category):
    """Desktop Tool_Category/type -> web ToolGroupMaster.ToolGroupID. Exact name
    match first, then keyword (Flexo Die->FLEXO DIE, 'EMBOSS BLOCK'->EMBOSS,
    Die->DIE, Plate->PLATES, ...). Returns None when nothing matches."""
    from core import db
    from core.mapping import _norm_name
    cache = _tool_group_id_cache.get(company_id)
    if cache is None:
        cache = {}
        for g in db.query_web(
                "SELECT ToolGroupID, ToolGroupName FROM ToolGroupMaster "
                "WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0", [company_id]):
            cache[_norm_name(g["ToolGroupName"])] = g["ToolGroupID"]
        _tool_group_id_cache[company_id] = cache
    tc = (tool_category or "").strip()
    if not tc:
        return None
    key = _norm_name(tc)
    if key in cache:
        return cache[key]
    low = tc.lower()
    for kw, gname in TOOL_GROUP_KEYWORDS:
        if kw in low:
            gid = cache.get(_norm_name(gname))
            if gid is not None:
                return gid
    return None

# Web ToolGroupID per tool group (resolved at runtime, hard ids for reference):
#   1 PLATES, 2 BLOCK, 3 DIE, 4 EMBOSS, 5 PRINTING CYLINDER, 6 ANILOX CYLINDER,
#   7 EMBOSSING CYLINDER, 8 FLEXO DIE, 9 MAGNETIC CYLINDER.
# Per-group field rules (QA). Two parts:
#   "cols"  : desktop source -> direct ToolMaster column
#   "eav"   : desktop source -> ToolMasterDetails field name (also added to
#             ToolGroupFieldMaster if missing)
# Applied on top of the base column_map; all groups also resolve LedgerName and
# write the ProductHSNName detail (= ProductHSNID).
TOOL_GROUP_FIELD_RULES = {
    8: {  # FLEXO DIE
        "cols": {"Circumference": "CircumferenceMM",
                 "Basic_Rate": "EstimationRate",
                 "Die_Type": "ToolType"},
        "eav": {"Gap_In_Row": "AroundGap",
                "Gap_In_Column": "AcrossGap",
                "Unit_Symbol": "UnitSymbol",
                "Product_Code": "ProductCode"},   # ProductCode added to field-master
    },
    9: {  # MAGNETIC CYLINDER
        "cols": {"Circumference": "CircumferenceMM",
                 "LPI": "LPI",
                 "Billion_Cubic_Microns": "BCM",
                 "Basic_Rate": "EstimationRate"},
        "eav": {"Unit_Symbol": "UnitSymbol"},
    },
    6: {  # ANILOX CYLINDER
        "cols": {"Billion_Cubic_Microns": "BCM"},
        "eav": {},
    },
    5: {  # PRINTING CYLINDER (CircumferenceInch derived from CircumferenceMM)
        "cols": {"Circumference": "CircumferenceMM"},
        "eav": {},
    },
    3: {  # DIE — only EAV flap fields (added to field-master)
        "cols": {},
        "eav": {"Pasting_Flap": "PastingFlap",
                "Open_Flap": "OpenFlap"},
    },
}


class ToolMigration(MappedEntity):
    name = "ToolMaster"
    target_table = "ToolMaster"
    target_identity = "ToolID"
    # Tool data lives in Tool_Master_Main (Tool_Master is empty in the desktop DB).
    source_table = "Tool_Master_Main"
    name_field_source = "Tool_Name"
    name_field_target = "ToolName"
    clear_child_tables = [("ToolMasterDetails", "ToolID")]
    extra_source_cols = [
        "Tool_Group_ID", "Tool_Type", "Product_Group_ID", "Client_ID",
        # per-group source fields used by TOOL_GROUP_FIELD_RULES
        "Circumference", "Gap_In_Row", "Gap_In_Column", "Die_Type", "Product_Code",
        "Billion_Cubic_Microns", "Pasting_Flap", "Open_Flap", "Unit_Symbol",
    ]
    child_eav = None            # detail rows built from ToolGroupFieldMaster below
    column_map = {
        "Tool_ID": "RefToolId",          # NEW: desktop Tool_Id reference
        "Tool_Name": "ToolName",
        "Job_Name": "JobName",
        "Tool_Type": "ToolType",
        "Tool_Code": "ToolCode",
        "Ups_Around": "UpsW",
        "Ups_Across": "UpsL",
        "Total_Ups": "TotalUps",
        "Basic_Rate": "PurchaseRate",
        "Purchase_Rate": "LastPurchaseRate",
        "Manufecturer": "Manufecturer",
        "Width": "SizeW",
        "Length": "SizeL",
        "Height": "SizeH",
        "Unit_Symbol": "StockUnit",
        "Purchase_Unit": "PurchaseUnit",
        "Remark": "Narration",
        "Is_Blocked": "IsBlocked",
        "No_Of_Teeth": "NoOfTeeth",
        "LPI": "LPI",
        "BCM": "BCM",
    }

    def __init__(self, subgroup=None, **kw):
        super().__init__(**kw)
        # subgroup = a web tool group id (int) to migrate only that group, or None = all.
        self.subgroup = subgroup
        self._hsn = RefMap("ProductHSNMaster", "RefProductHSNID", "ProductHSNID",
                           company_id=self.company_id)
        # LedgerName via the migrated client: RefLedgerID = desktop Client_ID.
        self._ledger_name = RefMap("LedgerMaster", "RefLedgerID", "LedgerName",
                                   company_id=self.company_id)
        self._tool_group_by_name: dict[str, int] = {}    # norm(name) -> ToolGroupID
        self._ensured_reftoolid = False
        # Per web ToolGroupID: the group's code prefix and a running MaxToolNo.
        # ToolCode stays the desktop code, but the ERP tool grid + next-number
        # logic read Prefix/MaxToolNo (mirrors legacy SaveDataToolMainMaster).
        self._grp_prefix: dict[int, str] = {}
        self._grp_max_no: dict[int, int] = {}

    def clear_group_filter(self):
        """Clear only the selected tool group's rows (subgroup = a web
        ToolGroupID). For 'Tool — All' (subgroup None) there's no group filter,
        so every tool group for the company is cleared."""
        if self.subgroup is not None:
            return "ToolGroupID=?", [self.subgroup]
        return "", []

    def _load_code_counters(self):
        from core import db
        if self._grp_prefix:
            return
        for g in db.query_web(
                "SELECT ToolGroupID, ISNULL(ToolGroupPrefix,'') AS Prefix "
                "FROM ToolGroupMaster WHERE ISNULL(IsDeletedTransaction,0)=0"):
            self._grp_prefix[g["ToolGroupID"]] = g["Prefix"]
        for m in db.query_web(
                "SELECT ToolGroupID, ISNULL(MAX(ISNULL(MaxToolNo,0)),0) AS MaxNo "
                "FROM ToolMaster WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0 "
                "GROUP BY ToolGroupID", [self.company_id]):
            self._grp_max_no[m["ToolGroupID"]] = int(m["MaxNo"])

    def _next_code_counter(self, gid: int) -> tuple[str, int]:
        self._load_code_counters()
        nxt = self._grp_max_no.get(gid, 0) + 1
        self._grp_max_no[gid] = nxt
        return self._grp_prefix.get(gid, ""), nxt

    def _load_tool_groups(self):
        from core import db
        from core.mapping import _norm_name
        if self._tool_group_by_name:
            return
        for g in db.query_web(
                "SELECT ToolGroupID, ToolGroupName FROM ToolGroupMaster "
                "WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0", [self.company_id]):
            self._tool_group_by_name[_norm_name(g["ToolGroupName"])] = g["ToolGroupID"]

    def _resolve_tool_group(self, row) -> int:
        """Desktop Tool_Type/group name -> web ToolGroupID. Exact name match
        first, then keyword (Offset Die->DIE, Plate/Block->PLATES). Falls back to
        the first web group when nothing matches (so a tool always has a group)."""
        from core.mapping import _norm_name
        self._load_tool_groups()
        ttype = (row.get("Tool_Type") or "").strip()
        key = _norm_name(ttype)
        if key in self._tool_group_by_name:
            return self._tool_group_by_name[key]
        low = ttype.lower()
        for kw, gname in TOOL_GROUP_KEYWORDS:
            if kw in low:
                gid = self._tool_group_by_name.get(_norm_name(gname))
                if gid is not None:
                    return gid
        # fallback: the lowest web ToolGroupID
        return min(self._tool_group_by_name.values()) if self._tool_group_by_name else 1

    def _ensure_reftoolid_column(self):
        """Add ToolMaster.RefToolId (bigint) if missing — holds the desktop
        Tool_Id (QA)."""
        if self._ensured_reftoolid:
            return
        from core import db
        from core.mapping import _has_column, _col_cache
        if not _has_column("ToolMaster", "RefToolId"):
            try:
                cur = db.get_web().cursor()
                cur.execute("ALTER TABLE ToolMaster ADD RefToolId BIGINT NULL")
                db.get_web().commit()
                _col_cache.pop("toolmaster", None)   # bust the column cache
                from core import engine
                engine.reset_schema_caches()         # bust the max-column cache too
            except Exception:
                db.get_web().rollback()
        self._ensured_reftoolid = True

    def read_source(self):
        self._ensure_reftoolid_column()
        rows = super().read_source()
        if self.subgroup is not None:
            rows = [r for r in rows
                    if self._resolve_tool_group(r) == self.subgroup]
        return rows

    def prepare_import(self):
        self._ensure_reftoolid_column()
        self._load_tool_groups()
        self._load_code_counters()

    def resolve_refs(self, row):
        hsn = self._hsn.resolve(row.get("Product_Group_ID"), required=False) \
            if "Product_Group_ID" in row else None
        refs = {}
        if hsn is not None:
            refs["ProductHSNID"] = hsn
        return refs

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        # Resolve the web ToolGroupID by NAME (desktop Tool_Type -> web group).
        gid = self._resolve_tool_group(row)
        self.group_id = gid
        if "ToolGroupID" not in cols:
            cols.append("ToolGroupID"); vals.append(gid)
        if "IsToolActive" not in cols:
            cols.append("IsToolActive"); vals.append(1)
        # Fill the ERP code counters + ToolCode (legacy SaveDataToolMainMaster
        # line 1226). Tool_Master has no code column, so ToolCode is generated
        # as <ToolGroupPrefix><MaxToolNo>; if a desktop code is ever present it
        # is kept.
        prefix, next_no = self._next_code_counter(gid)
        for c, v in (("Prefix", prefix), ("MaxToolNo", next_no)):
            if c in cols:
                vals[cols.index(c)] = v
            else:
                cols.append(c); vals.append(v)
        code_i = cols.index("ToolCode") if "ToolCode" in cols else None
        cur_code = vals[code_i] if code_i is not None else None
        if cur_code is None or str(cur_code).strip() == "":
            gen = f"{prefix}{next_no:05d}"
            if code_i is not None:
                vals[code_i] = gen
            else:
                cols.append("ToolCode"); vals.append(gen)

        def put(col, val):
            if col in cols:
                vals[cols.index(col)] = val
            else:
                cols.append(col); vals.append(val)

        # ---- per-group direct ToolMaster columns (QA) ----------------------
        rule = TOOL_GROUP_FIELD_RULES.get(gid, {})
        for src, tgt in rule.get("cols", {}).items():
            v = row.get(src)
            if v is not None and str(v).strip() != "":
                put(tgt, v)
        def r2(v):
            try:
                return round(float(v), 2) if v not in (None, "") and str(v).strip() != "" else v
            except (TypeError, ValueError):
                return v

        # QA: CircumferenceMM rounded to 2 decimals (Printing 5 / Magnetic 9 /
        # Flexo Die 8).
        if gid in (5, 9, 8) and "CircumferenceMM" in cols:
            i = cols.index("CircumferenceMM"); vals[i] = r2(vals[i])
        # QA: Flexo Die (8) SizeH rounded to 2 decimals (ToolMaster column;
        # the detail row is rounded in build_children).
        if gid == 8 and "SizeH" in cols:
            i = cols.index("SizeH"); vals[i] = r2(vals[i])
        # CircumferenceInch = CircumferenceMM / 25.4 (Printing 5 / Magnetic 9).
        if gid in (5, 9):
            mm = row.get("Circumference")
            try:
                if mm is not None and str(mm).strip() != "":
                    put("CircumferenceInch", round(float(mm) / 25.4, 4))
            except (TypeError, ValueError):
                pass
        # QA: Anilox (6) BCM rounded to 2 decimals.
        if gid == 6 and "BCM" in cols:
            i = cols.index("BCM"); vals[i] = r2(vals[i])

        # ---- LedgerName from the migrated client (RefLedgerID = Client_ID) ----
        lname = self._ledger_name.resolve(row.get("Client_ID"), required=False)
        if lname:
            put("LedgerName", lname)

        # ---- round PurchaseRate / EstimationRate to 2 decimals ----
        for c in ("PurchaseRate", "EstimationRate"):
            if c in cols:
                i = cols.index(c)
                vals[i] = r2(vals[i])
        return cols, vals

    def build_children(self, row, refs, parent_id):
        """ToolMasterDetails EAV rows from ToolGroupFieldMaster (Bucket D), plus
        the per-group EAV fields (gaps/unit/ProductCode/flaps) and the
        ProductHSNName detail (= ProductHSNID)."""
        from core.mapping import group_field_names, build_eav_detail_rows
        pcols, pvals = self.build_parent(row, refs)
        parent_map = dict(zip(pcols, pvals))
        gid = parent_map.get("ToolGroupID", self.group_id)

        def r2(v):
            try:
                return round(float(v), 2) if v not in (None, "") and str(v).strip() != "" else v
            except (TypeError, ValueError):
                return v

        # Inject the per-group EAV field VALUES into parent_map so the detail
        # builder picks them up (AroundGap/AcrossGap/UnitSymbol/ProductCode/
        # PastingFlap/OpenFlap, per group).
        rule = TOOL_GROUP_FIELD_RULES.get(gid, {})
        extra_fields = []
        for src, fld in rule.get("eav", {}).items():
            v = row.get(src)
            if fld == "AroundGap":                     # QA: Flexo AroundGap round 2
                v = r2(v)
            parent_map[fld] = v
            extra_fields.append(fld)
        # Flexo Die (8): EstimateRate detail = Basic_Rate; SizeH rounded to 2.
        if gid == 8:
            parent_map["EstimateRate"] = r2(row.get("Basic_Rate"))
            extra_fields.append("EstimateRate")
            if "SizeH" in parent_map:
                parent_map["SizeH"] = r2(parent_map["SizeH"])
        # ProductHSNName detail = ProductHSNID (all groups).
        hsn = parent_map.get("ProductHSNID")
        if hsn is None:
            hsn = refs.get("ProductHSNID")
        parent_map["ProductHSNName"] = hsn
        extra_fields.append("ProductHSNName")

        # Make sure ProductCode / PastingFlap / OpenFlap exist in the group's
        # ToolGroupFieldMaster (QA: "add to ToolGroupFieldMaster") — added once.
        self._ensure_tool_fields(gid, [f for f in rule.get("eav", {}).values()
                                       if f in ("ProductCode", "PastingFlap", "OpenFlap")])

        fields = group_field_names("ToolGroupFieldMaster", "ToolGroupID", gid)
        for f in extra_fields:
            if f not in fields:
                fields = fields + [f]
        cols, rows = build_eav_detail_rows(
            "ToolMasterDetails", "ToolID", "ToolGroupID", parent_id, gid,
            parent_map, fields, self.company_id, self.user_id, self.fyear,
            "IsActive", "1")
        return [("ToolMasterDetails", cols, rows)]

    _ensured_fields: set = None

    def _ensure_tool_fields(self, gid, fieldnames):
        """Add missing field definitions to ToolGroupFieldMaster for a group
        (QA: ProductCode for Flexo; PastingFlap/OpenFlap for Die)."""
        if not fieldnames:
            return
        from core import db
        if self._ensured_fields is None:
            self._ensured_fields = set()
        existing = {r["FieldName"] for r in db.query_web(
            "SELECT FieldName FROM ToolGroupFieldMaster WHERE ToolGroupID=? "
            "AND ISNULL(IsDeletedTransaction,0)=0", [gid])}
        seqrow = db.query_web(
            "SELECT ISNULL(MAX(FieldDrawSequence),0) mx FROM ToolGroupFieldMaster "
            "WHERE ToolGroupID=?", [gid])
        seq = int(seqrow[0]["mx"]) if seqrow and seqrow[0]["mx"] is not None else 0
        cur = db.get_web().cursor()
        added = False
        for f in fieldnames:
            key = (gid, f)
            if f in existing or key in self._ensured_fields:
                self._ensured_fields.add(key)
                continue
            seq += 1
            cur.execute(
                "INSERT INTO ToolGroupFieldMaster (ToolGroupID, FieldName, "
                "FieldDataType, FieldDescription, FieldDisplayName, FieldType, "
                "IsDisplay, IsCalculated, IsActive, FieldDrawSequence, FieldTabIndex, "
                "CompanyID, UserID, CreatedBy, ModifiedBy, IsDeletedTransaction) "
                "VALUES (?, ?, 'nvarchar(512)', ?, ?, 'text', 1, 0, 1, ?, ?, ?, ?, ?, ?, 0)",
                [gid, f, f, f, seq, seq, self.company_id, self.user_id,
                 self.user_id, self.user_id])
            self._ensured_fields.add(key)
            added = True
        if added:
            db.get_web().commit()
            # bust the field-name cache so the detail builder sees the new fields
            from core.mapping import _field_master_cache
            _field_master_cache.pop(("toolgroupfieldmaster", gid), None)
