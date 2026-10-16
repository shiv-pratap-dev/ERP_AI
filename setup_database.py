#!/usr/bin/env python3
"""
ERP_AI one-command local database setup.

Place this file in the repository root. Expected files:
    database/schema.sql
    database/seed.sql
    database/seed2.py

Run:
    python setup_database.py

This script never drops an existing database. It initializes and seeds only
a new/empty ERP database; existing databases are checked and left intact.
OS package installation may require administrator approval or interactive
installer steps, especially on Windows.
"""

from __future__ import annotations

import getpass
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_DIR = ROOT / "database"
SCHEMA_FILE = DB_DIR / "schema.sql"
SEED_FILE = DB_DIR / "seed.sql"
SEED2_FILE = DB_DIR / "seed2.py"
ENV_FILE = ROOT / ".env"
DB_NAME = "erp_ai"
EXPECTED_TABLES = {
    "users", "students", "teachers", "courses",
    "enrollments", "assignments", "submissions",
}


def say(message: str = "") -> None:
    print(message, flush=True)


def run(command: list[str], *, check: bool = False, capture: bool = False,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            command, check=check, text=True, capture_output=capture, env=env
        )
    except FileNotFoundError:
        return None


def tool(name: str) -> str | None:
    return shutil.which(name)


def read_env() -> dict[str, str]:
    result: dict[str, str] = {}
    if not ENV_FILE.exists():
        return result
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def write_env(values: dict[str, str]) -> None:
    """Update only keys managed by this script; retain unrelated lines/settings."""
    managed = {
        "JWT_SECRET_KEY", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD",
    }
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    output: list[str] = []
    written: set[str] = set()

    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in managed:
                if key not in written:
                    output.append(f'{key}="{values.get(key, "")}"')
                    written.add(key)
                continue
        output.append(raw)

    for key in managed:
        if key not in written:
            output.append(f'{key}="{values.get(key, "")}"')

    ENV_FILE.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def install_postgres() -> bool:
    system = platform.system()
    say("\n[1/7] PostgreSQL tools were not found. Attempting installation...")

    if system == "Linux":
        apt = tool("apt-get")
        if apt:
            if not tool("sudo"):
                say("sudo was not found. Install PostgreSQL with your Linux package manager, then rerun.")
                return False
            say("Installing PostgreSQL server and client with apt. Administrator approval may be requested.")
            for command in (
                ["sudo", apt, "update"],
                ["sudo", apt, "install", "-y", "postgresql", "postgresql-client", "postgresql-contrib"],
            ):
                result = run(command)
                if result is None or result.returncode != 0:
                    say("Package installation failed. Install PostgreSQL manually, then rerun this script.")
                    return False
            run(["sudo", "systemctl", "enable", "--now", "postgresql"])
            # Debian/Ubuntu can have the wrapper service without a cluster.
            if tool("pg_lsclusters") and tool("pg_createcluster"):
                result = run(["pg_lsclusters"], capture=True)
                output = result.stdout if result else ""
                if not any(line.strip() and line.strip()[0].isdigit() for line in output.splitlines()[1:]):
                    version_dir = Path("/usr/lib/postgresql")
                    versions = sorted(
                        [x.name for x in version_dir.iterdir() if x.is_dir() and x.name.isdigit()],
                        reverse=True,
                    ) if version_dir.exists() else []
                    if versions:
                        say(f"Initializing PostgreSQL {versions[0]} cluster...")
                        run(["sudo", "pg_createcluster", versions[0], "main", "--start"])
            return True
        say("Automatic installation is implemented for Debian/Ubuntu (apt) only.")
        say("Install PostgreSQL using your distribution's package manager, then rerun.")
        return False

    if system == "Darwin":
        brew = tool("brew")
        if not brew:
            say("Homebrew is not installed. Install Homebrew, then run: brew install postgresql@17")
            return False
        result = run([brew, "install", "postgresql@17"])
        if result is None or result.returncode != 0:
            # It may already be installed or Homebrew may have selected another version.
            pass
        run([brew, "services", "start", "postgresql@17"])
        return True

    if system == "Windows":
        winget = tool("winget")
        if not winget:
            say("winget was not found. Install PostgreSQL using the official Windows installer,")
            say("then rerun this script. Remember the password created for the postgres account.")
            return False
        say("Launching the PostgreSQL installer. Follow its prompts and remember the postgres password.")
        result = run([
            winget, "install", "--id", "PostgreSQL.PostgreSQL", "--exact", "--interactive"
        ])
        if result is None or result.returncode != 0:
            say("The installer did not complete successfully. Install PostgreSQL manually and rerun.")
            return False
        say("Installation finished. Restart this terminal if PostgreSQL tools are not on PATH.")
        return True

    say(f"Automatic installation is not configured for {system}. Install PostgreSQL and rerun.")
    return False


