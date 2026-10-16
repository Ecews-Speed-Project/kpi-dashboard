"""Central repository (Postgres), Excel and manual inputs."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def _lower(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def pg_engine(cfg: dict):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    user, pwd = os.environ.get("PG_USER"), os.environ.get("PG_PASS")
    if not user or not pwd:
        raise RuntimeError("Set PG_USER and PG_PASS in .env")
    url = URL.create("postgresql+psycopg2", username=user, password=pwd,
                     host=os.environ.get("PG_HOST") or cfg["host"], port=cfg["port"], database=cfg["dbname"])
    return create_engine(url, pool_pre_ping=True)


def read_pg_table(engine, schema: str, table: str, tries: int = 3, columns: list[str] | None = None,
                  page: int = 25000, where: str = "") -> pd.DataFrame:
    """Read a table (only `columns` when given - the linelist has 193 columns and the
    server drops long transfers). The server occasionally drops the connection: retry."""
    import time
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    sel = "*"
    if columns:
        with engine.connect() as con:
            have = {r[0].lower(): r[0] for r in con.execute(text(
                "select column_name from information_schema.columns where table_schema=:s and table_name=:t"),
                {"s": schema, "t": table})}
        keep = [have[c.lower()] for c in dict.fromkeys(columns) if c.lower() in have]
        sel = ", ".join(f'"{c}"' for c in keep) if keep else "*"
    q = f'SELECT {sel} FROM "{schema}"."{table}"' + (f" WHERE {where}" if where else "")

    def attempt(fn, what):
        for i in range(tries):
            try:
                return fn()
            except DBAPIError as e:
                if i == tries - 1:
                    raise
                print(f"    retry {i + 1} reading {what}: {type(e.orig).__name__}", flush=True)
                time.sleep(20 * (i + 1))

    # Large tables come in pages (physical row order), each retried on its own: the server
    # drops long transfers, and one dropped page should not restart the whole read.
    with engine.connect() as con:
        total = attempt(lambda: con.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"' + (f" WHERE {where}" if where else ""))).scalar(), f"{table} size")
    if not total or total <= page:
        return _lower(attempt(lambda: pd.read_sql(q, engine), table))
    parts = []
    for off in range(0, total, page):
        parts.append(attempt(lambda: pd.read_sql(f"{q} ORDER BY ctid LIMIT {page} OFFSET {off}", engine),
                             f"{table} rows {off:,}+"))
    return _lower(pd.concat(parts, ignore_index=True))


def read_excel(path: str | Path, sheet=0) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return _lower(pd.read_excel(p, sheet_name=sheet))


def read_manual(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    return _lower(pd.read_csv(p)) if p.exists() else pd.DataFrame()
