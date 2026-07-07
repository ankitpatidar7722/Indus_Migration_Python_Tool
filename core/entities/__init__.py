"""
Entity registry, organised as Module → Sub-module for the two-dropdown UI.

A MODULE is a top-level choice (Ledger, Item, Tool, Category, ...). A module may
have SUB-MODULES (Ledger → Client/Supplier/...; Item → Paper/Reel/Ink/...). The
UI shows a Sub-module dropdown only for modules that have them.

`MODULES` is the authoritative structure. A flat `REGISTRY` (entity-name ->
(factory, src_label, tgt_label)) is derived from it so the engine/`create()`
and "Migrate All" keep working unchanged.
"""

from core.entities.ledger_master import LedgerMasterMigration
from core.entities.tier1_masters import (
    CategoryMigration, ProductHSNMigration, DepartmentMigration,
    UnitMigration, ProductionUnitMigration,
)
from core.entities.tier2_masters import (
    WarehouseMigration, ProcessMigration, MachineMigration,
)
from core.entities.items import (
    PaperMigration, ReelMigration, RollMigration, OtherMaterialMigration,
)
from core.entities.spare_tool import SparePartMigration, ToolMigration
from core.entities.tier3_allocations import (
    ProcessAllocatedMachineMigration, ProcessSlabsMigration,
    MachineSlabMigration, MachineOnlineCoatingRatesMigration,
    ClientProcessCostSettingMigration, EmployeeMachineAllocationMigration,
)
from core.entities.product_master import ProductMasterMigration
from core.entities.employee_master import EmployeeMasterMigration
from core.entities.coa_parameters import COAParameterMigration
from core.entities.category_children import (
    ContentAllocationMigration, ProcessAllocationMigration,
    FGQCSamplingMigration, FGQCParameterMigration,
)
from core.entities.process_children import (
    LineClearanceMigration, InspectionParameterMigration,
    ToolGroupAllocationMigration,
)
from core.entities.machine_children import MachineItemSubGroupAllocationMigration
from core.entities.machine_tool_allocation import MachineToolAllocationMigration
from core.entities.flute_master import FluteMigration
from core.entities.tool_children import ToolQCParameterMigration
from core.entities.item_qc_parameters import (
    PaperQCMigration, ReelQCMigration, RollQCMigration, MaterialGroupQCMigration,
)
from core.entities.user_master import UserMasterMigration
from core.entities.material_group import MaterialGroupMigration


# A sub-module entry: (label, factory, source_label, target_label)
# A module: {"label", "submodules": [sub-entry, ...]}  (one sub-entry = no
# second dropdown; the module IS that single entity).
def _sub(label, factory, src, tgt):
    return (label, factory, src, tgt)


