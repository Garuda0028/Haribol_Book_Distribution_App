from .repository import query_df
from .settings import get_settings

DISTRIBUTION_REPORT_CATEGORIES = (
    "Small Books",
    "Medium Books",
    "Big Books",
    "Maha Big Books",
    "Bhagavatam",
    "Caitanya Caritamrita",
)


def _distribution_category(book_category, book_sub_category):
    category = " ".join(str(book_category or "").casefold().split())
    subcategory = " ".join(str(book_sub_category or "").casefold().split())
    combined = f"{category} {subcategory}"
    if "caitanya caritamrita" in combined:
        return "Caitanya Caritamrita"
    if "bhagavatam" in combined:
        return "Bhagavatam"
    for label, singular in (
        ("Small Books", "small book"),
        ("Medium Books", "medium book"),
        ("Big Books", "big book"),
        ("Maha Big Books", "maha big book"),
    ):
        if category == singular or category == singular + "s":
            return label
    return "Other"


def _distribution_category_totals(
    as_of_date,
    start_date=None,
    sale_date=None,
    end_date=None,
):
    if sale_date is None:
        date_filter = "sale_date <= ? AND (? IS NULL OR sale_date >= ?)"
        params = (as_of_date, start_date, start_date)
    else:
        date_filter = (
            "sale_date = ? "
            "AND (? IS NULL OR sale_date >= ?) "
            "AND (? IS NULL OR sale_date <= ?)"
        )
        params = (sale_date, start_date, start_date, end_date, end_date)
    rows = query_df(
        f"""
        SELECT book_category, book_sub_category,
               COALESCE(SUM(quantity),0) AS copies,
               COALESCE(SUM(COALESCE(reported_points_total, points * quantity)),0) AS points
        FROM sales
        WHERE {date_filter}
        GROUP BY book_category, book_sub_category
        """,
        params,
    )
    totals = {category: {"copies": 0, "points": 0.0} for category in DISTRIBUTION_REPORT_CATEGORIES}
    for row in rows.to_dict("records"):
        category = _distribution_category(
            row["book_category"],
            row["book_sub_category"],
        )
        totals.setdefault(category, {"copies": 0, "points": 0.0})
        totals[category]["copies"] += int(row["copies"])
        totals[category]["points"] += float(row["points"])
    return totals


def distribution_report(as_of_date):
    settings = get_settings()
    start_date = settings.get("overall_target_start_date") or None
    end_date = settings.get("overall_target_end_date") or None
    report_end_date = min(as_of_date, end_date) if end_date else as_of_date
    daily_totals = _distribution_category_totals(
        as_of_date,
        start_date=start_date,
        sale_date=as_of_date,
        end_date=end_date,
    )
    overall_totals = _distribution_category_totals(
        report_end_date,
        start_date=start_date,
    )
    categories = list(DISTRIBUTION_REPORT_CATEGORIES)
    if "Other" in overall_totals:
        categories.append("Other")

    daily = [
        {"category": category, **daily_totals.get(category, {"copies": 0, "points": 0.0})}
        for category in categories
    ]
    overall = [
        {"category": category, **overall_totals.get(category, {"copies": 0, "points": 0.0})}
        for category in categories
    ]

    target_points = float(settings.get("overall_target", "0"))
    total_book_points = accumulated_points(
        as_of_date,
        start_date=start_date,
        end_date=end_date,
    )
    target_progress_pct = (
        min(100.0, total_book_points / target_points * 100)
        if target_points > 0
        else None
    )
    submission_totals = query_df(
        """
        SELECT COALESCE(SUM(lakshmi_transferred),0) AS lakshmi_transferred,
               COALESCE(SUM(shastra_daan),0) AS shastra_daan
        FROM website_submissions
        WHERE sale_date <= ?
          AND (? IS NULL OR sale_date >= ?)
        """,
        (report_end_date, start_date, start_date),
    ).iloc[0]
    legacy_daan = query_df(
        "SELECT COALESCE(SUM(shastra_daan),0) AS shastra_daan "
        "FROM sales WHERE sale_date <= ? "
        "AND (? IS NULL OR sale_date >= ?)",
        (report_end_date, start_date, start_date),
    ).iloc[0]["shastra_daan"]
    return {
        "date": as_of_date,
        "daily": daily,
        "overall": overall,
        "daily_total_copies": sum(row["copies"] for row in daily),
        "daily_total_points": sum(row["points"] for row in daily),
        "overall_total_copies": sum(row["copies"] for row in overall),
        "overall_total_points": sum(row["points"] for row in overall),
        "target_points": target_points,
        "total_book_points": total_book_points,
        "target_progress_pct": target_progress_pct,
        "period_start_date": start_date,
        "period_end_date": report_end_date,
        "shastra_daan_collected": float(submission_totals["shastra_daan"])
        + float(legacy_daan),
        "total_lakshmi_collected": float(submission_totals["lakshmi_transferred"]),
    }


