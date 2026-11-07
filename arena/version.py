"""Single source of truth for the application version."""

APP_VERSION = "1.5.14"
VERSION_TUPLE = tuple(int(part) for part in APP_VERSION.split(".") if part.isdigit())
