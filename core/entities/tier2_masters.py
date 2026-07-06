"""
Tier 2 — dependent masters (need ids from Tier-1 entities already migrated).

FK resolution notes:
  * DepartmentID passes through unchanged: DepartmentMaster preserves the
    desktop Department_ID in its own DepartmentID column, so processes/machines
    just carry the same value (no remap).
  * ProductionUnitID is resolved BY NAME: ProductionUnitMaster stores the
    desktop name in RefProductionUnitName (it has no numeric ref id column).
"""

from __future__ import annotations

from core.mapping import MappedEntity, RefMap


def _prod_unit_refmap(company_id):
    # Resolve a desktop production-unit NAME to the migrated web ProductionUnitID.
    return RefMap("ProductionUnitMaster", "RefProductionUnitName",
                  "ProductionUnitID", company_id=None)


class WarehouseMigration(MappedEntity):
    name = "WarehouseMaster"
    target_table = "WarehouseMaster"
    target_identity = "WarehouseID"
    source_table = "Godown_Master"
    # QA warehouse mapping:
    #   WarehouseName    = Main_Godown_Name  (the parent "Main Godown")
    #   WarehouseBinName = Godown_Name       (the full combined godown name)
    #   BinName          = Bin_Name          (the bin within the godown)
    name_field_source = "Godown_Name"        # dup-check key = the unique full name
    name_field_target = "WarehouseBinName"
    column_map = {
        "Main_Godown_Name": "WarehouseName",
        "Godown_Name": "WarehouseBinName",
        "Bin_Name": "BinName",
        "Address": "Address",
        "City": "City",
        "Under_Company": "UnderCompany",
        "Location_X": "LocationX",
        "Location_Y": "LocationY",
        "Matrix_Size_Rows": "MatrixSizeRows",
        "Matrix_Size_Columns": "MatrixSizeColumns",
        "Tally_Godown_Name": "TallyWarehouseName",
        "Branch_ID": "BranchID",
        "Is_Blocked": "IsBlocked",
    }
    extra_source_cols = ["Production_Unit_ID", "Production_Unit_Name"]

    def __init__(self, **kw):
        super().__init__(**kw)
        self._pu = _prod_unit_refmap(self.company_id)
        self._max_no: int | None = None        # running MaxWarehouseCode

    def resolve_refs(self, row):
        # ProductionUnitID: resolve the desktop production unit by NAME via the
        # migrated ProductionUnitMaster; fall back to the raw Production_Unit_ID
        # when there's no name match (so the column is still populated).
        pu = self._pu.resolve(row.get("Production_Unit_Name"), required=False)
        if pu is None:
            pu = row.get("Production_Unit_ID")
        return {"ProductionUnitID": pu} if pu is not None else {}

    def _next_wh_no(self) -> int:
        from core import db
        if self._max_no is None:
            r = db.query_web(
                "SELECT ISNULL(MAX(ISNULL(MaxWarehouseCode,0)),0) AS MaxNo "
                "FROM WarehouseMaster WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0",
                [self.company_id])
            self._max_no = int(r[0]["MaxNo"]) if r else 0
        self._max_no += 1
        return self._max_no

    def build_parent(self, row, refs):
        from core.mapping import resolve_branch_id
        cols, vals = super().build_parent(row, refs)
        # QA: BranchID resolved from BranchMaster (desktop branch is empty/0).
        bid = resolve_branch_id(self.company_id, row.get("Branch_ID"))
        if bid is not None:
            if "BranchID" in cols:
                vals[cols.index("BranchID")] = bid
            else:
                cols.append("BranchID"); vals.append(bid)
        # Native warehouses use WarehouseCode 'WH00001' + WarehousePrefix/
        # MaxWarehouseCode; the desktop has no warehouse code, so generate them
        # (else the warehouse grid shows a blank code).
        next_no = self._next_wh_no()
        for c, v in (("WarehousePrefix", "WH"), ("MaxWarehouseCode", next_no)):
            if c in cols:
                vals[cols.index(c)] = v
            else:
                cols.append(c); vals.append(v)
        code_i = cols.index("WarehouseCode") if "WarehouseCode" in cols else None
        cur_code = vals[code_i] if code_i is not None else None
        if cur_code is None or str(cur_code).strip() == "":
            gen = f"WH{next_no:05d}"
            if code_i is not None:
                vals[code_i] = gen
            else:
                cols.append("WarehouseCode"); vals.append(gen)
        return cols, vals


