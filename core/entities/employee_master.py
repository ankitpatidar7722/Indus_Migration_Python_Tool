"""
EmployeeMaster migration.

The desktop keeps employees in a dedicated `Employee_Master` table, but the web
ERP has NO employee table — employees are stored as Ledgers in the "Employees"
group (LedgerGroupID=3), which carries Designation / DepartmentID / Password /
DateOfBirth columns for exactly this purpose.

So this migrates `Employee_Master` -> `LedgerMaster` (group "Employees"). It
reuses LedgerMasterMigration's machinery (group prefix + LedgerCode generation,
the ISLedgerActive detail row, and the UpdateLedgerMasterValues calc SP) but
swaps in the employee source table, column map, and a fixed group.

This also unblocks EmployeeMachineAllocation (Tier 3), which resolves employees
via LedgerMaster.RefLedgerID.
"""

from __future__ import annotations

from core import db
from core.entities.ledger_master import LedgerMasterMigration

# Employee_Master (desktop)  ->  LedgerMaster (web) column map.
EMPLOYEE_MAP = {
    "Employee_ID": "RefLedgerID",          # preserve desktop id for FK resolution
    "Employee_Name": "LedgerName",
    "Mailing_Name": "MailingName",
    "Address1": "Address1",
    "Address2": "Address2",
    "Address3": "Address3",
    "City": "City",
    "State": "State",
    "Country": "Country",
    "PinCode": "Pincode",
    "Phone": "TelephoneNo",
    "CRM_Mobile_No": "MobileNo",
    "Email": "Email",
    "PAN": "PANNo",
    "Designation": "Designation",
    "Department_ID": "DepartmentID",
    "Password": "Password",
    "CRM_Birth_Date": "DateOfBirth",
    "Is_Blocked": "IsBlocked",
}

EMPLOYEE_GROUP_NAME = "Employees"


class EmployeeMasterMigration(LedgerMasterMigration):
    name = "EmployeeMaster"
    target_table = "LedgerMaster"
    target_identity = "LedgerID"

    def clear_group_filter(self):
        """Clear ONLY the Employees ledger group (LedgerGroupID=3). The inherited
        Ledger clear would, for Employee (subgroup=None), wipe every ledger group
        — deleting Clients (grp 1) and Suppliers/Vendors (grp 2). Employees must
        be cleared in isolation, leaving all other ledger groups untouched."""
        from core import db
        rows = db.query_web(
            "SELECT LedgerGroupID FROM LedgerGroupMaster "
            "WHERE LOWER(LedgerGroupName)=? AND ISNULL(IsDeletedTransaction,0)=0",
            [EMPLOYEE_GROUP_NAME.lower()])
        gids = [r["LedgerGroupID"] for r in rows]
        if not gids:
            return "1=0", []                # match nothing (never wipe the table)
        ph = ",".join("?" for _ in gids)
        return f"LedgerGroupID IN ({ph})", gids

    # ---- source read: Employee_Master (+ Job_Coordinator_Master) ----------
    def read_source(self):
        self._load_target_maps()
        # Resolve the web "Employees" group id once.
        self._emp_group_id = self._group_name_to_web_id.get(EMPLOYEE_GROUP_NAME.lower())
        cols = ", ".join(f"[{c}]" for c in EMPLOYEE_MAP)
        rows = db.query_desktop(
            f"SELECT {cols} FROM Employee_Master "
            f"WHERE ISNULL(Employee_Name,'') <> '' ORDER BY Employee_Name")
        # After the employees, ALSO migrate Job Coordinators into the same
        # Employees group (LedgerGroupID=3) with Designation forced to
        # 'Job Coordinator'. Their columns are aliased to the Employee_Master
        # names so the identical build_parent / mapping / dedup logic applies.
        # (Guarded: a desktop DB without Job_Coordinator_Master still migrates
        # employees.)
        try:
            rows += db.query_desktop(
                "SELECT Job_Coordinator_ID AS Employee_ID, "
                "Job_Coordinator_Name AS Employee_Name, Mailing_Name, "
                "Address1, Address2, Address3, City, State, Country, PinCode, "
                "Phone, Email, PAN, Is_Blocked, "
                "'Job Coordinator' AS Designation "
                "FROM Job_Coordinator_Master "
                "WHERE ISNULL(Job_Coordinator_Name,'') <> '' "
                "ORDER BY Job_Coordinator_Name")
        except Exception:
            pass
        return rows

    def source_key(self, row):
        return (row.get("Employee_Name") or "?").strip()

    # employees always go to the fixed Employees group
    def _web_group_id(self, row):
        if self._emp_group_id is None:
            raise ValueError("Web LedgerGroupMaster has no 'Employees' group")
        return self._emp_group_id

    # ---- parent build uses the employee column map ------------------------
    def build_parent(self, row, refs):
        import datetime as _dt
        from core.mapping import to_sql_value, resolve_country, resolve_state
        cols, vals = [], []
        for src, tgt in EMPLOYEE_MAP.items():
            cols.append(tgt)
            vals.append(to_sql_value(row.get(src)))     # G2: strip quotes
        # Map Is_Blocked -> ISLedgerActive (blocked=1 -> inactive 0, else 1).
        blocked = row.get("Is_Blocked")
        is_active = 0 if (blocked in (1, True, "1", "True")) else 1
        now = _dt.datetime.now()
        cols += ["LedgerGroupID", "LedgerCode", "LedgerCodePrefix", "MaxLedgerNo",
                 "CompanyID", "UserID", "FYear", "CreatedBy", "ModifiedBy",
                 "CreatedDate", "ModifiedDate", "ISLedgerActive", "IsDeletedTransaction"]
        vals += [refs["LedgerGroupID"], refs["LedgerCode"], refs["LedgerCodePrefix"],
                 refs["MaxLedgerNo"], self.company_id, self.user_id, self.fyear,
                 self.user_id, self.user_id, now, now, is_active, 0]
        # G3: canonical Country/State from CountryStateMaster (keep desktop if unmatched).
        c = resolve_country(row.get("Country"), self.company_id)
        if c and "Country" in cols:
            vals[cols.index("Country")] = c
        s = resolve_state(row.get("State"), self.company_id)
        if s and "State" in cols:
            vals[cols.index("State")] = s
        return cols, vals
