"""Download open multi-table datasets as CSV files into eval/datasets/<name>/.

Usage (from the repo root):
    backend\\.venv\\Scripts\\python.exe eval\\fetch_datasets.py [jaffle_shop chinook tpch]
"""

from __future__ import annotations

import sys
import tempfile
import urllib.request
from pathlib import Path

import duckdb

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"

JAFFLE_BASE = "https://raw.githubusercontent.com/dbt-labs/jaffle_shop/main/seeds"
JAFFLE_FILES = ["raw_customers.csv", "raw_orders.csv", "raw_payments.csv"]
CHINOOK_URL = "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sqlite"
TPCH_SCALE_FACTOR = 0.01


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "csv-chat-eval"})
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)


def _export_tables(con: duckdb.DuckDBPyConnection, catalog: str, out_dir: Path) -> None:
    tables = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_catalog = ? ORDER BY table_name",
        [catalog],
    ).fetchall()
    for (table,) in tables:
        target = (out_dir / f"{table.lower()}.csv").as_posix().replace("'", "''")
        con.execute(f'COPY "{catalog}".main."{table}" TO \'{target}\' (HEADER, DELIMITER \',\')')


def fetch_jaffle_shop(out_dir: Path) -> None:
    for name in JAFFLE_FILES:
        _download(f"{JAFFLE_BASE}/{name}", out_dir / name)


def fetch_chinook(out_dir: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "chinook.sqlite"
        _download(CHINOOK_URL, db_file)
        with duckdb.connect() as con:
            con.execute("INSTALL sqlite; LOAD sqlite")
            con.execute(f"ATTACH '{db_file.as_posix()}' AS chinook (TYPE sqlite, READ_ONLY)")
            _export_tables(con, "chinook", out_dir)
            con.execute("DETACH chinook")


def fetch_tpch(out_dir: Path) -> None:
    with duckdb.connect() as con:
        con.execute("INSTALL tpch; LOAD tpch")
        con.execute(f"CALL dbgen(sf = {TPCH_SCALE_FACTOR})")
        _export_tables(con, "memory", out_dir)


FETCHERS = {"jaffle_shop": fetch_jaffle_shop, "chinook": fetch_chinook, "tpch": fetch_tpch}


def main(names: list[str]) -> None:
    unknown = set(names) - FETCHERS.keys()
    if unknown:
        raise SystemExit(f"Unknown dataset(s): {', '.join(sorted(unknown))}. Choose from {', '.join(FETCHERS)}.")
    for name in names or list(FETCHERS):
        out_dir = DATASETS_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"Fetching {name} ...", flush=True)
        FETCHERS[name](out_dir)
        files = sorted(out_dir.glob("*.csv"))
        print(f"  {len(files)} CSV files -> {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1:])