MODULES = [
    # ---- simple masters (no sub-module dropdown), in migration order ----
    {"label": "Production Unit", "submodules": [
        _sub("", lambda **kw: ProductionUnitMigration(**kw), "Production_Unit_Master", "ProductionUnitMaster")]},
    {"label": "Department", "submodules": [
        _sub("", lambda **kw: DepartmentMigration(**kw), "Department_Master", "DepartmentMaster")]},
    {"label": "Unit", "submodules": [
        _sub("", lambda **kw: UnitMigration(**kw), "Unit_Master", "UnitMaster")]},
    {"label": "Product HSN", "submodules": [
        _sub("", lambda **kw: ProductHSNMigration(**kw), "Product_Group_Master", "ProductHSNMaster")]},
    {"label": "Flute Master", "submodules": [
        _sub("", lambda **kw: FluteMigration(**kw), "Flute_Master", "FluteMaster")]},
    {"label": "Warehouse", "submodules": [
        _sub("", lambda **kw: WarehouseMigration(**kw), "Godown_Master", "WarehouseMaster")]},
    {"label": "Category", "submodules": [
        _sub("", lambda **kw: CategoryMigration(**kw), "Catagory_Master", "CategoryMaster")]},
    {"label": "Spare Part", "submodules": [
        _sub("", lambda **kw: SparePartMigration(**kw), "Spare_Part_Master", "SparePartMaster")]},
    {"label": "Process", "submodules": [
        _sub("", lambda **kw: ProcessMigration(**kw), "Operation_Master", "ProcessMaster")]},
    {"label": "Machine", "submodules": [
        _sub("", lambda **kw: MachineMigration(**kw), "Machine_Master", "MachineMaster")]},
    # Not a standalone module — migrates automatically as a child of Machine
    # (CHILDREN["Machine"]); hidden from the module dropdown.
    {"label": "Machine Tool Allocation", "hidden": True, "submodules": [
        _sub("", lambda **kw: MachineToolAllocationMigration(**kw), "Cylinder_Machine_Allocation", "MachineToolAllocationMaster")]},
    {"label": "Material Sub-Group", "submodules": [
        _sub("", lambda **kw: MaterialGroupMigration(**kw), "Material_Group_Master", "ItemSubGroupMaster")]},

    # ---- Item (sub-module dropdown) ----
    {"label": "Item", "submodules": [
        _sub("Paper",          lambda **kw: PaperMigration(**kw),          "Paper_Master",            "ItemMaster (grp 14)"),
        _sub("Reel",           lambda **kw: ReelMigration(**kw),           "Reel_Master",             "ItemMaster (grp 2)"),
        _sub("Ink",            lambda **kw: OtherMaterialMigration(subgroup="Ink", **kw),            "Material_Master (Ink)",        "ItemMaster (grp 3)"),
        _sub("Varnish",        lambda **kw: OtherMaterialMigration(subgroup="Varnish", **kw),        "Material_Master (Varnish)",    "ItemMaster (grp 4)"),
        _sub("Lamination",     lambda **kw: OtherMaterialMigration(subgroup="Lamination", **kw),     "Material_Master (Lamination)", "ItemMaster (grp 5)"),
        _sub("Foil",           lambda **kw: OtherMaterialMigration(subgroup="Foil", **kw),           "Material_Master (Foil)",       "ItemMaster (grp 6)"),
        _sub("Other Material", lambda **kw: OtherMaterialMigration(subgroup="Other Material", **kw), "Material_Master (Other)",      "ItemMaster (grp 8)"),
        _sub("Roll",           lambda **kw: RollMigration(**kw),           "Roll_Master",             "ItemMaster (grp 13)"),
        _sub("Material (All)", lambda **kw: OtherMaterialMigration(**kw),  "Material_Master (all)",   "ItemMaster (Ink/Varn/Lam/Foil/Other)"),
    ]},

    # ---- Ledger (sub-module dropdown; Employee is now a Ledger sub-group) ----
    {"label": "Ledger", "submodules": [
        _sub("All",            lambda **kw: LedgerMasterMigration(**kw),                          "Ledger_Master (all groups)",     "LedgerMaster"),
        _sub("Client",         lambda **kw: LedgerMasterMigration(subgroup="Client", **kw),       "Ledger_Master (Debtors)",        "LedgerMaster (grp 1)"),
        _sub("Supplier",       lambda **kw: LedgerMasterMigration(subgroup="Supplier", **kw),     "Ledger_Master (Creditors)",      "LedgerMaster (grp 2)"),
        _sub("Employee",       lambda **kw: EmployeeMasterMigration(**kw),                        "Employee_Master (+Job_Coordinator)", "LedgerMaster (Employees, grp 3)"),
        _sub("Consignee",      lambda **kw: LedgerMasterMigration(subgroup="Consignee", **kw),    "Ledger_Master (Is_Consignee=1)", "LedgerMaster (grp 4)"),
        _sub("Vendor",         lambda **kw: LedgerMasterMigration(subgroup="Vendor", **kw),       "Ledger_Master (Is_Vendor=1)",    "LedgerMaster (grp 8)"),
        _sub("Transporter",    lambda **kw: LedgerMasterMigration(subgroup="Transporter", **kw),  "Ledger_Master (Transporters)",   "LedgerMaster (grp 7)"),
        _sub("Duties & Taxes", lambda **kw: LedgerMasterMigration(subgroup="Duties & Taxes", **kw), "Ledger_Master (Duties & Taxes)", "LedgerMaster (grp 5)"),
        _sub("Purchase",       lambda **kw: LedgerMasterMigration(subgroup="Purchase", **kw),     "Ledger_Master (Purchase A/c)",   "LedgerMaster (grp 6)"),
        _sub("Sales",          lambda **kw: LedgerMasterMigration(subgroup="Sales", **kw),        "Ledger_Master (Sales A/c)",      "LedgerMaster (grp 9)"),
    ]},

    # ---- Tool (sub-module dropdown — the 9 ToolGroupMaster groups) ----
    {"label": "Tool", "submodules": [
        _sub("All",                 lambda **kw: ToolMigration(**kw),                "Tool_Master (all)",  "ToolMaster"),
        _sub("Plates",              lambda **kw: ToolMigration(subgroup=1, **kw),    "Tool_Master",        "ToolMaster (grp 1)"),
        _sub("Block",               lambda **kw: ToolMigration(subgroup=2, **kw),    "Tool_Master",        "ToolMaster (grp 2)"),
        _sub("Die",                 lambda **kw: ToolMigration(subgroup=3, **kw),    "Tool_Master",        "ToolMaster (grp 3)"),
        _sub("Emboss",              lambda **kw: ToolMigration(subgroup=4, **kw),    "Tool_Master",        "ToolMaster (grp 4)"),
        _sub("Printing Cylinder",   lambda **kw: ToolMigration(subgroup=5, **kw),    "Tool_Master",        "ToolMaster (grp 5)"),
        _sub("Anilox Cylinder",     lambda **kw: ToolMigration(subgroup=6, **kw),    "Tool_Master",        "ToolMaster (grp 6)"),
        _sub("Embossing Cylinder",  lambda **kw: ToolMigration(subgroup=7, **kw),    "Tool_Master",        "ToolMaster (grp 7)"),
        _sub("Flexo Die",           lambda **kw: ToolMigration(subgroup=8, **kw),    "Tool_Master",        "ToolMaster (grp 8)"),
        _sub("Magnetic Cylinder",   lambda **kw: ToolMigration(subgroup=9, **kw),    "Tool_Master",        "ToolMaster (grp 9)"),
    ]},

    # ---- User (outside the standard master sequence) ----
    {"label": "User", "submodules": [
        _sub("", lambda **kw: UserMasterMigration(**kw), "User_Master", "UserMaster")]},

    # ---- allocation / rate / slab — these are CHILD entities: not shown in the
    # dropdown; they migrate automatically with their parent (hidden=True). ----
    {"label": "Process Allocated Machine", "hidden": True, "submodules": [
        _sub("", lambda **kw: ProcessAllocatedMachineMigration(**kw), "Operation_Machine_Allocation_Master", "ProcessAllocatedMachineMaster")]},
    {"label": "Process Slabs", "hidden": True, "submodules": [
        _sub("", lambda **kw: ProcessSlabsMigration(**kw), "Operation_Slab_Master", "ProcessMasterSlabs")]},
    {"label": "Machine Slab", "hidden": True, "submodules": [
        _sub("", lambda **kw: MachineSlabMigration(**kw), "Machine_Slab_Master", "MachineSlabMaster")]},
    {"label": "Machine Online Coating Rates", "hidden": True, "submodules": [
        _sub("", lambda **kw: MachineOnlineCoatingRatesMigration(**kw), "Machine_Online_Coating_Rates", "MachineOnlineCoatingRates")]},
    {"label": "Client Process Cost Setting", "hidden": True, "submodules": [
        _sub("", lambda **kw: ClientProcessCostSettingMigration(**kw), "Client_Operation_Slab_Master", "ClientProcessCostSetting")]},
    {"label": "COA Parameter Setting", "hidden": True, "submodules": [
        _sub("", lambda **kw: COAParameterMigration(**kw), "Category_Wise_COA_Parameters", "CategoryWiseCOAParameterSetting")]},
    {"label": "Category Content Allocation", "hidden": True, "submodules": [
        _sub("", lambda **kw: ContentAllocationMigration(**kw), "Catagory_Wise_Default_Orientations", "CategoryContentAllocationMaster")]},
    {"label": "Category Process Allocation", "hidden": True, "submodules": [
        _sub("", lambda **kw: ProcessAllocationMigration(**kw), "Catagory_Wise_Default_Operations", "CategoryWiseProcessAllocation")]},
    {"label": "FG QC Sampling Plan", "hidden": True, "submodules": [
        _sub("", lambda **kw: FGQCSamplingMigration(**kw), "Finish_Goods_QC_Sampling_Plans", "FinishGoodsQCSamplingPlan")]},
    {"label": "FG QC Parameter Setting", "hidden": True, "submodules": [
        _sub("", lambda **kw: FGQCParameterMigration(**kw), "Finish_Goods_QC_Parameter", "FinishGoodsQCParameterSetting")]},
    {"label": "Process Line Clearance", "hidden": True, "submodules": [
        _sub("", lambda **kw: LineClearanceMigration(**kw), "Department_Wise_Line_Clearance_Parameters", "ProcessLineClearanceParameters")]},
    {"label": "Process Inspection Parameter", "hidden": True, "submodules": [
        _sub("", lambda **kw: InspectionParameterMigration(**kw), "Department_Wise_Process_Inspection_Parameters", "ProcessInspectionParameters")]},
    {"label": "Process Tool Group Allocation", "hidden": True, "submodules": [
        _sub("", lambda **kw: ToolGroupAllocationMigration(**kw), "Operation_Tool_Group_Allocation", "ProcessToolGroupAllocationMaster")]},
    {"label": "Machine Item Sub-Group Allocation", "hidden": True, "submodules": [
        _sub("", lambda **kw: MachineItemSubGroupAllocationMigration(**kw), "Machine_Material_Group_Allocation", "MachineItemSubGroupAllocationMaster")]},
    {"label": "Tool QC Parameter", "hidden": True, "submodules": [
        _sub("", lambda **kw: ToolQCParameterMigration(**kw), "Tool_Group_QC_Parameter", "ToolQCParameterSetting")]},
    {"label": "Paper QC Parameter", "hidden": True, "submodules": [
        _sub("", lambda **kw: PaperQCMigration(**kw), "Paper_QC_Parameter", "ItemQCParameterSetting (grp 14)")]},
    {"label": "Reel QC Parameter", "hidden": True, "submodules": [
        _sub("", lambda **kw: ReelQCMigration(**kw), "Reel_QC_Parameter", "ItemQCParameterSetting (grp 2)")]},
    {"label": "Roll QC Parameter", "hidden": True, "submodules": [
        _sub("", lambda **kw: RollQCMigration(**kw), "Roll_QC_Parameter", "ItemQCParameterSetting (grp 13)")]},
    {"label": "Material Group QC Parameter", "hidden": True, "submodules": [
        _sub("", lambda **kw: MaterialGroupQCMigration(**kw), "Material_Group_QC_Parameter", "ItemQCParameterSetting")]},
    # Employee Machine Allocation is no longer a separate module — it runs
    # automatically as a child of the Ledger → Employee migration (hidden).
    {"label": "Employee Machine Allocation", "hidden": True, "submodules": [
        _sub("", lambda **kw: EmployeeMachineAllocationMigration(**kw), "Employee_Machine_Allocation_Master", "EmployeeMachineAllocation")]},

    # ---- hierarchical ----
    {"label": "Product Master", "submodules": [
        _sub("", lambda **kw: ProductMasterMigration(**kw), "Product_Master (+contents/ops/...)", "ProductMaster (+children)")]},
]


