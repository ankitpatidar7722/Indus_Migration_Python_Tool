"""
LedgerMaster migration — the first entity, and the reference pattern for the
rest. Reads `Ledger_Master` from the desktop DB and writes `LedgerMaster`
(+ a `LedgerMasterDetails` activity row) in the web DB.

This re-implements (correctly) what the old VB `SaveDataLedgerMaster` did:
  * map the desktop ledger group → the web LedgerGroupID,
  * generate a prefixed LedgerCode per group,
  * insert the ledger + its "ISLedgerActive" detail row atomically,
  * run the web SP `UpdateLedgerMasterValues` to populate derived columns.

Everything is parameterised; nothing is string-concatenated.
"""

from __future__ import annotations

from core import db
from core.engine import EntityMigration


# Desktop Under_Group_ID  ->  canonical web group name (from IndusPrint_To_Web_DataMigration SP).
# The web LedgerGroupID is then looked up by name from LedgerGroupMaster (ids differ between DBs).
DESKTOP_GROUP_TO_WEB_NAME = {
    24: "Sundry Debtors",
    23: "Sundry Creditors",
    27: "Employees",
    43: "DUTIES & TAXES",
    20: "Purchase Accounts",
    21: "Sales Accounts",
    26: "SUNDRY CREDITORS TRANSPORTERS",
}

# Sub-group definitions for selective migration. Each maps a UI label to:
#   under_group : desktop Under_Group_ID to read
#   where       : extra WHERE on Ledger_Master (flag-based splits), or ""
#   web_name    : forced web LedgerGroupMaster name (overrides DESKTOP_GROUP_TO_WEB_NAME)
# When subgroup is None the entity migrates ALL groups (the original behaviour).
LEDGER_SUBGROUPS = {
    "Client":        {"under_group": 24, "where": "ISNULL(Is_Consignee,0)=0", "web_name": "Sundry Debtors"},
    "Consignee":     {"under_group": 24, "where": "ISNULL(Is_Consignee,0)=1", "web_name": "Consignee Master"},
    "Supplier":      {"under_group": 23, "where": "ISNULL(Is_Vendor,0)=0",    "web_name": "Sundry Creditors"},
    "Vendor":        {"under_group": 23, "where": "ISNULL(Is_Vendor,0)=1",    "web_name": "Sundry Creditors(Vendors)"},
    "Transporter":   {"under_group": 26, "where": "", "web_name": "SUNDRY CREDITORS TRANSPORTERS"},
    "Duties & Taxes":{"under_group": 43, "where": "", "web_name": "DUTIES & TAXES"},
    "Purchase":      {"under_group": 20, "where": "", "web_name": "Purchase Accounts"},
    "Sales":         {"under_group": 21, "where": "", "web_name": "Sales Accounts"},
}

# Source column  ->  target LedgerMaster column. Only columns that exist on both
# sides and carry meaning are mapped; the rest are left to defaults / the calc SP.
COLUMN_MAP = {
    "Ledger_ID": "RefLedgerID",          # preserve desktop id for Tier-3 FK resolution
    "Ledger_Name": "LedgerName",
    "Ledger_Type": "LedgerType",
    "Mailing_Name": "MailingName",
    "Address1": "Address1",
    "Address2": "Address2",
    "Address3": "Address3",
    "City": "City",
    "District": "District",
    "State": "State",
    "Country": "Country",
    "PinCode": "Pincode",
    "Phone": "TelephoneNo",
    "Mobile": "MobileNo",
    "GSTIN": "GSTNo",
    "PAN": "PANNo",
    "Email": "Email",
    "Tax_Type": "TaxType",
    "GST_Ledger_Type": "GSTLedgerType",
    "Tax_Percentage": "TaxPercentage",
    "GST_Calculation_On": "GSTCalculationOn",
    "Website": "Website",
    "Fax": "FAX",
    "Currency_Code": "CurrencyCode",
    "Tally_Ledger_Name": "TallyLedgerName",
    "Is_GST_Applicable": "GSTApplicable",
    "Is_Blocked": "IsBlocked",
    "Inventry_Effect": "InventoryEffect",
    "Maintain_Billwise": "MaintainBillWise",
}

