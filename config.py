"""Configuration for Rabza Jugg V2."""

import os
from pathlib import Path


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEFAULT_ADMIN_USER_IDS = frozenset({8970668955, 8742788260})
_admin_ids_value = (
    os.getenv("ADMIN_USER_IDS", "").strip()
    or os.getenv("ADMIN_USER_ID", "").strip()
)
try:
    configured_admin_ids = frozenset(
        int(value.strip())
        for value in _admin_ids_value.split(",")
        if value.strip()
    )
except ValueError as error:
    raise ValueError(
        "ADMIN_USER_IDS doit contenir des identifiants Telegram numériques séparés par des virgules."
    ) from error
ADMIN_USER_IDS = configured_admin_ids or DEFAULT_ADMIN_USER_IDS
if any(admin_id <= 0 for admin_id in ADMIN_USER_IDS):
    raise ValueError("Les identifiants configurés dans ADMIN_USER_IDS doivent être positifs.")

PROJECT_ROOT = Path(__file__).resolve().parent
configured_database_path = (
    os.getenv("DB_PATH", "").strip()
    or os.getenv("DATABASE_PATH", "").strip()
)
if configured_database_path:
    database_path = Path(configured_database_path).expanduser()
    if not database_path.is_absolute():
        database_path = (PROJECT_ROOT / database_path).resolve()
else:
    database_path = PROJECT_ROOT / "rabza_jugg_bot.sqlite3"
DB_PATH = str(database_path)
DATABASE_PATH = DB_PATH


def validate_config() -> None:
    """Fail early with a clear message if BotFather's token is missing."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "La variable BOT_TOKEN est absente. "
            "Configure le token fourni par BotFather avant de lancer le bot."
        )