# ---------------------------------------------------------------------------
# Derive a flat REGISTRY (unique entity name -> (factory, src, tgt)) so the
# rest of the app (engine, create(), Migrate-All) keeps working.
# Entity name = "Module" for single-sub modules, else "Module — Sub".
# ---------------------------------------------------------------------------
REGISTRY = {}
for _m in MODULES:
    _subs = _m["submodules"]
    for _label, _factory, _src, _tgt in _subs:
        _name = _m["label"] if (len(_subs) == 1 or _label == "") else f"{_m['label']} — {_label}"
        REGISTRY[_name] = (_factory, _src, _tgt)


def modules() -> list[dict]:
    """[{label, submodules:[(label, name)]}] for the two-dropdown UI.
    submodule 'name' is the REGISTRY key to migrate."""
    out = []
    for m in MODULES:
        if m.get("hidden"):
            continue   # child-only entities — migrate with their parent, not pickable
        subs = m["submodules"]
        sublist = []
        for label, _f, _s, _t in subs:
            name = m["label"] if (len(subs) == 1 or label == "") else f"{m['label']} — {label}"
            sublist.append((label, name))
        out.append({"label": m["label"], "submodules": sublist,
                    "has_submodules": len(subs) > 1})
    return out


# Dependency-correct entity names for "Migrate All" — follows the updated module
# sequence (children run right after their parent).
MIGRATION_ALL = [
    "Production Unit", "Department", "Unit", "Product HSN", "Flute Master",
    "Warehouse",
    "Category", "Category Content Allocation", "Category Process Allocation",
    "FG QC Sampling Plan", "FG QC Parameter Setting", "COA Parameter Setting",
    "Spare Part",
    "Process", "Process Slabs", "Process Allocated Machine",
    "Process Line Clearance", "Process Inspection Parameter",
    "Process Tool Group Allocation", "Client Process Cost Setting",
    "Machine", "Machine Slab", "Machine Online Coating Rates",
    "Machine Item Sub-Group Allocation",
    "Material Sub-Group",
    "Item — Paper", "Paper QC Parameter",
    "Item — Reel", "Reel QC Parameter",
    "Item — Ink", "Item — Varnish", "Item — Lamination", "Item — Foil",
    "Item — Other Material", "Material Group QC Parameter",
    "Item — Roll", "Roll QC Parameter",
    "Ledger — Client", "Ledger — Supplier",
    "Ledger — Employee", "Employee Machine Allocation",
    "Ledger — Consignee", "Ledger — Vendor", "Ledger — Transporter",
    "Ledger — Duties & Taxes", "Ledger — Purchase", "Ledger — Sales",
    "Tool — Plates", "Tool — Block", "Tool — Die", "Tool — Emboss",
    "Tool — Printing Cylinder", "Tool — Anilox Cylinder",
    "Tool — Embossing Cylinder", "Tool — Flexo Die", "Tool — Magnetic Cylinder",
    "Tool QC Parameter",
    "Machine Tool Allocation",
    "Product Master",
]


