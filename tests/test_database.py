import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from database import Database
from game import PRISON_DURATION_SECONDS


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(str(Path(self.temp_dir.name) / "test.sqlite3"))
        await self.database.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_player_registration_and_action_result(self) -> None:
        await self.database.ensure_player(101, "rabza")
        player = await self.database.get_player(101)
        self.assertEqual(player["cash"], 500)
        self.assertEqual(player["heat"], 0)
        self.assertEqual(player["gamos"], "Aucun")

        applied = await self.database.apply_action_result(
            101, 350, 12, "RAV4", None
        )
        self.assertTrue(applied)
        player = await self.database.get_player(101)
        self.assertEqual(player["cash"], 850)
        self.assertEqual(player["heat"], 12)
        self.assertEqual(player["gamos"], "RAV4")

    async def test_schema_has_context_scoped_player_and_inventory_keys(self) -> None:
        connection = sqlite3.connect(self.database.path)
        try:
            player_columns = connection.execute(
                "PRAGMA table_info(players)"
            ).fetchall()
            inventory_columns = connection.execute(
                "PRAGMA table_info(inventory)"
            ).fetchall()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()

        player_primary_key = [
            row[1] for row in sorted(player_columns, key=lambda row: row[5]) if row[5]
        ]
        inventory_primary_key = [
            row[1]
            for row in sorted(inventory_columns, key=lambda row: row[5])
            if row[5]
        ]
        self.assertEqual(player_primary_key, ["chat_id", "user_id"])
        self.assertEqual(
            inventory_primary_key, ["chat_id", "user_id", "item_id"]
        )
        self.assertTrue({"players", "items", "inventory"} <= tables)

    async def test_prison_blocks_actions_and_bail_releases_player(self) -> None:
        await self.database.ensure_player(202, "en_taupe")
        deadline = datetime.now(timezone.utc) + timedelta(seconds=PRISON_DURATION_SECONDS)
        applied = await self.database.apply_action_result(
            202, 0, 15, None, deadline
        )
        self.assertTrue(applied)

        blocked = await self.database.apply_action_result(202, 250, 8, None, None)
        self.assertFalse(blocked)
        self.assertFalse(await self.database.pay_bail(202, 600))
        self.assertTrue(await self.database.pay_bail(202, 300))

        player = await self.database.get_player(202)
        self.assertEqual(player["cash"], 200)
        self.assertEqual(player["in_prison_until"], 0)

    async def test_leaderboard_is_ordered_by_cash(self) -> None:
        await self.database.ensure_player(1, "premier")
        await self.database.ensure_player(2, "deuxieme")
        await self.database.apply_action_result(2, 200, 0, None, None)

        leaders = await self.database.get_top_players()
        self.assertEqual(leaders[0]["username"], "deuxieme")
        self.assertEqual(leaders[0]["cash"], 700)

    async def test_xp_updates_level_and_shop_purchase_is_atomic(self) -> None:
        await self.database.ensure_player(303, "level_up")
        await self.database.apply_action_result(303, 10000, 0, None, None, xp_gain=19000)

        player = await self.database.get_player(303)
        self.assertEqual(player["level"], 20)
        self.assertEqual(player["xp"], 19000)
        self.assertEqual(await self.database.purchase_item(303, "obd_device"), "ok")
        self.assertEqual(await self.database.purchase_item(303, "glock_19"), "ok")
        effects = await self.database.get_item_effects(303)
        self.assertTrue(effects["has_obd"])
        self.assertTrue(effects["has_weapon"])
        self.assertEqual(effects["success_bonus"], 0.11)
        player = await self.database.get_player(303)
        self.assertEqual(player["cash"], 3000)

    async def test_shop_enforces_level_and_insufficient_cash(self) -> None:
        await self.database.ensure_player(404, "nouveau")
        self.assertEqual(await self.database.purchase_item(404, "glock_19"), "level")
        self.assertEqual(await self.database.purchase_item(404, "tech_fleece"), "ok")
        self.assertEqual(await self.database.purchase_item(404, "tech_fleece"), "ok")
        self.assertEqual(await self.database.purchase_item(404, "tech_fleece"), "cash")
        self.assertEqual(await self.database.purchase_item(404, "unknown"), "missing")

    async def test_shop_equipment_bonuses_are_added_for_juggs(self) -> None:
        await self.database.ensure_player(405, "bonus_items")
        self.assertEqual(await self.database.purchase_item(405, "tech_fleece"), "ok")
        self.assertEqual(await self.database.purchase_item(405, "bixi"), "ok")

        effects = await self.database.get_item_effects(405)

        self.assertAlmostEqual(effects["success_bonus"], 0.04)

    async def test_pvp_cash_transfer_and_counter_fine(self) -> None:
        await self.database.ensure_player(505, "attaquant")
        await self.database.ensure_player(606, "victime")
        await self.database.apply_action_result(
            505, 1000, 0, None, None, xp_gain=19000
        )
        result = await self.database.apply_pvp_result(
            505, 606, "success", 0, None, 70
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["amount"], 100)
        self.assertEqual((await self.database.get_player(505))["cash"], 1600)
        self.assertEqual((await self.database.get_player(606))["cash"], 400)

        boosted = await self.database.apply_pvp_result(
            505, 606, "success", 0, None, 70, transfer_rate=0.28
        )
        self.assertEqual(boosted["amount"], 112)
        self.assertEqual((await self.database.get_player(606))["cash"], 288)

        jail_until = datetime.now(timezone.utc) + timedelta(seconds=PRISON_DURATION_SECONDS)
        counter = await self.database.apply_pvp_result(
            505, 606, "counter", 100, jail_until, 0
        )
        self.assertEqual(counter["status"], "counter")
        self.assertEqual(counter["fine"], 100)
        self.assertEqual((await self.database.get_player(505))["cash"], 1612)
        self.assertEqual((await self.database.get_player(606))["cash"], 388)

    async def test_pvp_failure_sets_prison_without_transferring_cash(self) -> None:
        await self.database.ensure_player(808, "joueur_a")
        await self.database.ensure_player(909, "joueur_b")
        await self.database.apply_action_result(
            808, 0, 0, None, None, xp_gain=19000
        )
        deadline = datetime.now(timezone.utc) + timedelta(seconds=PRISON_DURATION_SECONDS)

        result = await self.database.apply_pvp_result(
            808, 909, "failed", 0, deadline, 0
        )

        self.assertEqual(result["status"], "failed")
        attacker = await self.database.get_player(808)
        victim = await self.database.get_player(909)
        self.assertEqual(attacker["cash"], 500)
        self.assertGreater(attacker["in_prison_until"], datetime.now(timezone.utc).timestamp())
        self.assertEqual(victim["cash"], 500)

    async def test_action_unlocks_after_jail_deadline_expires(self) -> None:
        await self.database.ensure_player(1001, "released")
        expired_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
        await self.database.apply_action_result(
            1001, 0, 0, None, expired_deadline
        )

        applied = await self.database.apply_action_result(
            1001, 120, 5, None, None
        )

        self.assertTrue(applied)
        player = await self.database.get_player(1001)
        self.assertEqual(player["cash"], 620)
        self.assertEqual(player["in_prison_until"], 0)

    async def test_get_player_persistently_clears_expired_unix_prison(self) -> None:
        await self.database.ensure_player(1002, "released_unix")
        async with aiosqlite.connect(self.database.path) as connection:
            await connection.execute(
                "UPDATE players SET in_prison_until = ? WHERE user_id = ?",
                (time.time() - 1, 1002),
            )
            await connection.commit()

        player = await self.database.get_player(1002)
        self.assertEqual(player["in_prison_until"], 0)
        connection = sqlite3.connect(self.database.path)
        try:
            persisted_value = connection.execute(
                "SELECT in_prison_until FROM players WHERE user_id = ?",
                (1002,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(persisted_value, 0)

    async def test_group_players_keep_separate_balances_and_inventories(self) -> None:
        await self.database.ensure_player(1111, "player_one")
        await self.database.ensure_player(2222, "player_two")
        await self.database.apply_action_result(
            1111, 4000, 0, None, None, xp_gain=19000
        )
        self.assertEqual(await self.database.purchase_item(1111, "glock_19"), "ok")

        first = await self.database.get_player(1111)
        second = await self.database.get_player(2222)
        first_items = await self.database.get_inventory(1111)
        second_items = await self.database.get_inventory(2222)
        self.assertEqual(first["cash"], 1000)
        self.assertEqual(second["cash"], 500)
        self.assertEqual([item["item_id"] for item in first_items], ["glock_19"])
        self.assertEqual(second_items, [])

    async def test_same_player_has_isolated_private_and_group_state(self) -> None:
        user_id = 1212
        private_chat_id = user_id
        group_chat_id = -100987
        await self.database.ensure_player(user_id, "multi_context", private_chat_id)
        await self.database.ensure_player(user_id, "multi_context", group_chat_id)
        await self.database.apply_action_result(
            user_id,
            4000,
            0,
            None,
            time.time() + PRISON_DURATION_SECONDS,
            xp_gain=19000,
            chat_id=group_chat_id,
        )
        self.assertEqual(
            await self.database.purchase_item(user_id, "glock_19", group_chat_id),
            "ok",
        )

        private_player = await self.database.get_player(user_id, private_chat_id)
        group_player = await self.database.get_player(user_id, group_chat_id)
        private_inventory = await self.database.get_inventory(user_id, private_chat_id)
        group_inventory = await self.database.get_inventory(user_id, group_chat_id)
        self.assertEqual(private_player["cash"], 500)
        self.assertEqual(private_player["in_prison_until"], 0)
        self.assertEqual(group_player["cash"], 1000)
        self.assertGreater(group_player["in_prison_until"], time.time())
        self.assertEqual(private_inventory, [])
        self.assertEqual([item["item_id"] for item in group_inventory], ["glock_19"])

    async def test_initialize_migrates_existing_players_table(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE players (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    cash INTEGER NOT NULL DEFAULT 500,
                    heat INTEGER NOT NULL DEFAULT 0,
                    in_prison_until TIMESTAMP,
                    gamos TEXT NOT NULL DEFAULT 'Aucun'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE items (
                    item_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    min_level INTEGER NOT NULL,
                    xp_bonus INTEGER NOT NULL DEFAULT 0,
                    success_bonus REAL NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE inventory (
                    user_id INTEGER NOT NULL,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (user_id, item_id)
                )
                """
            )
            connection.execute(
                "INSERT INTO players (user_id, username) VALUES (707, 'legacy')"
            )
            connection.execute(
                "INSERT INTO items VALUES ('tech_fleece', 'Tech Fleece', 'clothes', 250, 1, 5, 0)"
            )
            connection.execute(
                "INSERT INTO inventory (user_id, item_id) VALUES (707, 'tech_fleece')"
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Database(str(legacy_path))
        await migrated.initialize()
        player = await migrated.get_player(707, 707)
        self.assertEqual(player["level"], 1)
        self.assertEqual(player["xp"], 0)
        self.assertEqual(
            [item["item_id"] for item in await migrated.get_inventory(707, 707)],
            ["tech_fleece"],
        )


if __name__ == "__main__":
    unittest.main()
