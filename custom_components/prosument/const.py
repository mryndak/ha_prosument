"""Stałe integracji Prosument (net-metering)."""

DOMAIN = "prosument"

# Ustawienia
CONF_IMPORT = "import_entity"
CONF_EXPORT = "export_entity"
CONF_FACTOR = "factor"
CONF_PRICE = "price"
CONF_HOURLY = "hourly_balancing"
CONF_EXPIRY_MONTHS = "expiry_months"
CONF_SETTLEMENTS = "settlements"

# Pola pojedynczego rozliczenia (rachunku)
S_FROM = "period_from"
S_TO = "period_to"
S_STORAGE = "storage"
S_IMPORT = "billed_import"
S_EXPORT = "billed_export"

DEFAULT_FACTOR = 0.8
DEFAULT_PRICE = 1.10
DEFAULT_EXPIRY_MONTHS = 12
MAX_SETTLEMENTS = 12
UPDATE_MINUTES = 10
