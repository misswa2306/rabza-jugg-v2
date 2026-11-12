"""Configuration for Rabza Jugg V2."""

import os
from pathlib import Path


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PROJECT_ROOT = Path(__file__).resolve().parent
configured_database_path = os.getenv("DATABASE_PATH", "").strip()
if configured_database_path:
    database_path = Path(configured_database_path).expanduser()
    if not database_path.is_absolute():
        database_path = (PROJECT_ROOT / database_path).resolve()
else:
    database_path = PROJECT_ROOT / "rabza_jugg_bot.sqlite3"
DATABASE_PATH = str(database_path)


def validate_config() -> None:
    """Fail early with a clear message if BotFather's token is missing."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "La variable BOT_TOKEN est absente. "
            "Configure le token fourni par BotFather avant de lancer le bot."
        )
