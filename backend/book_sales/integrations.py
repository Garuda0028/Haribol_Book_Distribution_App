import csv
import hashlib
import io
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation

from .catalogue import (
    _write_catalogue_file,
    get_book_pricing_catalogue,
    get_book_variant_pricing_catalogue,
    merge_distribution_catalogue,
)
from .config import normalize_spreadsheet_id
from .db import connect
from .importer import parse_date
from .repository import now_iso, query_df
from .settings import get_settings

RESPONSES_RANGE = "A:N"
SUBMISSIONS_RANGE = "A:J"
LISTS_RANGE = "A:S"
WEBSITE_SOURCE = "IBC Sarjapur web form"
_GOOGLE_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
_LISTS_HEADERS = (
    "Book Category",
    "Sub Category",
    "Book",
    "Language",
    "Price",
    "Points",
    "Stock",
    "Stock Value",
    "Books Distributed",
    "Balance Books",
    "Amount Collected",
    "Books Billed",
    "Billed Amount",
    "Books Unbilled",
    "Unbilled Amount",
    "Allotted Books",
    "Allotted Books Amt",
    "IBC-S Stock",
    "IBC-S Stock Amt",
)
_LISTS_NUMERIC_FIELDS = (
    "price",
    "points",
    "stock",
    "stockvalue",
    "booksdistributed",
    "balancebooks",
    "amountcollected",
    "booksbilled",
    "billedamount",
    "booksunbilled",
    "unbilledamount",
    "allottedbooks",
    "allottedbooksamt",
    "ibcsstock",
    "ibcsstockamt",
)
_HEADER_ALIASES = {
    "allottedboksamt": "allottedbooksamt",
}


def _normalize_header(value):
    return "".join(character.lower() for character in str(value) if character.isalnum())


def _canonical_header(value):
    normalized = _normalize_header(value)
    return _HEADER_ALIASES.get(normalized, normalized)


def _spreadsheet_id(spreadsheet_config=None):
    settings = spreadsheet_config or get_settings()
    return normalize_spreadsheet_id(settings.get("spreadsheet_id"))


def _sheet_range(tab_key, columns, spreadsheet_config=None):
    settings = spreadsheet_config or get_settings()
    tab_name = str(settings.get(tab_key) or "").strip()
    if (
        not tab_name
        or len(tab_name) > 100
        or "\n" in tab_name
        or "\r" in tab_name
    ):
        raise ValueError(f"Configure a valid {tab_key.replace('_', ' ')} name.")
    quoted_tab = "'" + tab_name.replace("'", "''") + "'"
    return f"{quoted_tab}!{columns}"


def _connection_scope(connection):
    return nullcontext(connection) if connection is not None else connect()


def parse_distribution_catalogue_csv(content):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The distribution catalogue must use UTF-8 encoding.") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ValueError("The distribution catalogue CSV has no header row.")
    columns = {_canonical_header(name): name for name in reader.fieldnames}
    required = {"bookcategory", "book", "language", "price", "points"}
    if not required.issubset(columns):
        raise ValueError(
            "The Lists tab must contain these columns: "
            + ", ".join(_LISTS_HEADERS)
            + "."
        )

    rows = []
    seen = set()
    for row_number, row in enumerate(reader, start=2):
        category = str(row.get(columns["bookcategory"]) or "").strip()
        subcategory_field = columns.get("subcategory")
        subcategory = (
            str(row.get(subcategory_field) or "").strip()
            if subcategory_field
            else ""
        )
        book = str(row.get(columns["book"]) or "").strip()
        language = str(row.get(columns["language"]) or "").strip()
        if not category or not book or not language:
            raise ValueError(
                f"Distribution catalogue row {row_number}: category, book, and language are required."
            )
        variant_key = (book.casefold(), language.casefold())
        if variant_key in seen:
            raise ValueError(
                f"Distribution catalogue row {row_number}: duplicate book and language."
            )
        seen.add(variant_key)
        values = []
        for field in _LISTS_NUMERIC_FIELDS:
            source_field = columns.get(field)
            raw_value = str(row.get(source_field) or "").strip() if source_field else ""
            if not raw_value:
                values.append(None)
                continue
            numeric_value_text = raw_value.replace(",", "")
            if field in {
                "price",
                "stockvalue",
                "amountcollected",
                "billedamount",
                "unbilledamount",
                "allottedbooksamt",
                "ibcsstockamt",
            }:
                numeric_value_text = numeric_value_text.replace("₹", "").strip()
            try:
                numeric_value = Decimal(numeric_value_text)
            except InvalidOperation as exc:
                raise ValueError(
                    f"Lists row {row_number}: {field.title()} must be a number."
                ) from exc
            if not numeric_value.is_finite() or numeric_value < 0:
                raise ValueError(
                    f"Lists row {row_number}: {field.title()} "
                    "must be finite and non-negative."
                )
            values.append(float(numeric_value))
        rows.append(
            (book, category, language, values[0], values[1], subcategory, *values[2:])
        )

    if not rows:
        raise ValueError("The distribution catalogue CSV contains no book rows.")
    return rows


