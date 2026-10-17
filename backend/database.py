import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# Load the project's root .env file
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def get_connection():
    """Create a PostgreSQL connection using environment configuration."""
    params = {
        "dbname": os.getenv("PGDATABASE", "erp_ai"),
    }

    host = os.getenv("PGHOST")
    port = os.getenv("PGPORT")
    user = os.getenv("PGUSER")
    password = os.getenv("PGPASSWORD")

    if host:
        params["host"] = host

    if port:
        params["port"] = port

    if user:
        params["user"] = user

    if password:
        params["password"] = password

    return psycopg.connect(**params)


def get_db():
    """Provide a database connection for FastAPI dependency injection."""
    conn = get_connection()

    try:
        yield conn
    finally:
        conn.close()