def find_pg_tool(name: str) -> str | None:
    found = tool(name)
    if found:
        return found
    if platform.system() == "Darwin" and tool("brew"):
        for formula in ("postgresql@17", "postgresql@16", "postgresql"):
            result = run(["brew", "--prefix", formula], capture=True)
            if result and result.returncode == 0:
                candidate = Path(result.stdout.strip()) / "bin" / name
                if candidate.exists():
                    return str(candidate)
    return None


def ensure_linux_cluster() -> None:
    if platform.system() != "Linux" or not tool("pg_lsclusters"):
        return
    result = run(["pg_lsclusters"], capture=True)
    if not result:
        return
    rows = [line for line in result.stdout.splitlines()[1:] if line.strip()]
    if rows:
        # Start any cluster that is listed but not online.
        for row in rows:
            parts = row.split()
            if len(parts) >= 4 and parts[3] != "online":
                version, cluster = parts[0], parts[1]
                say(f"Starting PostgreSQL cluster {version}/{cluster}...")
                run(["sudo", "pg_ctlcluster", version, cluster, "start"])
        return

    versions_dir = Path("/usr/lib/postgresql")
    versions = sorted(
        [x.name for x in versions_dir.iterdir() if x.is_dir() and x.name.isdigit()],
        reverse=True,
    ) if versions_dir.exists() else []
    if versions and tool("pg_createcluster"):
        say(f"No PostgreSQL cluster exists. Creating {versions[0]}/main...")
        run(["sudo", "pg_createcluster", versions[0], "main", "--start"])


def wait_ready(pg_isready: str, host: str, port: str) -> bool:
    for _ in range(20):
        args = [pg_isready, "-p", port]
        if host:
            args += ["-h", host]
        result = run(args, capture=True)
        if result and result.returncode == 0:
            return True
        time.sleep(1)
    return False


