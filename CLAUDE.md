# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Indus Migration Tool migrates data from the **desktop** Indus ERP database
(SOURCE) to the **web** Indus ERP database (TARGET). The two versions have
different schemas; this tool reads from the old desktop DB and writes to the
new web DB. It is a rewrite of an old ASP.NET Web Forms (VB.NET) migration site
(`D:\Office Data\Indus Web Modules\IndusMigration - OLD\IndusMigrationLatest`)
into a standalone Python + PyQt6 desktop tool. Built for **Indas Analytics Pvt. Ltd.**

It deliberately mirrors the sibling project **IndusDB Tool**
(`..\IndusDB Tool`) — same stack, structure, styling, packaging, and patterns.
When in doubt about a convention, look there first.

## Running the Application

```bash
python IndusMigration.py        # opens the dual-connection screen
```

Two screens: the **dual-connection** landing (connect both DBs) → the
**migration** screen, which is a **Load → Preview → Import** flow (see below).
There are no automated tests; verify by running the app against the local DBs.

### Environment Setup
```bash
python -m venv myenv
myenv\Scripts\activate
pip install -r requirements.txt
```
Requires an **ODBC driver for SQL Server** (Driver 17/18 recommended). Note:
`db.build_connection_string` currently pins `DRIVER={SQL Server}` (the legacy
driver) with a default `INDUS` login — change the DRIVER there if that legacy
driver isn't installed.

### Building the Executable
```bash
pyinstaller IndusMigration.spec  # single-file IndusMigration.exe (console=False, upx=True)
```

## Architecture

### Two-layer structure: `core/` (logic) and `ui/` (PyQt6 windows)

**`core/db.py`** — The key difference from IndusDB Tool. Where IndusDB Tool holds
ONE global connection, this holds **two named connections** keyed by role:
`db.DESKTOP` (source) and `db.WEB` (target). Both must be live before migration.
- `connect(role, ...)` / `connect_desktop` / `connect_web` — open a role's connection (`autocommit=False`).
- `get(role)` / `get_desktop` / `get_web` — fetch the live connection (raises if not connected).
- `query(role, sql, params)` / `query_desktop` / `query_web` — **parameterised** SELECT → list of dicts.
- `both_connected()` — gates the migration UI.
- Settings persist to `connection.json` with a per-role block (`desktop`, `web`) plus a shared `server_history`.

**`ui/connection_window.py`** — Landing screen. Two `ConnectionPanel`s side by
side (Desktop source / Web target), each with Fetch-databases + Connect/Disconnect
running in `QThread` workers. `ConnectionWindow` enables **Open Migration** only
when `db.both_connected()`, then lazily opens `MigrationWindow`.

**`ui/migration_window.py`** — The migration screen. It is a **Load → Preview →
Import** flow, NOT a one-shot run:
1. Pick a **Module** + (optional) **Sub-module** and the target context. Company
   is chosen from the web DB's `CompanyMaster`; **User and FYear are auto-resolved**
   from `UserMaster` for that company (hidden combos, shown as a label).
2. **Load Data** (`_LoadWorker` → `engine.preview_entity`) reads the source, maps
   to target columns, resolves FKs, and fits values — but **writes nothing**.
   Results fill a colour-coded, per-row **checkbox** grid (green=will import,
   amber=already migrated, red=issue-with-reason).
3. **Import Ticked Rows** (`_ImportWorker` → `engine.import_preview`) writes only
   the ticked rows; each record's outcome streams back into the grid.
- **Auto-migrate chain**: if the chosen master has dependent children
  (`entities.dependent_chain`), importing it runs `_ChainWorker`, which imports
  the master then fully previews+imports each child entity in order.
- **Clear-before-import**: an optional destructive step (`_maybe_clear_before_import`
  → `engine.clear_entity`) that deletes this entity's existing web rows (scoped to
  company + sub-group) and its children first, then re-previews.

**`core/engine.py`** — The reusable migration engine driving an `EntityMigration`
subclass through its hooks. Three public entry points:
- `preview_entity(entity, ...)` → `PreviewResult` (build every target row, resolve
  refs, fit values — **no writes**).
- `import_preview(entity, preview, selected_indexes, ...)` → inserts chosen rows.
- `run_entity(entity, ...)` → read + insert in one pass (used for non-preview paths).
- `clear_entity(entity)` → delete this entity's rows (company/sub-group scoped, children first).

Per-record flow: `read_source → [already_migrated? → resolve_refs → build_parent
→ build_children → after_insert]`. Extra one-off hooks: `before_import` (run a
prerequisite migration first, e.g. Material_Group → ItemSubGroupMaster) and
`prepare_import` (rebuild per-instance maps on the fresh import-worker entity, e.g.
hierarchical ProductMaster). Key mechanics:
- `_insert_parent` uses `OUTPUT INSERTED.<identity>` for reliable id capture (or a
  plain insert when the entity supplies its own PK value).
- `_insert_children` uses `fast_executemany`, **except** when a target column is an
  unbounded MAX type (would blow memory) — cached per table.
- `_fit_values` coerces empty/junk into numeric/date/bit columns as `None` and
  truncates over-long strings to the target width (schema cached from `sys.columns`)
  rather than failing the record.
- `_commit_in_batches` commits every `_COMMIT_BATCH` (50) records for speed; on a
  batch failure it rolls back and retries that batch **row-by-row** so one bad
  record fails alone and good records still land.
- After any runtime `ALTER TABLE`, call `reset_schema_caches()`.