def available() -> list[str]:
    """Flat list of all migratable entity names (REGISTRY order)."""
    return list(REGISTRY.keys())


def migration_all_order() -> list[str]:
    return [n for n in MIGRATION_ALL if n in REGISTRY]


def labels(name: str) -> tuple[str, str]:
    _f, src, tgt = REGISTRY[name]
    return src, tgt


def create(name: str, **context):
    if name not in REGISTRY:
        raise KeyError(f"Unknown entity: {name}")
    return REGISTRY[name][0](**context)


# ---------------------------------------------------------------------------
# Dependent-children map for auto-migration: picking a master can auto-run the
# CHILD masters that build on it (downstream FK dependents), in order. Item,
# Ledger, Employee and Product Master are NEVER auto-pulled (migrate manually).
# ---------------------------------------------------------------------------
AUTO_MIGRATE_EXCLUDED = {
    # entity-name prefixes that must never be auto-migrated as a side effect
    "Item", "Ledger", "Employee", "Product Master",
}

CHILDREN = {
    "Tool — All": [
        "Tool QC Parameter",
    ],
    "Item — Paper":          ["Paper QC Parameter"],
    "Item — Reel":           ["Reel QC Parameter"],
    "Item — Roll":           ["Roll QC Parameter"],
    "Item — Material (All)": ["Material Group QC Parameter"],
    "Category": [
        "Category Content Allocation",
        "Category Process Allocation",
        "FG QC Sampling Plan",
        "FG QC Parameter Setting",
        "COA Parameter Setting",
    ],
    "Process": [
        "Process Slabs",
        "Process Allocated Machine",
        "Process Line Clearance",
        "Process Inspection Parameter",
        "Process Tool Group Allocation",
        "Client Process Cost Setting",
    ],
    "Machine": [
        "Machine Slab",
        "Machine Online Coating Rates",
        "Machine Item Sub-Group Allocation",
        "Process Allocated Machine",
        "Machine Tool Allocation",
    ],
    # Employee is now the Ledger → Employee sub-group; its machine allocation
    # migrates automatically with it.
    "Ledger — Employee": [
        "Employee Machine Allocation",
    ],
}


def _is_excluded(name: str) -> bool:
    # Exclude the master itself and its em-dash sub-group forms (e.g.
    # "Item — Paper", "Ledger — Client"), but NOT a separate child entity that
    # merely shares a word prefix — e.g. "Employee Machine Allocation" is a
    # legitimate child of Employee/Machine, not the Employee master.
    return any(name == x or name.startswith(x + " —")
               for x in AUTO_MIGRATE_EXCLUDED)


def dependent_chain(name: str) -> list[str]:
    """Return the ordered list of CHILD entities to auto-migrate after `name`
    (excluding Item/Ledger/Employee/Product Master, and de-duplicated). Returns
    [] if the master has no auto-migratable children."""
    out: list[str] = []
    for child in CHILDREN.get(name, []):
        if child in REGISTRY and not _is_excluded(child) and child not in out:
            out.append(child)
    return out
