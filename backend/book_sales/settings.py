import math
from datetime import date

from .db import connect
from .config import get_env_setting, save_env_settings


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
    return settings

def save_settings(values):
    env_keys = {
        "overall_target": "OVERALL_POINTS_TARGET",
        "fallback_points": "FALLBACK_POINTS",
        "overall_target_start_date": "OVERALL_TARGET_START_DATE",
        "overall_target_end_date": "OVERALL_TARGET_END_DATE",
    }
    if any(key not in env_keys for key in values):
        raise ValueError("Only supported application settings can be saved.")
    env_values = {}
    for key, value in values.items():
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
    save_env_settings(env_values)
