"""
User Master (minimal).

Migrates desktop `User_Master` -> web `UserMaster`. This is the minimal version:
core identity (UserName) plus ProductionUnitID and BranchID. The full ~60-field
profile and the custom password-scramble are intentionally deferred.
"""

from __future__ import annotations

from core.mapping import MappedEntity


class UserMasterMigration(MappedEntity):
    name = "UserMaster"
    target_table = "UserMaster"
    target_identity = "UserID"
    source_table = "User_Master"
    name_field_source = "User_Name"
    name_field_target = "UserName"
    column_map = {
        "User_Name": "UserName",
        "Production_Unit_ID": "ProductionUnitID",
        "Branch_ID": "BranchID",
    }
