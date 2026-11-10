"""Apply sql/*.sql to DATABASE_URL."""

from __future__ import annotations

import sys

from landis import config as cfg
from landis import store_pg


def main() -> int:
    try:
        store_pg.init_schema()
    except Exception as exc:
        print(f"Schema initialisation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Schema applied from {cfg.SQL_DIR}")
    try:
        store_pg.ping_database()
    except Exception as exc:
        print(f"Connection check failed: {exc}", file=sys.stderr)
        return 1
    print("Database connection OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
