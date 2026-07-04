# Indus Migration Tool — Setup Guide

Migrates data from the **desktop** Indus ERP database (source) to the **web**
Indus ERP database (target). Rewrite of the old ASP.NET Web Forms migration
site as a standalone Python desktop tool, matching the IndusDB Tool stack.

---

## Requirements

- **Python 3.11+** — https://python.org/downloads
  ✅ During install, check **"Add Python to PATH"**
- **SQL Server ODBC Driver** — required by pyodbc.
  Install **ODBC Driver 17** or **18** for SQL Server.
- The machine running this tool must be able to reach **both** SQL Servers
  (the desktop DB and the web DB) at the same time.

---

## Install Python Dependencies

Open a terminal in this folder and run:

```
python -m venv myenv
myenv\Scripts\activate          # Windows
pip install -r requirements.txt
```

| Package | Purpose |
|---------|---------|
| PyQt6   | UI framework |
| pyodbc  | SQL Server connection |
| openpyxl| Excel import/export (for spreadsheet-based imports) |

---

## Running the App

```
python IndusMigration.py
```

Opens on the **dual-connection screen**:

1. **Desktop ERP (Source)** — enter the server, click *Fetch* to list databases,
   pick the old desktop database, then **Connect**.
2. **Web ERP (Target)** — do the same for the new web database.
3. Once **both** show *✓ Connected*, the **Open Migration** button enables.

Connection settings for each side are saved automatically to
`connection.json` (next to the exe / project root) and pre-filled next time.

---

## Building the Executable

```
pyinstaller IndusMigration.spec       # Produces single-file IndusMigration.exe
```

`console=False`, `upx=True` — same packaging as IndusDB Tool. `connection.json`
is created at runtime next to the exe.

---

## Folder Structure

```
IndusMigration/
├── IndusMigration.py        ← entry point (opens dual-connection screen)
├── IndusMigration.spec      ← PyInstaller build spec
├── requirements.txt
├── connection.json          ← saved connections (created at runtime)
├── core/
│   └── db.py                ← dual pyodbc connections (desktop + web)
└── ui/
    ├── style.py             ← app-wide stylesheet (Fusion + QSS)
    ├── widgets.py           ← shared table/grid helpers
    └── connection_window.py ← dual-connection landing screen
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `pyodbc.Error: Data source name not found` | Install ODBC Driver 17/18 for SQL Server |
| App won't connect | Check server IP/port and that the machine can reach that SQL Server |
| One side connects, the other doesn't | They are independent — re-check that side's server/database/credentials |
| Migration button stays grey | Both Desktop and Web must show *✓ Connected* |
