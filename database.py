"""SQLite persistence for players."""

from datetime import datetime, timezone
from contextlib import asynccontextmanager
import time
from typing import Any, AsyncIterator

import aiosqlite

from game import ITEMS, level_for_xp


MIN_HUSTLE_COOLDOWN_SECONDS = 15
MAX_HUSTLE_COOLDOWN_SECONDS = 30


def _epoch_timestamp(value: object) -> float | None:
    """Normalize Unix timestamps and legacy ISO timestamps to epoch seconds."""
    if value is None or value == 0:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        return float(value)
    elif isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            parsed = datetime.fromisoformat(value)
    else:
        raise TypeError(f"Format d'échéance de prison non pris en charge: {type(value)!r}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open an immediate SQLite transaction and roll it back on errors."""
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            tables = {row[0] for row in await cursor.fetchall()}
            if "players" in tables:
                cursor = await connection.execute("PRAGMA table_info(players)")
                player_columns = {row[1] for row in await cursor.fetchall()}
            else:
                player_columns = set()

            if "players" in tables and "chat_id" not in player_columns:
                await connection.execute("PRAGMA foreign_keys = OFF")
                await connection.execute("BEGIN IMMEDIATE")
                if "inventory" in tables:
                    await connection.execute(
                        "ALTER TABLE inventory RENAME TO inventory_legacy"
                    )
                    tables.add("inventory_legacy")
                await connection.execute(
                    "ALTER TABLE players RENAME TO players_legacy"
                )
                tables.add("players_legacy")
                await connection.execute(
                    """
                    CREATE TABLE players (
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        username TEXT,
                        cash INTEGER NOT NULL DEFAULT 500 CHECK (cash >= 0),
                        balance INTEGER GENERATED ALWAYS AS (cash) VIRTUAL,
                        heat INTEGER NOT NULL DEFAULT 0 CHECK (heat BETWEEN 0 AND 100),
                        in_prison_until REAL NOT NULL DEFAULT 0,
                        gamos TEXT NOT NULL DEFAULT 'Aucun',
                        level INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 60),
                        xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
                        search_level INTEGER NOT NULL DEFAULT 0
                            CHECK (search_level BETWEEN 0 AND 100),
                        last_hustle_time REAL NOT NULL DEFAULT 0,
                        last_hustle_cooldown INTEGER NOT NULL DEFAULT 0,
                        last_jugg_time REAL NOT NULL DEFAULT 0,
                        last_jugg_cooldown INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (chat_id, user_id)
                    )
                    """
                )
                cursor = await connection.execute(
                    "PRAGMA table_info(players_legacy)"
                )
                legacy_columns = {row[1] for row in await cursor.fetchall()}
                level_column = "level" if "level" in legacy_columns else "1"
                xp_column = "xp" if "xp" in legacy_columns else "0"
                search_level_column = (
                    "search_level"
                    if "search_level" in legacy_columns
                    else ("heat" if "heat" in legacy_columns else "0")
                )
                last_hustle_column = (
                    "last_hustle_time"
                    if "last_hustle_time" in legacy_columns
                    else "0"
                )
                last_hustle_cooldown_column = (
                    "last_hustle_cooldown"
                    if "last_hustle_cooldown" in legacy_columns
                    else "0"
                )
                await connection.execute(
                    f"""
                    INSERT INTO players
                        (chat_id, user_id, username, cash, heat,
                         in_prison_until, gamos, level, xp,
                         search_level, last_hustle_time,
                         last_hustle_cooldown)
                    SELECT user_id, user_id, username, cash, heat,
                           COALESCE(in_prison_until, 0), gamos,
                           {level_column}, {xp_column},
                           {search_level_column}, {last_hustle_column},
                           {last_hustle_cooldown_column}
                    FROM players_legacy
                    """
                )
            elif "players" not in tables:
                await connection.execute(
                    """
                    CREATE TABLE players (
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        username TEXT,
                        cash INTEGER NOT NULL DEFAULT 500 CHECK (cash >= 0),
                        balance INTEGER GENERATED ALWAYS AS (cash) VIRTUAL,
                        heat INTEGER NOT NULL DEFAULT 0 CHECK (heat BETWEEN 0 AND 100),
                        in_prison_until REAL NOT NULL DEFAULT 0,
                        gamos TEXT NOT NULL DEFAULT 'Aucun',
                        level INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 60),
                        xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
                        search_level INTEGER NOT NULL DEFAULT 0
                            CHECK (search_level BETWEEN 0 AND 100),
                        last_hustle_time REAL NOT NULL DEFAULT 0,
                        last_hustle_cooldown INTEGER NOT NULL DEFAULT 0,
                        last_jugg_time REAL NOT NULL DEFAULT 0,
                        last_jugg_cooldown INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (chat_id, user_id)
                    )
                    """
                )
            cursor = await connection.execute("PRAGMA table_xinfo(players)")
            player_columns = {row[1] for row in await cursor.fetchall()}
            if "balance" not in player_columns:
                await connection.execute(
                    """
                    ALTER TABLE players
                    ADD COLUMN balance INTEGER GENERATED ALWAYS AS (cash) VIRTUAL
                    """
                )
            if "search_level" not in player_columns:
                await connection.execute(
                    """
                    ALTER TABLE players ADD COLUMN search_level INTEGER
                    NOT NULL DEFAULT 0 CHECK (search_level BETWEEN 0 AND 100)
                    """
                )
                await connection.execute(
                    "UPDATE players SET search_level = heat"
                )
            if "last_hustle_time" not in player_columns:
                await connection.execute(
                    """
                    ALTER TABLE players ADD COLUMN last_hustle_time REAL
                    NOT NULL DEFAULT 0
                    """
                )
            if "last_hustle_cooldown" not in player_columns:
                await connection.execute(
                    """
                    ALTER TABLE players ADD COLUMN last_hustle_cooldown INTEGER
                    NOT NULL DEFAULT 0
                    """
                )
            if "last_jugg_time" not in player_columns:
                await connection.execute(
                    """
                    ALTER TABLE players ADD COLUMN last_jugg_time REAL
                    NOT NULL DEFAULT 0
                    """
                )
            if "last_jugg_cooldown" not in player_columns:
                await connection.execute(
                    """
                    ALTER TABLE players ADD COLUMN last_jugg_cooldown INTEGER
                    NOT NULL DEFAULT 0
                    """
                )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    item_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price INTEGER NOT NULL CHECK (price >= 0),
                    min_level INTEGER NOT NULL CHECK (min_level BETWEEN 1 AND 60),
                    xp_bonus INTEGER NOT NULL DEFAULT 0,
                    success_bonus REAL NOT NULL DEFAULT 0,
                    description TEXT NOT NULL DEFAULT ''
                )
                """
            )
            cursor = await connection.execute("PRAGMA table_info(items)")
            item_columns = {row[1] for row in await cursor.fetchall()}
            if "description" not in item_columns:
                await connection.execute(
                    """
                    ALTER TABLE items ADD COLUMN description TEXT NOT NULL DEFAULT ''
                    """
                )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_id TEXT NOT NULL REFERENCES items(item_id),
                    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
                    PRIMARY KEY (chat_id, user_id, item_id),
                    FOREIGN KEY (chat_id, user_id)
                        REFERENCES players(chat_id, user_id) ON DELETE CASCADE
                )
                """
            )
            await connection.executemany(
                """
                INSERT INTO items
                    (item_id, name, category, price, min_level, xp_bonus,
                     success_bonus, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    price = excluded.price,
                    min_level = excluded.min_level,
                    xp_bonus = excluded.xp_bonus,
                    success_bonus = excluded.success_bonus,
                    description = excluded.description
                """,
                [
                    (
                        item.item_id,
                        item.name,
                        item.category,
                        item.price,
                        item.min_level,
                        item.xp_bonus,
                        item.success_bonus,
                        item.description,
                    )
                    for item in ITEMS
                ],
            )
            await connection.execute(
                "UPDATE players SET in_prison_until = 0 WHERE in_prison_until IS NULL"
            )
            if "players_legacy" in tables:
                legacy_inventory_exists = "inventory_legacy" in tables
                if legacy_inventory_exists:
                    await connection.execute(
                        """
                        INSERT OR IGNORE INTO inventory
                            (chat_id, user_id, item_id, quantity)
                        SELECT user_id, user_id, item_id, quantity
                        FROM inventory_legacy
                        """
                    )
                    await connection.execute("DROP TABLE inventory_legacy")
                await connection.execute("DROP TABLE players_legacy")
            await connection.commit()
            await connection.execute("PRAGMA foreign_keys = ON")

    async def ensure_player(
        self, user_id: int, username: str | None, chat_id: int = 0
    ) -> None:
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute(
                """
                INSERT INTO players (chat_id, user_id, username)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET username = excluded.username
                """,
                (chat_id, user_id, username),
            )
            await connection.commit()

    async def get_player(
        self, user_id: int, chat_id: int = 0
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT user_id, username, cash, balance, heat,
                       in_prison_until, gamos,
                       level, xp, search_level, last_hustle_time,
                       last_hustle_cooldown, last_jugg_time,
                       last_jugg_cooldown
                FROM players WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            player = dict(row)
            end_time = _epoch_timestamp(player["in_prison_until"])
            if end_time is not None and end_time <= time.time():
                await connection.execute(
                    """
                    UPDATE players SET in_prison_until = 0
                    WHERE user_id = ? AND chat_id = ? AND in_prison_until = ?
                    """,
                    (user_id, chat_id, player["in_prison_until"]),
                )
                await connection.commit()
                player["in_prison_until"] = 0
            return player

    async def get_player_balance(
        self, user_id: int, chat_id: int = 0
    ) -> int | None:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                "SELECT cash FROM players WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row is not None else None

    async def settle_casino_bet(
        self,
        user_id: int,
        chat_id: int,
        stake: int,
        net_change: int,
    ) -> int | None:
        """Apply a wager's net balance change if its stake is affordable."""
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                """
                UPDATE players
                SET cash = cash + ?
                WHERE user_id = ? AND chat_id = ? AND cash >= ?
                """,
                (net_change, user_id, chat_id, stake),
            )
            if cursor.rowcount != 1:
                await connection.rollback()
                return None

            cursor = await connection.execute(
                "SELECT cash FROM players WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await connection.rollback()
                return None
            await connection.commit()
            return int(row[0])

    async def get_top_players(
        self, limit: int = 10, chat_id: int = 0
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT username, cash
                FROM players WHERE chat_id = ?
                ORDER BY cash DESC, user_id ASC
                LIMIT ?
                """,
                (chat_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def apply_action_result(
        self,
        user_id: int,
        cash_delta: int,
        heat_delta: int,
        gamos: str | None,
        prison_until: datetime | float | None,
        xp_gain: int = 0,
        chat_id: int = 0,
    ) -> bool:
        """Apply a game result atomically, unless the player is already jailed."""
        now = time.time()
        new_prison_until = _epoch_timestamp(prison_until) or 0
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT in_prison_until, xp FROM players
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await connection.rollback()
                return False

            until_value = row["in_prison_until"]
            current_end_time = _epoch_timestamp(until_value)
            if current_end_time is not None and current_end_time > now:
                await connection.rollback()
                return False
            new_xp = row["xp"] + max(0, xp_gain)
            new_level = level_for_xp(new_xp)
            await connection.execute(
                """
                UPDATE players
                SET cash = MAX(0, cash + ?),
                    heat = MIN(100, MAX(0, heat + ?)),
                    search_level = MIN(100, MAX(0, search_level + ?)),
                    gamos = COALESCE(?, gamos),
                    in_prison_until = ?,
                    xp = ?,
                    level = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (
                    cash_delta,
                    heat_delta,
                    heat_delta,
                    gamos,
                    new_prison_until,
                    new_xp,
                    new_level,
                    user_id,
                    chat_id,
                ),
            )
            await connection.commit()
            return True

    async def apply_hustle_result(
        self,
        user_id: int,
        chat_id: int,
        cooldown_seconds: int,
        cash_delta: int,
        search_level_delta: int,
    ) -> tuple[str, int, int, int, int]:
        """Atomically apply a hustle, global cooldown, cash, and search level."""
        now = time.time()
        cooldown_seconds = min(
            MAX_HUSTLE_COOLDOWN_SECONDS,
            max(MIN_HUSTLE_COOLDOWN_SECONDS, int(cooldown_seconds)),
        )
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT cash, heat, search_level, in_prison_until,
                       last_hustle_time, last_hustle_cooldown
                FROM players
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            player = await cursor.fetchone()
            if player is None:
                await connection.rollback()
                return "missing", 0, 0, 0, 0

            prison_until = _epoch_timestamp(player["in_prison_until"])
            if prison_until is not None and prison_until > now:
                await connection.rollback()
                return (
                    "prison",
                    max(1, int(prison_until - now)),
                    int(player["cash"]),
                    int(player["search_level"]),
                    0,
                )

            last_hustle_time = float(player["last_hustle_time"] or 0)
            previous_cooldown = min(
                MAX_HUSTLE_COOLDOWN_SECONDS,
                int(player["last_hustle_cooldown"] or 0),
            )
            remaining = previous_cooldown - (now - last_hustle_time)
            if last_hustle_time > 0 and remaining > 0:
                await connection.rollback()
                return (
                    "cooldown",
                    max(1, int(remaining + 0.999)),
                    int(player["cash"]),
                    int(player["search_level"]),
                    0,
                )

            actual_cash_delta = cash_delta
            if cash_delta < 0:
                actual_cash_delta = max(-int(player["cash"]), cash_delta)
            new_search_level = min(
                100,
                max(0, int(player["search_level"]) + search_level_delta),
            )
            new_heat = min(
                100,
                max(0, int(player["heat"]) + search_level_delta),
            )
            await connection.execute(
                """
                UPDATE players SET cash = cash + ?, heat = ?,
                    search_level = ?, last_hustle_time = ?,
                    last_hustle_cooldown = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (
                    actual_cash_delta,
                    new_heat,
                    new_search_level,
                    now,
                    cooldown_seconds,
                    user_id,
                    chat_id,
                ),
            )
            cursor = await connection.execute(
                "SELECT cash FROM players WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
            updated_player = await cursor.fetchone()
            if updated_player is None:
                await connection.rollback()
                return "missing", 0, 0, 0, 0
            await connection.commit()
            return (
                "ok",
                0,
                int(updated_player["cash"]),
                new_search_level,
                actual_cash_delta,
            )

    async def apply_jugg_result(
        self,
        user_id: int,
        chat_id: int,
        cooldown_seconds: int,
        cash_delta: int,
        heat_delta: int,
        prison_seconds: int = 0,
    ) -> dict[str, int | str]:
        """Atomically apply one fictional jugg result and its dedicated cooldown."""
        cooldown_seconds = min(30, max(15, int(cooldown_seconds)))
        prison_seconds = max(0, int(prison_seconds))
        now = time.time()
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT cash, heat, search_level, in_prison_until,
                       last_jugg_time, last_jugg_cooldown
                FROM players
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            player = await cursor.fetchone()
            if player is None:
                await connection.rollback()
                return {"status": "missing"}

            prison_until = _epoch_timestamp(player["in_prison_until"])
            if prison_until is not None and prison_until > now:
                await connection.rollback()
                return {
                    "status": "prison",
                    "remaining": max(1, int(prison_until - now + 0.999)),
                }

            last_jugg_time = float(player["last_jugg_time"] or 0)
            previous_cooldown = min(
                30, int(player["last_jugg_cooldown"] or 0)
            )
            remaining = previous_cooldown - (now - last_jugg_time)
            if last_jugg_time > 0 and remaining > 0:
                await connection.rollback()
                return {
                    "status": "cooldown",
                    "remaining": max(1, int(remaining + 0.999)),
                }

            previous_cash = int(player["cash"])
            new_cash = max(0, previous_cash + cash_delta)
            actual_cash_delta = new_cash - previous_cash
            previous_heat = int(player["heat"])
            new_heat = min(100, max(0, previous_heat + heat_delta))
            new_search_level = min(
                100, max(0, int(player["search_level"]) + heat_delta)
            )
            new_prison_until = now + prison_seconds if prison_seconds else 0
            await connection.execute(
                """
                UPDATE players
                SET cash = ?, heat = ?, search_level = ?,
                    in_prison_until = ?, last_jugg_time = ?,
                    last_jugg_cooldown = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (
                    new_cash,
                    new_heat,
                    new_search_level,
                    new_prison_until,
                    now,
                    cooldown_seconds,
                    user_id,
                    chat_id,
                ),
            )
            await connection.commit()
            return {
                "status": "ok",
                "balance": new_cash,
                "cash_delta": actual_cash_delta,
                "heat_delta": new_heat - previous_heat,
                "heat": new_heat,
                "search_level": new_search_level,
                "prison_seconds": prison_seconds,
                "cooldown_seconds": cooldown_seconds,
            }

    async def get_inventory(
        self, user_id: int, chat_id: int = 0
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT i.item_id, i.name, i.category, i.description, i.xp_bonus,
                       i.success_bonus, inv.quantity
                FROM inventory AS inv
                JOIN items AS i ON i.item_id = inv.item_id
                WHERE inv.user_id = ? AND inv.chat_id = ?
                ORDER BY i.category, i.name
                """,
                (user_id, chat_id),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def purchase_item(
        self, user_id: int, item_id: str, chat_id: int = 0
    ) -> str:
        """Buy one item atomically. Return an outcome code for the caller."""
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT p.cash, p.level, i.price, i.min_level
                FROM players AS p CROSS JOIN items AS i
                WHERE p.user_id = ? AND p.chat_id = ? AND i.item_id = ?
                """,
                (user_id, chat_id, item_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await connection.rollback()
                return "missing"
            if row["level"] < row["min_level"]:
                await connection.rollback()
                return "level"
            if row["cash"] < row["price"]:
                await connection.rollback()
                return "cash"

            await connection.execute(
                """
                UPDATE players SET cash = cash - ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (row["price"], user_id, chat_id),
            )
            await connection.execute(
                """
                INSERT INTO inventory (chat_id, user_id, item_id, quantity)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(chat_id, user_id, item_id)
                DO UPDATE SET quantity = quantity + 1
                """,
                (chat_id, user_id, item_id),
            )
            await connection.commit()
            return "ok"

    async def get_item_effects(
        self, user_id: int, chat_id: int = 0
    ) -> dict[str, float | int | bool | str | None]:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                """
                SELECT
                    COALESCE(MAX(i.xp_bonus), 0),
                    COALESCE(SUM(i.success_bonus), 0),
                    COALESCE(MAX(CASE WHEN i.item_id = 'obd_device' THEN 1 ELSE 0 END), 0),
                    COALESCE(MAX(CASE WHEN i.category IN
                        ('armes_poing', 'armes_lourdes', 'armes_longues')
                        THEN 1 ELSE 0 END), 0),
                    MAX(CASE WHEN i.category IN
                        ('armes_poing', 'armes_lourdes', 'armes_longues')
                        THEN i.name END),
                    MAX(CASE WHEN i.category = 'protection' THEN 1 ELSE 0 END),
                    MAX(CASE WHEN i.category = 'protection' THEN i.name END)
                FROM inventory AS inv
                JOIN items AS i ON i.item_id = inv.item_id
                WHERE inv.user_id = ? AND inv.chat_id = ?
                """,
                (user_id, chat_id),
            )
            (
                xp_bonus,
                success_bonus,
                has_obd,
                has_weapon,
                weapon_name,
                has_armor,
                armor_name,
            ) = await cursor.fetchone()
            return {
                "xp_bonus": xp_bonus,
                "success_bonus": success_bonus,
                "has_obd": bool(has_obd),
                "has_weapon": bool(has_weapon),
                "weapon_name": weapon_name,
                "has_armor": bool(has_armor),
                "armor_name": armor_name,
            }

    async def get_player_by_username(
        self, username: str, chat_id: int = 0
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT user_id, username, cash, heat, in_prison_until, gamos, level, xp
                FROM players
                WHERE username = ? COLLATE NOCASE AND chat_id = ?
                """,
                (username.lstrip("@"), chat_id),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def transfer_cash(
        self, sender_id: int, recipient_id: int, amount: int, chat_id: int = 0
    ) -> dict[str, int | str]:
        """Transfer cash between two players atomically within one chat."""
        if amount <= 0:
            raise ValueError("Le montant du virement doit être positif.")
        if sender_id == recipient_id:
            return {"status": "self"}

        async with self.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT user_id, cash
                FROM players
                WHERE chat_id = ? AND user_id IN (?, ?)
                """,
                (chat_id, sender_id, recipient_id),
            )
            players = {int(row[0]): int(row[1]) for row in await cursor.fetchall()}
            if sender_id not in players:
                return {"status": "sender_missing"}
            if recipient_id not in players:
                return {"status": "recipient_missing"}

            sender_balance = players[sender_id]
            if sender_balance < amount:
                return {
                    "status": "insufficient",
                    "sender_balance": sender_balance,
                }

            cursor = await connection.execute(
                """
                UPDATE players
                SET cash = cash - ?
                WHERE chat_id = ? AND user_id = ? AND cash >= ?
                """,
                (amount, chat_id, sender_id, amount),
            )
            if cursor.rowcount != 1:
                return {
                    "status": "insufficient",
                    "sender_balance": sender_balance,
                }
            cursor = await connection.execute(
                """
                UPDATE players
                SET cash = cash + ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (amount, chat_id, recipient_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Le destinataire a disparu pendant le virement.")

            cursor = await connection.execute(
                """
                SELECT user_id, cash FROM players
                WHERE chat_id = ? AND user_id IN (?, ?)
                """,
                (chat_id, sender_id, recipient_id),
            )
            balances = {int(row[0]): int(row[1]) for row in await cursor.fetchall()}
            return {
                "status": "ok",
                "sender_balance": balances[sender_id],
                "recipient_balance": balances[recipient_id],
            }

    async def apply_pvp_result(
        self,
        attacker_id: int,
        victim_id: int,
        outcome: str,
        fine: int,
        prison_until: datetime | float | None,
        xp_gain: int,
        transfer_rate: float = 0.20,
        chat_id: int = 0,
    ) -> dict[str, int | str] | None:
        """Apply an abstract PVP result and cash transfer atomically."""
        if attacker_id == victim_id:
            return None
        now = time.time()
        new_prison_until = _epoch_timestamp(prison_until) or 0
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT user_id, cash, xp, level, in_prison_until
                FROM players
                WHERE (user_id = ? AND chat_id = ?)
                   OR (user_id = ? AND chat_id = ?)
                """,
                (attacker_id, chat_id, victim_id, chat_id),
            )
            players = {row["user_id"]: dict(row) for row in await cursor.fetchall()}
            if attacker_id not in players or victim_id not in players:
                await connection.rollback()
                return None
            attacker = players[attacker_id]
            victim = players[victim_id]
            if attacker["level"] < 20:
                await connection.rollback()
                return {"status": "level"}
            if attacker["in_prison_until"]:
                until = _epoch_timestamp(attacker["in_prison_until"])
                if until is not None and until > now:
                    await connection.rollback()
                    return {"status": "prison"}

            if outcome == "counter":
                actual_fine = min(attacker["cash"], max(0, fine))
                await connection.execute(
                    """
                    UPDATE players
                    SET cash = cash - ?, in_prison_until = ?
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (actual_fine, new_prison_until, attacker_id, chat_id),
                )
                await connection.execute(
                    """
                    UPDATE players SET cash = cash + ?
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (actual_fine, victim_id, chat_id),
                )
                await connection.commit()
                return {"status": "counter", "fine": actual_fine}

            if outcome != "success":
                if outcome == "failed" and new_prison_until:
                    await connection.execute(
                        """
                        UPDATE players SET in_prison_until = ?
                        WHERE user_id = ? AND chat_id = ?
                        """,
                        (new_prison_until, attacker_id, chat_id),
                    )
                await connection.commit()
                return {"status": outcome}

            rate = min(0.35, max(0.15, transfer_rate))
            amount = min(victim["cash"], max(1, int(victim["cash"] * rate)))
            new_xp = attacker["xp"] + max(0, xp_gain)
            new_level = level_for_xp(new_xp)
            await connection.execute(
                """
                UPDATE players SET cash = cash + ?, xp = ?, level = ?,
                    in_prison_until = 0
                WHERE user_id = ? AND chat_id = ?
                """,
                (amount, new_xp, new_level, attacker_id, chat_id),
            )
            await connection.execute(
                """
                UPDATE players SET cash = cash - ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (amount, victim_id, chat_id),
            )
            await connection.commit()
            return {
                "status": "success",
                "amount": amount,
                "rate": rate,
                "xp": xp_gain,
                "level": new_level,
            }

    async def pay_bail(
        self, user_id: int, amount: int, chat_id: int = 0
    ) -> bool:
        """Charge bail and release an incarcerated player as one transaction."""
        now = time.time()
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT cash, in_prison_until FROM players
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await connection.rollback()
                return False

            until = _epoch_timestamp(row["in_prison_until"])
            if until is None:
                await connection.rollback()
                return False
            if until <= now:
                await connection.execute(
                    """
                    UPDATE players SET in_prison_until = 0
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (user_id, chat_id),
                )
                await connection.commit()
                return False
            if row["cash"] < amount:
                await connection.rollback()
                return False

            await connection.execute(
                """
                UPDATE players
                SET cash = cash - ?, in_prison_until = 0
                WHERE user_id = ? AND chat_id = ?
                """,
                (amount, user_id, chat_id),
            )
            await connection.commit()
            return True

    async def try_escape(
        self,
        user_id: int,
        chat_id: int,
        success: bool,
        failure_seconds: int = 30,
        search_increase: int = 5,
    ) -> tuple[str, int, int]:
        """Resolve a fictional escape attempt atomically with the prison state."""
        now = time.time()
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT in_prison_until, heat, search_level FROM players
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            player = await cursor.fetchone()
            if player is None:
                await connection.rollback()
                return "missing", 0, 0
            deadline = _epoch_timestamp(player["in_prison_until"])
            if deadline is None:
                await connection.rollback()
                return "free", 0, int(player["search_level"])
            if deadline <= now:
                await connection.execute(
                    """
                    UPDATE players SET in_prison_until = 0
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (user_id, chat_id),
                )
                await connection.commit()
                return "expired", 0, int(player["search_level"])

            if success:
                await connection.execute(
                    """
                    UPDATE players SET in_prison_until = 0
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (user_id, chat_id),
                )
                await connection.commit()
                return "escaped", 0, int(player["search_level"])

            new_deadline = max(deadline, now) + failure_seconds
            new_heat = min(100, int(player["heat"]) + search_increase)
            new_search_level = min(
                100, int(player["search_level"]) + search_increase
            )
            await connection.execute(
                """
                UPDATE players
                SET in_prison_until = ?, heat = ?, search_level = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (new_deadline, new_heat, new_search_level, user_id, chat_id),
            )
            await connection.commit()
            return (
                "failed",
                max(1, int(new_deadline - now)),
                new_search_level,
            )