def main() -> int:
    say("=" * 54)
    say(" ERP_AI — database setup")
    say("=" * 54)
    say("This script will not drop a database or overwrite existing records.")

    missing = [str(p.relative_to(ROOT)) for p in (SCHEMA_FILE, SEED_FILE, SEED2_FILE) if not p.is_file()]
    if missing:
        say("\nERROR: Required files are missing:")
        for name in missing:
            say(f"  - {name}")
        say("Expected: database/schema.sql, database/seed.sql, database/seed2.py")
        return 1

    try:
        import psycopg
    except ImportError:
        say("\nERROR: Python package 'psycopg' is missing.")
        say("Activate your virtual environment and run: python -m pip install -r requirements.txt")
        return 1

    pg_isready = find_pg_tool("pg_isready")
    psql = find_pg_tool("psql")
    if not pg_isready or not psql:
        if not install_postgres():
            return 1
        # Refresh PATH and discover versioned Homebrew tools.
        pg_isready = find_pg_tool("pg_isready")
        psql = find_pg_tool("psql")

    if not pg_isready or not psql:
        say("\nERROR: PostgreSQL client tools are still unavailable.")
        say("Restart the terminal, ensure PostgreSQL's bin directory is on PATH, and rerun.")
        return 1

    ensure_linux_cluster()

    existing_env = read_env()
    system = platform.system()
    if system == "Linux":
        # Debian/Ubuntu's default local auth is commonly peer. Use a Unix socket
        # and the current OS username, creating a matching local role if needed.
        host = existing_env.get("PGHOST", "")
        port = existing_env.get("PGPORT", "5432")
        user = existing_env.get("PGUSER", "") or os.environ.get("USER", "postgres")
        password = existing_env.get("PGPASSWORD", "")
        if not host:
            say("\nUsing the local PostgreSQL socket (Linux peer-auth default).")
            say(f"Local database role: {user}")
            # Create a role matching the OS user if it doesn't already exist.
            check_role = run(["sudo", "-u", "postgres", "psql", "-tAc",
                              "SELECT 1 FROM pg_roles WHERE rolname = '" + user.replace("'", "''") + "'"],
                             capture=True)
            if check_role and check_role.returncode == 0 and check_role.stdout.strip() != "1":
                say(f"Creating local PostgreSQL role '{user}'...")
                created = run(["sudo", "-u", "postgres", "createuser", "--createdb", "--superuser", user])
                if created is None or created.returncode != 0:
                    say("Could not create the local PostgreSQL role. Ask an administrator to create it.")
                    return 1
            # Use socket-based peer auth; don't test TCP localhost.
    elif system == "Darwin":
        host = existing_env.get("PGHOST", "")
        port = existing_env.get("PGPORT", "5432")
        user = existing_env.get("PGUSER", "") or os.environ.get("USER", "postgres")
        password = existing_env.get("PGPASSWORD", "")
    else:
        host = existing_env.get("PGHOST", "localhost") or "localhost"
        port = existing_env.get("PGPORT", "5432")
        user = existing_env.get("PGUSER", "postgres") or "postgres"
        password = existing_env.get("PGPASSWORD")
        if password is None:
            password = getpass.getpass("PostgreSQL 'postgres' account password: ")

    # Test readiness. Linux socket mode omits -h; other platforms use host.
    readiness_host = host
    if not wait_ready(pg_isready, readiness_host, port):
        say(f"\nERROR: PostgreSQL is not responding on {host or 'the local socket'} (port {port}).")
        if system == "Linux":
            say("Try: sudo systemctl status postgresql --no-pager")
            say("Then: pg_lsclusters")
        elif system == "Darwin":
            say("Try: brew services list")
            say("Then start the installed postgresql service with brew services start <formula>.")
        else:
            say("Check that the PostgreSQL Windows service is running and port 5432 is available.")
        return 1

    # Keep libpq settings for psql and seed2.py; no credentials are printed.
    child_env = os.environ.copy()
    child_env.update({"PGPORT": port, "PGUSER": user, "PGPASSWORD": password})
    if host:
        child_env["PGHOST"] = host
    else:
        child_env.pop("PGHOST", None)

    try:
        # Connect to maintenance database first and create erp_ai if missing.
        admin_params = {"dbname": "postgres", "port": int(port), "user": user}
        if host:
            admin_params["host"] = host
        if password:
            admin_params["password"] = password
        with psycopg.connect(**admin_params, connect_timeout=5, autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{DB_NAME}"')
                say(f"\n[2/7] Created database '{DB_NAME}'.")
            else:
                say(f"\n[2/7] Database '{DB_NAME}' already exists.")

        app_params = dict(admin_params)
        app_params["dbname"] = DB_NAME
        with psycopg.connect(**app_params, connect_timeout=5) as conn:
            table_rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
            table_names = {row[0] for row in table_rows}

        if not table_names:
            say("\n[3/7] Creating database schema...")
            result = run([psql, "-v", "ON_ERROR_STOP=1", "-d", DB_NAME, "-f", str(SCHEMA_FILE)],
                         env=child_env)
            if result is None or result.returncode != 0:
                say("ERROR: Schema initialization failed. Fix the reported error before rerunning.")
                return 1

            say("\n[4/7] Loading initial seed data...")
            result = run([psql, "-v", "ON_ERROR_STOP=1", "-d", DB_NAME, "-f", str(SEED_FILE)],
                         env=child_env)
            if result is None or result.returncode != 0:
                say("ERROR: Seed loading failed. The database may be partially initialized.")
                say("Inspect it before rerunning; the script deliberately will not delete it.")
                return 1
        elif not EXPECTED_TABLES.issubset(table_names):
            missing_tables = sorted(EXPECTED_TABLES - table_names)
            say("\nERROR: Database exists but is only partially initialized.")
            say("Missing tables: " + ", ".join(missing_tables))
            say("No schema or seed files were run to protect existing data.")
            say("Repair the schema manually or use a fresh local database.")
            return 1
        else:
            say("\n[3/7] Existing schema detected; preserving existing data.")
            say("[4/7] Seed file skipped to prevent duplicate inserts.")

        # seed2.py reads libpq environment variables via psycopg.connect("dbname=erp_ai").
        with psycopg.connect(**app_params, connect_timeout=5) as conn:
            course_count = conn.execute(
                "SELECT COUNT(*) FROM courses WHERE course_code = ANY(%s)",
                (["CS301", "CS302", "CS303", "CS304", "MA301", "MA302",
                  "EC301", "EC302", "CS305", "CS306"],),
            ).fetchone()[0]

        if course_count == 10:
            say("\n[5/7] Applying course-specific assignment seed...")
            result = run([sys.executable, str(SEED2_FILE)], env=child_env)
            if result is None or result.returncode != 0:
                say("ERROR: seed2.py failed. Review its output; no database was dropped.")
                return 1
        else:
            say(f"\n[5/7] Assignment customization skipped ({course_count}/10 expected courses found).")

        jwt_secret = existing_env.get("JWT_SECRET_KEY", "")
        if not jwt_secret or jwt_secret in {
            "your_generated_secret_here", "paste_the_generated_key_here",
            "paste-your-generated-secret-here",
        }:
            jwt_secret = secrets.token_hex(32)
            say("\n[6/7] Generated a secure local JWT secret.")
        else:
            say("\n[6/7] Preserved your existing JWT secret.")

        env_values = {
            "JWT_SECRET_KEY": jwt_secret,
            "PGHOST": host,
            "PGPORT": port,
            "PGUSER": user,
            "PGPASSWORD": password,
        }
        write_env(env_values)
        say(f"Saved local configuration to {ENV_FILE}")

        with psycopg.connect(**app_params, connect_timeout=5) as conn:
            counts = {
                table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in sorted(EXPECTED_TABLES)
            }

        say("\n[7/7] Verification complete.")
        say("SUCCESS — ERP_AI database setup finished.")
        say(f"Database: {DB_NAME}")
        say("Row counts: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
        say("\nNext: start the backend from the repository root:")
        say("  python -m uvicorn backend.main:app --reload")
        say("\nThen open frontend/index.html with VS Code Live Server.")
        say("If backend startup reports an import/configuration error, that is separate from database setup.")
        return 0

    except Exception as exc:
        say(f"\nSETUP FAILED: {type(exc).__name__}: {exc}")
        say("No database was dropped. Review the error above, correct the configuration, and rerun.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())