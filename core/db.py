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
def build_connection_string(server: str, database: str,
                            username: str = "INDUS",
                            password: str = "Param@99811") -> str:
    return (
        f"DRIVER={{SQL Server}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
    )


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

    conn_str = build_connection_string(server, database, username, password)
    conn = pyodbc.connect(conn_str, timeout=10)
    conn.autocommit = False

    # Replace any existing connection for this role
    close(role)
    _connections[role] = conn
    _info[role] = (server, database)
    _save_role_settings(role, server, database, username, password)
    return True


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
    conn_str = build_connection_string(server, "master", username, password)
    conn = pyodbc.connect(conn_str, timeout=10)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sys.databases WHERE state_desc = 'ONLINE' "
            "AND name NOT IN ('master', 'model', 'msdb', 'tempdb') ORDER BY name"
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()
