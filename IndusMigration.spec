# -*- mode: python ; coding: utf-8 -*-

# Entity migrations are pulled in through the core.entities registry. They are
# statically imported there, but we list them explicitly so the bundle never
# silently drops one as more entities are added.
hiddenimports = [
    'pyodbc',
    # pyodbc's fast_executemany path does a lazy `import uuid` (C-level), which
    # PyInstaller's static analysis misses. Without it, batch inserts on tables
    # without an nvarchar(max) column crash with "No module named 'uuid'"
    # (hit first by the Product Master child inserts). Bundle it explicitly.
    'uuid',
    'core.engine',
    'core.mapping',
    'core.entities',
    'core.entities.ledger_master',
    'core.entities.tier1_masters',
    'core.entities.tier2_masters',
    'core.entities.items',
    'core.entities.spare_tool',
    'core.entities.tier3_allocations',
    'core.entities.product_master',
    'core.entities.employee_master',
    'core.entities.coa_parameters',
    'core.entities.category_children',
    'core.entities.process_children',
    'core.entities.machine_children',
    'core.entities.flute_master',
    'core.entities.tool_children',
    'core.entities.item_qc_parameters',
    'core.entities.user_master',
    'core.entities.material_group',
    'ui.migration_window',
]

a = Analysis(
    ['IndusMigration.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='IndusMigration',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
