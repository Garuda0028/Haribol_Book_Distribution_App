import math
from datetime import date

from .db import connect
from .config import (
    DEFAULT_GOOGLE_SHEETS_SETTINGS,
    get_env_setting,
    normalize_spreadsheet_id,
    save_env_settings,
)


def get_settings():
    with connect() as con:
        settings = {
            r["setting_key"]: r["setting_value"]
            for r in con.execute("SELECT setting_key,setting_value FROM settings")
        }
    for setting_key, env_key in (
        ("overall_target", "OVERALL_POINTS_TARGET"),
        ("overall_target_start_date", "OVERALL_TARGET_START_DATE"),
        ("overall_target_end_date", "OVERALL_TARGET_END_DATE"),
        ("fallback_points", "FALLBACK_POINTS"),
    ):
        configured_value = get_env_setting(env_key)
        if configured_value is not None:
            settings[setting_key] = configured_value
    for setting_key, default_value in DEFAULT_GOOGLE_SHEETS_SETTINGS.items():
        settings.setdefault(setting_key, default_value)
    return settings

def save_settings(values):
    env_keys = {
        "overall_target": "OVERALL_POINTS_TARGET",
        "fallback_points": "FALLBACK_POINTS",
        "overall_target_start_date": "OVERALL_TARGET_START_DATE",
        "overall_target_end_date": "OVERALL_TARGET_END_DATE",
    }
    google_sheet_keys = {
        "spreadsheet_id",
        "lists_tab",
        "responses_tab",
        "submissions_tab",
    }
    if any(key not in env_keys and key not in google_sheet_keys for key in values):
        raise ValueError("Only supported application settings can be saved.")
    env_values = {}
    database_values = {}
    for key, value in values.items():
        if key in ("spreadsheet_id", "lists_tab", "responses_tab", "submissions_tab"):
            normalized = str(value or "").strip()
            if key == "spreadsheet_id":
                if not normalized:
                    raise ValueError("A Google Sheets spreadsheet ID or URL is required.")
                normalized = normalize_spreadsheet_id(normalized)
            elif (
                not normalized
                or len(normalized) > 100
                or "\n" in normalized
                or "\r" in normalized
            ):
                raise ValueError(
                    f"{key} must be a non-empty sheet tab name of 100 characters or fewer."
                )
            database_values[key] = normalized
            continue
        if key in ("overall_target_start_date", "overall_target_end_date"):
            raw_date = str(value or "").strip()
            if raw_date:
                try:
                    normalized_date = date.fromisoformat(raw_date).isoformat()
                except ValueError as exc:
                    raise ValueError(f"{key} must be a valid ISO date.") from exc
            else:
                normalized_date = ""
            env_values[env_keys[key]] = normalized_date
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a finite, non-negative number.") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{key} must be a finite, non-negative number.")
        env_values[env_keys[key]] = str(number)
    start_date = env_values.get("OVERALL_TARGET_START_DATE", "")
    end_date = env_values.get("OVERALL_TARGET_END_DATE", "")
    if start_date and end_date and start_date > end_date:
        raise ValueError("The target start date must be on or before the end date.")
    if env_values:
        save_env_settings(env_values)
    if database_values:
        with connect() as con:
            con.executemany(
                """
                INSERT INTO settings(setting_key, setting_value) VALUES(?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
                """,
                database_values.items(),
            )