# Web LedgerType per web group name (the ERP's own vocabulary — QA wants these,
# not the raw desktop Ledger_Type). Lower-cased group name -> LedgerType.
LEDGER_TYPE_BY_GROUP = {
    "sundry debtors": "Sundry Debtors",
    "consignee master": "Consignee",
    "sundry creditors": "Suppliers",
    "sundry creditors(vendors)": "Vendors",
    "sundry creditors transporters": "Transporters",
    "purchase accounts": "Purchase A/C",
    "sales accounts": "Sales A/C",
    "duties & taxes": "Tax Ledger Master",
}
# Group whose ledgers are CLIENTS (get isClient/isLead/IsClientApproval = 1).
CLIENT_GROUP_NAME = "sundry debtors"

# EAV (LedgerMasterDetails) fields the ERP UI expects with a default value when
# the desktop carries none (QA). Field name -> default. These are also registered
# into LedgerGroupFieldMaster so the UI renders them.
LEDGER_EAV_DEFAULTS = {
    "GSTRegistrationType": "Unknown",
    "PartyType": "Not Applicable",
}

# Dynamic EAV fields: web FieldName -> desktop source column. Inserted as
# LedgerMasterDetails rows (value from the desktop column) and registered into
# LedgerGroupFieldMaster ONLY IF MISSING (no duplicate fields). These are the
# non-column fields the UI keeps in the EAV detail.
LEDGER_DYNAMIC_EAV = {
    "CreditDays":   "Credit_Days",
    "PaymentTerms": "Payment_Terms",
    "IFSCCode":     "IFSC_Code",
    "BankAcNo":     "Bank_Ac_No",
    "BranchName":   "Branch_Name",
    "BankName":     "Bank_Name",
}

# Direct LedgerMaster columns sourced from desktop (written in build_parent).
LEDGER_DYNAMIC_COLUMNS = {
    "DeliveredQtyTolerance": "Delivered_Qty_Tolerance",
    "MaxCreditLimit":        "Credit_Limit",
}


