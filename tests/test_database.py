import asyncio
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from database import Database
from game import ITEMS, PRISON_DURATION_SECONDS, SHOP_CATEGORIES


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
        self.assertEqual(player["bank"], 0)
        self.assertEqual(player["balance"], 500)
        self.assertEqual(player["heat"], 0)
        self.assertEqual(player["search_level"], 0)
        self.assertEqual(player["last_hustle_time"], 0)
        self.assertEqual(player["last_hustle_cooldown"], 0)
        self.assertEqual(player["last_jugg_time"], 0)
        self.assertEqual(player["last_jugg_cooldown"], 0)
        self.assertEqual(player["gamos"], "Aucun")

        applied = await self.database.apply_action_result(
            101, 350, 12, "RAV4", None
        )
        self.assertTrue(applied)
        player = await self.database.get_player(101)
        self.assertEqual(player["cash"], 850)
        self.assertEqual(player["heat"], 12)
        self.assertEqual(player["search_level"], 12)
        self.assertEqual(player["gamos"], "RAV4")

    async def test_hustle_cooldown_is_atomic_and_global_to_the_player(self) -> None:
        await self.database.ensure_player(-100123, "admin anonyme", -100123)

        result = await self.database.apply_hustle_result(
            -100123, -100123, 20, 100, 1, xp_gain=25
        )
        self.assertEqual(result, ("ok", 0, 600, 1, 100))
        player = await self.database.get_player(-100123, -100123)
        self.assertEqual(player["xp"], 25)
        self.assertGreater(player["last_hustle_time"], 0)
        self.assertEqual(player["last_hustle_cooldown"], 20)

        blocked = await self.database.apply_hustle_result(
            -100123, -100123, 45, -50, 1
        )
        self.assertEqual(blocked[0], "cooldown")
        self.assertGreater(blocked[1], 0)
        self.assertEqual(blocked[2:], (600, 1, 0))

    async def test_cash_rewards_always_grant_at_least_one_xp(self) -> None:
        await self.database.ensure_player(104, "minimum_xp")

        self.assertTrue(
            await self.database.apply_action_result(104, 10, 0, None, None)
        )
        player = await self.database.get_player(104)
        self.assertEqual(player["xp"], 1)

        await self.database.apply_hustle_result(104, 0, 15, 5, 0)
        player = await self.database.get_player(104)
        self.assertEqual(player["xp"], 2)

        jugg = await self.database.apply_jugg_result(104, 0, 15, 5, 0)
        self.assertEqual(jugg["xp_gain"], 1)
        player = await self.database.get_player(104)
        self.assertEqual(player["xp"], 3)

        await self.database.settle_casino_bet(104, 0, 1, 1)
        player = await self.database.get_player(104)
        self.assertEqual(player["xp"], 4)

    async def test_hustle_cooldowns_are_bounded_including_existing_profiles(self) -> None:
        await self.database.ensure_player(101, "rabza")
        async with aiosqlite.connect(self.database.path) as connection:
            await connection.execute(
                """
                UPDATE players
                SET last_hustle_time = ?, last_hustle_cooldown = 120
                WHERE user_id = ? AND chat_id = 0
                """,
                (time.time(), 101),
            )
            await connection.commit()

        blocked = await self.database.apply_hustle_result(101, 0, 120, 100, 0)
        self.assertEqual(blocked[0], "cooldown")
        self.assertGreater(blocked[1], 0)
        self.assertLessEqual(blocked[1], 30)

        async with aiosqlite.connect(self.database.path) as connection:
            await connection.execute(
                "UPDATE players SET last_hustle_time = 0 WHERE user_id = ?",
                (101,),
            )
            await connection.commit()
        await self.database.apply_hustle_result(101, 0, 120, 100, 0)
        player = await self.database.get_player(101)
        self.assertEqual(player["last_hustle_cooldown"], 30)

    async def test_jugg_result_is_atomic_and_uses_a_dedicated_cooldown(self) -> None:
        await self.database.ensure_player(102, "jugg")

        result = await self.database.apply_jugg_result(
            102, 0, 15, 1200, 1, xp_gain=100
        )

        self.assertEqual(
            result,
            {
                "status": "ok",
                "balance": 1700,
                "cash_delta": 1200,
                "heat_delta": 1,
                "heat": 1,
                "search_level": 1,
                "xp": 100,
                "xp_gain": 100,
                "previous_level": 1,
                "level": 2,
                "prison_seconds": 0,
                "cooldown_seconds": 15,
            },
        )
        player = await self.database.get_player(102)
        self.assertGreater(player["last_jugg_time"], 0)
        self.assertEqual(player["last_jugg_cooldown"], 15)
        self.assertEqual(player["last_hustle_time"], 0)
        self.assertEqual(player["xp"], 100)
        self.assertEqual(player["level"], 2)

        blocked = await self.database.apply_jugg_result(102, 0, 30, 15000, 5)
        self.assertEqual(blocked["status"], "cooldown")
        self.assertGreater(int(blocked["remaining"]), 0)
        self.assertLessEqual(int(blocked["remaining"]), 15)
        self.assertEqual(await self.database.get_player_balance(102), 1700)

    async def test_jugg_fine_is_capped_and_failure_can_set_short_prison(self) -> None:
        await self.database.ensure_player(103, "jugg_fail")
        fined = await self.database.apply_jugg_result(103, 0, 15, -700, 4)
        self.assertEqual(fined["status"], "ok")
        self.assertEqual(fined["cash_delta"], -500)
        self.assertEqual(fined["balance"], 0)

        async with aiosqlite.connect(self.database.path) as connection:
            await connection.execute(
                "UPDATE players SET last_jugg_time = 0 WHERE user_id = ?",
                (103,),
            )
            await connection.commit()
        jailed = await self.database.apply_jugg_result(103, 0, 30, 0, 8, 20)
        self.assertEqual(jailed["status"], "ok")
        self.assertEqual(jailed["prison_seconds"], 20)
        player = await self.database.get_player(103)
        self.assertGreater(player["in_prison_until"], time.time())

    async def test_schema_has_context_scoped_player_and_inventory_keys(self) -> None:
        connection = sqlite3.connect(self.database.path)
        try:
            player_columns = connection.execute(
                "PRAGMA table_xinfo(players)"
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
        self.assertTrue(
            {
                "balance",
                "bank",
                "search_level",
                "last_hustle_time",
                "last_hustle_cooldown",
            }
            <= {row[1] for row in player_columns}
        )

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

    async def test_bank_moves_all_and_amounts_atomically(self) -> None:
        await self.database.ensure_player(215, "banker")

        deposited = await self.database.move_bank_funds(215, 0, 200, True)
        self.assertEqual(
            deposited,
            {"status": "ok", "amount": 200, "cash": 300, "bank": 200},
        )
        deposited_all = await self.database.move_bank_funds(215, 0, None, True)
        self.assertEqual(
            deposited_all,
            {"status": "ok", "amount": 300, "cash": 0, "bank": 500},
        )
        withdrawn = await self.database.move_bank_funds(215, 0, None, False)
        self.assertEqual(
            withdrawn,
            {"status": "ok", "amount": 500, "cash": 500, "bank": 0},
        )

        self.assertEqual(
            (await self.database.move_bank_funds(215, 0, 0, True))["status"],
            "invalid",
        )
        self.assertEqual(
            (await self.database.move_bank_funds(215, 0, 501, True))["status"],
            "insufficient",
        )

    async def test_bank_is_protected_from_fines_prison_and_pvp(self) -> None:
        await self.database.ensure_player(216, "attaquant")
        await self.database.ensure_player(217, "victime")
        await self.database.move_bank_funds(216, 0, 300, True)
        await self.database.move_bank_funds(217, 0, 300, True)

        await self.database.apply_action_result(
            216, 0, 0, None, None, xp_gain=19000
        )
        await self.database.apply_action_result(216, -200, 0, None, None)
        self.assertEqual((await self.database.get_player(216))["bank"], 300)
        await self.database.apply_pvp_result(216, 217, "success", 0, None, 70)
        self.assertEqual((await self.database.get_player(216))["bank"], 300)
        self.assertEqual((await self.database.get_player(217))["bank"], 300)

    async def test_escape_failure_extends_sentence_and_success_releases_player(self) -> None:
        await self.database.ensure_player(212, "escape")
        await self.database.apply_action_result(
            212, 0, 0, None, time.time() + 60
        )

        result, remaining, search = await self.database.try_escape(
            212, 0, success=False
        )
        player = await self.database.get_player(212)
        self.assertEqual(result, "failed")
        self.assertGreaterEqual(remaining, 89)
        self.assertEqual(search, 5)
        self.assertEqual(player["search_level"], 5)

        result, _, _ = await self.database.try_escape(212, 0, success=True)
        player = await self.database.get_player(212)
        self.assertEqual(result, "escaped")
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
        self.assertEqual(player["cash"], 5300)

    async def test_shop_enforces_level_and_insufficient_cash(self) -> None:
        await self.database.ensure_player(404, "nouveau")
        self.assertEqual(await self.database.purchase_item(404, "obd_device"), "level")
        self.assertEqual(await self.database.purchase_item(404, "glock_19"), "cash")
        self.assertEqual(await self.database.purchase_item(404, "tech_fleece"), "ok")
        self.assertEqual(await self.database.purchase_item(404, "tech_fleece"), "ok")
        self.assertEqual(await self.database.purchase_item(404, "tech_fleece"), "cash")
        self.assertEqual(await self.database.purchase_item(404, "unknown"), "missing")

    async def test_weapon_and_protection_catalog_is_purchased_and_applies_pvp_effects(self) -> None:
        expected = {
            "glock_19": ("armes_poing", 1200),
            "beretta_m9": ("armes_poing", 950),
            "sw_357_magnum": ("armes_poing", 1600),
            "desert_eagle_50": ("armes_poing", 4500),
            "mini_uzi_9mm": ("armes_lourdes", 2800),
            "mac_10": ("armes_lourdes", 3200),
            "mp5k": ("armes_lourdes", 5000),
            "thompson_tommy_gun": ("armes_lourdes", 7500),
            "ak_47": ("armes_longues", 8500),
            "ar15_custom": ("armes_longues", 11000),
            "remington_870": ("armes_longues", 2200),
            "tactical_vest_level_3": ("protection", 1800),
            "fast_ballistic_helmet": ("protection", 1200),
        }
        catalog = {item.item_id: item for item in ITEMS}
        self.assertTrue(set(expected) <= catalog.keys())
        self.assertEqual(
            {item_id: (catalog[item_id].category, catalog[item_id].price)
             for item_id in expected},
            expected,
        )
        self.assertTrue(all(catalog[item_id].description for item_id in expected))

        await self.database.ensure_player(409, "gear")
        await self.database.apply_action_result(
            409, 20000, 0, None, None, xp_gain=19000
        )
        self.assertEqual(await self.database.purchase_item(409, "beretta_m9"), "ok")
        self.assertEqual(
            await self.database.purchase_item(409, "tactical_vest_level_3"), "ok"
        )
        effects = await self.database.get_item_effects(409)
        inventory = await self.database.get_inventory(409)
        self.assertTrue(effects["has_weapon"])
        self.assertEqual(effects["weapon_name"], "Beretta M9 9 mm — édition arcade")
        self.assertTrue(effects["has_armor"])
        self.assertEqual(
            effects["armor_name"], "Gilet de protection renforcé — niveau III+ (jeu)"
        )
        self.assertEqual(
            {item["item_id"] for item in inventory},
            {"beretta_m9", "tactical_vest_level_3"},
        )

    async def test_new_clothing_catalog_is_seeded_and_purchases_persist_descriptions(self) -> None:
        clothing_category = "vetements_accessoires"
        clothing_categories = {
            "vetements_accessoires",
            "tete_visage",
            "corps_vestes",
            "accessoires_sacoches",
            "pieds",
            "vetement",
            "accessoire",
            "luxe",
        }
        clothing_items = [
            item for item in ITEMS if item.category in clothing_categories
        ]
        new_item_ids = {
            "stealth_style_balaclava",
            "carhartt_paintball_beanie",
            "gold_ny_cap",
            "cartier_buffs_sunglasses",
            "carhartt_canvas_cap",
            "north_face_700_nuptse",
            "nike_tech_full_black_windbreaker",
            "lacoste_classic_tracksuit",
            "vegan_leather_jacket",
            "canadiens_retro_91_jersey",
            "montreal_hockey_scarf",
            "gucci_lv_crossbody_game",
            "gucci_gold_buckle_belt",
            "mile_end_silver_teardrop_chain",
            "apple_watch_game_replica",
            "north_face_crossbody_pouch",
            "silver_hoop_earrings",
            "tn_shark_blue_black",
            "air_force_1_pristine_white",
            "adidas_slides_socks",
            "winter_boots_montreal",
            "retro_basketball_sneakers",
            "cartier_buffs",
            "prada_sunglasses",
            "louis_vuitton_millionaire",
            "gucci_oversized",
            "moncler_maya",
            "dior_oblique_puffer",
            "balenciaga_tracksuit",
            "off_white_varsity",
            "palm_angels_track",
            "lv_discovery_bag",
            "gucci_gg_belt_bag",
            "prada_re_edition",
            "rolex_datejust",
            "audemars_piguet_royaloak",
            "cartier_tank",
            "patek_philippe_nautilus",
        }
        luxury_categories = {"vetement", "accessoire", "luxe"}
        luxury_items = [item for item in ITEMS if item.category in luxury_categories]

        self.assertEqual(SHOP_CATEGORIES[clothing_category], "Vêtements & Accessoires")
        self.assertTrue(new_item_ids <= {item.item_id for item in clothing_items})
        self.assertTrue(all(item.description for item in clothing_items))
        self.assertEqual(len({item.item_id for item in ITEMS}), len(ITEMS))
        self.assertEqual(
            {item.item_id for item in luxury_items},
            new_item_ids - {
                "stealth_style_balaclava",
                "carhartt_paintball_beanie",
                "gold_ny_cap",
                "cartier_buffs_sunglasses",
                "carhartt_canvas_cap",
                "north_face_700_nuptse",
                "nike_tech_full_black_windbreaker",
                "lacoste_classic_tracksuit",
                "vegan_leather_jacket",
                "canadiens_retro_91_jersey",
                "montreal_hockey_scarf",
                "gucci_lv_crossbody_game",
                "gucci_gold_buckle_belt",
                "mile_end_silver_teardrop_chain",
                "apple_watch_game_replica",
                "north_face_crossbody_pouch",
                "silver_hoop_earrings",
                "tn_shark_blue_black",
                "air_force_1_pristine_white",
                "adidas_slides_socks",
                "winter_boots_montreal",
                "retro_basketball_sneakers",
            },
        )
        self.assertTrue(all(item.description for item in luxury_items))
        self.assertEqual(SHOP_CATEGORIES["tete_visage"], "Vêtements & Accessoires — Tête & Visage")
        self.assertEqual(SHOP_CATEGORIES["corps_vestes"], "Vêtements & Accessoires — Corps & Vestes")
        self.assertEqual(SHOP_CATEGORIES["accessoires_sacoches"], "Vêtements & Accessoires — Accessoires & Sacoches")
        self.assertEqual(SHOP_CATEGORIES["pieds"], "Vêtements & Accessoires — Pieds")
        self.assertEqual(
            SHOP_CATEGORIES["vetement"], "Vêtements & Accessoires — Vêtements"
        )
        self.assertEqual(
            SHOP_CATEGORIES["accessoire"], "Vêtements & Accessoires — Accessoires"
        )
        self.assertEqual(SHOP_CATEGORIES["luxe"], "Vêtements & Accessoires — Luxe")

        await self.database.ensure_player(406, "fashion")
        self.assertEqual(await self.database.purchase_item(406, "montreal_toque"), "ok")
        self.assertEqual(await self.database.purchase_item(406, "adidas_slides_socks"), "ok")
        player = await self.database.get_player(406)
        inventory = await self.database.get_inventory(406)

        self.assertEqual(player["cash"], 325)
        self.assertEqual(len(inventory), 2)
        inventory_by_id = {item["item_id"]: item for item in inventory}
        self.assertEqual(
            inventory_by_id["montreal_toque"]["category"], clothing_category
        )
        self.assertEqual(
            inventory_by_id["adidas_slides_socks"]["category"], "pieds"
        )
        self.assertIn("tuque", inventory_by_id["montreal_toque"]["description"].casefold())

    async def test_luxury_shop_item_is_seeded_and_purchased_atomically(self) -> None:
        await self.database.ensure_player(407, "luxury")
        await self.database.apply_action_result(
            407, 5000, 0, None, None, xp_gain=19000
        )

        self.assertEqual(await self.database.purchase_item(407, "cartier_buffs"), "ok")

        player = await self.database.get_player(407)
        inventory = await self.database.get_inventory(407)
        self.assertEqual(player["cash"], 2000)
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["item_id"], "cartier_buffs")
        self.assertEqual(inventory[0]["category"], "accessoire")
        self.assertEqual(inventory[0]["description"], next(
            item.description for item in ITEMS if item.item_id == "cartier_buffs"
        ))

    async def test_vehicle_catalog_is_seeded_and_purchases_appear_in_inventory(self) -> None:
        vehicle_prices = {
            "audi_rs3_nardo": 45000,
            "bmw_m3_g80_competition": 68000,
            "mercedes_c63_amg_w204": 38000,
            "bmw_m4_f82": 55000,
            "porsche_911_turbo_s_992": 195000,
            "honda_civic_si_k24": 12000,
            "toyota_supra_mk4": 85000,
            "nissan_skyline_gtr_r34": 120000,
            "subaru_impreza_wrx_sti": 28000,
            "mercedes_amg_g63_brabus": 160000,
            "bmw_x5_m_competition": 82000,
            "range_rover_sport_svr": 95000,
        }
        vehicles = {item.item_id: item for item in ITEMS if item.category == "vehicules"}
        self.assertTrue(set(vehicle_prices) <= vehicles.keys())
        self.assertEqual(
            {item_id: vehicles[item_id].price for item_id in vehicle_prices},
            vehicle_prices,
        )
        self.assertTrue(all(vehicles[item_id].description for item_id in vehicle_prices))

        await self.database.ensure_player(408, "carlover")
        await self.database.apply_action_result(
            408, 200000, 0, None, None, xp_gain=19000
        )
        self.assertEqual(
            await self.database.purchase_item(408, "porsche_911_turbo_s_992"),
            "ok",
        )

        player = await self.database.get_player(408)
        inventory = await self.database.get_inventory(408)
        self.assertEqual(player["cash"], 5500)
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["item_id"], "porsche_911_turbo_s_992")
        self.assertEqual(inventory[0]["category"], "vehicules")
        self.assertIn("Icône sportive", inventory[0]["description"])

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

    async def test_cash_transfer_moves_money_atomically(self) -> None:
        await self.database.ensure_player(1101, "sender", -1001)
        await self.database.ensure_player(1102, "recipient", -1001)

        result = await self.database.transfer_cash(1101, 1102, 175, -1001)

        self.assertEqual(
            result,
            {
                "status": "ok",
                "sender_balance": 325,
                "recipient_balance": 675,
            },
        )
        self.assertEqual(
            (await self.database.get_player(1101, -1001))["cash"], 325
        )
        self.assertEqual(
            (await self.database.get_player(1102, -1001))["cash"], 675
        )

    async def test_cash_transfer_rejects_self_missing_and_insufficient_accounts(self) -> None:
        await self.database.ensure_player(1201, "sender", -1002)
        await self.database.ensure_player(1202, "recipient", -1002)

        self.assertEqual(
            await self.database.transfer_cash(1201, 1201, 1, -1002),
            {"status": "self"},
        )
        self.assertEqual(
            await self.database.transfer_cash(1201, 1299, 1, -1002),
            {"status": "recipient_missing"},
        )
        self.assertEqual(
            await self.database.transfer_cash(1201, 1202, 501, -1002),
            {"status": "insufficient", "sender_balance": 500},
        )
        self.assertEqual(
            (await self.database.get_player(1201, -1002))["cash"], 500
        )
        self.assertEqual(
            (await self.database.get_player(1202, -1002))["cash"], 500
        )

    async def test_cash_transfer_requires_positive_amount(self) -> None:
        with self.assertRaises(ValueError):
            await self.database.transfer_cash(1, 2, 0)

    async def test_concurrent_transfers_cannot_overdraw_sender(self) -> None:
        await self.database.ensure_player(1301, "sender", -1003)
        await self.database.ensure_player(1302, "first", -1003)
        await self.database.ensure_player(1303, "second", -1003)

        results = await asyncio.gather(
            self.database.transfer_cash(1301, 1302, 400, -1003),
            self.database.transfer_cash(1301, 1303, 400, -1003),
        )

        self.assertCountEqual(
            [result["status"] for result in results], ["ok", "insufficient"]
        )
        self.assertEqual(
            (await self.database.get_player(1301, -1003))["cash"], 100
        )
        recipient_balances = [
            (await self.database.get_player(user_id, -1003))["cash"]
            for user_id in (1302, 1303)
        ]
        self.assertCountEqual(recipient_balances, [900, 500])

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
        self.assertEqual(first["cash"], 3300)
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
        self.assertEqual(group_player["cash"], 3300)
        self.assertGreater(group_player["in_prison_until"], time.time())
        self.assertEqual(private_inventory, [])
        self.assertEqual([item["item_id"] for item in group_inventory], ["glock_19"])

    async def test_same_player_has_independent_profiles_in_two_groups(self) -> None:
        user_id = 1313
        first_group = -100111
        second_group = -100222
        await self.database.ensure_player(user_id, "multi_group", first_group)
        await self.database.ensure_player(user_id, "multi_group", second_group)

        await self.database.apply_action_result(
            user_id, 200, 0, None, None, xp_gain=80, chat_id=first_group
        )
        await self.database.apply_action_result(
            user_id, 50, 0, None, None, xp_gain=5, chat_id=second_group
        )
        await self.database.move_bank_funds(user_id, first_group, 100, True)
        self.assertEqual(
            await self.database.purchase_item(user_id, "tech_fleece", first_group),
            "ok",
        )

        first_hustle = await self.database.apply_hustle_result(
            user_id, first_group, 20, 10, 0, xp_gain=20
        )
        second_hustle = await self.database.apply_hustle_result(
            user_id, second_group, 20, 10, 0, xp_gain=1
        )
        self.assertEqual(first_hustle[0], "ok")
        self.assertEqual(second_hustle[0], "ok")
        await self.database.apply_action_result(
            user_id,
            0,
            0,
            None,
            time.time() + PRISON_DURATION_SECONDS,
            chat_id=first_group,
        )

        first = await self.database.get_player(user_id, first_group)
        second = await self.database.get_player(user_id, second_group)
        self.assertEqual(
            (first["cash"], first["bank"], first["xp"], first["level"]),
            (360, 100, 100, 2),
        )
        self.assertEqual(
            (second["cash"], second["bank"], second["xp"], second["level"]),
            (560, 0, 6, 1),
        )
        self.assertGreater(first["in_prison_until"], time.time())
        self.assertEqual(second["in_prison_until"], 0)
        self.assertEqual(
            [item["item_id"] for item in await self.database.get_inventory(
                user_id, first_group
            )],
            ["tech_fleece"],
        )
        self.assertEqual(await self.database.get_inventory(user_id, second_group), [])

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
        self.assertEqual(player["bank"], 0)
        self.assertEqual(player["last_jugg_time"], 0)
        self.assertEqual(player["last_jugg_cooldown"], 0)
        self.assertEqual(
            [item["item_id"] for item in await migrated.get_inventory(707, 707)],
            ["tech_fleece"],
        )
        await migrated.initialize()
        connection = sqlite3.connect(legacy_path)
        try:
            retained_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            legacy_cash = connection.execute(
                "SELECT cash FROM players_legacy WHERE user_id = 707"
            ).fetchone()[0]
            legacy_quantity = connection.execute(
                "SELECT quantity FROM inventory_legacy WHERE user_id = 707"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertTrue({"players_legacy", "inventory_legacy"} <= retained_tables)
        self.assertEqual(legacy_cash, 500)
        self.assertEqual(legacy_quantity, 1)

    async def test_initialize_repairs_non_composite_context_keys_without_data_loss(
        self,
    ) -> None:
        legacy_path = Path(self.temp_dir.name) / "old_context.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE players (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    username TEXT,
                    cash INTEGER NOT NULL DEFAULT 500,
                    heat INTEGER NOT NULL DEFAULT 0,
                    in_prison_until REAL NOT NULL DEFAULT 0,
                    gamos TEXT NOT NULL DEFAULT 'Aucun',
                    level INTEGER NOT NULL DEFAULT 1,
                    xp INTEGER NOT NULL DEFAULT 0
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
                "INSERT INTO players (user_id, chat_id, cash) VALUES (808, 9, 1200)"
            )
            connection.execute(
                "INSERT INTO items VALUES ('tech_fleece', 'Tech Fleece', 'clothes', 250, 1, 5, 0)"
            )
            connection.execute(
                "INSERT INTO inventory (user_id, item_id) VALUES (808, 'tech_fleece')"
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Database(str(legacy_path))
        await migrated.initialize()
        await migrated.ensure_player(808, "legacy", 10)
        first = await migrated.get_player(808, 9)
        second = await migrated.get_player(808, 10)
        self.assertEqual(first["cash"], 1200)
        self.assertEqual(second["cash"], 500)
        self.assertEqual(
            [item["item_id"] for item in await migrated.get_inventory(808, 9)],
            ["tech_fleece"],
        )
        self.assertEqual(await migrated.get_inventory(808, 10), [])

        connection = sqlite3.connect(legacy_path)
        try:
            primary_key = [
                row[1]
                for row in sorted(
                    connection.execute("PRAGMA table_info(players)").fetchall(),
                    key=lambda row: row[5],
                )
                if row[5]
            ]
            retained_cash = connection.execute(
                "SELECT cash FROM players_legacy WHERE user_id = 808"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(primary_key, ["chat_id", "user_id"])
        self.assertEqual(retained_cash, 1200)

    async def test_initialize_creates_missing_database_parent_directory(self) -> None:
        database_path = Path(self.temp_dir.name) / "nested" / "state.sqlite3"
        database = Database(str(database_path))
        await database.initialize()
        self.assertTrue(database_path.is_file())

    async def test_initialize_alters_existing_context_players_to_add_bank(self) -> None:
        existing_path = Path(self.temp_dir.name) / "existing.sqlite3"
        connection = sqlite3.connect(existing_path)
        try:
            connection.execute(
                """
                CREATE TABLE players (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    cash INTEGER NOT NULL DEFAULT 500,
                    heat INTEGER NOT NULL DEFAULT 0,
                    in_prison_until REAL NOT NULL DEFAULT 0,
                    gamos TEXT NOT NULL DEFAULT 'Aucun',
                    level INTEGER NOT NULL DEFAULT 1,
                    xp INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            connection.execute(
                "INSERT INTO players (chat_id, user_id, cash) VALUES (9, 707, 1200)"
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Database(str(existing_path))
        await migrated.initialize()
        player = await migrated.get_player(707, 9)
        self.assertEqual(player["cash"], 1200)
        self.assertEqual(player["bank"], 0)


if __name__ == "__main__":
    unittest.main()