def dashboard_summary(sale_date=None):
    settings = get_settings()
    active_sellers = query_df("SELECT COUNT(*) AS n FROM sellers WHERE active=1").iloc[0]["n"]
    active_books = query_df("SELECT COUNT(*) AS n FROM books WHERE active=1").iloc[0]["n"]
    totals = query_df("""
        SELECT COALESCE(SUM(quantity),0) AS books_sold,
               COALESCE(SUM(COALESCE(reported_points_total, points * quantity)),0) AS points,
               COALESCE(ROUND(SUM(COALESCE(amount_collected, unit_price * quantity)),2),0) AS revenue,
               COALESCE(ROUND(SUM(shastra_daan),2),0) AS shastra_daan,
               COUNT(*) AS sale_records,
               COUNT(DISTINCT seller_name) AS participating_devotees
        FROM sales
        WHERE (? IS NULL OR sale_date = ?)
    """, (sale_date, sale_date)).iloc[0]
    return {
        "books_sold": int(totals["books_sold"]),
        "points": float(totals["points"]),
        "revenue": float(totals["revenue"]),
        "shastra_daan": float(totals["shastra_daan"]),
        "sale_records": int(totals["sale_records"]),
        "participating_devotees": int(totals["participating_devotees"]),
        "active_sellers": int(active_sellers),
        "active_books": int(active_books),
        "overall_target": float(settings.get("overall_target", "0")),
        "overall_target_start_date": settings.get("overall_target_start_date", ""),
        "overall_target_end_date": settings.get("overall_target_end_date", ""),
    }


def accumulated_points(as_of_date, start_date=None, end_date=None):
    period_end = min(as_of_date, end_date) if end_date else as_of_date
    total = query_df(
        "SELECT COALESCE(SUM(COALESCE(reported_points_total, points * quantity)), 0) AS points "
        "FROM sales WHERE sale_date <= ? "
        "AND (? IS NULL OR sale_date >= ?)",
        (period_end, start_date, start_date),
    ).iloc[0]["points"]
    return float(total)

def leaderboard(include_all_active=False, sale_date=None, limit=None):
    sql = """
        SELECT s.seller_name AS seller,
               COALESCE(SUM(sa.quantity),0) AS books_sold,
               COALESCE(SUM(COALESCE(sa.reported_points_total, sa.points * sa.quantity)),0) AS points,
               COALESCE(ROUND(SUM(COALESCE(sa.amount_collected, sa.unit_price * sa.quantity)),2),0) AS revenue,
               COALESCE(ROUND(SUM(sa.shastra_daan),2),0) AS shastra_daan,
               MAX(sa.sale_date) AS latest_sale_date,
               COALESCE(t.target_points,0) AS target_points
        FROM sellers s
        LEFT JOIN sales sa ON lower(trim(sa.seller_name)) = lower(trim(s.seller_name))
                         AND (? IS NULL OR sa.sale_date <= ?)
        LEFT JOIN seller_targets t ON t.seller_id = s.id
    """
    if not include_all_active:
        sql += " WHERE s.active=1"
        sql += " AND EXISTS (SELECT 1 FROM sales recent "
        sql += "WHERE lower(trim(recent.seller_name)) = lower(trim(s.seller_name)) "
        sql += "AND (? IS NULL OR recent.sale_date <= ?))"
    sql += (
        " GROUP BY s.id, s.seller_name, t.target_points "
        "ORDER BY latest_sale_date DESC, points DESC, seller COLLATE NOCASE"
    )
    params = (sale_date, sale_date)
    if not include_all_active:
        params += (sale_date, sale_date)
    if limit is not None:
        sql += " LIMIT ?"
        params += (max(0, int(limit)),)
    df = query_df(sql, params)
    if not df.empty:
        df["target_progress_pct"] = df.apply(
            lambda r: round(float(r["points"]) / float(r["target_points"]) * 100, 1) if r["target_points"] > 0 else None,
            axis=1
        )
    return df