class LedgerMasterMigration(EntityMigration):
    name = "LedgerMaster"
    target_table = "LedgerMaster"
    target_identity = "LedgerID"
    clear_child_tables = [("LedgerMasterDetails", "LedgerID")]

    def clear_group_filter(self):
        """Clear only the selected subgroup's web LedgerGroupID(s). When
        migrating ALL ledgers, clear every group this tool migrates into."""
        from core import db
        # web group name(s) this run targets
        if self.subgroup:
            names = [LEDGER_SUBGROUPS[self.subgroup]["web_name"].lower()]
        else:
            names = [n.lower() for n in DESKTOP_GROUP_TO_WEB_NAME.values()]
            names += ["consignee master", "sundry creditors(vendors)"]
        rows = db.query_web(
            "SELECT LedgerGroupID, LOWER(LedgerGroupName) AS nm FROM LedgerGroupMaster "
            "WHERE ISNULL(IsDeletedTransaction,0)=0")
        gids = [r["LedgerGroupID"] for r in rows if r["nm"] in set(names)]
        if not gids:
            # No matching group → match nothing (don't wipe the whole table).
            return "1=0", []
        ph = ",".join("?" for _ in gids)
        return f"LedgerGroupID IN ({ph})", gids

    def __init__(self, company_id: int = 2, user_id: int = 1, fyear: str = "",
                 subgroup: str | None = None):
        self.company_id = company_id
        self.user_id = user_id
        self.fyear = fyear
        # subgroup = one of LEDGER_SUBGROUPS (selective migration), or None = all.
        self.subgroup = subgroup if subgroup in LEDGER_SUBGROUPS else None
        self._group_name_to_web_id: dict[str, int] = {}
        self._group_id_to_name: dict[int, str] = {}
        self._group_prefix: dict[int, str] = {}
        self._max_no: dict[int, int] = {}      # web LedgerGroupID -> running MaxLedgerNo
        self._existing: set[tuple[str, int]] = set()  # (lower(name), web group id)
        # Resolves a desktop Client_ID -> the migrated client's web LedgerID
        # (for consignees, which point at their parent client ledger).
        from core.mapping import RefMap
        self._client_ref = RefMap("LedgerMaster", "RefLedgerID", "LedgerID",
                                  company_id=company_id)

    # ---- setup -------------------------------------------------------------
    def _load_target_maps(self):
        """Load web ledger groups (name→id, id→prefix) and existing ledgers."""
        groups = db.query_web(
            "SELECT LedgerGroupID, LedgerGroupName, "
            "ISNULL(LedgerGroupPrefix,'') AS Prefix "
            "FROM LedgerGroupMaster WHERE ISNULL(IsDeletedTransaction,0)=0"
        )
        for g in groups:
            name = (g["LedgerGroupName"] or "").strip()
            self._group_name_to_web_id[name.lower()] = g["LedgerGroupID"]
            self._group_id_to_name[g["LedgerGroupID"]] = name
            self._group_prefix[g["LedgerGroupID"]] = g["Prefix"]

        # Seed running max-number per group from the target (so generated codes
        # continue from whatever is already there — idempotent & non-colliding).
        maxes = db.query_web(
            "SELECT LedgerGroupID, ISNULL(MAX(ISNULL(MaxLedgerNo,0)),0) AS MaxNo "
            "FROM LedgerMaster WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0 "
            "GROUP BY LedgerGroupID", [self.company_id]
        )
        for m in maxes:
            self._max_no[m["LedgerGroupID"]] = int(m["MaxNo"])

        # Existing (name, group) pairs for idempotent skip.
        existing = db.query_web(
            "SELECT LedgerName, LedgerGroupID FROM LedgerMaster "
            "WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0",
            [self.company_id]
        )
        for e in existing:
            self._existing.add(((e["LedgerName"] or "").strip().lower(),
                                e["LedgerGroupID"]))

    # ---- engine hooks ------------------------------------------------------
    def prepare_import(self):
        # The import worker uses a FRESH entity (no read_source). Reload the
        # group name/id maps so build_parent's per-group logic (LedgerType, etc.)
        # works on import, not just preview.
        if not self._group_id_to_name:
            self._load_target_maps()

    def read_source(self) -> list[dict]:
        self._load_target_maps()
        # extra desktop source columns for the dynamic EAV fields + columns
        extra = list(dict.fromkeys(
            list(LEDGER_DYNAMIC_EAV.values()) + list(LEDGER_DYNAMIC_COLUMNS.values())))
        extra = [c for c in extra if c not in COLUMN_MAP]
        cols = (", ".join(f"[{c}]" for c in COLUMN_MAP) +
                "".join(f", [{c}]" for c in extra) +
                ", [Under_Group_ID], [Ledger_Code], [Client_ID], "
                "ISNULL(Is_Consignee,0) AS Is_Consignee, "
                "ISNULL(Is_Vendor,0) AS Is_Vendor")
        if self.subgroup:
            sg = LEDGER_SUBGROUPS[self.subgroup]
            where = f"Under_Group_ID = {int(sg['under_group'])}"
            if sg["where"]:
                where += f" AND {sg['where']}"
            params: list = []
        else:
            group_ids = list(DESKTOP_GROUP_TO_WEB_NAME.keys())
            where = "Under_Group_ID IN (" + ", ".join("?" for _ in group_ids) + ")"
            params = group_ids
        sql = (f"SELECT {cols} FROM Ledger_Master "
               f"WHERE {where} AND ISNULL(Ledger_Name,'') <> '' "
               f"ORDER BY Under_Group_ID, Ledger_Name")
        rows = db.query_desktop(sql, params)
        self._extend_field_master(rows)
        return rows

    # Mapped columns that must NOT become LedgerGroupFieldMaster fields. IsBlocked
    # is a name-convention pair with the web active flag (Is_Blocked=1 -> the
    # ISLedgerActive detail is 'False'); the UI doesn't want it as its own field
    # (QA). The active state is carried by the ISLedgerActive detail instead.
    # IsBlocked: name-convention pair with the active flag (not a UI field).
    # RefLedgerID: internal FK backpointer — must NOT become a UI/EAV field.
    _NON_FIELD_TARGETS = {"IsBlocked", "RefLedgerID"}

    # QA: fields that must NOT be inserted into LedgerGroupFieldMaster for a given
    # web LedgerGroupID (they already exist there or are unwanted per group).
    #   1 Sundry Debtors (Clients), 2 Sundry Creditors (Supplier),
    #   4 Consignee Master, 7 Transporters, 8 Sundry Creditors(Vendors).
    _GROUP_FIELD_EXCLUDE = {
        1: {"PartyType", "FAX"},
        2: {"FAX", "GSTRegistrationType", "PartyType"},
        4: {"FAX", "GSTRegistrationType", "PartyType"},
        7: {"GSTRegistrationType", "PartyType"},
        8: {"FAX", "GSTRegistrationType", "PartyType"},
    }

    def _extend_field_master(self, rows):
        """Self-extend LedgerGroupFieldMaster: add any mapped field that has data
        but is missing from the field-master for its web group — once, up front,
        before the per-record transactions (so it isn't committed mid-record)."""
        from core.mapping import ensure_group_fields
        by_group: dict = {}
        for row in rows:
            try:
                gid = self._web_group_id(row)
            except ValueError:
                continue
            present = by_group.setdefault(gid, set())
            for src, tgt in COLUMN_MAP.items():
                if tgt in self._NON_FIELD_TARGETS:
                    continue
                v = row.get(src)
                if v is not None and str(v).strip() != "":
                    present.add(tgt)
            # Dynamic EAV fields (CreditDays, PaymentTerms, ...) — only register
            # the field when the desktop row actually has data for it.
            for fld, src in LEDGER_DYNAMIC_EAV.items():
                v = row.get(src)
                if v is not None and str(v).strip() != "":
                    present.add(fld)
            # QA EAV defaults (PartyType, GSTRegistrationType) always show.
            present.update(LEDGER_EAV_DEFAULTS.keys())
            # ensure_group_fields() only inserts fields that are MISSING, so
            # re-running never duplicates a dynamic field.
        for gid, fields in by_group.items():
            # QA: drop the per-group excluded fields (FAX/GSTRegistrationType/
            # PartyType) so they're never added to LedgerGroupFieldMaster.
            fields = fields - self._GROUP_FIELD_EXCLUDE.get(gid, set())
            ensure_group_fields("LedgerGroupFieldMaster", "LedgerGroupID", gid,
                                "LedgerMaster", sorted(fields), self.company_id,
                                self.user_id, self.fyear)

    def source_key(self, row: dict) -> str:
        return (row.get("Ledger_Name") or "?").strip()

    def _web_group_id(self, row: dict) -> int:
        # If a specific sub-group is selected, force its web group.
        if self.subgroup:
            web_name = LEDGER_SUBGROUPS[self.subgroup]["web_name"]
        else:
            under = row.get("Under_Group_ID")
            under = int(under) if under is not None else -1
            # When migrating ALL, still route the flag-based splits correctly:
            if under == 24 and row.get("Is_Consignee") in (1, True, "1"):
                web_name = "Consignee Master"
            elif under == 23 and row.get("Is_Vendor") in (1, True, "1"):
                web_name = "Sundry Creditors(Vendors)"
            else:
                web_name = DESKTOP_GROUP_TO_WEB_NAME.get(under)
        if not web_name:
            raise ValueError(f"Unmapped desktop group Under_Group_ID={row.get('Under_Group_ID')}")
        gid = self._group_name_to_web_id.get(web_name.lower())
        if gid is None:
            raise ValueError(f"Web LedgerGroupMaster has no group named '{web_name}'")
        return gid

    def already_migrated(self, row: dict) -> bool:
        try:
            gid = self._web_group_id(row)
        except ValueError:
            return False
        return ((self.source_key(row).lower(), gid) in self._existing)

    def resolve_refs(self, row: dict) -> dict:
        gid = self._web_group_id(row)             # validates group mapping
        prefix = self._group_prefix.get(gid, "")
        next_no = self._max_no.get(gid, 0) + 1
        self._max_no[gid] = next_no               # reserve it for this run
        ledger_code = f"{prefix}{next_no:05d}"
        return {
            "LedgerGroupID": gid,
            "LedgerCodePrefix": prefix,
            "MaxLedgerNo": next_no,
            "LedgerCode": ledger_code,
        }

    def build_parent(self, row: dict, refs: dict):
        cols: list[str] = []
        vals: list = []

        # Mapped business columns (G2: quote characters stripped from text).
        from core.mapping import to_sql_value
        for src, tgt in COLUMN_MAP.items():
            cols.append(tgt)
            vals.append(to_sql_value(row.get(src)))

        # Bucket-C corrections (MigrationIssue.txt), applied at insert time:
        #  * ISLedgerActive = 0 when desktop Is_Blocked = 1, else 1   (line 421)
        #  * SupplyTypeCode = 'B2B'                                   (line 518)
        #  * LedgerRefCode  = desktop Ledger_Code (keep same code)    (line 530)
        blocked = row.get("Is_Blocked")
        is_active = 0 if (blocked in (1, True, "1", "True")) else 1

        # Engine-generated / contextual columns. G1: CreatedDate/ModifiedDate =
        # current system date/time (the columns have no DB default).
        import datetime as _dt
        now = _dt.datetime.now()
        cols += ["LedgerGroupID", "LedgerCode", "LedgerCodePrefix", "MaxLedgerNo",
                 "CompanyID", "UserID", "FYear", "CreatedBy", "ModifiedBy",
                 "CreatedDate", "ModifiedDate",
                 "ISLedgerActive", "SupplyTypeCode", "LedgerRefCode",
                 "IsDeletedTransaction"]
        vals += [refs["LedgerGroupID"], refs["LedgerCode"], refs["LedgerCodePrefix"],
                 refs["MaxLedgerNo"], self.company_id, self.user_id, self.fyear,
                 self.user_id, self.user_id, now, now, is_active, "B2B",
                 row.get("Ledger_Code"), 0]

        group_name = (self._group_id_to_name.get(refs["LedgerGroupID"], "")).lower()

        def put(col, val):
            if col in cols:
                vals[cols.index(col)] = val
            else:
                cols.append(col); vals.append(val)

        # QA: LegalName = MailingName.
        put("LegalName", row.get("Mailing_Name"))
        # QA: Mobile '' -> default 0.
        mob = row.get("Mobile")
        if mob is None or str(mob).strip() == "":
            put("MobileNo", "0")
        # QA: LedgerType = the ERP's own per-group value (not the raw desktop type).
        lt = LEDGER_TYPE_BY_GROUP.get(group_name)
        if lt:
            put("LedgerType", lt)
        # QA: clients (Sundry Debtors) -> isClient / isLead / IsClientApproval = 1.
        if group_name == CLIENT_GROUP_NAME:
            put("isClient", 1)
            put("isLead", 1)
            put("IsClientApproval", 1)

        # G3: store the canonical Country/State from CountryStateMaster (matched
        # case-insensitively; unmatched -> keep the desktop value rather than blank
        # so we never lose data the master simply doesn't list).
        from core.mapping import resolve_country, resolve_state
        c = resolve_country(row.get("Country"), self.company_id)
        if c:
            put("Country", c)
        s = resolve_state(row.get("State"), self.company_id)
        if s:
            put("State", s)

        # Dynamic LedgerMaster COLUMNS sourced from desktop (DeliveredQtyTolerance,
        # MaxCreditLimit). EAV dynamic fields are handled in build_children.
        for tgt, src in LEDGER_DYNAMIC_COLUMNS.items():
            v = row.get(src)
            if v is not None and str(v).strip() != "":
                put(tgt, v)

        # Consignee (web group 4): link to its parent CLIENT by resolving the
        # desktop Client_ID to the migrated client's new web LedgerID (QA #465).
        consignee_gid = self._group_name_to_web_id.get("consignee master")
        if refs["LedgerGroupID"] == consignee_gid and row.get("Client_ID"):
            web_client = self._client_ref.resolve(row.get("Client_ID"), required=False)
            if web_client:
                cols.append("RefClientID"); vals.append(web_client)
        return cols, vals

    def build_children(self, row: dict, refs: dict, parent_id: int):
        """Write the LedgerMasterDetails (EAV) rows the web ERP expects: one per
        field defined in LedgerGroupFieldMaster for this group, value pulled from
        the main column — plus the ISLedgerActive flag. (Bucket D, at migration
        time.)"""
        from core.mapping import group_field_names, build_eav_detail_rows
        gid = refs["LedgerGroupID"]
        pcols, pvals = self.build_parent(row, refs)
        parent_map = dict(zip(pcols, pvals))
        from core.mapping import to_sql_value
        # Dynamic EAV field VALUES from the desktop source columns (CreditDays,
        # PaymentTerms, IFSCCode, BankAcNo, BranchName, BankName).
        for fld, src in LEDGER_DYNAMIC_EAV.items():
            v = row.get(src)
            if v is not None and str(v).strip() != "":
                parent_map[fld] = to_sql_value(v)
        # QA EAV defaults: ensure these UI fields show with a default value when
        # the desktop has none. GSTRegistrationType -> 'Unknown', PartyType ->
        # 'Not Applicable'. (Registered into LedgerGroupFieldMaster in read_source.)
        for fname, default in LEDGER_EAV_DEFAULTS.items():
            if not parent_map.get(fname):
                parent_map[fname] = default
        blocked = row.get("Is_Blocked")
        # QA: the UI checkbox reads the ISLedgerActive detail as the boolean
        # STRING 'True'/'False' (verified against native rows). Writing '1'/'0'
        # left the checkbox unticked even though the column was active.
        active = "False" if (blocked in (1, True, "1", "True")) else "True"
        fields = group_field_names("LedgerGroupFieldMaster", "LedgerGroupID", gid)
        # QA: don't emit a detail row for IsBlocked (not a UI field) even if a
        # prior run left it in the field-master.
        fields = [f for f in fields if f not in self._NON_FIELD_TARGETS]
        # include the default EAV fields even if the field-master misses them
        for f in LEDGER_EAV_DEFAULTS:
            if f not in fields:
                fields = fields + [f]
        # QA: never emit a detail row for a field excluded from this group's
        # field-master (FAX/GSTRegistrationType/PartyType per group).
        exclude = self._GROUP_FIELD_EXCLUDE.get(gid, set())
        if exclude:
            fields = [f for f in fields if f not in exclude]
        cols, rows = build_eav_detail_rows(
            "LedgerMasterDetails", "LedgerID", "LedgerGroupID", parent_id, gid,
            parent_map, fields, self.company_id, self.user_id, self.fyear,
            "ISLedgerActive", active)
        return [("LedgerMasterDetails", cols, rows)]

    def after_insert(self, row: dict, refs: dict, parent_id: int, cursor):
        # Web-side calc SP (kept from the old tool) — parameterised.
        cursor.execute("EXEC UpdateLedgerMasterValues @CompanyID=?, @LedgerID=?",
                       [self.company_id, parent_id])
        while cursor.nextset():
            pass
        # Record this as migrated so a same-session re-run won't duplicate it.
        self._existing.add((self.source_key(row).lower(), refs["LedgerGroupID"]))
