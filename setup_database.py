#!/usr/bin/env python3
"""
ERP_AI one-command local database setup.

Run from the repository root:
    python setup_database.py

Expected files:
    database/schema.sql
    database/seed.sql
    database/seed2.py

The script never drops an existing database or deletes existing records.
It initializes an empty database and preserves existing data.

Supports PostgreSQL installations on Linux, macOS, and Windows.
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
    "users",
    "students",
    "teachers",
    "courses",
    "enrollments",
    "assignments",
    "submissions",
}


def say(message: str = "") -> None:
    print(message, flush=True)


def run(
    command: list[str],
    *,
    check: bool = False,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess | None:
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


def tool(name: str) -> str | None:
    return shutil.which(name)


def find_pg_tool(name: str) -> str | None:
    """Find PostgreSQL utilities, including common Homebrew installations."""
    found = tool(name)
    if found:
        return found

    if platform.system() == "Darwin" and tool("brew"):
        for formula in (
            "postgresql@18",
            "postgresql@17",
            "postgresql@16",
            "postgresql",
        ):
            result = run(
                ["brew", "--prefix", formula],
                capture=True,
            )
            if result and result.returncode == 0:
                candidate = Path(result.stdout.strip()) / "bin" / name
                if candidate.exists():
                    return str(candidate)

    if platform.system() == "Windows":
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "PostgreSQL"
        ]
        for root in roots:
            if root.exists():
                versions = sorted(
                    [p for p in root.iterdir() if p.is_dir()],
                    reverse=True,
                )
                for version in versions:
                    candidate = version / "bin" / f"{name}.exe"
                    if candidate.exists():
                        return str(candidate)

    return None


def read_env() -> dict[str, str]:
    """Read simple KEY=VALUE entries from the root .env file."""
    values: dict[str, str] = {}

    if not ENV_FILE.exists():
        return values

    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values


def write_env(values: dict[str, str]) -> None:
    """Update managed configuration while preserving unrelated .env entries."""
    managed = {
        "JWT_SECRET_KEY",
        "PGHOST",
        "PGPORT",
        "PGUSER",
        "PGPASSWORD",
        "PGDATABASE",
    }

    lines = (
        ENV_FILE.read_text(encoding="utf-8").splitlines()
        if ENV_FILE.exists()
        else []
    )

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

    for key in sorted(managed):
        if key not in written:
            output.append(f'{key}="{values.get(key, "")}"')

    ENV_FILE.write_text(
        "\n".join(output).rstrip() + "\n",
        encoding="utf-8",
    )

    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def install_postgres() -> bool:
    """Attempt supported package-manager installation."""
    system = platform.system()

    say("\nPostgreSQL tools are missing. Attempting installation...")

    if system == "Linux":
        apt = tool("apt-get")

        if apt and tool("sudo"):
            commands = [
                ["sudo", apt, "update"],
                [
                    "sudo",
                    apt,
                    "install",
                    "-y",
                    "postgresql",
                    "postgresql-client",
                    "postgresql-contrib",
                ],
            ]

            for command in commands:
                result = run(command)

                if result is None or result.returncode != 0:
                    say("Automatic installation failed.")
                    return False

            run(["sudo", "systemctl", "enable", "--now", "postgresql"])
            return True

        say("Automatic installation requires apt-get and sudo.")
        say("Install PostgreSQL using your distribution's package manager.")
        return False

    if system == "Darwin":
        brew = tool("brew")

        if not brew:
            say("Homebrew is required for automatic macOS installation.")
            say("Install PostgreSQL manually and rerun this script.")
            return False

        result = run([brew, "install", "postgresql@17"])

        if result is not None and result.returncode != 0:
            say("Homebrew could not install PostgreSQL automatically.")
            say("Check your PostgreSQL installation and rerun.")
            return False

        run([brew, "services", "start", "postgresql@17"])
        return True

    if system == "Windows":
        winget = tool("winget")

        if not winget:
            say("winget is unavailable. Install PostgreSQL using its Windows installer.")
            return False

        result = run(
            [
                winget,
                "install",
                "--id",
                "PostgreSQL.PostgreSQL",
                "--exact",
                "--interactive",
            ]
        )

        if result is None or result.returncode != 0:
            say("PostgreSQL installation did not complete successfully.")
            return False

        say("Restart the terminal if PostgreSQL tools are not on PATH.")
        return True

    say(f"Automatic installation is not configured for {system}.")
    return False


def ensure_linux_cluster() -> None:
    """Start an existing Debian/Ubuntu cluster when possible."""
    if platform.system() != "Linux" or not tool("pg_lsclusters"):
        return

    result = run(["pg_lsclusters"], capture=True)

    if not result or result.returncode != 0:
        return

    rows = [
        line
        for line in result.stdout.splitlines()[1:]
        if line.strip()
    ]

    if not rows:
        return

    for row in rows:
        parts = row.split()

        if len(parts) >= 4 and parts[3] != "online":
            version, cluster = parts[0], parts[1]
            say(f"Starting PostgreSQL cluster {version}/{cluster}...")
            run(["sudo", "pg_ctlcluster", version, cluster, "start"])


def wait_ready(
    pg_isready: str,
    host: str,
    port: str,
) -> bool:
    for _ in range(15):
        command = [pg_isready, "-p", port]

        if host:
            command.extend(["-h", host])

        result = run(command, capture=True)

        if result and result.returncode == 0:
            return True

        time.sleep(1)

    return False


def connect_params(
    database: str,
    host: str,
    port: str,
    user: str,
    password: str,
) -> dict:
    """Build psycopg parameters, omitting optional empty values."""
    params = {
        "dbname": database,
        "connect_timeout": 5,
    }

    if host:
        params["host"] = host

    if port:
        params["port"] = int(port)

    if user:
        params["user"] = user

    if password:
        params["password"] = password

    return params


def try_connection(psycopg, params: dict) -> bool:
    try:
        with psycopg.connect(**params):
            return True
    except Exception:
        return False


def detect_connection(psycopg, existing_env: dict[str, str]):
    """
    Detect a working local PostgreSQL connection.

    Explicit environment/.env settings take priority. If they fail, try
    common local defaults without printing credentials.
    """
    system = platform.system()

    env_host = (
        existing_env.get("PGHOST")
        or os.environ.get("PGHOST", "")
    )
    env_port = (
        existing_env.get("PGPORT")
        or os.environ.get("PGPORT", "5432")
    )
    env_user = (
        existing_env.get("PGUSER")
        or os.environ.get("PGUSER", "")
    )
    env_password = (
        existing_env.get("PGPASSWORD")
        if "PGPASSWORD" in existing_env
        else os.environ.get("PGPASSWORD", "")
    )

    os_user = (
        os.environ.get("USER")
        or os.environ.get("USERNAME")
        or getpass.getuser()
    )

    candidates = []

    # First respect the user's configured PostgreSQL settings.
    candidates.append(
        (env_host, env_port, env_user or os_user, env_password)
    )

    # Linux local peer authentication commonly uses a Unix socket.
    if system == "Linux":
        for socket_path in (
            "",
            "/var/run/postgresql",
            "/run/postgresql",
        ):
            candidates.append(
                (socket_path, env_port, env_user or os_user, env_password)
            )

        # A local TCP connection may use password authentication.
        candidates.append(
            ("localhost", env_port, env_user or os_user, env_password)
        )

    elif system == "Darwin":
        candidates.extend(
            [
                ("localhost", env_port, env_user or os_user, env_password),
                ("", env_port, env_user or os_user, env_password),
            ]
        )

    else:
        candidates.extend(
            [
                ("localhost", env_port, env_user or "postgres", env_password),
                ("127.0.0.1", env_port, env_user or "postgres", env_password),
            ]
        )

    # Remove duplicate candidate combinations.
    unique_candidates = list(dict.fromkeys(candidates))

    for host, port, user, password in unique_candidates:
        params = connect_params(
            "postgres",
            host,
            port,
            user,
            password,
        )

        if try_connection(psycopg, params):
            return host, port, user, password

    # If password authentication appears necessary, prompt interactively.
    if system == "Windows" or system == "Darwin":
        user = env_user or ("postgres" if system == "Windows" else os_user)
        password = getpass.getpass(
            f"Password for PostgreSQL user '{user}': "
        )

        for host in ("localhost", "127.0.0.1", ""):
            params = connect_params(
                "postgres",
                host,
                env_port,
                user,
                password,
            )

            if try_connection(psycopg, params):
                return host, env_port, user, password

    return None


def main() -> int:
    say("=" * 56)
    say(" ERP_AI — local database setup")
    say("=" * 56)
    say("Existing databases and records will not be deleted.")

    missing = [
        str(path.relative_to(ROOT))
        for path in (SCHEMA_FILE, SEED_FILE, SEED2_FILE)
        if not path.is_file()
    ]

    if missing:
        say("\nERROR: Required files are missing:")

        for filename in missing:
            say(f"  - {filename}")

        return 1

    try:
        import psycopg
    except ImportError:
        say("\nERROR: Python package 'psycopg' is missing.")
        say("Run: python -m pip install -r requirements.txt")
        return 1

    pg_isready = find_pg_tool("pg_isready")
    psql = find_pg_tool("psql")

    if not pg_isready or not psql:
        if not install_postgres():
            return 1

        pg_isready = find_pg_tool("pg_isready")
        psql = find_pg_tool("psql")

    if not pg_isready or not psql:
        say("\nERROR: PostgreSQL client tools are still unavailable.")
        say("Restart the terminal and verify PostgreSQL's bin directory is on PATH.")
        return 1

    ensure_linux_cluster()

    existing_env = read_env()

    detected = detect_connection(psycopg, existing_env)

    if not detected:
        say("\nERROR: Could not connect to PostgreSQL using detected settings.")
        say("Check that PostgreSQL is running and that your user has permission")
        say("to connect to its maintenance database, 'postgres'.")
        say("For password authentication, set PGUSER/PGPASSWORD in .env and rerun.")
        return 1

    host, port, user, password = detected

    if not wait_ready(pg_isready, host, port):
        say("\nERROR: PostgreSQL did not pass its readiness check.")
        return 1

    say(f"\nDetected PostgreSQL connection for user '{user}'.")

    # Ensure child scripts use precisely the same connection settings.
    child_env = os.environ.copy()
    child_env.update(
        {
            "PGDATABASE": DB_NAME,
            "PGPORT": port,
            "PGUSER": user,
        }
    )

    if host:
        child_env["PGHOST"] = host
    else:
        child_env.pop("PGHOST", None)

    if password:
        child_env["PGPASSWORD"] = password
    else:
        child_env.pop("PGPASSWORD", None)

    admin_params = connect_params(
        "postgres",
        host,
        port,
        user,
        password,
    )

    try:
        # Create the application database only when it doesn't exist.
        with psycopg.connect(**admin_params, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (DB_NAME,),
            ).fetchone()

            if not exists:
                conn.execute(f'CREATE DATABASE "{DB_NAME}"')
                say(f"\nCreated database '{DB_NAME}'.")
            else:
                say(f"\nDatabase '{DB_NAME}' already exists.")

        app_params = connect_params(
            DB_NAME,
            host,
            port,
            user,
            password,
        )

        with psycopg.connect(**app_params) as conn:
            table_rows = conn.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                """
            ).fetchall()

            table_names = {row[0] for row in table_rows}

        if not table_names:
            say("\n[1/5] Initializing schema...")

            result = run(
                [
                    psql,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-d",
                    DB_NAME,
                    "-f",
                    str(SCHEMA_FILE),
                ],
                env=child_env,
            )

            if result is None or result.returncode != 0:
                say("ERROR: Schema initialization failed.")
                return 1

            say("\n[2/5] Loading initial seed data...")

            result = run(
                [
                    psql,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-d",
                    DB_NAME,
                    "-f",
                    str(SEED_FILE),
                ],
                env=child_env,
            )

            if result is None or result.returncode != 0:
                say("ERROR: Seed loading failed. Inspect the database before retrying.")
                return 1

        elif not EXPECTED_TABLES.issubset(table_names):
            missing_tables = sorted(EXPECTED_TABLES - table_names)

            say("\nERROR: The database has a partial schema.")
            say("Missing tables: " + ", ".join(missing_tables))
            say("No schema or seed files were run to protect existing data.")
            return 1

        else:
            say("\nExisting schema detected; preserving current records.")
            say("Initial seed file skipped.")

        with psycopg.connect(**app_params) as conn:
            course_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM courses
                WHERE course_code = ANY(%s)
                """,
                (
                    [
                        "CS301",
                        "CS302",
                        "CS303",
                        "CS304",
                        "MA301",
                        "MA302",
                        "EC301",
                        "EC302",
                        "CS305",
                        "CS306",
                    ],
                ),
            ).fetchone()[0]

        if course_count == 10:
            say("\n[3/5] Applying course-specific assignment seed...")

            result = run(
                [sys.executable, str(SEED2_FILE)],
                env=child_env,
            )

            if result is None or result.returncode != 0:
                say("ERROR: seed2.py failed. Review its output.")
                return 1
        else:
            say(
                f"\n[3/5] Assignment customization skipped "
                f"({course_count}/10 expected courses found)."
            )

        jwt_secret = existing_env.get("JWT_SECRET_KEY", "").strip()

        placeholders = {
            "",
            "your_generated_secret_here",
            "paste_the_generated_key_here",
            "paste-your-generated-secret-here",
        }

        if jwt_secret in placeholders:
            jwt_secret = secrets.token_hex(32)
            say("\nGenerated a new local JWT secret.")
        else:
            say("\nPreserved the existing JWT secret.")

        env_values = {
            "JWT_SECRET_KEY": jwt_secret,
            "PGDATABASE": DB_NAME,
            "PGHOST": host,
            "PGPORT": port,
            "PGUSER": user,
            "PGPASSWORD": password,
        }

        write_env(env_values)

        say(f"\nSaved machine-specific configuration to {ENV_FILE}.")

        with psycopg.connect(**app_params) as conn:
            counts = {
                table: conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                for table in sorted(EXPECTED_TABLES)
            }

        say("\n[4/5] Database verification complete.")
        say("SUCCESS — ERP_AI setup finished.")
        say(f"Database: {DB_NAME}")
        say(
            "Row counts: "
            + ", ".join(
                f"{table}={count}"
                for table, count in counts.items()
            )
        )

        say("\n[5/5] Next steps:")
        say("1. Start the backend from the backend directory:")
        say("   cd backend")
        say("   python -m uvicorn main:app --reload")
        say("2. Open frontend/index.html with VS Code Live Server.")

        return 0

    except Exception as exc:
        say(f"\nSETUP FAILED: {type(exc).__name__}: {exc}")
        say("No database was dropped. Correct the configuration and rerun.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())