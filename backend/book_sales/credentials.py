from streamlit.errors import StreamlitSecretNotFoundError


_REQUIRED_SERVICE_ACCOUNT_FIELDS = (
    "type",
    "project_id",
    "private_key",
    "client_email",
    "token_uri",
)


def load_google_service_account(secrets):
    try:
        service_account_info = secrets.get("google_service_account")
    except StreamlitSecretNotFoundError:
        return None

    if service_account_info is None:
        return None
    if not hasattr(service_account_info, "items"):
        raise ValueError("The google_service_account secret must be a TOML table.")

    service_account_info = dict(service_account_info)
    missing_fields = [
        field
        for field in _REQUIRED_SERVICE_ACCOUNT_FIELDS
        if not str(service_account_info.get(field) or "").strip()
    ]
    if missing_fields:
        raise ValueError(
            "The google_service_account secret is missing required fields: "
            + ", ".join(missing_fields)
            + "."
        )
    if service_account_info["type"] != "service_account":
        raise ValueError(
            'The google_service_account "type" must be "service_account".'
        )
    return service_account_info
