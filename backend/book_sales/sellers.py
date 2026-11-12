import math

from .repository import execute, query_df, now_iso

def add_seller(name):
    name = str(name or "").strip()
    if not name:
        raise ValueError("Devotee name is required.")
    try:
        execute("INSERT INTO sellers(seller_name,active,created_at) VALUES(?,1,?)", (name, now_iso()))
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise ValueError("A devotee with that name already exists.") from exc
        raise

def update_seller(seller_id, active):
    execute("UPDATE sellers SET active=? WHERE id=?", (1 if active else 0, int(seller_id)))

def list_sellers(include_inactive=False):
    sql = "SELECT id, seller_name, active, created_at FROM sellers"
    if not include_inactive:
        sql += " WHERE active=1"
    sql += " ORDER BY seller_name COLLATE NOCASE"
    return query_df(sql)

def set_seller_target(seller_name, target_points):
    try:
        target_points = float(target_points)
    except (TypeError, ValueError) as exc:
        raise ValueError("Target must be a finite, non-negative number.") from exc
    if not math.isfinite(target_points) or target_points < 0:
        raise ValueError("Target must be a finite, non-negative number.")
    with __import__("book_sales.db", fromlist=["connect"]).connect() as con:
        row = con.execute("SELECT id FROM sellers WHERE seller_name=? COLLATE NOCASE", (seller_name,)).fetchone()
        if not row:
            raise ValueError("Devotee not found. Add the devotee first.")
        con.execute(
            "INSERT INTO seller_targets(seller_id,target_points) VALUES(?,?) "
            "ON CONFLICT(seller_id) DO UPDATE SET target_points=excluded.target_points",
            (row["id"], target_points)
        )

def list_seller_targets():
    return query_df("""
        SELECT s.seller_name, COALESCE(t.target_points,0) AS target_points
        FROM sellers s LEFT JOIN seller_targets t ON t.seller_id=s.id
        WHERE s.active=1 ORDER BY s.seller_name COLLATE NOCASE
    """)
