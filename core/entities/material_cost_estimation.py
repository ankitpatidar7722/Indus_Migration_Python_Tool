"""
Material_Group_Costing_Field_Master  ->  MaterialCostEstimationSetting.

Per material group the desktop stores costing fields with Excel-style positional
formulas ($N = the field at Trans_ID N). The web stores the same rows with NAMED JS
formulas (Number(e.<FieldName>)). This entity:

  * resolves desktop Material_Group_ID -> web (ItemGroupID, ItemSubGroupID) via a
    confirmed per-group map (INKS/UV Varnish/Window Film/Lamination Film for now),
  * derives each row's web FieldName by precedence:
        Field_Description map  ->  AppVariableName  ->  ItemMasterFieldName,
  * translates the $N formula to web JS using that per-group position->FieldName map
    (core.formula_translator),
  * maps AppVariableName (from Variable_Name, whitelist-valid) and ItemMasterFieldName
    (from Master_Field_Name via ItemGroupFieldMaster + explicit aliases).

A formula that references a position with no resolvable FieldName is left BLANK
(flagged) rather than emitting a wrong formula.
"""

from __future__ import annotations

from core import db
from core.mapping import MappedEntity, _has_column, _norm_name
from core.formula_translator import translate_formula


class MaterialCostEstimationMigration(MappedEntity):
    name = "MaterialCostEstimationSetting"
    target_table = "MaterialCostEstimationSetting"
    target_identity = "CostEstimationSettingID"
    source_table = "Material_Group_Costing_Field_Master"
    column_map = {}                       # custom build_parent

    # desktop Material_Group_ID -> (ItemGroupID, ItemSubGroupID). Adhesive groups
    # (-3/-4/-5) are intentionally left out for now.
    GROUP_MAP = {-2: (3, -3), -6: (4, -4), -12: (5, -5), -102: (5, -5)}

    # desktop Variable_Name (normalized) -> AppVariableName (all whitelist-valid).
    APPVAR_ALIAS = {
        "processl": "CutSizeL", "processw": "CutSizeW",
        "totalcutsheets": "TotalCutSheets", "jobh": "SizeHeight",
        "orderquantity": "PlanContQty",
    }
    # explicit desktop Master_Field_Name (normalized) -> ItemMasterFieldName (the ones
    # that don't match ItemGroupFieldMaster.FieldName directly).
    ITEMMASTER_ALIAS = {
        "rate": "EstimationRate", "materialname": "ItemName",
        "materialthickness": "Thickness",
    }
    # desktop Field_Description (normalized) -> (web FieldDescription, web FieldName).
    FIELD_DESC_MAP = {
        "inkname": ("Ink Name", "ItemName"),
        "filmname": ("Film Name", "ItemName"),
        "windowfilmname": ("Film Name", "ItemName"),
        "adhesivename": ("Adhesive Name", "ItemName"),
        "pastingadhesivename": ("Adhesive Name", "ItemName"),
        "coatingmaterial": ("Coating Material", "ItemName"),
        "adhesivegsm": ("Adhesive GSM", "GSM"),
        "gsm": ("GSM", "GSM"),
        "gsmgrams": ("GSM", "GSM"),
        "inkgsm": ("Ink GSM", "GSM"),
        "uvvarnishgsmgrams": ("GSM", "GSM"),
        "amount": ("Amount", "EstimatedAmount"),
        "density": ("Density", "Density"),
        "filmthickness": ("Film Thickness", "Thickness"),
        "micron": ("Thickness", "Thickness"),
        "thickness": ("Thickness", "Thickness"),
        "inkrate": ("Rate", "EstimationRate"),
        "rate": ("Rate", "EstimationRate"),
        "orderquantity": ("Order Quantity", "PlanContQty"),
        "productheight": ("SizeHeight", "SizeHeight"),
        "requiredquantity": ("Required Quantity", "EstimatedQuantity"),
        "sheetlength": ("Sheet Length", "CutSizeL"),
        "sheetwidth": ("Sheet Width", "CutSizeW"),
        "sheetquantity": ("Sheet Quantity", ""),
        "sheetsquantity": ("Sheets Quantity", ""),
        "windowlength": ("Window Length", ""),
        "windowwidth": ("Window Width", ""),
        "newparameter": ("New Parameter", ""),
    }

    def __init__(self, **kw):
        super().__init__(**kw)
        self._igfm = None
        self._posmap = None
        self._existing = None

    @staticmethod
    def _k(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    # ---- resolvers --------------------------------------------------------
    def _load_igfm(self):
        """{normalized FieldName -> FieldName} from ItemGroupFieldMaster (all groups)."""
        if self._igfm is None:
            self._igfm = {}
            for r in db.query_web("SELECT DISTINCT FieldName FROM ItemGroupFieldMaster "
                                  "WHERE ISNULL(FieldName,'')<>''"):
                self._igfm.setdefault(_norm_name(r["FieldName"]), r["FieldName"])
        return self._igfm

    def _app_var(self, variable_name):
        return self.APPVAR_ALIAS.get(_norm_name(variable_name))

    def _item_master_field(self, master_field_name):
        n = _norm_name(master_field_name)
        if not n:
            return None
        return self.ITEMMASTER_ALIAS.get(n) or self._load_igfm().get(n)

    def _field_desc(self, field_description):
        return self.FIELD_DESC_MAP.get(_norm_name(field_description))

    def _field_name(self, row):
        """FieldName precedence: Field_Description map -> AppVariableName -> ItemMasterFieldName."""
        fd = self._field_desc(row.get("Field_Description"))
        if fd and fd[1]:
            return fd[1]
        return self._app_var(row.get("Variable_Name")) or \
            self._item_master_field(row.get("Master_Field_Name"))

    # ---- read -------------------------------------------------------------
    def read_source(self):
        ids = ",".join(str(g) for g in self.GROUP_MAP)
        rows = db.query_desktop(
            "SELECT Material_Group_ID, Trans_ID, Field_Description, Variable_Name, "
            "Master_Field_Name, Field_Display_Name, Module_Type, Default_Value, "
            "Calculation_Formula FROM Material_Group_Costing_Field_Master "
            f"WHERE Material_Group_ID IN ({ids}) ORDER BY Material_Group_ID, Trans_ID")
        # per-group position(Trans_ID) -> FieldName, for formula translation
        self._posmap = {}
        for r in rows:
            g = self._k(r.get("Material_Group_ID"))
            self._posmap.setdefault(g, {})[self._k(r.get("Trans_ID"))] = self._field_name(r)
        # idempotency: existing (ItemGroupID, ItemSubGroupID, TransID)
        self._existing = set()
        for r in db.query_web(
                "SELECT ItemGroupID, ItemSubGroupID, TransID FROM MaterialCostEstimationSetting "
                "WHERE ISNULL(IsDeletedTransaction,0)=0"):
            self._existing.add((self._k(r["ItemGroupID"]), self._k(r["ItemSubGroupID"]),
                                self._k(r["TransID"])))
        return rows

    def prepare_import(self):
        if not self._posmap:
            self.read_source()

    # ---- per-row hooks ----------------------------------------------------
    def source_key(self, row):
        return f"G{row.get('Material_Group_ID')} / T{row.get('Trans_ID')} " \
               f"({row.get('Field_Description') or '?'})"

    def already_migrated(self, row):
        grp = self.GROUP_MAP.get(self._k(row.get("Material_Group_ID")))
        if not grp:
            return False
        key = (self._k(grp[0]), self._k(grp[1]), self._k(row.get("Trans_ID")))
        return key in (self._existing or set())

    def resolve_refs(self, row):
        if self._k(row.get("Material_Group_ID")) not in self.GROUP_MAP:
            raise ValueError("group not mapped")
        return {}

    def build_parent(self, row, refs):
        g = self._k(row.get("Material_Group_ID"))
        igrp, isub = self.GROUP_MAP[g]
        trans = self._k(row.get("Trans_ID"))
        fd = self._field_desc(row.get("Field_Description"))
        # translate formula with ONLY resolvable positions (missing -> blank formula).
        formula = ""
        raw = (row.get("Calculation_Formula") or "").strip()
        if raw:
            clean_pos = {k: v for k, v in self._posmap.get(g, {}).items() if v}
            try:
                formula = translate_formula(raw, clean_pos)
            except KeyError:
                formula = ""
        fields = {
            "ItemGroupID": igrp, "ItemSubGroupID": isub, "TransID": trans,
            "FieldDescription": fd[0] if fd else row.get("Field_Description"),
            "FieldName": self._posmap.get(g, {}).get(trans),
            "AppVariableName": self._app_var(row.get("Variable_Name")),
            "ItemMasterFieldName": self._item_master_field(row.get("Master_Field_Name")),
            "FieldDisplayName": row.get("Field_Display_Name"),
            "CalculationFormula": formula,
            "DefaultValue": row.get("Default_Value"),
            "DomainType": row.get("Module_Type"),
            "DisplaySequenceNo": trans, "IsDisplayField": 1,
            "IsEditableField": 1, "IsDeletedTransaction": 0,
        }
        for c, v in self.context_columns().items():
            fields.setdefault(c, v)
        cols = [c for c in fields if _has_column(self.target_table, c)]
        return cols, [fields[c] for c in cols]