def daily_summary(start_date=None, end_date=None):
    return query_df(
        """
        SELECT sale_date, SUM(quantity) AS books_sold,
               SUM(COALESCE(reported_points_total, points * quantity)) AS points,
               ROUND(SUM(COALESCE(amount_collected, unit_price * quantity)),2) AS revenue,
               ROUND(SUM(shastra_daan),2) AS shastra_daan
        FROM sales
        WHERE (? IS NULL OR sale_date >= ?)
          AND (? IS NULL OR sale_date <= ?)
        GROUP BY sale_date ORDER BY sale_date DESC
        """,
        (start_date, start_date, end_date, end_date),
    )


def book_performance(sale_date=None):
    return query_df(
        """
        SELECT sales.book_name AS book, sales.language AS language,
               COALESCE(books.book_tag, '') AS book_tag,
               SUM(quantity) AS copies,
               SUM(COALESCE(reported_points_total, points * quantity)) AS points,
               ROUND(SUM(COALESCE(amount_collected, unit_price * quantity)),2) AS revenue,
               ROUND(SUM(shastra_daan),2) AS shastra_daan
        FROM sales
        LEFT JOIN books ON books.book_name = sales.book_name COLLATE NOCASE
        WHERE (? IS NULL OR sale_date = ?)
        GROUP BY sales.book_name, sales.language, books.book_tag
        ORDER BY copies DESC, book COLLATE NOCASE
        """,
        (sale_date, sale_date),
    )


def list_sales():
    return query_df("""
        SELECT id, form_timestamp, external_submission_id, seller_name, sale_date,
               location, book_name, book_category, book_sub_category, language,
               quantity, points,
               COALESCE(reported_points_total, points * quantity) AS total_points, unit_price,
               COALESCE(amount_collected, unit_price * quantity) AS amount_collected,
               shastra_daan, remarks, source_file, imported_at
        FROM sales ORDER BY sale_date DESC, id DESC
    """)

def list_imports():
    return query_df("""
        SELECT source_file, imported_at, rows_seen, rows_added, duplicates_skipped, invalid_rows, notes
        FROM import_runs ORDER BY id DESC LIMIT 100
    """)

def update_sale_points(sale_id, points):
    import math

    from .repository import execute
    points = float(points)
    if not math.isfinite(points) or points < 0:
        raise ValueError("Points must be a finite, non-negative number.")
    execute(
        "UPDATE sales SET points=?, reported_points_total=NULL WHERE id=?",
        (points, int(sale_id)),
    )

def export_csv(frame):
    import unicodedata

    def escape_formula(value):
        if not isinstance(value, str):
            return value
        index = 0
        while index < len(value):
            character = value[index]
            if not (
                character.isspace()
                or unicodedata.category(character) in {"Cc", "Cf"}
            ):
                break
            index += 1
        if value[index : index + 1] in ("=", "+", "-", "@"):
            return "'" + value
        return value

    return frame.map(escape_formula).to_csv(index=False).encode("utf-8-sig")


def export_sales_csv(frame):
    return export_csv(frame)
