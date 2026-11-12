from datetime import datetime
import pandas as pd
from .db import connect

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def rows_to_frame(rows):
    return pd.DataFrame([dict(r) for r in rows])

def query_df(sql, params=()):
    with connect() as con:
        return pd.read_sql_query(sql, con, params=params)

def execute(sql, params=()):
    with connect() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid, cur.rowcount
