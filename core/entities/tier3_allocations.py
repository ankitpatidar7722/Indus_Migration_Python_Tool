"""
Tier 3 — allocation / rate / slab tables.

These resolve foreign keys to TWO already-migrated parents each, via the Ref*
columns the parents preserved:
  * ProcessID  <- ProcessMaster.RefProcessID   (desktop Operation_ID)
  * MachineID  <- MachineMaster.RefMachineID    (desktop Machine_ID)
  * LedgerID   <- LedgerMaster.RefLedgerID       (desktop Ledger_ID / Employee_ID)

So Tier-1 (Ledger) and Tier-2 (Process, Machine) must be migrated first.
"""

from __future__ import annotations

from core.mapping import MappedEntity, RefMap


def _process_ref(cid):  return RefMap("ProcessMaster", "RefProcessID", "ProcessID", company_id=cid)
def _machine_ref(cid):  return RefMap("MachineMaster", "RefMachineID", "MachineId", company_id=cid)
def _ledger_ref(cid):   return RefMap("LedgerMaster", "RefLedgerID", "LedgerID", company_id=cid)


class ProcessAllocatedMachineMigration(MappedEntity):
    name = "ProcessAllocatedMachineMaster"
    target_table = "ProcessAllocatedMachineMaster"
    target_identity = "ProcessMachineAllocationId"
    source_table = "Operation_Machine_Allocation_Master"
    name_field_source = "Operation_Machine_Allocation_ID"
    column_map = {
        "Machine_Speed": "MachineSpeed",
        "Make_Ready_Time": "MakeReadyTime",
        "Job_Change_Over_Time": "JobChangeOverTime",
        "Is_Default_Machine": "IsDefaultMachine",
    }
    extra_source_cols = ["Operation_ID", "Machine_ID"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ref_resolvers = {
            "ProcessID": (_process_ref(self.company_id), "Operation_ID", True),
            "MachineID": (_machine_ref(self.company_id), "Machine_ID", True),
        }

    def after_insert(self, row, refs, parent_id, cursor):
        # QA: ProcessMaster.AllocattedMachineID must be the CSV of MachineIDs
        # allocated to that process (built from ProcessAllocatedMachineMaster).
        # Rebuild it from the allocation rows so it's correct regardless of
        # insert order and free of duplicates/spaces.
        process_id = refs.get("ProcessID")
        if process_id is None:
            return
        cursor.execute(
            "SELECT MachineID FROM ProcessAllocatedMachineMaster "
            "WHERE ProcessID=? AND CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0 "
            "AND MachineID IS NOT NULL ORDER BY MachineID",
            [process_id, self.company_id])
        ids = [str(r[0]) for r in cursor.fetchall()]
        # de-dup, preserve order
        seen, uniq = set(), []
        for m in ids:
            if m not in seen:
                seen.add(m); uniq.append(m)
        csv = ",".join(uniq)
        cursor.execute(
            "UPDATE ProcessMaster SET AllocattedMachineID=? "
            "WHERE ProcessID=? AND CompanyID=?",
            [csv, process_id, self.company_id])


class ProcessSlabsMigration(MappedEntity):
    name = "ProcessSlabs"
    target_table = "ProcessMasterSlabs"
    target_identity = "SlabID"
    source_table = "Operation_Slab_Master"
    name_field_source = "Slab_ID"
    column_map = {
        "Rate": "Rate",
        "Slab_From": "FromQty",
        "Slab_To": "ToQty",
    }
    extra_source_cols = ["Operation_ID"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ref_resolvers = {
            "ProcessID": (_process_ref(self.company_id), "Operation_ID", True),
        }


class MachineSlabMigration(MappedEntity):
    name = "MachineSlabMaster"
    target_table = "MachineSlabMaster"
    target_identity = "PKSlabID"
    source_table = "Machine_Slab_Master"
    name_field_source = "Slab_ID"
    column_map = {
        "Sheet_Range_From": "SheetRangeFrom",
        "Sheet_Range_To": "SheetRangeTo",
        "Rate": "Rate",
        "Plate_Charges": "PlateCharges",
        "Wastage": "Wastage",
        "Flat_Rate": "FlatRate",
        "Flat_Wastage_Sheets": "FlatWastageSheets",
        "PS_Plate_Charges": "PSPlateCharges",
        "CTCP_Plate_Charges": "CTCPPlateCharges",
        "Coating_Charges": "CoatingCharges",
        "Special_Color_Front_Charges": "SpecialColorFrontCharges",
        "Special_Color_Back_Charges": "SpecialColorBackCharges",
        "Apply_As_Fixed_Charge": "ApplyAsFixedCharge",
        "SingleSide_PrintingRate": "SingleSidePrintingRate",
        "BothSide_PrintingRate": "BothSidePrintingRate",
        "P_ID": "PID",
        "Branch_ID": "BranchID",
        "Paper_Group": "PaperGroup",
        "Max_Plan_L": "MaxPlanL",
        "Max_Plan_W": "MaxPlanW",
        "Min_Charges": "MinCharges",
        "Running_Meter_Range_From": "RunningMeterRangeFrom",
        "Running_Meter_Range_To": "RunningMeterRangeTo",
        "Expected_Roll_Quantity": "ExpectedRollQuantity",
        "Avg_Each_Roll_Running_Meter": "AvgEachRollRunningMeter",
        "Process_Wastage_Percentage": "ProcessWastagePercentage",
    }
    extra_source_cols = ["Machine_ID"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ref_resolvers = {
            "MachineID": (_machine_ref(self.company_id), "Machine_ID", True),
        }


class MachineOnlineCoatingRatesMigration(MappedEntity):
    name = "MachineOnlineCoatingRates"
    target_table = "MachineOnlineCoatingRates"
    target_identity = "MachineOnlineCoatingID"
    source_table = "Machine_Online_Coating_Rates"
    name_field_source = "Coating_Name"
    column_map = {
        "Sheet_Range_From": "SheetRangeFrom",
        "Sheet_Range_To": "SheetRangeTo",
        "Coating_Name": "CoatingName",
        "Rate": "Rate",
        "Sort_Order_ID": "SortOrderID",
        "Basic_Coating_Charges": "BasicCoatingCharges",
    }
    extra_source_cols = ["Machine_ID"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ref_resolvers = {
            "MachineID": (_machine_ref(self.company_id), "Machine_ID", True),
        }


class ClientProcessCostSettingMigration(MappedEntity):
    name = "ClientProcessCostSetting"
    target_table = "ClientProcessCostSetting"
    target_identity = "ClientProcessRateSettingID"
    source_table = "Client_Operation_Slab_Master"
    name_field_source = "Trans_ID"
    column_map = {
        "Rate": "Rate",
        "Slab_From": "MinimumQuantityToBeCharged",
    }
    extra_source_cols = ["Operation_ID", "Ledger_ID"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ref_resolvers = {
            "ProcessID": (_process_ref(self.company_id), "Operation_ID", True),
            "LedgerID": (_ledger_ref(self.company_id), "Ledger_ID", True),
        }


class EmployeeMachineAllocationMigration(MappedEntity):
    name = "EmployeeMachineAllocation"
    target_table = "EmployeeMachineAllocation"
    target_identity = "ID"
    source_table = "Employee_Machine_Allocation_Master"
    name_field_source = "Employee_Machine_Allocation_ID"
    column_map = {
        "Employee_Machine_Allocation_ID": "EmployeeMachineAllocationID",
        "Branch_ID": "BranchID",
    }
    extra_source_cols = ["Employee_ID", "Machine_ID"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.ref_resolvers = {
            "LedgerID": (_ledger_ref(self.company_id), "Employee_ID", True),
            "MachineID": (_machine_ref(self.company_id), "Machine_ID", True),
        }

    def after_insert(self, row, refs, parent_id, cursor):
        # Store the CSV of every web MachineID allocated to this employee in
        # MachineIDString — on ALL of the employee's allocation rows (rebuilt so
        # it's correct regardless of insert order, de-duped, no spaces).
        ledger_id = refs.get("LedgerID")
        if ledger_id is None:
            return
        cursor.execute(
            "SELECT MachineID FROM EmployeeMachineAllocation "
            "WHERE LedgerID=? AND CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0 "
            "AND MachineID IS NOT NULL ORDER BY MachineID",
            [ledger_id, self.company_id])
        seen, uniq = set(), []
        for r in cursor.fetchall():
            m = str(r[0])
            if m not in seen:
                seen.add(m); uniq.append(m)
        csv = ",".join(uniq)
        cursor.execute(
            "UPDATE EmployeeMachineAllocation SET MachineIDString=? "
            "WHERE LedgerID=? AND CompanyID=?",
            [csv, ledger_id, self.company_id])
