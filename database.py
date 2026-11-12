"""SQLite persistence for players."""

from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
import time
from typing import Any, AsyncIterator

import aiosqlite

from game import ITEMS, VEHICLE_RENTAL_TERMS, level_for_xp, vehicle_rental_quote


MIN_HUSTLE_COOLDOWN_SECONDS = 15
MAX_HUSTLE_COOLDOWN_SECONDS = 30


def _reward_xp(xp_gain: int, cash_delta: int) -> int:
    """Never persist a positive cash reward without at least one XP."""
    return max(1, xp_gain) if cash_delta > 0 else max(0, xp_gain)


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
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            tables = {row[0] for row in await cursor.fetchall()}
            if "players" in tables:
                cursor = await connection.execute("PRAGMA table_info(players)")
                player_info = await cursor.fetchall()
                player_columns = {row[1] for row in player_info}
                player_primary_key = [
                    row[1] for row in sorted(player_info, key=lambda row: row[5])
                    if row[5]
                ]
            else:
                player_columns = set()
                player_primary_key = []

            migrate_players = "players" in tables and (
                "chat_id" not in player_columns
                or player_primary_key != ["chat_id", "user_id"]
            )
            players_backup_table: str | None = None
            inventory_backup_table: str | None = None
            migrate_inventory = False
            if "inventory" in tables:
                cursor = await connection.execute("PRAGMA table_info(inventory)")
                inventory_info = await cursor.fetchall()
                inventory_columns = {row[1] for row in inventory_info}
                inventory_primary_key = [
                    row[1] for row in sorted(inventory_info, key=lambda row: row[5])
                    if row[5]
                ]
                migrate_inventory = (
                    "chat_id" not in inventory_columns
                    or inventory_primary_key
                    != ["chat_id", "user_id", "item_id"]
                )

            if migrate_players or migrate_inventory:
                await connection.execute("PRAGMA foreign_keys = OFF")
                await connection.execute("BEGIN IMMEDIATE")
                if "inventory" in tables and (migrate_players or migrate_inventory):
                    inventory_backup_table = self._legacy_table_name(
                        "inventory", tables
                    )
                    await connection.execute(
                        f"ALTER TABLE inventory RENAME TO {inventory_backup_table}"
                    )
                    tables.add(inventory_backup_table)
                if migrate_players:
                    players_backup_table = self._legacy_table_name("players", tables)
                    await connection.execute(
                        f"ALTER TABLE players RENAME TO {players_backup_table}"
                    )
                    tables.add(players_backup_table)
                await connection.execute(
                    """
                    CREATE TABLE players (
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        username TEXT,
                        cash INTEGER NOT NULL DEFAULT 500 CHECK (cash >= 0),
                        bank INTEGER NOT NULL DEFAULT 0 CHECK (bank >= 0),
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
            if "players" not in tables:
                await connection.execute(
                    """
                    CREATE TABLE players (
                        chat_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        username TEXT,
                        cash INTEGER NOT NULL DEFAULT 500 CHECK (cash >= 0),
                        bank INTEGER NOT NULL DEFAULT 0 CHECK (bank >= 0),
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

            if players_backup_table is not None:
                cursor = await connection.execute(
                    f"PRAGMA table_info({players_backup_table})"
                )
                legacy_columns = {row[1] for row in await cursor.fetchall()}
                legacy_values = {
                    "chat_id": "chat_id" if "chat_id" in legacy_columns else "user_id",
                    "username": "username" if "username" in legacy_columns else "NULL",
                    "cash": "COALESCE(cash, 500)" if "cash" in legacy_columns else "500",
                    "bank": "COALESCE(bank, 0)" if "bank" in legacy_columns else "0",
                    "heat": "COALESCE(heat, 0)" if "heat" in legacy_columns else "0",
                    "in_prison_until": (
                        "COALESCE(in_prison_until, 0)"
                        if "in_prison_until" in legacy_columns
                        else "0"
                    ),
                    "gamos": (
                        "COALESCE(gamos, 'Aucun')" if "gamos" in legacy_columns
                        else "'Aucun'"
                    ),
                    "level": "COALESCE(level, 1)" if "level" in legacy_columns else "1",
                    "xp": "COALESCE(xp, 0)" if "xp" in legacy_columns else "0",
                    "search_level": (
                        "COALESCE(search_level, 0)"
                        if "search_level" in legacy_columns
                        else (
                            "COALESCE(heat, 0)" if "heat" in legacy_columns else "0"
                        )
                    ),
                    "last_hustle_time": (
                        "COALESCE(last_hustle_time, 0)"
                        if "last_hustle_time" in legacy_columns
                        else "0"
                    ),
                    "last_hustle_cooldown": (
                        "COALESCE(last_hustle_cooldown, 0)"
                        if "last_hustle_cooldown" in legacy_columns
                        else "0"
                    ),
                    "last_jugg_time": (
                        "COALESCE(last_jugg_time, 0)"
                        if "last_jugg_time" in legacy_columns
                        else "0"
                    ),
                    "last_jugg_cooldown": (
                        "COALESCE(last_jugg_cooldown, 0)"
                        if "last_jugg_cooldown" in legacy_columns
                        else "0"
                    ),
                }
                await connection.execute(
                    f"""
                    INSERT INTO players
                        (chat_id, user_id, username, cash, bank, heat,
                         in_prison_until, gamos, level, xp, search_level,
                         last_hustle_time, last_hustle_cooldown,
                         last_jugg_time, last_jugg_cooldown)
                    SELECT {legacy_values["chat_id"]}, user_id,
                           {legacy_values["username"]}, {legacy_values["cash"]},
                           {legacy_values["bank"]}, {legacy_values["heat"]},
                           {legacy_values["in_prison_until"]},
                           {legacy_values["gamos"]}, {legacy_values["level"]},
                           {legacy_values["xp"]}, {legacy_values["search_level"]},
                           {legacy_values["last_hustle_time"]},
                           {legacy_values["last_hustle_cooldown"]},
                           {legacy_values["last_jugg_time"]},
                           {legacy_values["last_jugg_cooldown"]}
                    FROM {players_backup_table}
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
            if "bank" not in player_columns:
                await connection.execute(
                    """
                    ALTER TABLE players ADD COLUMN bank INTEGER
                    NOT NULL DEFAULT 0 CHECK (bank >= 0)
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
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vehicle_rentals (
                    rental_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_id TEXT NOT NULL REFERENCES items(item_id),
                    started_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    deposit_cash_initial INTEGER NOT NULL DEFAULT 0
                        CHECK (deposit_cash_initial >= 0),
                    deposit_bank_initial INTEGER NOT NULL DEFAULT 0
                        CHECK (deposit_bank_initial >= 0),
                    deposit_cash_remaining INTEGER NOT NULL DEFAULT 0
                        CHECK (deposit_cash_remaining >= 0),
                    deposit_bank_remaining INTEGER NOT NULL DEFAULT 0
                        CHECK (deposit_bank_remaining >= 0),
                    damage_state TEXT NOT NULL DEFAULT 'intact'
                        CHECK (damage_state IN ('intact', 'light', 'moderate', 'major')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    ended_at REAL,
                    end_reason TEXT,
                    FOREIGN KEY (chat_id, user_id)
                        REFERENCES players(chat_id, user_id) ON DELETE CASCADE
                )
                """
            )
            cursor = await connection.execute(
                "PRAGMA table_info(vehicle_rentals)"
            )
            rental_columns = {row[1] for row in await cursor.fetchall()}
            if "deposit_cash_initial" not in rental_columns:
                await connection.execute(
                    """
                    ALTER TABLE vehicle_rentals
                    ADD COLUMN deposit_cash_initial INTEGER NOT NULL DEFAULT 0
                    """
                )
                await connection.execute(
                    """
                    UPDATE vehicle_rentals
                    SET deposit_cash_initial = deposit_cash_remaining
                    """
                )
            if "deposit_bank_initial" not in rental_columns:
                await connection.execute(
                    """
                    ALTER TABLE vehicle_rentals
                    ADD COLUMN deposit_bank_initial INTEGER NOT NULL DEFAULT 0
                    """
                )
                await connection.execute(
                    """
                    UPDATE vehicle_rentals
                    SET deposit_bank_initial = deposit_bank_remaining
                    """
                )
            if "repair_due" not in rental_columns:
                await connection.execute(
                    """
                    ALTER TABLE vehicle_rentals
                    ADD COLUMN repair_due INTEGER NOT NULL DEFAULT 0
                    CHECK (repair_due >= 0)
                    """
                )
            await connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_vehicle_rental
                ON vehicle_rentals(chat_id, user_id) WHERE active = 1
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shoot_cooldowns (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    last_shot_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, user_id, target_id),
                    FOREIGN KEY (chat_id, user_id)
                        REFERENCES players(chat_id, user_id) ON DELETE CASCADE
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS drive_cooldowns (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_drive_at REAL NOT NULL,
                    PRIMARY KEY (chat_id, user_id),
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
            if inventory_backup_table is not None:
                cursor = await connection.execute(
                    f"PRAGMA table_info({inventory_backup_table})"
                )
                legacy_inventory_columns = {
                    row[1] for row in await cursor.fetchall()
                }
                legacy_quantity = (
                    "COALESCE(inv.quantity, 1)"
                    if "quantity" in legacy_inventory_columns
                    else "1"
                )
                if "chat_id" in legacy_inventory_columns:
                    legacy_inventory_select = f"""
                        SELECT inv.chat_id, inv.user_id, inv.item_id,
                               {legacy_quantity}
                        FROM {inventory_backup_table} AS inv
                        JOIN players AS p
                          ON p.chat_id = inv.chat_id AND p.user_id = inv.user_id
                    """
                else:
                    legacy_inventory_select = f"""
                        SELECT p.chat_id, inv.user_id, inv.item_id,
                               {legacy_quantity}
                        FROM {inventory_backup_table} AS inv
                        JOIN players AS p ON p.user_id = inv.user_id
                    """
                await connection.execute(
                    f"""
                    INSERT OR IGNORE INTO inventory
                        (chat_id, user_id, item_id, quantity)
                    {legacy_inventory_select}
                    """
                )
            await connection.commit()
            if migrate_players or migrate_inventory:
                await connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _legacy_table_name(table_name: str, tables: set[str]) -> str:
        candidate = f"{table_name}_legacy"
        suffix = 2
        while candidate in tables:
            candidate = f"{table_name}_legacy_{suffix}"
            suffix += 1
        return candidate

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
                SELECT user_id, username, cash, bank, balance, heat,
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

    async def move_bank_funds(
        self,
        user_id: int,
        chat_id: int,
        amount: int | None,
        deposit: bool,
    ) -> dict[str, int | str]:
        """Move cash between a player's wallet and protected bank atomically."""
        if amount is not None and amount <= 0:
            return {"status": "invalid"}

        async with self.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT cash, bank FROM players
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return {"status": "missing"}

            cash, bank = int(row[0]), int(row[1])
            available = cash if deposit else bank
            if available <= 0:
                return {"status": "empty"}
            moved = available if amount is None else amount
            if moved > available:
                return {"status": "insufficient"}

            if deposit:
                await connection.execute(
                    """
                    UPDATE players SET cash = cash - ?, bank = bank + ?
                    WHERE user_id = ? AND chat_id = ? AND cash >= ?
                    """,
                    (moved, moved, user_id, chat_id, moved),
                )
            else:
                await connection.execute(
                    """
                    UPDATE players SET cash = cash + ?, bank = bank - ?
                    WHERE user_id = ? AND chat_id = ? AND bank >= ?
                    """,
                    (moved, moved, user_id, chat_id, moved),
                )

            cursor = await connection.execute(
                """
                SELECT cash, bank FROM players
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            updated = await cursor.fetchone()
            if updated is None:
                raise RuntimeError("Le joueur a disparu pendant le virement bancaire.")
            return {
                "status": "ok",
                "amount": moved,
                "cash": int(updated[0]),
                "bank": int(updated[1]),
            }

    async def settle_casino_bet(
        self,
        user_id: int,
        chat_id: int,
        stake: int,
        net_change: int,
        xp_gain: int = 0,
    ) -> int | None:
        """Apply a wager's net balance change if its stake is affordable."""
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT cash, xp FROM players
                WHERE user_id = ? AND chat_id = ?
                """,
                (user_id, chat_id),
            )
            player = await cursor.fetchone()
            if player is None or int(player["cash"]) < stake:
                await connection.rollback()
                return None
            new_xp = int(player["xp"]) + _reward_xp(xp_gain, net_change)
            new_level = level_for_xp(new_xp)
            cursor = await connection.execute(
                """
                UPDATE players
                SET cash = cash + ?, xp = ?, level = ?
                WHERE user_id = ? AND chat_id = ? AND cash >= ?
                """,
                (net_change, new_xp, new_level, user_id, chat_id, stake),
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
            new_xp = row["xp"] + _reward_xp(xp_gain, cash_delta)
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
        xp_gain: int = 0,
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
                SELECT cash, heat, search_level, xp, in_prison_until,
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
            new_xp = int(player["xp"]) + _reward_xp(xp_gain, cash_delta)
            new_level = level_for_xp(new_xp)
            await connection.execute(
                """
                UPDATE players SET cash = cash + ?, heat = ?,
                    search_level = ?, xp = ?, level = ?, last_hustle_time = ?,
                    last_hustle_cooldown = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (
                    actual_cash_delta,
                    new_heat,
                    new_search_level,
                    new_xp,
                    new_level,
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
        xp_gain: int = 0,
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
                SELECT cash, heat, search_level, xp, level, in_prison_until,
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
            new_xp = int(player["xp"]) + _reward_xp(xp_gain, cash_delta)
            new_level = level_for_xp(new_xp)
            await connection.execute(
                """
                UPDATE players
                SET cash = ?, heat = ?, search_level = ?,
                    in_prison_until = ?, xp = ?, level = ?, last_jugg_time = ?,
                    last_jugg_cooldown = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (
                    new_cash,
                    new_heat,
                    new_search_level,
                    new_prison_until,
                    new_xp,
                    new_level,
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
                "xp": new_xp,
                "xp_gain": _reward_xp(xp_gain, cash_delta),
                "previous_level": int(player["level"]),
                "level": new_level,
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

    async def get_owned_vehicles(
        self, user_id: int, chat_id: int = 0
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT i.item_id, i.name, inv.quantity
                FROM inventory AS inv
                JOIN items AS i ON i.item_id = inv.item_id
                WHERE inv.user_id = ? AND inv.chat_id = ?
                  AND i.category = 'vehicules'
                ORDER BY i.name
                """,
                (user_id, chat_id),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def has_available_vehicle(self, user_id: int, chat_id: int = 0) -> bool:
        rental = await self.get_vehicle_rental(user_id, chat_id)
        if rental is not None and rental.get("status") != "expired":
            return True
        return bool(await self.get_owned_vehicles(user_id, chat_id))

    async def repair_vehicle_rental(
        self, user_id: int, chat_id: int = 0
    ) -> dict[str, Any]:
        """Charge outstanding rental repair costs before the vehicle is reused."""
        now = time.time()
        async with self.transaction() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT r.rental_id, r.expires_at, r.repair_due, i.name, p.cash
                FROM vehicle_rentals AS r
                JOIN items AS i ON i.item_id = r.item_id
                JOIN players AS p
                  ON p.user_id = r.user_id AND p.chat_id = r.chat_id
                WHERE r.user_id = ? AND r.chat_id = ? AND r.active = 1
                """,
                (user_id, chat_id),
            )
            rental = await cursor.fetchone()
            if rental is None:
                return {"status": "missing"}
            if float(rental["expires_at"]) <= now:
                return {"status": "expired"}
            due = int(rental["repair_due"])
            if due <= 0:
                return {"status": "clean", "name": rental["name"]}
            if int(rental["cash"]) < due:
                return {"status": "funds", "fee": due}
            await connection.execute(
                """
                UPDATE players SET cash = cash - ?
                WHERE user_id = ? AND chat_id = ? AND cash >= ?
                """,
                (due, user_id, chat_id, due),
            )
            await connection.execute(
                """
                UPDATE vehicle_rentals
                SET repair_due = 0, damage_state = 'intact'
                WHERE rental_id = ? AND user_id = ? AND chat_id = ? AND active = 1
                """,
                (rental["rental_id"], user_id, chat_id),
            )
            return {"status": "ok", "name": rental["name"], "fee": due}

    async def complete_vehicle_drive(
        self,
        user_id: int,
        chat_id: int,
        incident_roll: float,
        severity_roll: float,
        maintenance_roll: float,
        cooldown_seconds: int = 60,
    ) -> dict[str, Any]:
        """Resolve a city drive, its rewards, upkeep, rental risk, and cooldown."""
        if not all(
            0 <= roll < 1
            for roll in (incident_roll, severity_roll, maintenance_roll)
        ):
            raise ValueError("Les tirages doivent être compris entre 0 inclus et 1 exclu.")
        if cooldown_seconds <= 0:
            raise ValueError("Le cooldown doit être positif.")
        now = time.time()
        async with self.transaction() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT p.cash, p.xp, p.level, p.in_prison_until,
                       c.last_drive_at
                FROM players AS p
                LEFT JOIN drive_cooldowns AS c
                  ON c.user_id = p.user_id AND c.chat_id = p.chat_id
                WHERE p.user_id = ? AND p.chat_id = ?
                """,
                (user_id, chat_id),
            )
            player = await cursor.fetchone()
            if player is None:
                return {"status": "missing"}
            prison_until = _epoch_timestamp(player["in_prison_until"])
            if prison_until is not None and prison_until > now:
                return {"status": "prison", "remaining": int(prison_until - now) + 1}
            rental_cursor = await connection.execute(
                """
                SELECT r.*, i.name
                FROM vehicle_rentals AS r
                JOIN items AS i ON i.item_id = r.item_id
                WHERE r.user_id = ? AND r.chat_id = ? AND r.active = 1
                """,
                (user_id, chat_id),
            )
            rental = await rental_cursor.fetchone()
            if rental is not None and float(rental["expires_at"]) <= now:
                await self._settle_vehicle_rental(
                    connection, rental, user_id, chat_id, now, "expired"
                )
                rental = None

            vehicle_name: str | None = None
            is_rental = rental is not None
            if rental is not None:
                vehicle_name = str(rental["name"])
                if int(rental["repair_due"]) > 0:
                    return {
                        "status": "repair_required",
                        "name": vehicle_name,
                        "fee": int(rental["repair_due"]),
                    }
            else:
                cursor = await connection.execute(
                    """
                    SELECT i.name
                    FROM inventory AS inv
                    JOIN items AS i ON i.item_id = inv.item_id
                    WHERE inv.user_id = ? AND inv.chat_id = ?
                      AND i.category = 'vehicules'
                    ORDER BY i.name
                    LIMIT 1
                    """,
                    (user_id, chat_id),
                )
                owned = await cursor.fetchone()
                if owned is None:
                    return {"status": "no_vehicle"}
                vehicle_name = str(owned["name"])

            last_drive_at = player["last_drive_at"]
            if last_drive_at is not None:
                remaining = cooldown_seconds - (now - float(last_drive_at))
                if remaining > 0:
                    return {"status": "cooldown", "remaining": int(remaining) + 1}

            await connection.execute(
                """
                INSERT INTO drive_cooldowns (chat_id, user_id, last_drive_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET last_drive_at = excluded.last_drive_at
                """,
                (chat_id, user_id, now),
            )

            incident = is_rental and incident_roll < 0.25
            damage_cost = 0
            damage_status = "clear"
            police = False
            maintenance_fee = 0
            if incident and rental is not None:
                severity = (
                    "light" if severity_roll < 0.6
                    else "moderate" if severity_roll < 0.9
                    else "major"
                )
                deposit_cash = int(rental["deposit_cash_remaining"])
                deposit_bank = int(rental["deposit_bank_remaining"])
                deposit_total = deposit_cash + deposit_bank
                retention_percent = 30 if severity == "light" else 70
                damage_cost = (
                    deposit_total if severity == "major"
                    else min(deposit_total, max(1, deposit_total * retention_percent // 100))
                )
                cash_loss = min(deposit_cash, damage_cost)
                bank_loss = damage_cost - cash_loss
                remainder = deposit_total - damage_cost
                repair_fee = max(10, damage_cost)
                confiscated = severity == "major" or remainder == 0
                await connection.execute(
                    """
                    UPDATE vehicle_rentals
                    SET deposit_cash_remaining = ?,
                        deposit_bank_remaining = ?,
                        damage_state = ?,
                        repair_due = ?,
                        active = ?,
                        ended_at = ?,
                        end_reason = ?
                    WHERE rental_id = ? AND user_id = ? AND chat_id = ? AND active = 1
                    """,
                    (
                        max(0, deposit_cash - cash_loss),
                        max(0, deposit_bank - bank_loss),
                        severity,
                        0 if confiscated else repair_fee,
                        0 if confiscated else 1,
                        now if confiscated else None,
                        "major_damage" if severity == "major"
                        else "confiscated" if confiscated else None,
                        rental["rental_id"],
                        user_id,
                        chat_id,
                    ),
                )
                damage_status = "major" if severity == "major" else (
                    "confiscated" if confiscated else severity
                )
                if not confiscated:
                    return {
                        "status": damage_status,
                        "name": vehicle_name,
                        "damage_cost": damage_cost,
                        "repair_fee": repair_fee,
                        "deposit_remaining": remainder,
                        "is_rental": 1,
                    }
            elif not is_rental and maintenance_roll < 0.02:
                maintenance_fee = min(15, int(player["cash"]))
                await connection.execute(
                    """
                    UPDATE players SET cash = cash - ?
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (maintenance_fee, user_id, chat_id),
                )

            current_cash = int(player["cash"]) - maintenance_fee
            reward = 75
            cash_delta = reward - maintenance_fee
            if incident:
                cash_delta = -damage_cost
            new_cash = max(0, current_cash + (reward if not incident else 0))
            new_xp = int(player["xp"]) + 20
            new_level = level_for_xp(new_xp)
            await connection.execute(
                """
                UPDATE players SET cash = ?, xp = ?, level = ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (new_cash, new_xp, new_level, user_id, chat_id),
            )
            return {
                "status": damage_status,
                "name": vehicle_name,
                "is_rental": int(is_rental),
                "cash_delta": cash_delta,
                "cash": new_cash,
                "xp": 20,
                "previous_level": int(player["level"]),
                "level": new_level,
                "maintenance_fee": maintenance_fee,
                "damage_cost": damage_cost,
                "police_intervention": int(police),
            }

    async def create_vehicle_rental(
        self, user_id: int, chat_id: int, item_id: str, duration: str
    ) -> dict[str, Any]:
        """Create a scoped rental and charge rent plus deposit atomically."""
        term = VEHICLE_RENTAL_TERMS.get(duration)
        item = next((candidate for candidate in ITEMS if candidate.item_id == item_id), None)
        if term is None or item is None or item.category != "vehicules":
            return {"status": "missing"}
        label, seconds, _, _ = term
        rent, deposit = vehicle_rental_quote(item, duration)
        now = time.time()
        async with self.transaction() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT rental_id, expires_at, deposit_cash_remaining,
                       deposit_bank_remaining
                FROM vehicle_rentals
                WHERE user_id = ? AND chat_id = ? AND active = 1
                """,
                (user_id, chat_id),
            )
            current = await cursor.fetchone()
            if current is not None and float(current["expires_at"]) <= now:
                await self._settle_vehicle_rental(
                    connection, current, user_id, chat_id, now, "expired"
                )
                current = None
            if current is not None:
                return {"status": "active"}

            cursor = await connection.execute(
                """
                SELECT p.cash, p.bank, p.level, i.min_level
                FROM players AS p
                CROSS JOIN items AS i
                WHERE p.user_id = ? AND p.chat_id = ? AND i.item_id = ?
                  AND i.category = 'vehicules'
                """,
                (user_id, chat_id, item_id),
            )
            player = await cursor.fetchone()
            if player is None:
                return {"status": "missing"}
            if int(player["level"]) < int(player["min_level"]):
                return {"status": "level"}
            total_due = rent + deposit
            cash = int(player["cash"])
            bank = int(player["bank"])
            if cash + bank < total_due:
                return {"status": "funds"}

            cash_payment = min(cash, total_due)
            bank_payment = total_due - cash_payment
            cash_for_rent = min(cash_payment, rent)
            cash_deposit = cash_payment - cash_for_rent
            bank_deposit = deposit - cash_deposit
            await connection.execute(
                """
                UPDATE players SET cash = cash - ?, bank = bank - ?
                WHERE user_id = ? AND chat_id = ?
                """,
                (cash_payment, bank_payment, user_id, chat_id),
            )
            expires_at = now + seconds
            await connection.execute(
                """
                INSERT INTO vehicle_rentals (
                    chat_id, user_id, item_id, started_at, expires_at,
                    deposit_cash_initial, deposit_bank_initial,
                    deposit_cash_remaining, deposit_bank_remaining
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    user_id,
                    item_id,
                    now,
                    expires_at,
                    cash_deposit,
                    bank_deposit,
                    cash_deposit,
                    bank_deposit,
                ),
            )
            return {
                "status": "ok",
                "name": item.name,
                "duration": label,
                "rent": rent,
                "deposit": deposit,
                "cash_paid": cash_payment,
                "bank_paid": bank_payment,
                "expires_at": expires_at,
            }

    async def _settle_vehicle_rental(
        self,
        connection: aiosqlite.Connection,
        rental: aiosqlite.Row,
        user_id: int,
        chat_id: int,
        now: float,
        reason: str,
    ) -> dict[str, int]:
        cash_refund = int(rental["deposit_cash_remaining"])
        bank_refund = int(rental["deposit_bank_remaining"])
        await connection.execute(
            """
            UPDATE players SET cash = cash + ?, bank = bank + ?
            WHERE user_id = ? AND chat_id = ?
            """,
            (cash_refund, bank_refund, user_id, chat_id),
        )
        await connection.execute(
            """
            UPDATE vehicle_rentals
            SET deposit_cash_remaining = 0, deposit_bank_remaining = 0,
                active = 0, ended_at = ?, end_reason = ?
            WHERE rental_id = ? AND user_id = ? AND chat_id = ? AND active = 1
            """,
            (now, reason, rental["rental_id"], user_id, chat_id),
        )
        return {"cash_refund": cash_refund, "bank_refund": bank_refund}

    async def get_vehicle_rental(
        self, user_id: int, chat_id: int = 0
    ) -> dict[str, Any] | None:
        """Return the active rental, settling and refunding it if expired."""
        now = time.time()
        async with self.transaction() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT r.*, i.name
                FROM vehicle_rentals AS r
                JOIN items AS i ON i.item_id = r.item_id
                WHERE r.user_id = ? AND r.chat_id = ? AND r.active = 1
                """,
                (user_id, chat_id),
            )
            rental = await cursor.fetchone()
            if rental is None:
                return None
            if float(rental["expires_at"]) <= now:
                await self._settle_vehicle_rental(
                    connection, rental, user_id, chat_id, now, "expired"
                )
                return {
                    "status": "expired",
                    "name": rental["name"],
                    "cash_refund": int(rental["deposit_cash_remaining"]),
                    "bank_refund": int(rental["deposit_bank_remaining"]),
                }
            return dict(rental)

    async def return_vehicle_rental(
        self, user_id: int, chat_id: int = 0
    ) -> dict[str, Any]:
        """Return an active vehicle or settle it at its scheduled expiry."""
        now = time.time()
        async with self.transaction() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT r.*, i.name
                FROM vehicle_rentals AS r
                JOIN items AS i ON i.item_id = r.item_id
                WHERE r.user_id = ? AND r.chat_id = ? AND r.active = 1
                """,
                (user_id, chat_id),
            )
            rental = await cursor.fetchone()
            if rental is None:
                return {"status": "missing"}
            reason = (
                "expired" if float(rental["expires_at"]) <= now else "returned"
            )
            refund = await self._settle_vehicle_rental(
                connection, rental, user_id, chat_id, now, reason
            )
            return {
                "status": reason,
                "name": rental["name"],
                **refund,
            }

    async def apply_vehicle_trip(
        self,
        user_id: int,
        chat_id: int,
        incident_roll: float,
        severity_roll: float,
    ) -> dict[str, Any]:
        """Apply one arcade trip's rental risk without affecting player progression."""
        now = time.time()
        async with self.transaction() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT r.*, i.name
                FROM vehicle_rentals AS r
                JOIN items AS i ON i.item_id = r.item_id
                WHERE r.user_id = ? AND r.chat_id = ? AND r.active = 1
                """,
                (user_id, chat_id),
            )
            rental = await cursor.fetchone()
            if rental is None:
                return {"status": "none"}
            if float(rental["expires_at"]) <= now:
                refund = await self._settle_vehicle_rental(
                    connection, rental, user_id, chat_id, now, "expired"
                )
                return {"status": "expired", "name": rental["name"], **refund}
            if int(rental["repair_due"]) > 0:
                return {
                    "status": "repair_required",
                    "name": rental["name"],
                    "fee": int(rental["repair_due"]),
                }
            if incident_roll >= 0.25:
                return {"status": "clear", "name": rental["name"]}

            severity = "light" if severity_roll < 0.6 else (
                "moderate" if severity_roll < 0.9 else "major"
            )
            cash_deposit = int(rental["deposit_cash_remaining"])
            bank_deposit = int(rental["deposit_bank_remaining"])
            if severity == "major":
                damage_cost = cash_deposit + bank_deposit
            else:
                retention_percent = 30 if severity == "light" else 70
                initial_cursor = await connection.execute(
                    """
                    SELECT
                        CASE WHEN deposit_cash_initial + deposit_bank_initial > 0
                            THEN deposit_cash_initial + deposit_bank_initial
                            ELSE deposit_cash_remaining + deposit_bank_remaining
                        END
                    FROM vehicle_rentals WHERE rental_id = ?
                    """,
                    (rental["rental_id"],),
                )
                current_deposit = int((await initial_cursor.fetchone())[0])
                # Damage is charged against the remaining guarantee.
                damage_cost = current_deposit * retention_percent // 100
                if current_deposit and damage_cost == 0:
                    damage_cost = 1
            damage_cost = min(damage_cost, cash_deposit + bank_deposit)
            cash_loss = min(cash_deposit, damage_cost)
            bank_loss = damage_cost - cash_loss
            cash_deposit -= cash_loss
            bank_deposit -= bank_loss
            repair_fee = max(10, damage_cost)
            exhausted = severity == "major" or cash_deposit + bank_deposit == 0
            await connection.execute(
                """
                UPDATE vehicle_rentals
                SET deposit_cash_remaining = ?, deposit_bank_remaining = ?,
                    damage_state = ?, repair_due = ?, active = ?,
                    ended_at = ?, end_reason = ?
                WHERE rental_id = ? AND user_id = ? AND chat_id = ? AND active = 1
                """,
                (
                    cash_deposit,
                    bank_deposit,
                    severity,
                    0 if exhausted else repair_fee,
                    0 if exhausted else 1,
                    now if exhausted else None,
                    "major_damage" if severity == "major" else (
                        "confiscated" if exhausted else None
                    ),
                    rental["rental_id"],
                    user_id,
                    chat_id,
                ),
            )
            return {
                "status": (
                    "major"
                    if severity == "major"
                    else "confiscated" if exhausted else severity
                ),
                "name": rental["name"],
                "damage_cost": damage_cost,
                "repair_fee": repair_fee,
                "deposit_remaining": cash_deposit + bank_deposit,
            }

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

    async def get_weapon_profile(
        self, user_id: int, chat_id: int = 0
    ) -> dict[str, int | str | None]:
        """Return the strongest owned weapon's abstract arcade rating."""
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                """
                SELECT i.item_id, i.name, i.category
                FROM inventory AS inv
                JOIN items AS i ON i.item_id = inv.item_id
                WHERE inv.user_id = ? AND inv.chat_id = ?
                  AND i.category IN
                    ('armes', 'armes_poing', 'armes_lourdes', 'armes_longues')
                ORDER BY CASE i.category
                    WHEN 'armes_longues' THEN 3
                    WHEN 'armes_lourdes' THEN 2
                    ELSE 1
                END DESC, i.name
                LIMIT 1
                """,
                (user_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return {"has_weapon": 0, "power": 0, "name": None}
            power = {
                "armes": 1,
                "armes_poing": 1,
                "armes_lourdes": 2,
                "armes_longues": 3,
            }[str(row[2])]
            return {"has_weapon": 1, "power": power, "name": str(row[1])}

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

            rate = min(0.50, max(0.15, transfer_rate))
            amount = min(victim["cash"], max(1, int(victim["cash"] * rate)))
            new_xp = attacker["xp"] + _reward_xp(xp_gain, amount)
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

    async def apply_shootout_result(
        self,
        attacker_id: int,
        victim_id: int,
        winner_id: int,
        xp_gain: int,
        police_intervention: bool,
        prison_until: datetime | float | None,
        chat_id: int = 0,
        cooldown_seconds: int = 60,
    ) -> dict[str, int | str] | None:
        """Apply one group-scoped arcade duel, its loot, and target cooldown."""
        if (
            attacker_id == victim_id
            or winner_id not in {attacker_id, victim_id}
            or cooldown_seconds <= 0
        ):
            return None
        loser_id = victim_id if winner_id == attacker_id else attacker_id
        now = time.time()
        prison_deadline = _epoch_timestamp(prison_until) or 0
        async with self.transaction() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT user_id, cash, bank, xp, level, in_prison_until
                FROM players
                WHERE chat_id = ? AND user_id IN (?, ?)
                """,
                (chat_id, attacker_id, victim_id),
            )
            players = {int(row["user_id"]): dict(row) for row in await cursor.fetchall()}
            if attacker_id not in players or victim_id not in players:
                return {"status": "missing"}

            cursor = await connection.execute(
                """
                SELECT last_shot_at FROM shoot_cooldowns
                WHERE chat_id = ? AND user_id = ? AND target_id = ?
                """,
                (chat_id, attacker_id, victim_id),
            )
            cooldown = await cursor.fetchone()
            if cooldown is not None:
                remaining = cooldown_seconds - (now - float(cooldown["last_shot_at"]))
                if remaining > 0:
                    return {"status": "cooldown", "remaining": int(remaining) + 1}

            for player_id in (attacker_id, victim_id):
                until = _epoch_timestamp(players[player_id]["in_prison_until"])
                if until is not None and until > now:
                    return {
                        "status": "prison",
                        "player_id": player_id,
                        "remaining": int(until - now) + 1,
                    }

            await connection.execute(
                """
                INSERT INTO shoot_cooldowns (chat_id, user_id, target_id, last_shot_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id, target_id)
                DO UPDATE SET last_shot_at = excluded.last_shot_at
                """,
                (chat_id, attacker_id, victim_id, now),
            )

            winner = players[winner_id]
            loser = players[loser_id]
            cash_loss = min(
                int(loser["cash"]),
                max(1, int(int(loser["cash"]) * 0.25)),
            ) if int(loser["cash"]) > 0 else 0
            hospital_fee = min(cash_loss, 100)
            loot = cash_loss - hospital_fee
            new_xp = int(winner["xp"]) + _reward_xp(xp_gain, loot)
            new_level = level_for_xp(new_xp)
            await connection.execute(
                """
                UPDATE players SET cash = cash - ?,
                    in_prison_until = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (
                    cash_loss,
                    prison_deadline if police_intervention else 0,
                    chat_id,
                    loser_id,
                ),
            )
            await connection.execute(
                """
                UPDATE players SET cash = cash + ?, xp = ?, level = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (loot, new_xp, new_level, chat_id, winner_id),
            )
            return {
                "status": "ok",
                "winner_id": winner_id,
                "loser_id": loser_id,
                "cash_loss": cash_loss,
                "hospital_fee": hospital_fee,
                "loot": loot,
                "xp": _reward_xp(xp_gain, loot),
                "level": new_level,
                "previous_level": int(winner["level"]),
                "police_intervention": int(police_intervention),
                "prison_seconds": (
                    max(0, int(prison_deadline - now))
                    if police_intervention and prison_deadline
                    else 0
                ),
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
