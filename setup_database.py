#!/usr/bin/env python3
"""
ERP_AI local database bootstrapper.

Run from the repository root:
    python setup_database.py

Expected root-level files:
    schema.sql
    seed.sql
    seed2.py

The script can install PostgreSQL using common package managers, but OS
installers may still require administrator approval or interactive input.
It never drops an existing database and will not seed a non-empty database.
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

try:
    import psycopg
    from psycopg.conninfo import make_conninfo
    from dotenv import dotenv_values
except ImportError:
    print("Missing Python packages. Activate your project venv and run:")
    print("  python -m pip install -r requirements.txt")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent
SCHEMA_FILE = ROOT / "database" / "schema.sql"
SEED_FILE = ROOT / "database" / "seed.sql"
SEED2_FILE = ROOT / "database" / "seed2.py"
ENV_FILE = ROOT / ".env"
DB_NAME = "erp_ai"


def run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    env: dict[str, str] | None = None,
):
    try:
        return subprocess.run(
            command,
            check=check,
            text=True,
            capture_output=capture,
            env=env,
        )
    except FileNotFoundError:
        return None


def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found

    # Homebrew may install versioned PostgreSQL tools outside PATH.
    if platform.system() == "Darwin" and shutil.which("brew"):
        result = run(["brew", "--prefix", "postgresql@17"], check=False, capture=True)
        if result and result.returncode == 0:
            candidate = Path(result.stdout.strip()) / "bin" / name
            if candidate.exists():
                return str(candidate)
        result = run(["brew", "--prefix", "postgresql"], check=False, capture=True)
        if result and result.returncode == 0:
            candidate = Path(result.stdout.strip()) / "bin" / name
            if candidate.exists():
                return str(candidate)
    return None


def install_postgres() -> bool:
    system = platform.system()
    print("\nPostgreSQL tools were not found. Attempting an installation...")

    if system == "Darwin":
        brew = shutil.which("brew")
        if not brew:
            print("Homebrew is not installed. Install Homebrew and PostgreSQL, then rerun:")
            print("  brew install postgresql@17")
            print("  brew services start postgresql@17")
            return False
        result = run([brew, "install", "postgresql@17"], check=False)
        if result is None or result.returncode != 0:
            print("Automatic installation failed. Try: brew install postgresql@17")
            return False
        run([brew, "services", "start", "postgresql@17"], check=False)
        return True

    if system == "Linux":
        if shutil.which("apt-get"):
            print("Installing PostgreSQL through apt (administrator permission may be requested).")
            result = run(["sudo", "apt-get", "update"], check=False)
            if result is None or result.returncode != 0:
                return False
            result = run(
                ["sudo", "apt-get", "install", "-y", "postgresql", "postgresql-contrib"],
                check=False,
            )
            if result is None or result.returncode != 0:
                return False
            run(["sudo", "service", "postgresql", "start"], check=False)
            print(
                "\nPostgreSQL was installed. Linux commonly uses peer authentication for the "
                "'postgres' account. If the connection step fails, create/configure a local "
                "database role with permission to create databases, then rerun this script."
            )
            return True
        print("Automatic installation is supported here only for Debian/Ubuntu (apt-get).")
        print("Install PostgreSQL using your distribution's package manager, then rerun.")
        return False

    if system == "Windows":
        winget = shutil.which("winget")
        if not winget:
            print("winget was not found. Install PostgreSQL using the official Windows installer,")
            print("set a password for the postgres database user, then rerun this script.")
            return False
        print("Launching the PostgreSQL installer. Follow its prompts and remember the password.")
        result = run(
            [winget, "install", "--id", "PostgreSQL.PostgreSQL", "--exact", "--interactive"],
            check=False,
        )
        if result is None or result.returncode != 0:
            print("The installer did not complete successfully. Install PostgreSQL manually and rerun.")
            return False
        print("Installation finished. If this script cannot find psql yet, restart the terminal and rerun.")
        return True

    print(f"Automatic installation is not configured for {system}. Install PostgreSQL and rerun.")
    return False


def wait_for_server(pg_isready: str | None, host: str, port: int) -> bool:
    if not pg_isready:
        return False
    for _ in range(15):
        result = run([pg_isready, "-h", host, "-p", str(port)], check=False, capture=True)
        if result and result.returncode == 0:
            return True
        time.sleep(1)
    return False


def read_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    values = dotenv_values(ENV_FILE)
    return {key: value for key, value in values.items() if value is not None}


def write_env(values: dict[str, str]) -> None:
    # Preserve comments and unrelated settings; only append/update managed keys.
    existing_lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    managed = {
        "JWT_SECRET_KEY",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "ERP_DATABASE_URL",
    }
    output: list[str] = []
    seen: set[str] = set()

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in managed:
                if key not in seen:
                    output.append(f"{key}={values.get(key, '')}")
                    seen.add(key)
                continue
        output.append(line)

    for key in managed:
        if key not in seen:
            output.append(f"{key}={values.get(key, '')}")

    ENV_FILE.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def main() -> int:
    print("=== ERP_AI database setup ===")
    print("This script does not delete databases or overwrite existing seed data.\n")

    missing_files = [p.name for p in (SCHEMA_FILE, SEED_FILE, SEED2_FILE) if not p.is_file()]
    if missing_files:
        print("Required files were not found at the repository root:")
        for name in missing_files:
            print(f"  - {name}")
        print("Expected locations: database/schema.sql, database/seed.sql, and database/seed2.py.")
        return 1

    psql = find_tool("psql")
    pg_isready = find_tool("pg_isready")

    if not psql:
        if not install_postgres():
            return 1
        psql = find_tool("psql")
        pg_isready = find_tool("pg_isready")

    if not psql:
        print("\nPostgreSQL installation may have completed, but psql was not found.")
        print("Restart your terminal, ensure PostgreSQL's bin directory is on PATH, and rerun.")
        return 1

    current_env = read_env()
    host = current_env.get("PGHOST") or input("PostgreSQL host [localhost]: ").strip() or "localhost"
    port_text = current_env.get("PGPORT") or input("PostgreSQL port [5432]: ").strip() or "5432"
    try:
        port = int(port_text)
    except ValueError:
        print("Port must be a number.")
        return 1

    default_user = current_env.get("PGUSER") or (os.getenv("USER") or os.getenv("USERNAME") or "postgres")
    user = input(f"PostgreSQL admin/local username [{default_user}]: ").strip() or default_user
    password = current_env.get("PGPASSWORD")
    if password is None:
        password = getpass.getpass("PostgreSQL password (leave blank if local auth needs none): ")

    env = os.environ.copy()
    env.update({"PGHOST": host, "PGPORT": str(port), "PGUSER": user, "PGPASSWORD": password})

    if not wait_for_server(pg_isready, host, port):
        print(f"\nPostgreSQL is not responding at {host}:{port}.")
        print("Start the PostgreSQL service, then run this script again.")
        return 1

    admin_conninfo = make_conninfo(host=host, port=port, user=user, password=password, dbname="postgres")
    try:
        with psycopg.connect(admin_conninfo, connect_timeout=5, autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{DB_NAME}"')
                print(f"Created database: {DB_NAME}")
            else:
                print(f"Database already exists: {DB_NAME}")

        app_conninfo = make_conninfo(host=host, port=port, user=user, password=password, dbname=DB_NAME)
        with psycopg.connect(app_conninfo, connect_timeout=5) as conn:
            tables = conn.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                """
            ).fetchall()
            table_names = {row[0] for row in tables}

        expected_tables = {"users", "students", "teachers", "courses", "enrollments", "assignments", "submissions"}
        if not table_names:
            print("\nInitializing empty database schema...")
            result = run(
                [psql, "-h", host, "-p", str(port), "-U", user, "-d", DB_NAME,
                 "-v", "ON_ERROR_STOP=1", "-f", str(SCHEMA_FILE)],
                check=False,
                capture=False,
            )
            if result is None or result.returncode != 0:
                print("Schema initialization failed. Fix the error above and rerun.")
                return 1

            print("Loading seed.sql...")
            result = run(
                [psql, "-h", host, "-p", str(port), "-U", user, "-d", DB_NAME,
                 "-v", "ON_ERROR_STOP=1", "-f", str(SEED_FILE)],
                check=False,
                capture=False,
            )
            if result is None or result.returncode != 0:
                print("Seed loading failed. The schema may now exist; inspect the database before rerunning.")
                return 1
        elif not expected_tables.issubset(table_names):
            missing = sorted(expected_tables - table_names)
            print("Database is partially initialized. Missing tables: " + ", ".join(missing))
            print("No schema or seed changes were applied. Resolve this manually to avoid data loss.")
            return 1
        else:
            print("Existing schema detected; schema.sql and seed.sql were NOT rerun.")
            print("This protects existing records from duplicate inserts.")

        # Run seed2 only when the expected demo courses/assignments exist.
        with psycopg.connect(app_conninfo, connect_timeout=5) as conn:
            course_count = conn.execute(
                "SELECT COUNT(*) FROM courses WHERE course_code IN "
                "('CS301','CS302','CS303','CS304','MA301','MA302','EC301','EC302','CS305','CS306')"
            ).fetchone()[0]
        if course_count == 10:
            print("\nApplying/validating course-specific assignment titles with seed2.py...")
            seed_env = env.copy()
            seed_env["PGHOST"] = host
            seed_env["PGPORT"] = str(port)
            seed_env["PGUSER"] = user
            seed_env["PGPASSWORD"] = password
            result = run([sys.executable, str(SEED2_FILE)], check=False, env=seed_env)
            if result is None or result.returncode != 0:
                print("seed2.py failed. Review its output; its transaction should roll back on validation errors.")
                return 1
        else:
            print(f"\nFound {course_count}/10 expected demo courses; skipped seed2.py.")

        # Preserve an existing JWT secret; generate only if missing/placeholder.
        jwt_secret = current_env.get("JWT_SECRET_KEY", "")
        if not jwt_secret or jwt_secret in {"your_generated_secret_here", "paste-your-generated-secret-here"}:
            jwt_secret = secrets.token_hex(32)
            print("Generated a new local JWT secret.")
        else:
            print("Preserved the existing JWT secret.")

        dsn = make_conninfo(host=host, port=port, user=user, password=password, dbname=DB_NAME)
        env_values = {
            "JWT_SECRET_KEY": jwt_secret,
            "PGHOST": host,
            "PGPORT": str(port),
            "PGUSER": user,
            "PGPASSWORD": password,
            "ERP_DATABASE_URL": dsn,
        }
        write_env(env_values)

        with psycopg.connect(app_conninfo, connect_timeout=5) as conn:
            counts = {}
            for table in ("users", "students", "teachers", "courses", "enrollments", "assignments", "submissions"):
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        print("\nSUCCESS — ERP_AI database is ready.")
        print("Database: erp_ai")
        print("Tables: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        print(f"Configuration written to: {ENV_FILE}")
        print("\nStart the backend with:")
        print("  python -m uvicorn backend.main:app --reload")
        print("\nNote: your backend modules must load .env before connecting, and use package-correct imports.")
        return 0

    except psycopg.Error as exc:
        print(f"\nPostgreSQL connection/setup error: {exc}")
        print("Check that PostgreSQL is running and the username/password can connect and create databases.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
