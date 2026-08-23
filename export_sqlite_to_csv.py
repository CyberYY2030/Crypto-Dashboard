#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export tables from a SQLite DB to CSV files.

Usage examples:
  python export_sqlite_to_csv.py --db a_share_mvp.db --out ./csv_out
  python export_sqlite_to_csv.py --db a_share_mvp.db --out ./csv_out --tables kline_daily meta

Notes:
  - For very large tables, exporting may take a while.
  - CSVs are written in UTF-8 with BOM for Excel-friendliness.
"""

import argparse
import os
import sqlite3
from typing import List

import pandas as pd


def list_tables(conn: sqlite3.Connection) -> List[str]:
    df = pd.read_sql_query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        conn,
    )
    return df["name"].tolist()


def export_table(conn: sqlite3.Connection, table: str, out_dir: str) -> str:
    df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    path = os.path.join(out_dir, f"{table}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to sqlite db")
    ap.add_argument("--out", required=True, help="Output folder")
    ap.add_argument("--tables", nargs="*", default=None, help="Optional subset of tables")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    conn = sqlite3.connect(args.db)
    try:
        tables = args.tables or list_tables(conn)
        if not tables:
            raise SystemExit("No tables found.")

        print(f"DB: {os.path.abspath(args.db)}")
        print(f"OUT: {os.path.abspath(args.out)}")
        print("Tables:", tables)

        for t in tables:
            try:
                p = export_table(conn, t, args.out)
                print(f"[OK] {t} -> {p}")
            except Exception as e:
                print(f"[FAIL] {t}: {e}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
