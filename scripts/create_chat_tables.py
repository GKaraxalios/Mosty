#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
os.chdir(_project_root)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Load .env into os.environ so get_settings() sees it (fixes cwd/env_file path)
_env_file = os.path.join(_project_root, ".env")
if os.path.isfile(_env_file):
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k.startswith("DB_") or k in ("APP_ENV", "MEMORY_BACKEND"):
                    os.environ[k] = v

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


def build_server_url(settings) -> str:
    """
    Connect to MySQL server without selecting a database,
    so we can create the database if it does not exist.
    """
    return str(
        URL.create(
            drivername="mysql+pymysql",
            username=settings.DB_USER,
            password=settings.DB_PASSWORD or "",
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=None,
            query={"charset": settings.DB_CHARSET},
        )
    )


def build_database_url(settings) -> str:
    """
    Connect directly to the target database.
    """
    if settings.DATABASE_URL and settings.DATABASE_URL.strip():
        return settings.DATABASE_URL.strip()

    return str(
        URL.create(
            drivername="mysql+pymysql",
            username=settings.DB_USER,
            password=settings.DB_PASSWORD or "",
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=settings.DB_NAME,
            query={"charset": settings.DB_CHARSET},
        )
    )


def ensure_database_exists(settings) -> None:
    server_url = build_server_url(settings)
    db_name = settings.DB_NAME

    print(f"Server URL: mysql+pymysql://{settings.DB_USER}:***@{settings.DB_HOST}:{settings.DB_PORT}?charset={settings.DB_CHARSET}")
    print(f"Database name: {db_name}")
    print(f"Creating database '{db_name}' if it does not exist...")

    engine = create_engine(server_url, pool_pre_ping=True)

    create_db_sql = text(
        f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )

    with engine.connect() as conn:
        conn.execute(create_db_sql)
        conn.commit()

    engine.dispose()


def _build_url_from_env() -> str:
    """Build DB URL from os.environ (already loaded from .env). Safe for special chars."""
    from urllib.parse import quote_plus
    host = os.environ.get("DB_HOST_LOCAL") or os.environ.get("DB_HOST") or "127.0.0.1"
    port = os.environ.get("DB_PORT_LOCAL") or os.environ.get("DB_PORT") or "8889"
    name = os.environ.get("DB_NAME_LOCAL") or os.environ.get("DB_NAME") or "mosty"
    user = os.environ.get("DB_USER_LOCAL") or os.environ.get("DB_USER") or "root"
    password = os.environ.get("DB_PASSWORD_LOCAL") or os.environ.get("DB_PASSWORD") or ""
    charset = os.environ.get("DB_CHARSET") or "utf8mb4"
    user_enc = quote_plus(user)
    pass_enc = quote_plus(password)
    return f"mysql+pymysql://{user_enc}:{pass_enc}@{host}:{port}/{name}?charset={charset}"


def main() -> int:
    from app.db.models import Base

    backend = (os.environ.get("MEMORY_BACKEND") or "memory").strip().lower()
    print("Resolved (from .env):")
    print(f"  APP_ENV={os.environ.get('APP_ENV', 'local')}")
    print(f"  MEMORY_BACKEND={backend}")
    print(f"  DB_HOST_LOCAL={os.environ.get('DB_HOST_LOCAL', '')}")
    print(f"  DB_PORT_LOCAL={os.environ.get('DB_PORT_LOCAL', '')}")
    print(f"  DB_NAME_LOCAL={os.environ.get('DB_NAME_LOCAL', '')}")
    print(f"  DB_USER_LOCAL={os.environ.get('DB_USER_LOCAL', '')}")

    if backend != "mysql":
        print("Warning: MEMORY_BACKEND is not 'mysql'. Set MEMORY_BACKEND=mysql.")

    try:
        db_url = _build_url_from_env()
        print(f"Connecting to database...")
        engine = create_engine(db_url, pool_pre_ping=True)

        print("Creating tables (chat_sessions, chat_messages)...")
        Base.metadata.create_all(bind=engine)

        for table in Base.metadata.sorted_tables:
            print(f"  OK: {table.name}")

        engine.dispose()
        print("Done.")
        return 0

    except Exception as e:
        print("\nDatabase connection failed.", file=sys.stderr)
        print(f"Error: {e}", file=sys.stderr)
        print("\nWhat to check:", file=sys.stderr)
        print("1. Is MySQL running?", file=sys.stderr)
        print("2. Are APP_ENV and DB_* variables correct?", file=sys.stderr)
        print("3. Are DB_USER_LOCAL / DB_PASSWORD_LOCAL correct in .env?", file=sys.stderr)
        print("4. Does the target database already exist if CREATE DATABASE is not allowed?", file=sys.stderr)
        print("5. If you use MAMP, is MySQL really on port 8889?", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())