class ProcessMigration(MappedEntity):
    name = "ProcessMaster"
    target_table = "ProcessMaster"
    target_identity = "ProcessID"
    source_table = "Operation_Master"
    name_field_source = "Operation_Name"
    name_field_target = "ProcessName"
    # If a process already exists by name, stamp RefProcessID on it so its
    # children (line clearance, inspection, slabs, ...) can still resolve it.
    backfill_ref = ("RefProcessID", "Operation_ID", "ProcessName")
    # QA: keep the real DepartmentID when non-zero; only 0 -> 100 (printing dept).
    normalize_department_id = False
    department_zero_to_100_only = True
    extra_source_cols = ["Module_Type"]
    column_map = {
        "Operation_ID": "RefProcessID",
        "Operation_Name": "ProcessName",
        "Type_of_Charges": "TypeofCharges",
        "Size_To_Be_Considered": "SizeToBeConsidered",
        "Rate": "Rate",
        "Minimum_Charges": "MinimumCharges",
        "Department_ID": "DepartmentID",           # passthrough (see module docstring)
        "Pre_Press": "PrePress",
        "Production_Mode": "ProductionMode",
        "Setup_Charges": "SetupCharges",
        "Is_Display": "IsDisplay",
        "Is_Display_Online": "IsDisplayOnline",
        "Charge_Apply_On_Sheets": "ChargeApplyOnSheets",
        "Display_Operation_Name": "DisplayProcessName",
        "Start_Unit": "StartUnit",
        "End_Unit": "EndUnit",
        "Unit_Conversion": "UnitConversion",
        "Minimum_L": "MinimumL",
        "Minimum_W": "MinimumW",
        "Maximum_L": "MaximumL",
        "Maximum_W": "MaximumW",
        "Power_Consumption": "PowerConsumption",
        "Speed": "Speed",
        "Is_Blocked": "IsBlocked",
        "Is_Default_Operation": "IsDefaultProcess",
        "Is_Gang": "IsGang",
        "Is_Form_Wise_Production": "IsFormWiseProduction",
        "Is_Gathering": "IsGathering",
        "Make_Ready_Time": "MakeReadyTime",
        "Avg_Machine_Speed": "AvgMachineSpeed",
        "Tool_Required": "ToolRequired",
        "Tool_Category": "ToolCategory",
        "Minimum_Quantity_To_Be_Charged": "MinimumQuantityToBeCharged",
        "Is_Combine_Contents_Binding": "IsCombineContents_Binding",
    }

    @staticmethod
    def _module_type(raw) -> str:
        """Map desktop Module_Type -> web ProcessModuleType:
          Label -> Flexo ; Common (or empty/blank) -> Universal ; Offset -> Offset ;
          any other value is kept as-is."""
        s = (raw or "").strip()
        low = s.lower()
        if low == "label":
            return "Flexo"
        if low == "common" or s == "":
            return "Universal"
        return s

    @staticmethod
    def _round3(v):
        """Round a numeric value to 3 decimals (QA: Rate / MinimumCharges)."""
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return v
        try:
            return round(float(v), 3)
        except (TypeError, ValueError):
            return v

    @staticmethod
    def _tool_required(v):
        return str(v).strip().lower() in ("1", "true", "yes", "y")

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        # QA: Rate and MinimumCharges rounded to 3 decimals.
        for c in ("Rate", "MinimumCharges"):
            if c in cols:
                vals[cols.index(c)] = self._round3(vals[cols.index(c)])
        # QA: ProcessModuleType — Label->Flexo, empty->Universal (else keep).
        mt = self._module_type(row.get("Module_Type"))
        if "ProcessModuleType" in cols:
            vals[cols.index("ProcessModuleType")] = mt
        else:
            cols.append("ProcessModuleType"); vals.append(mt)
        # QA: a process that requires a tool (Tool_Required=1) gets a ToolGroupID
        # resolved from its Tool_Category via ToolGroupMaster — the same value that
        # is also written into ProcessToolGroupAllocationMaster.
        if self._tool_required(row.get("Tool_Required")):
            from core.entities.spare_tool import resolve_tool_group_id
            from core.mapping import _has_column
            tgid = resolve_tool_group_id(self.company_id, row.get("Tool_Category"))
            if tgid is not None and _has_column("ProcessMaster", "ToolGroupID"):
                if "ToolGroupID" in cols:
                    vals[cols.index("ToolGroupID")] = tgid
                else:
                    cols.append("ToolGroupID"); vals.append(tgid)
        # QA: every process in DepartmentID 100 (printing dept) defaults to
        # ProcessProductionType='Printing', regardless of the desktop value.
        # DepartmentID is read AFTER super()/normalization (desktop 0 -> 100).
        from core.mapping import _has_column
        dept = vals[cols.index("DepartmentID")] if "DepartmentID" in cols else None
        try:
            is_dept_100 = int(dept) == 100
        except (TypeError, ValueError):
            is_dept_100 = False
        if is_dept_100 and _has_column("ProcessMaster", "ProcessProductionType"):
            if "ProcessProductionType" in cols:
                vals[cols.index("ProcessProductionType")] = "Printing"
            else:
                cols.append("ProcessProductionType"); vals.append("Printing")
        return cols, vals