**`core/mapping.py`** — The **declarative layer** most entities are built on.
`MappedEntity(EntityMigration)` turns a small class-level declaration
(`source_table`, `column_map`, `constant_columns`, `ref_resolvers`, `child_eav`,
`name_field_*`, `source_where`, …) into a full migration — override a hook only for
the unusual bits. Helpers here:
- `RefMap(table, ref_col, id_col)` — resolve a desktop source id → the web id of
  the already-migrated row via its `Ref*` back-pointer column.
- EAV / field-master machinery: `group_field_names`, `ensure_group_fields`,
  `build_eav_detail_rows`, `ensure_int_identity_pk` (widens a tinyint IDENTITY PK
  that has maxed at 255) — for `*MasterDetails` + `*GroupFieldMaster` tables.
- Value hygiene + resolvers: `strip_quotes`/`to_sql_value` (G2 quote removal),
  `resolve_country`/`resolve_state` (CountryStateMaster), `resolve_content_id`,
  `resolve_subgroup_id`, `resolve_branch_id`, and DepartmentID normalisation
  (desktop 0 → default dept 100; other refs → 200; opt-outs via class flags).

**`core/entities/`** — One module per entity, aggregated in `__init__.py` as a
two-level **`MODULES`** structure (top-level Module → Sub-modules) that drives the
two-dropdown UI; a flat **`REGISTRY`** (entity-name → factory) is derived from it.
Other exports from `__init__.py`:
- `modules()` — module/sub-module lists for the UI (skips `hidden` child entities).
- `create(name, **ctx)` / `labels(name)` / `available()` / `migration_all_order()`.
- `CHILDREN` + `dependent_chain(name)` — the auto-migrate dependency map (e.g.
  migrating `Category` also runs its content/process/QC/COA children;
  Item/Ledger/Employee/Product Master are never auto-pulled).
- Child-only entities are marked `hidden: True` (not pickable; migrate with a parent).

`ledger_master.py` is the hand-written **reference** (per-group codes, EAV details,
QA-driven column rules, the `UpdateLedgerMasterValues` calc SP) — richer than most.
For a typical new entity, subclass `MappedEntity`, add a module/sub-module entry to
`MODULES`, and (if it has dependents) wire it into `CHILDREN`.

**`core/formula_translator.py`** — Standalone: translates desktop material-cost
formulas (Excel-style positional `$N` + `ROUND`) into web JS (`Number(e.<Field>)` +
`parseFloat(...toFixed(n))`), given a per-group position→web-field-name map.

**`ui/style.py`** — Global QSS applied via `style.apply(app)` (Fusion). Button
variants via `setObjectName("btn_success"/"btn_danger"/"btn_warning")`. Copied 1:1
from IndusDB Tool — keep them in sync.

**`ui/widgets.py`** — Shared `QTableWidget` helpers (`make_table`, `fill_table`,
`set_row_color`) and row colours: `COLOR_SUCCESS` (green), `COLOR_ERROR` (red),
`COLOR_SKIPPED` (amber, for duplicates), `COLOR_PENDING` (white).

### Key Patterns (inherited from IndusDB Tool)
- **QThread for I/O**: DB fetch/connect run in workers that emit `finished(success, data, error)`.
- **Lazy UI imports**: child windows imported inside click handlers to avoid circular imports.
- **Button styling**: object names map to colour variants in the stylesheet.

## Why the rewrite (what the OLD code got wrong)

The old VB/JS tool "did not migrate the entire/proper data" and was slow. Root
causes found in the old codebase — the rewrite must NOT reproduce them:
- **String-concatenated SQL** everywhere (injection + breaks on quotes/apostrophes). → Always use parameterised queries (`db.query(role, sql, params)`).
- **Per-row INSERT loops** (1 round-trip per row). → Batch with `fast_executemany` / set-based inserts.
- **No transaction boundaries** across a logical record → orphaned parents when a child insert failed. → Wrap each record (parent + children) in one transaction; `autocommit=False` is already set.
- **Swallowed exceptions** returning `"fail"`/`ex.Message` as a *success* string. → Surface real errors; log per-row outcomes.
- **No foreign-key validation** against the target before insert. → Validate refs exist in the web DB first.
- **Column mapping lived only in JavaScript**. → Move mapping into the tool (per-entity mapping layer).

## Source-read strategy (decided)

Do **NOT** depend on the desktop stored procedure `IndusPrint_To_Web_DataMigration`:
it only implements 2 of ~20 entities (Users, Ledgers), is declared with 2 params
but the old code calls it with 3, does lossy quote-stripping, and its companion
`usp_GetCleanedDataWithDefaults` is not even deployed. Instead, each entity's
source query lives as parameterised SQL in a Python per-entity mapping registry
(seed from the SP where it has real logic + from the old VB `SaveData*` methods).
**Keep** the web-side SPs `UpdateLedgerMasterValues` and `GetFilteredLedgerMasterData`
(post-insert calculations) — call them parameterised.

Ledger sub-group map (from the SP), `Under_Group_ID`: Sundry Debtors=24,
Sundry Creditors=23, Employees=27, Duties & Taxes=43, Purchase Accounts=20,
Sales Accounts=21, Consignee=24+Is_Consignee, Transporters=26, Vendors=23+Is_Vendor.

## Local test databases

SQL Server 2022 Express at `ABHINAVHP\SQLEXPRESS`, login `Indus` / `Param@99811`
(use `sqlcmd -C`):
- `IndusDesktop` — source (930 tables). Source table for ledgers is `Ledger_Master`.
- `IndusWeb` — target (485 tables).

## Configuration

- `connection.json` — auto-saved per-role connections + shared server history (created at runtime next to the exe / project root).
