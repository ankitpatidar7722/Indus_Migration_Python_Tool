"""
Dual database connection module for IndusMigration.

Unlike IndusDB Tool (single global connection), the migration tool needs TWO
live connections at once:

    * DESKTOP  -> the customer's old desktop ERP database  (migration SOURCE)
    * WEB      -> the new web ERP database                 (migration TARGET)

Both are opened via the visual Connection screen. The last-used settings for
each side are saved to connection.json automatically (with a shared server
history so either dropdown can reuse a server you've typed before).

All reads/writes are parameterised — no string-concatenated SQL — and the
connections use autocommit=False so the migration engine can wrap each logical
record (parent + children) in a single transaction.
"""

import json
import os
import sys

import pyodbc

# ----------------------------------------------------------------------------
# Connection roles
# ----------------------------------------------------------------------------
DESKTOP = "desktop"   # source
WEB = "web"           # target
_ROLES = (DESKTOP, WEB)

# Global connections, one per role
_connections: dict[str, pyodbc.Connection | None] = {DESKTOP: None, WEB: None}
_info: dict[str, tuple[str, str]] = {DESKTOP: ("", ""), WEB: ("", "")}
# Full credentials kept in memory so a dropped connection can be transparently
# re-established (reconnect) during a long migration over a flaky remote link.
_creds: dict[str, tuple] = {}


# ----------------------------------------------------------------------------
# Settings persistence (connection.json next to the exe / project root)
# ----------------------------------------------------------------------------
def _get_settings_dir() -> str:
    """Persistent directory for settings — works both as script and as exe."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)          # next to the exe
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root


_SETTINGS_FILE = os.path.join(_get_settings_dir(), "connection.json")


def load_saved_settings() -> dict:
    """Load saved connection settings. Shape:

        {
          "desktop": {"server", "database", "username", "password"},
          "web":     {"server", "database", "username", "password"},
          "server_history": [...]
        }
    """
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_role_settings(role: str, server: str, database: str,
                        username: str, password: str):
    """Persist one role's settings, merging into the shared server history."""
    existing = load_saved_settings()
    history = existing.get("server_history", [])
    if server and server not in history:
        history.insert(0, server)

    existing[role] = {
        "server": server,
        "database": database,
        "username": username,
        "password": password,
    }
    existing["server_history"] = history
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Connection string
# ----------------------------------------------------------------------------
# Driver choice is the single biggest performance factor over a network. The
# legacy 'SQL Server' driver is slow AND does not support pyodbc's
# fast_executemany, so batch child inserts silently degrade to one round-trip
# per row — fine locally, catastrophic on a remote server. We therefore prefer a
# modern 'ODBC Driver NN for SQL Server' (fast + fast_executemany) and only fall
# back to the legacy driver if nothing better is installed / it fails to connect.
_PREFERRED_DRIVERS = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13.1 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "ODBC Driver 11 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",                       # legacy fallback (slow; no fast_executemany)
]
_driver_order: list[str] | None = None


def available_sql_drivers() -> list[str]:
    """Installed SQL Server ODBC drivers, best-first. Cached."""
    global _driver_order
    if _driver_order is None:
        try:
            installed = set(pyodbc.drivers())
        except Exception:
            installed = set()
        _driver_order = [d for d in _PREFERRED_DRIVERS if d in installed] or ["SQL Server"]
    return _driver_order


def build_connection_string(server: str, database: str,
                            username: str = "INDUS",
                            password: str = "Param@99811",
                            driver: str | None = None) -> str:
    drv = driver or available_sql_drivers()[0]
    s = (f"DRIVER={{{drv}}};"
         f"SERVER={server};"
         f"DATABASE={database};"
         f"UID={username};"
         f"PWD={password};")
    # Modern drivers: skip TLS on the (trusted) LAN — matches the legacy driver's
    # unencrypted behaviour, avoids cert prompts, and shaves per-connect overhead.
    # ConnectRetry* lets the driver transparently recover an idle-dropped connection
    # (helps over flaky remote/VPS links); harmless on a LAN.
    if drv != "SQL Server":
        s += "Encrypt=no;ConnectRetryCount=3;ConnectRetryInterval=10;"
    s += "TrustServerCertificate=yes;"
    return s