class MachineMigration(MappedEntity):
    name = "MachineMaster"
    target_table = "MachineMaster"
    target_identity = "MachineId"
    source_table = "Machine_Master"
    name_field_source = "Machine_Name"
    name_field_target = "MachineName"
    # QA: keep the real DepartmentID when non-zero; only 0 -> 100 (printing dept).
    normalize_department_id = False
    department_zero_to_100_only = True
    # If a machine already exists by name, stamp RefMachineID on it so its
    # children (slab, coating, item-subgroup allocation) can resolve it.
    backfill_ref = ("RefMachineID", "Machine_Id", "MachineName")
    column_map = {
        "Machine_Id": "RefMachineID",
        "Machine_Name": "MachineName",
        "Minimum_Sheet": "MinimumSheet",
        "Gripper": "Gripper",
        "Max_Length": "MaxLength",
        "Max_Width": "MaxWidth",
        "Min_Length": "MinLength",
        "Min_Width": "MinWidth",
        "Max_Print_L": "MaxPrintL",
        "Max_Print_W": "MaxPrintW",
        "Min_Print_L": "MinPrintL",
        "Min_Print_W": "MinPrintW",
        "Colors": "Colors",
        "Make_Ready_Charges": "MakeReadyCharges",
        "Make_Ready_Wastage_Sheet": "MakeReadyWastageSheet",
        "Department_ID": "DepartmentID",           # passthrough
        "Machine_Type": "MachineType",
        "Make_Ready_Time": "MakeReadyTime",
        "Electric_Consumption": "ElectricConsumption",
        "Machine_Speed": "MachineSpeed",
        "Labour_Charges": "LabourCharges",
        "Charges_Type": "ChargesType",
        "Basic_Printing_Charges": "BasicPrintingCharges",
        "Job_Change_Over_Time": "JobChangeOverTime",
        "Plate_Length": "PlateLength",
        "Plate_Width": "PlateWidth",
        "Other_Charges": "OtherCharges",
        "Board_Thickness_Min": "BoardThicknessMin",
        "Board_Thickness_Max": "BoardThicknessMax",
        "Wastage_Type": "WastageType",
        "Wastage_Calculation_On": "WastageCalculationOn",
        "Is_Blocked": "IsBlocked",
        "Branch_ID": "BranchID",
        "Machine_Code": "MachineCode",
        "Plate_Charges": "PlateCharges",
        # --- full machine specs (added so nothing is silently dropped) ---
        "Printing_Margin": "PrintingMargin",
        "Web_CutOff_Size": "WebCutOffSize",
        "Min_Reel_Size": "MinReelSize",
        "Max_Reel_Size": "MaxReelSize",
        "Web_CutOff_Size_Min": "WebCutOffSizeMin",
        "Web_CutOff_Size_Max": "WebCutOffSizeMax",
        "Roundof_Impressions_With": "RoundofImpressionsWith",
        "Is_Perfecta_Machine": "IsPerfectaMachine",
        "Radius_Max": "RadiusMax",
        "Radius_Min": "RadiusMin",
        "Per_Hour_Cost": "PerHourCost",
        "Cost_Per_Hour": "CostPerHour",
        "Show_In_Schedule": "ShowInSchedule",
        "Electric_Consumption_Unit_Per_Minute": "ElectricConsumptionUnitPerMinute",
        "Average_Speed_Per_Hour": "AverageSpeedPerHour",
        "Current_Status": "CurrentStatus",
        "Min_Roll_Width": "MinRollWidth",
        "Max_Roll_Width": "MaxRollWidth",
        "Delam_Or_Relam": "DelamOrRelam",
        "Make_Ready_Wastage_Running_Meter": "MakeReadyWastageRunningMeter",
        "Avg_Break_Down_Time": "AvgBreakDownTime",
        "Roll_Change_Time": "RollChangeTime",
        "Avg_Break_Down_Running_Meters": "AvgBreakDownRunningMeters",
        "Machine_Paper_Length": "MachinePaperLength",
        "Machine_Width": "MachineWidth",
        "Average_Roll_Change_Wastage": "AverageRollChangeWastage",
        "Average_Roll_Length": "AverageRollLength",
        "Min_Circumference": "MinCircumference",
        "Max_Circumference": "MaxCircumference",
        "Speed_Running_Meters": "SpeedRunningMeters",
        "Variable_CutOff": "IsVariableCutOff",
    }

    # PrintingUnitID + Production_Unit_ID are real numeric FKs in source.
    extra_source_cols = ["Printing_Unit_ID", "Production_Unit_ID"]

    def build_parent(self, row, refs):
        cols, vals = super().build_parent(row, refs)
        if row.get("Printing_Unit_ID") and "PrintingUnitID" not in cols:
            cols.append("PrintingUnitID"); vals.append(row.get("Printing_Unit_ID"))

        # QA: migrate ProductionUnitID. Desktop Production_Unit_ID is 0 or 1; the
        # company's single production unit is id 1, so 0 -> 1 (default), else keep.
        try:
            pu = int(row.get("Production_Unit_ID") or 0)
        except (TypeError, ValueError):
            pu = 0
        pu = pu if pu > 0 else 1
        if "ProductionUnitID" in cols:
            vals[cols.index("ProductionUnitID")] = pu
        else:
            cols.append("ProductionUnitID"); vals.append(pu)

        # QA: BranchID resolved from BranchMaster (desktop branch is empty/0).
        from core.mapping import resolve_branch_id
        bid = resolve_branch_id(self.company_id, row.get("Branch_ID"))
        if bid is not None:
            if "BranchID" in cols:
                vals[cols.index("BranchID")] = bid
            else:
                cols.append("BranchID"); vals.append(bid)

        # Rule (QA #1303): a machine with desktop Department_ID=0 (Printing dept)
        # is a planning machine -> IsPlanningMachine = 1, else 0.
        try:
            is_planning = 1 if int(row.get("Department_ID") or 0) == 0 else 0
        except (TypeError, ValueError):
            is_planning = 1
        if "IsPlanningMachine" in cols:
            vals[cols.index("IsPlanningMachine")] = is_planning
        else:
            cols.append("IsPlanningMachine"); vals.append(is_planning)

        # Rule (QA #1318): all machines get CurrentStatus = 'ACTIVE'.
        if "CurrentStatus" in cols:
            vals[cols.index("CurrentStatus")] = "ACTIVE"
        else:
            cols.append("CurrentStatus"); vals.append("ACTIVE")

        # IsActive is always 1 for every migrated machine, regardless of desktop.
        if "IsActive" in cols:
            vals[cols.index("IsActive")] = 1
        else:
            cols.append("IsActive"); vals.append(1)

        # Rule (QA #1320): seed the print sizes from the machine sizes —
        # MaxPrintL/W from Max_Length/Width, MinPrintL/W from Min_Length/Width.
        for tgt, src in (("MaxPrintL", "Max_Length"), ("MaxPrintW", "Max_Width"),
                         ("MinPrintL", "Min_Length"), ("MinPrintW", "Min_Width")):
            v = row.get(src)
            if tgt in cols:
                vals[cols.index(tgt)] = v
            else:
                cols.append(tgt); vals.append(v)

        # Fill the ERP MaxMachineNo counter + MachineCode (legacy
        # ImportMachineMasterData line 881, prefix 'MM'). The desktop
        # Machine_Code is empty for every machine, so a blank MachineCode would
        # be invisible in the ERP grid — generate MM##### when it's empty, but
        # keep any real desktop code if one is ever present.
        next_no = self._next_machine_no()
        if "MaxMachineNo" in cols:
            vals[cols.index("MaxMachineNo")] = next_no
        else:
            cols.append("MaxMachineNo"); vals.append(next_no)
        code_i = cols.index("MachineCode") if "MachineCode" in cols else None
        cur_code = vals[code_i] if code_i is not None else None
        if cur_code is None or str(cur_code).strip() == "":
            gen = f"MM{next_no:05d}"
            if code_i is not None:
                vals[code_i] = gen
            else:
                cols.append("MachineCode"); vals.append(gen)
        return cols, vals

    _max_machine_no: int | None = None

    def _next_machine_no(self) -> int:
        """Next MaxMachineNo, seeded from the target (single company-wide series)."""
        from core import db
        if self._max_machine_no is None:
            r = db.query_web(
                "SELECT ISNULL(MAX(ISNULL(MaxMachineNo,0)),0) AS MaxNo "
                "FROM MachineMaster WHERE CompanyID=? AND ISNULL(IsDeletedTransaction,0)=0",
                [self.company_id])
            self._max_machine_no = int(r[0]["MaxNo"]) if r else 0
        self._max_machine_no += 1
        return self._max_machine_no
