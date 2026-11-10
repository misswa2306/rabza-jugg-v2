"""Configuration for Rabza Jugg V2."""

import os


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "rabza_jugg_bot.sqlite3")


def validate_config() -> None:
    """Fail early with a clear message if BotFather's token is missing."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "La variable TELEGRAM_BOT_TOKEN est absente. "
            "Configure le token fourni par BotFather avant de lancer le bot."
        )