def _catalogue_records_csv(records):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(_LISTS_HEADERS)
    for record in records:
        writer.writerow(
            [
                record.get(_canonical_header(header), "")
                for header in _LISTS_HEADERS
            ]
        )
    return output.getvalue().encode("utf-8")


def _fetch_distribution_catalogue_rows(
    api_key=None,
    service_account_info=None,
    spreadsheet_config=None,
):
    records = _fetch_sheet_records(
        _sheet_range("lists_tab", LISTS_RANGE, spreadsheet_config),
        {_canonical_header(header) for header in _LISTS_HEADERS},
        "Lists",
        api_key,
        service_account_info,
        spreadsheet_config,
    )
    return parse_distribution_catalogue_csv(
        _catalogue_records_csv(records)
    )


def sync_distribution_catalogue(
    api_key=None,
    service_account_info=None,
    spreadsheet_config=None,
):
    rows = _fetch_distribution_catalogue_rows(
        api_key,
        service_account_info,
        spreadsheet_config,
    )
    return merge_distribution_catalogue(rows)


def _fetch_sheet_records(
    sheet_range,
    required_headers,
    sheet_label,
    api_key=None,
    service_account_info=None,
    spreadsheet_config=None,
):
    api_key = str(api_key or "").strip()
    spreadsheet_id = _spreadsheet_id(spreadsheet_config)
    headers = {"User-Agent": "HaribolBookDistribution/1.0"}
    if service_account_info:
        try:
            from google.auth.exceptions import GoogleAuthError
            from google.auth.transport.requests import Request
            from google.oauth2.service_account import Credentials

            credentials = Credentials.from_service_account_info(
                dict(service_account_info),
                scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
            )
            credentials.refresh(Request())
            headers["Authorization"] = f"Bearer {credentials.token}"
        except (GoogleAuthError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Google service account configuration is invalid: {exc}"
            ) from exc
    elif not api_key:
        raise ValueError(
            "Provide a Google service-account credential or a permitted Sheets API key "
            "before syncing."
        )
    encoded_range = urllib.parse.quote(sheet_range, safe="!:")
    url = f"{_GOOGLE_SHEETS_API}/{spreadsheet_id}/values/{encoded_range}"
    query = {
        "valueRenderOption": "UNFORMATTED_VALUE",
        "dateTimeRenderOption": "FORMATTED_STRING",
    }
    if not service_account_info:
        query["key"] = api_key
    url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_body = response.read(5_000_001)
        if len(response_body) > 5_000_000:
            raise ValueError(
                f"The {sheet_label} sheet response must be 5 MB or smaller."
            )
        payload = json.loads(response_body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = str(exc)
        try:
            error_payload = json.loads(exc.read(5_000_001).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass
        else:
            google_error = (
                error_payload.get("error")
                if isinstance(error_payload, dict)
                else None
            )
            if isinstance(google_error, dict) and google_error.get("message"):
                detail = str(google_error["message"])
        if exc.code == 403:
            detail = (
                "Google Sheets denied access (HTTP 403). Confirm the spreadsheet "
                "is shared with the service-account email as Viewer, and that the Google "
                f"Sheets API is enabled. Google detail: {detail}"
            )
        raise ValueError(f"Could not read the {sheet_label} sheet: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError(f"Could not read the {sheet_label} sheet: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"The {sheet_label} sheet returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"The {sheet_label} sheet returned an unexpected response.")
    if "error" in payload:
        error = payload["error"]
        message = (
            error.get("message", "Google Sheets API error.")
            if isinstance(error, dict)
            else str(error)
        )
        raise ValueError(f"Could not read the {sheet_label} sheet: {message}")
    values = payload.get("values")
    if not isinstance(values, list) or not values or not values[0]:
        raise ValueError(
            f"The {sheet_label} sheet is empty or has no header row."
        )

    headers = [_canonical_header(value) for value in values[0]]
    if not required_headers.issubset(headers):
        raise ValueError(
            f"The {sheet_label} sheet is missing required columns: "
            + ", ".join(sorted(required_headers - set(headers)))
        )

    records = []
    for row in values[1:]:
        record = {
            headers[index]: value
            for index, value in enumerate(row)
            if index < len(headers)
        }
        records.append(record)
    return records


def fetch_response_sheet(
    api_key=None,
    service_account_info=None,
    spreadsheet_config=None,
):
    return _fetch_sheet_records(
        _sheet_range("responses_tab", RESPONSES_RANGE, spreadsheet_config),
        {
            "submissionid",
            "submittedat",
            "name",
            "location",
            "date",
            "bookcategory",
            "booksubcategory",
            "bookname",
            "language",
            "quantity",
            "price",
            "amount",
            "points",
            "pointstotal",
        },
        "responses",
        api_key,
        service_account_info,
        spreadsheet_config,
    )


def fetch_submission_sheet(
    api_key=None,
    service_account_info=None,
    spreadsheet_config=None,
):
    return _fetch_sheet_records(
        _sheet_range("submissions_tab", SUBMISSIONS_RANGE, spreadsheet_config),
        {
            "submissionid",
            "submittedat",
            "name",
            "location",
            "date",
            "totalcopies",
            "totalpoints",
            "lakshmidue",
            "lakshmitransferred",
            "shastradaan",
        },
        "submissions",
        api_key,
        service_account_info,
        spreadsheet_config,
    )


def test_google_sheets_connection(
    api_key=None,
    service_account_info=None,
    spreadsheet_config=None,
):
    spreadsheet_config = spreadsheet_config or get_settings()
    spreadsheet_id = _spreadsheet_id(spreadsheet_config)
    if not api_key and not service_account_info:
        raise ValueError(
            "Provide a Google service-account credential before testing the connection."
        )
    tabs = (
        (
            "lists_tab",
            "Lists",
            "A1:S1",
            {_canonical_header(header) for header in _LISTS_HEADERS},
        ),
        (
            "responses_tab",
            "responses",
            "A1:N1",
            {
                "submissionid",
                "submittedat",
                "name",
                "location",
                "date",
                "bookcategory",
                "booksubcategory",
                "bookname",
                "language",
                "quantity",
                "price",
                "amount",
                "points",
                "pointstotal",
            },
        ),
        (
            "submissions_tab",
            "submissions",
            "A1:J1",
            {
                "submissionid",
                "submittedat",
                "name",
                "location",
                "date",
                "totalcopies",
                "totalpoints",
                "lakshmidue",
                "lakshmitransferred",
                "shastradaan",
            },
        ),
    )
    reached_tabs = []
    for tab_key, label, columns, required_headers in tabs:
        tab_name = str(spreadsheet_config.get(tab_key) or "").strip()
        try:
            sheet_range = _sheet_range(tab_key, columns, spreadsheet_config)
            _fetch_sheet_records(
                sheet_range,
                required_headers,
                label,
                api_key,
                service_account_info,
                spreadsheet_config,
            )
        except ValueError as exc:
            reached = ", ".join(reached_tabs) if reached_tabs else "none"
            raise ValueError(
                f"Could not validate tab {tab_name!r} in spreadsheet "
                f"{spreadsheet_id}. Tabs reached before failure: {reached}. {exc}"
            ) from exc
        reached_tabs.append(tab_name)
    return {"spreadsheet_id": spreadsheet_id, "tabs": reached_tabs}


def _sheet_number(value, field, row_number, allow_blank=False):
    raw_value = "" if value is None else str(value).strip()
    if not raw_value and allow_blank:
        return None
    try:
        number = Decimal(raw_value)
    except InvalidOperation as exc:
        raise ValueError(
            f"Response row {row_number}: {field} must be a number."
        ) from exc
    if not number.is_finite() or number < 0:
        raise ValueError(
            f"Response row {row_number}: {field} must be finite and non-negative."
        )
    return float(number)


def _pricing_catalogues(connection):
    books = connection.execute(
        "SELECT book_name, default_points, price FROM books WHERE active=1"
    ).fetchall()
    variants = connection.execute(
        """
        SELECT b.book_name, v.language, v.points, v.price
        FROM book_variants v JOIN books b ON b.id=v.book_id
        WHERE v.active=1 AND b.active=1
        """
    ).fetchall()
    return (
        {
            str(book["book_name"]).casefold(): (
                float(book["default_points"]),
                float(book["price"]),
            )
            for book in books
        },
        {
            (str(variant["book_name"]).casefold(), str(variant["language"]).casefold()): (
                float(variant["points"]),
                float(variant["price"]),
            )
            for variant in variants
        },
    )


def import_distribution_responses(records, connection=None):
    settings = get_settings()
    try:
        fallback_points = float(settings.get("fallback_points", "1"))
    except (TypeError, ValueError) as exc:
        raise ValueError("FALLBACK_POINTS must be a valid number.") from exc
    if not math.isfinite(fallback_points) or fallback_points < 0:
        raise ValueError("FALLBACK_POINTS must be a finite, non-negative number.")

    if connection is None:
        pricing = get_book_pricing_catalogue()
        variant_pricing = get_book_variant_pricing_catalogue()
    else:
        pricing, variant_pricing = _pricing_catalogues(connection)
    added = updated = duplicates = invalid = 0
    issues = []
    submission_occurrences = {}
    imported_at = now_iso()

    with _connection_scope(connection) as con:
        for row_number, record in enumerate(records, start=2):
            submission_id = str(record.get("submissionid") or "").strip()
            submitted_at = str(record.get("submittedat") or "").strip()
            seller = str(record.get("name") or "").strip()
            sale_date = parse_date(record.get("date"))
            category = str(record.get("bookcategory") or "").strip()
            subcategory = str(record.get("booksubcategory") or "").strip()
            book = str(record.get("bookname") or "").strip()
            language = str(record.get("language") or "").strip()
            location = str(record.get("location") or "").strip()
            try:
                quantity_value = Decimal(str(record.get("quantity") or "").strip())
                quantity = int(quantity_value)
                if not quantity_value.is_finite() or quantity_value != quantity:
                    quantity = 0
            except (InvalidOperation, ValueError, TypeError):
                quantity = 0
            try:
                sheet_price = _sheet_number(
                    record.get("price"), "price", row_number, allow_blank=True
                )
                amount_collected = _sheet_number(
                    record.get("amount"), "amount", row_number, allow_blank=True
                )
                sheet_points = _sheet_number(
                    record.get("points"), "points", row_number, allow_blank=True
                )
                reported_points_total = _sheet_number(
                    record.get("pointstotal"),
                    "points_total",
                    row_number,
                    allow_blank=True,
                )
            except ValueError as exc:
                invalid += 1
                if len(issues) < 12:
                    issues.append(str(exc))
                continue

            if (
                not submission_id
                or not seller
                or not sale_date
                or not book
                or not language
                or not category
                or quantity < 1
            ):
                invalid += 1
                if len(issues) < 12:
                    issues.append(
                        f"Response row {row_number}: submission, name, date, book, language, "
                        "category, and a positive whole-number quantity are required."
                    )
                continue

            occurrence = submission_occurrences.get(submission_id, 0) + 1
            submission_occurrences[submission_id] = occurrence
            key_source = f"{submission_id}|{occurrence}"
            row_key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
            catalogue_points, catalogue_price = variant_pricing.get(
                (book.casefold(), language.casefold()),
                pricing.get(book.casefold(), (fallback_points, 0.0)),
            )
            points = sheet_points if sheet_points is not None else catalogue_points
            unit_price = sheet_price if sheet_price is not None else catalogue_price
            con.execute(
                "INSERT OR IGNORE INTO sellers(seller_name,active,created_at) VALUES(?,1,?)",
                (seller, imported_at),
            )
            existing = con.execute(
                """
                SELECT seller_name, sale_date, book_name, language, book_category,
                   book_sub_category, location, quantity, form_timestamp, points, unit_price,
                   amount_collected, reported_points_total
                FROM sales WHERE row_key=?
                """,
                (row_key,),
            ).fetchone()
            if existing is None:
                con.execute(
                    """
                    INSERT INTO sales
                        (row_key,form_timestamp,seller_name,sale_date,book_name,language,
                         book_category,book_sub_category,external_submission_id,location,remarks,points,
                         quantity,unit_price,amount_collected,shastra_daan,
                         reported_points_total,source_file,imported_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)
                    """,
                    (
                        row_key,
                        submitted_at,
                        seller,
                        sale_date,
                        book,
                        language,
                        category,
                        subcategory,
                        submission_id,
                        location,
                        "",
                        points,
                        quantity,
                        unit_price,
                        amount_collected,
                        reported_points_total,
                        WEBSITE_SOURCE,
                        imported_at,
                    ),
                )
                added += 1
            else:
                source_values = (
                    submitted_at,
                    seller,
                    sale_date,
                    book,
                    language,
                    category,
                    subcategory,
                    location,
                    quantity,
                    points,
                    unit_price,
                    amount_collected,
                    reported_points_total,
                )
                existing_values = (
                    existing["form_timestamp"],
                    existing["seller_name"],
                    existing["sale_date"],
                    existing["book_name"],
                    existing["language"],
                    existing["book_category"],
                    existing["book_sub_category"],
                    existing["location"],
                    existing["quantity"],
                    existing["points"],
                    existing["unit_price"],
                    existing["amount_collected"],
                    existing["reported_points_total"],
                )
                if source_values != existing_values:
                    con.execute(
                        """
                        UPDATE sales
                        SET form_timestamp=?, seller_name=?, sale_date=?, book_name=?,
                            language=?, book_category=?, book_sub_category=?, location=?, quantity=?,
                            points=?, unit_price=?, amount_collected=?,
                            reported_points_total=?, imported_at=?
                        WHERE row_key=?
                        """,
                        (*source_values, imported_at, row_key),
                    )
                    updated += 1
                else:
                    duplicates += 1

        if added or updated or invalid:
            notes = list(issues)
            if updated:
                notes.append(f"{updated} existing response row(s) updated.")
            con.execute(
                """
                INSERT INTO import_runs
                    (source_file,imported_at,rows_seen,rows_added,duplicates_skipped,invalid_rows,notes)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    WEBSITE_SOURCE,
                    imported_at,
                    len(records),
                    added,
                    duplicates,
                    invalid,
                    " | ".join(notes),
                ),
            )

    return {
        "rows_seen": len(records),
        "added": added,
        "updated": updated,
        "duplicates": duplicates,
        "invalid": invalid,
        "issues": issues,
    }


def import_distribution_submissions(records, connection=None):
    added = updated = unchanged = invalid = 0
    issues = []
    imported_at = now_iso()
    with _connection_scope(connection) as con:
        for row_number, record in enumerate(records, start=2):
            submission_id = str(record.get("submissionid") or "").strip()
            submitted_at = str(record.get("submittedat") or "").strip()
            seller = str(record.get("name") or "").strip()
            location = str(record.get("location") or "").strip()
            sale_date = parse_date(record.get("date"))
            try:
                total_copies_value = _sheet_number(
                    record.get("totalcopies"), "total_copies", row_number
                )
                total_copies = int(total_copies_value)
                if total_copies_value != total_copies:
                    total_copies = -1
                values = {
                    "total_points": _sheet_number(
                        record.get("totalpoints"), "total_points", row_number
                    ),
                    "lakshmi_due": _sheet_number(
                        record.get("lakshmidue"), "lakshmi_due", row_number
                    ),
                    "lakshmi_transferred": _sheet_number(
                        record.get("lakshmitransferred"),
                        "lakshmi_transferred",
                        row_number,
                    ),
                    "shastra_daan": _sheet_number(
                        record.get("shastradaan"), "shastra_daan", row_number
                    ),
                }
            except ValueError as exc:
                invalid += 1
                if len(issues) < 12:
                    issues.append(str(exc))
                continue

            if (
                not submission_id
                or not seller
                or not sale_date
                or total_copies < 0
            ):
                invalid += 1
                if len(issues) < 12:
                    issues.append(
                        f"Submission row {row_number}: submission ID, name, date, "
                        "and a non-negative whole-number total_copies are required."
                    )
                continue

            source_values = (
                submitted_at,
                seller,
                location,
                sale_date,
                total_copies,
                values["total_points"],
                values["lakshmi_due"],
                values["lakshmi_transferred"],
                values["shastra_daan"],
            )
            existing = con.execute(
                """
                SELECT submitted_at, seller_name, location, sale_date, total_copies,
                       total_points, lakshmi_due, lakshmi_transferred, shastra_daan
                FROM website_submissions WHERE submission_id=?
                """,
                (submission_id,),
            ).fetchone()
            if existing is None:
                con.execute(
                    """
                    INSERT INTO website_submissions
                        (submission_id,submitted_at,seller_name,location,sale_date,
                         total_copies,total_points,lakshmi_due,lakshmi_transferred,
                         shastra_daan,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (submission_id, *source_values, imported_at),
                )
                added += 1
            elif source_values != tuple(existing):
                con.execute(
                    """
                    UPDATE website_submissions
                    SET submitted_at=?, seller_name=?, location=?, sale_date=?,
                        total_copies=?, total_points=?, lakshmi_due=?,
                        lakshmi_transferred=?, shastra_daan=?, updated_at=?
                    WHERE submission_id=?
                    """,
                    (*source_values, imported_at, submission_id),
                )
                updated += 1
            else:
                unchanged += 1

    return {
        "rows_seen": len(records),
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "invalid": invalid,
        "issues": issues,
    }


def list_website_submissions():
    return query_df(
        """
        SELECT submission_id, submitted_at, seller_name, location, sale_date,
               total_copies, total_points, lakshmi_due, lakshmi_transferred,
               shastra_daan, updated_at
        FROM website_submissions
        ORDER BY sale_date DESC, submitted_at DESC
        """
    )


def _reject_invalid_sync_rows(label, result):
    if not result["invalid"]:
        return
    issues = result.get("issues", [])
    details = "\n".join(issues)
    raise ValueError(
        f"Sync validation found {result['invalid']} invalid {label} row(s); "
        "no spreadsheet data was applied."
        + (f"\n{details}" if details else "")
    )


def sync_distribution_responses(
    api_key=None,
    service_account_info=None,
    spreadsheet_config=None,
):
    spreadsheet_config = spreadsheet_config or get_settings()

    # Read and validate all three tabs before opening a write transaction.
    records = fetch_response_sheet(
        api_key, service_account_info, spreadsheet_config
    )
    submissions = fetch_submission_sheet(
        api_key, service_account_info, spreadsheet_config
    )
    catalogue_rows = _fetch_distribution_catalogue_rows(
        api_key, service_account_info, spreadsheet_config
    )

    with connect() as con:
        catalogue_result = merge_distribution_catalogue(
            catalogue_rows,
            connection=con,
        )
        result = import_distribution_responses(records, connection=con)
        _reject_invalid_sync_rows("response", result)
        summary_result = import_distribution_submissions(
            submissions,
            connection=con,
        )
        _reject_invalid_sync_rows("submission summary", summary_result)

    try:
        _write_catalogue_file()
    except OSError as exc:
        result["catalogue_file_warning"] = (
            "The database sync succeeded, but the local catalogue mirror "
            f"could not be refreshed: {exc}"
        )
    result["catalogue_added"] = catalogue_result["added"]
    result["catalogue_updated"] = catalogue_result["updated"]
    result["variants_added"] = catalogue_result["variants_added"]
    result["variants_updated"] = catalogue_result["variants_updated"]
    result["submissions_added"] = summary_result["added"]
    result["submissions_updated"] = summary_result["updated"]
    result["submissions_unchanged"] = summary_result["unchanged"]
    result["submissions_invalid"] = summary_result["invalid"]
    result["submission_issues"] = summary_result["issues"]
    return result