def _connect_any(server: str, database: str, username: str, password: str,
                 timeout: int = 10) -> pyodbc.Connection:
    """Open a connection trying each installed driver best-first; raise the last
    error only if every driver fails (so a modern-driver hiccup still falls back
    to the legacy driver and connectivity never regresses)."""
    last_err: Exception | None = None
    for drv in available_sql_drivers():
        try:
            return pyodbc.connect(
                build_connection_string(server, database, username, password, driver=drv),
                timeout=timeout)
        except pyodbc.Error as e:
            last_err = e
    raise last_err or pyodbc.Error("No usable SQL Server ODBC driver found")


# ----------------------------------------------------------------------------
# Connect / disconnect (per role)
# ----------------------------------------------------------------------------
def connect(role: str, server: str, database: str,
            username: str, password: str) -> bool:
    """Open (or replace) the connection for one role. Saves settings on success.

    Raises pyodbc.Error on failure so the UI can show the message.
    """
    if role not in _ROLES:
        raise ValueError(f"Unknown connection role: {role!r}")

    conn = _connect_any(server, database, username, password, timeout=30)
    conn.autocommit = False

    # Replace any existing connection for this role
    close(role)
    _connections[role] = conn
    _info[role] = (server, database)
    _creds[role] = (server, database, username, password)
    _save_role_settings(role, server, database, username, password)
    return True


def reconnect(role: str) -> bool:
    """Re-open a role's connection using the last-used credentials — for recovering
    a dropped link mid-migration. Raises if there are no stored credentials."""
    c = _creds.get(role)
    if not c:
        raise RuntimeError(f"No stored credentials to reconnect {role!r}.")
    return connect(role, *c)


def reconnect_web() -> bool:
    return reconnect(WEB)


def connect_desktop(server, database, username, password) -> bool:
    return connect(DESKTOP, server, database, username, password)


def connect_web(server, database, username, password) -> bool:
    return connect(WEB, server, database, username, password)


def get(role: str) -> pyodbc.Connection:
    """Return the active connection for a role. Raises if not connected."""
    conn = _connections.get(role)
    if conn is None:
        label = "Desktop (source)" if role == DESKTOP else "Web (target)"
        raise RuntimeError(f"{label} database is not connected.")
    return conn


def get_desktop() -> pyodbc.Connection:
    return get(DESKTOP)


def get_web() -> pyodbc.Connection:
    return get(WEB)


def get_info(role: str) -> tuple[str, str]:
    """Return (server, database) for a role."""
    return _info.get(role, ("", ""))


def is_connected(role: str) -> bool:
    return _connections.get(role) is not None


def both_connected() -> bool:
    return is_connected(DESKTOP) and is_connected(WEB)


def close(role: str):
    conn = _connections.get(role)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _connections[role] = None
    _info[role] = ("", "")


def close_all():
    for role in _ROLES:
        close(role)


# ----------------------------------------------------------------------------
# Query helpers (parameterised)
# ----------------------------------------------------------------------------
def query(role: str, sql: str, params=None) -> list[dict]:
    """Run a SELECT against a role's connection; return list of row dicts."""
    cursor = get(role).cursor()
    cursor.execute(sql, params or [])
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def query_desktop(sql: str, params=None) -> list[dict]:
    return query(DESKTOP, sql, params)


def query_web(sql: str, params=None) -> list[dict]:
    return query(WEB, sql, params)


def list_databases(server: str, username: str, password: str) -> list[str]:
    """Connect to a server's master DB and list online user databases."""
    conn = _connect_any(server, "master", username, password, timeout=10)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sys.databases WHERE state_desc = 'ONLINE' "
            "AND name NOT IN ('master', 'model', 'msdb', 'tempdb') ORDER BY name"
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()
