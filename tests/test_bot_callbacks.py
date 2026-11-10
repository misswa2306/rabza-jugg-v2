from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Bot, Update, User
from telegram.error import BadRequest
from telegram.ext import CommandHandler

from bot import (
    HUSTLES,
    blackjack_hand_value,
    braquer,
    casino,
    edit_callback_message,
    help_command,
    handle_game_action,
    player_context,
    missions,
    missions_command,
    mission_command,
    casino_command,
    profil,
    give_command,
    main as bot_main,
    mission_colis,
    mission_phishing,
    mission_catalyseur,
    mission_vapote,
    mission_depanneur,
    mission_sneakers,
    prison_remaining_seconds,
    shop_callback,
)
from config import validate_config
from database import Database


class HelpCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_aliases_await_the_existing_handlers(self) -> None:
        update = SimpleNamespace()
        context = SimpleNamespace()

        with (
            patch("bot.casino", new_callable=AsyncMock) as casino_handler,
            patch("bot.missions", new_callable=AsyncMock) as missions_handler,
        ):
            await casino_command(update, context)
            casino_handler.assert_awaited_once_with(update, context)

            await missions_command(update, context)
            missions_handler.assert_awaited_once_with(update, context)

            missions_handler.reset_mock()
            await mission_command(update, context)
            missions_handler.assert_awaited_once_with(update, context)

    async def test_help_lists_all_registered_command_families(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        await help_command(SimpleNamespace(effective_message=message), None)

        text = message.reply_text.await_args.args[0]
        for command in (
            "/start",
            "/profil",
            "/give",
            "/casino",
            "/missions",
            "/mission",
            "/mission_colis",
            "/mission_phishing",
            "/mission_catalyseur",
            "/mission_vapote",
            "/mission_depanneur",
            "/mission_sneakers",
            "/help",
            "/jugg",
            "/braquer",
            "/shop",
            "/prison",
            "/evade",
            "/inventory",
            "/top",
        ):
            with self.subTest(command=command):
                self.assertIn(command, text)

    def test_command_handlers_accept_plain_and_bot_mentioned_commands(self) -> None:
        bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
        bot._bot_user = User(
            id=123456,
            is_bot=True,
            first_name="Rabza",
            username="Rabza_Jugg_V2_bot",
        )
        command_handlers = [
            CommandHandler(command, help_command)
            for command in (
                "start",
                "profil",
                "give",
                "casino",
                "missions",
                "mission",
                "mission_colis",
                "mission_phishing",
                "mission_catalyseur",
                "mission_vapote",
                "mission_depanneur",
                "mission_sneakers",
                "help",
                "jugg",
                "braquer",
                "shop",
                "prison",
                "inventory",
                "top",
            )
        ]

        for command_handler in command_handlers:
            for mention in ("", "@Rabza_Jugg_V2_bot"):
                with self.subTest(command=command_handler.commands, mention=mention):
                    text = f"/{next(iter(command_handler.commands))}{mention}"
                    update = Update.de_json(
                        {
                            "update_id": 1,
                            "message": {
                                "message_id": 1,
                                "date": int(datetime.now(timezone.utc).timestamp()),
                                "chat": {
                                    "id": -100123456789,
                                    "type": "supergroup",
                                    "title": "Groupe test",
                                },
                                "text": text,
                                "entities": [
                                    {
                                        "type": "bot_command",
                                        "offset": 0,
                                        "length": len(text),
                                    }
                                ],
                            },
                        },
                        bot,
                    )

                    self.assertIsNotNone(command_handler.check_update(update))

    def test_main_registers_give_command(self) -> None:
        application = MagicMock()
        builder = MagicMock()
        builder.token.return_value = builder
        builder.post_init.return_value = builder
        builder.build.return_value = application

        with (
            patch("bot.ApplicationBuilder", return_value=builder),
            patch("bot.validate_config"),
        ):
            bot_main()

        handlers = [call.args[0] for call in application.add_handler.call_args_list]
        self.assertTrue(
            any(
                isinstance(handler, CommandHandler) and "give" in handler.commands
                for handler in handlers
            )
        )


class GiveCommandTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, database: AsyncMock, args: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=args,
        )

    def _update(self, message: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(
            effective_user=SimpleNamespace(
                id=101, username="sender", full_name="Sender Name", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=message,
        )

    async def test_give_by_username_transfers_cash_and_confirms_balance(self) -> None:
        database = AsyncMock()
        database.get_player_by_username.return_value = {
            "user_id": 202,
            "username": "recipient",
        }
        database.transfer_cash.return_value = {
            "status": "ok",
            "sender_balance": 425,
            "recipient_balance": 575,
        }
        message = SimpleNamespace(
            reply_to_message=None, reply_text=AsyncMock()
        )

        await give_command(
            self._update(message), self._context(database, ["@recipient", "75"])
        )

        database.transfer_cash.assert_awaited_once_with(101, 202, 75, -1001)
        message.reply_text.assert_awaited_once_with(
            "💸 Virement effectué ! Tu as envoyé 75$ à recipient.\n"
            "💰 Ton nouveau solde: 425$",
            parse_mode="HTML",
        )

    async def test_give_by_reply_resolves_recipient_without_username_lookup(self) -> None:
        database = AsyncMock()
        database.transfer_cash.return_value = {
            "status": "ok",
            "sender_balance": 400,
            "recipient_balance": 600,
        }
        replied_message = SimpleNamespace(
            from_user=SimpleNamespace(
                id=202, full_name="Reply Recipient", is_bot=False
            ),
            sender_chat=None,
        )
        message = SimpleNamespace(
            reply_to_message=replied_message, reply_text=AsyncMock()
        )

        await give_command(self._update(message), self._context(database, ["100"]))

        database.get_player_by_username.assert_not_awaited()
        database.transfer_cash.assert_awaited_once_with(101, 202, 100, -1001)
        self.assertIn("Reply Recipient", message.reply_text.await_args.args[0])

    async def test_give_rejects_invalid_amount_and_self_transfer(self) -> None:
        database = AsyncMock()
        reply = SimpleNamespace(
            from_user=SimpleNamespace(id=101, full_name="Sender Name", is_bot=False),
            sender_chat=None,
        )
        message = SimpleNamespace(
            reply_to_message=reply, reply_text=AsyncMock()
        )

        await give_command(self._update(message), self._context(database, ["0"]))
        message.reply_text.assert_awaited_once_with(
            "❌ Le montant doit être supérieur à 0$."
        )
        database.transfer_cash.assert_not_awaited()

        message.reply_text.reset_mock()
        await give_command(self._update(message), self._context(database, ["50"]))
        message.reply_text.assert_awaited_once_with(
            "❌ Tu ne peux pas t'envoyer du cash à toi-même."
        )
        database.transfer_cash.assert_not_awaited()


class ShopCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_clothing_category_shows_item_descriptions_and_buy_buttons(self) -> None:
        database = AsyncMock()
        database.get_player.return_value = {"level": 1, "cash": 500}
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="shop:101:vetements_accessoires")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101,
                username="joueur",
                full_name="Joueur",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=SimpleNamespace(sender_chat=None),
        )

        await shop_callback(update, context)

        query.edit_message_text.assert_awaited_once()
        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Vêtements & Accessoires", text)
        self.assertIn("Cagoule Nike Tech noire", text)
        self.assertIn("Accessoire de sport noir", text)
        self.assertIn("Montre Rolex Submariner", text)
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("buy:101:montreal_toque", callback_data)

    async def test_accessory_subcategory_shows_new_items_and_purchase_callback(self) -> None:
        database = AsyncMock()
        database.get_player.return_value = {"level": 1, "cash": 500}
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="shop:101:tete_visage")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101,
                username="joueur",
                full_name="Joueur",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=SimpleNamespace(sender_chat=None),
        )

        await shop_callback(update, context)

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Vêtements & Accessoires — Tête & Visage", text)
        self.assertIn("Cagoule Stealth noire (cosmétique)", text)
        self.assertIn("Casquette NY dorée", text)
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("buy:101:stealth_style_balaclava", callback_data)

    async def test_luxury_category_lists_items_with_purchase_buttons(self) -> None:
        database = AsyncMock()
        database.get_player.return_value = {"level": 60, "cash": 50000}
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="shop:101:luxe")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101,
                username="joueur",
                full_name="Joueur",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=SimpleNamespace(sender_chat=None),
        )

        await shop_callback(update, context)

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Vêtements & Accessoires — Luxe", text)
        self.assertIn("Rolex Datejust 41 mm acier et or — 14500$", text)
        self.assertIn("Le Saint Graal horloger", text)
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("buy:101:patek_philippe_nautilus", callback_data)

    async def test_vehicle_category_lists_new_cars_and_purchase_buttons(self) -> None:
        database = AsyncMock()
        database.get_player.return_value = {"level": 1, "cash": 250000}
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="shop:101:vehicules")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101,
                username="joueur",
                full_name="Joueur",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=SimpleNamespace(sender_chat=None),
        )

        await shop_callback(update, context)

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Véhicules", text)
        self.assertIn("Audi RS3 Sportback (Nardo Grey) — 45000$", text)
        self.assertIn("Porsche 911 Turbo S (992) — 195000$", text)
        self.assertIn("Icône sportive de prestige", text)
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("buy:101:porsche_911_turbo_s_992", callback_data)

    async def test_weapon_and_protection_categories_are_navigable(self) -> None:
        database = AsyncMock()
        database.get_player.return_value = {"level": 1, "cash": 25000}
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="shop:101:armes_poing")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101,
                username="joueur",
                full_name="Joueur",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=SimpleNamespace(sender_chat=None),
        )

        await shop_callback(update, context)

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Armes — Poing (jeu)", text)
        self.assertIn("Glock 19 Gen 5 — édition arcade — 1200$", text)
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("buy:101:beretta_m9", callback_data)

        query.data = "shop:101:protection"
        await shop_callback(update, context)

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Protection & Défense (jeu)", text)
        self.assertIn("Gilet de protection renforcé — niveau III+ (jeu)", text)
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("buy:101:tactical_vest_level_3", callback_data)


class EditCallbackMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_message_is_treated_as_noop(self) -> None:
        query = AsyncMock()
        query.edit_message_text.side_effect = BadRequest("Message is not modified")

        await edit_callback_message(query, "Même texte")

        query.edit_message_text.assert_awaited_once_with(
            "Même texte", reply_markup=None
        )

    async def test_other_telegram_edit_errors_are_propagated(self) -> None:
        query = AsyncMock()
        error = BadRequest("Message to edit not found")
        query.edit_message_text.side_effect = error

        with self.assertRaises(BadRequest) as raised:
            await edit_callback_message(query, "Résultat")

        self.assertIs(raised.exception, error)


class GroupPlayerContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_users_in_same_group_get_distinct_accounts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "group-test.sqlite3"))
            await database.initialize()
            context = SimpleNamespace(
                application=SimpleNamespace(bot_data={"database": database})
            )
            chat = SimpleNamespace(id=-100123)
            message = object()
            first_user = SimpleNamespace(
                id=101, username="first", full_name="First", is_bot=False
            )
            second_user = SimpleNamespace(
                id=202, username="second", full_name="Second", is_bot=False
            )

            first = await player_context(
                SimpleNamespace(
                    effective_user=first_user,
                    effective_chat=chat,
                    effective_message=message,
                ),
                context,
            )
            second = await player_context(
                SimpleNamespace(
                    effective_user=second_user,
                    effective_chat=chat,
                    effective_message=message,
                ),
                context,
            )

            self.assertEqual(first[0]["id"], 101)
            self.assertEqual(second[0]["id"], 202)
            self.assertEqual(first[1].id, second[1].id)
            first_player = await database.get_player(first[0]["id"], chat.id)
            second_player = await database.get_player(second[0]["id"], chat.id)
            self.assertEqual(first_player["user_id"], 101)
            self.assertEqual(second_player["user_id"], 202)
            self.assertNotEqual(first_player["user_id"], second_player["user_id"])

    async def test_non_chat_or_bot_updates_are_not_player_commands(self) -> None:
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": AsyncMock()})
        )
        missing_chat = await player_context(
            SimpleNamespace(
                effective_user=SimpleNamespace(
                    id=1, username="user", full_name="User", is_bot=False
                ),
                effective_chat=None,
                effective_message=object(),
            ),
            context,
        )
        bot_user = await player_context(
            SimpleNamespace(
                effective_user=SimpleNamespace(
                    id=2, username="bot", full_name="Bot", is_bot=True
                ),
                effective_chat=SimpleNamespace(id=-1),
                effective_message=object(),
            ),
            context,
        )
        self.assertIsNone(missing_chat)
        self.assertIsNone(bot_user)

    async def test_anonymous_admin_uses_sender_chat_id_and_title(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "anonymous-admin.sqlite3"))
            await database.initialize()
            context = SimpleNamespace(
                application=SimpleNamespace(bot_data={"database": database})
            )
            sender_chat = SimpleNamespace(
                id=-100456,
                title="Admins de Montréal",
            )
            message = SimpleNamespace(
                sender_chat=sender_chat,
                reply_text=AsyncMock(),
            )
            update = SimpleNamespace(
                effective_user=None,
                effective_chat=SimpleNamespace(id=-100456),
                effective_message=message,
            )

            await profil(update, context)

            player = await database.get_player(sender_chat.id, -100456)
            self.assertEqual(player["user_id"], sender_chat.id)
            self.assertEqual(player["username"], sender_chat.title)
            message.reply_text.assert_awaited_once()
            self.assertIn(
                "Profil de Admins de Montréal",
                message.reply_text.await_args.args[0],
            )

    async def test_anonymous_admin_uses_sender_chat_when_telegram_sets_bot_user(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "anonymous-proxy.sqlite3"))
            await database.initialize()
            context = SimpleNamespace(
                application=SimpleNamespace(bot_data={"database": database})
            )
            sender_chat = SimpleNamespace(id=-100789, title="Admin anonyme")
            update = SimpleNamespace(
                effective_user=SimpleNamespace(
                    id=1087968824,
                    username="GroupAnonymousBot",
                    full_name="GroupAnonymousBot",
                    is_bot=True,
                ),
                effective_chat=SimpleNamespace(id=-100789),
                effective_message=SimpleNamespace(sender_chat=sender_chat),
            )

            actor = await player_context(update, context)

            self.assertEqual(actor[0]["id"], -100789)
            self.assertEqual(
                await database.get_player(-100789, -100789),
                {
                    "user_id": -100789,
                    "username": "Admin anonyme",
                    "cash": 500,
                    "balance": 500,
                    "heat": 0,
                    "in_prison_until": 0,
                    "gamos": "Aucun",
                    "level": 1,
                    "xp": 0,
                    "search_level": 0,
                    "last_hustle_time": 0,
                    "last_hustle_cooldown": 0,
                },
            )


class PrisonTimerTests(unittest.TestCase):
    def test_remaining_time_counts_down_and_expires(self) -> None:
        from datetime import datetime, timedelta, timezone

        self.assertEqual(prison_remaining_seconds({"in_prison_until": None}), 0)
        future = datetime.now(timezone.utc) + timedelta(seconds=44)
        remaining = prison_remaining_seconds(
            {"in_prison_until": future.isoformat()}
        )
        self.assertGreaterEqual(remaining, 1)
        self.assertLessEqual(remaining, 44)

        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(
            prison_remaining_seconds({"in_prison_until": past.isoformat()}), 0
        )

    def test_unix_deadline_reports_exactly_45_seconds(self) -> None:
        with patch("bot.time.time", return_value=1_700_000_000):
            self.assertEqual(
                prison_remaining_seconds(
                    {"in_prison_until": 1_700_000_045}
                ),
                45,
            )


class CasinoTests(unittest.IsolatedAsyncioTestCase):
    async def _create_context(self, chat_id: int = -100456):
        temp_dir = TemporaryDirectory()
        database = Database(str(Path(temp_dir.name) / "casino.sqlite3"))
        await database.initialize()
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=[],
        )
        return temp_dir, database, context, chat_id

    async def test_casino_menu_is_displayed_without_arguments(self) -> None:
        temp_dir, _, context, chat_id = await self._create_context()
        self.addCleanup(temp_dir.cleanup)
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=123, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
            effective_message=message,
        )

        await casino(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn("/casino blackjack <mise>", text)
        self.assertIn("/casino roulette", text)

    async def test_anonymous_admin_can_play_roulette_with_negative_database_id(
        self,
    ) -> None:
        temp_dir, database, context, chat_id = await self._create_context()
        self.addCleanup(temp_dir.cleanup)
        context.args = ["roulette", "rouge", "100"]
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            sender_chat=SimpleNamespace(id=chat_id, title="Admin de Montréal"),
        )
        update = SimpleNamespace(
            effective_user=None,
            effective_chat=SimpleNamespace(
                id=chat_id, title="Admin de Montréal", type="supergroup"
            ),
            effective_message=message,
        )

        with patch("bot.random.randint", return_value=1):
            await casino(update, context)

        self.assertEqual(await database.get_player_balance(chat_id, chat_id), 600)
        text = message.reply_text.await_args.args[0]
        self.assertIn("Roulette pour Admin de Montréal", text)
        self.assertIn("Nouveau solde: 600$", text)

    async def test_blackjack_natural_pays_one_and_a_half_times_the_stake(
        self,
    ) -> None:
        temp_dir, database, context, chat_id = await self._create_context()
        self.addCleanup(temp_dir.cleanup)
        context.args = ["blackjack", "101"]
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=44, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
            effective_message=message,
        )

        with patch("bot.deal_blackjack", return_value=(["A", "K"], ["9", "7"])):
            await casino(update, context)

        self.assertEqual(await database.get_player_balance(44, chat_id), 651)
        self.assertIn(
            "Blackjack! Tu gagnes 151$ net",
            message.reply_text.await_args.args[0],
        )

    async def test_exact_roulette_number_pays_thirty_five_times_the_stake(self) -> None:
        temp_dir, database, context, chat_id = await self._create_context()
        self.addCleanup(temp_dir.cleanup)
        context.args = ["roulette", "17", "20"]
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=66, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
            effective_message=message,
        )

        with patch("bot.random.randint", return_value=17):
            await casino(update, context)

        self.assertEqual(await database.get_player_balance(66, chat_id), 1200)
        self.assertIn("Tu gagnes 700$ net", message.reply_text.await_args.args[0])

    async def test_casino_rejects_insufficient_and_invalid_bets(self) -> None:
        temp_dir, database, context, chat_id = await self._create_context()
        self.addCleanup(temp_dir.cleanup)
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=55, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
            effective_message=message,
        )

        context.args = ["blackjack", "501"]
        await casino(update, context)
        self.assertIn("dépasse ton solde", message.reply_text.await_args.args[0])
        self.assertEqual(await database.get_player_balance(55, chat_id), 500)

        message.reply_text.reset_mock()
        context.args = ["roulette", "vert", "100"]
        await casino(update, context)
        self.assertIn("Choix invalide", message.reply_text.await_args.args[0])
        self.assertEqual(await database.get_player_balance(55, chat_id), 500)


class MissionTests(unittest.IsolatedAsyncioTestCase):
    async def _setup_mission(self, args: list[str]):
        temp_dir = TemporaryDirectory()
        database = Database(str(Path(temp_dir.name) / "missions.sqlite3"))
        await database.initialize()
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=args,
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        chat = SimpleNamespace(id=-100777, type="supergroup")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=777,
                username="mission_player",
                full_name="Mission Player",
                is_bot=False,
            ),
            effective_chat=chat,
            effective_message=message,
        )
        await database.ensure_player(777, "mission_player", chat.id)
        return temp_dir, database, context, update, message, chat

    async def test_mission_list_displays_all_missions_and_rewards(self) -> None:
        temp_dir, _, context, update, message, _ = await self._setup_mission([])
        self.addCleanup(temp_dir.cleanup)

        await missions(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn("Vol de civic rouillée", text)
        self.assertIn("Carjacking d'une Audi", text)
        self.assertIn("niv. 10", text)
        self.assertIn("🔒 Verrouillée", text)
        self.assertIn("✅ Disponible", text)
        self.assertIn("Réussite: 25% | Gain: 7500$ | XP: 600", text)

    async def test_successful_mission_updates_cash_and_xp(self) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_mission(
            ["civic"]
        )
        self.addCleanup(temp_dir.cleanup)

        with patch("bot.random.random", return_value=0.0):
            await missions(update, context)

        player = await database.get_player(777, chat.id)
        self.assertEqual(player["cash"], 600)
        self.assertEqual(player["xp"], 20)
        self.assertEqual(player["level"], 1)
        self.assertIn("Mission réussie", message.reply_text.await_args.args[0])

    async def test_locked_mission_does_not_change_player_balance(self) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_mission(
            ["audi"]
        )
        self.addCleanup(temp_dir.cleanup)

        await missions(update, context)

        player = await database.get_player(777, chat.id)
        self.assertEqual(player["cash"], 500)
        self.assertIn("niveau 3 requis", message.reply_text.await_args.args[0])

    async def test_failed_mission_can_apply_fine_and_prison(self) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_mission(
            ["civic"]
        )
        self.addCleanup(temp_dir.cleanup)

        with (
            patch("bot.random.random", side_effect=[0.99, 0.0]),
            patch("bot.random.randint", return_value=50),
        ):
            await missions(update, context)

        player = await database.get_player(777, chat.id)
        self.assertEqual(player["cash"], 450)
        self.assertGreater(player["in_prison_until"], 0)
        self.assertIn("Mission échouée", message.reply_text.await_args.args[0])
        self.assertIn("Amende: -50$", message.reply_text.await_args.args[0])


class HustleTests(unittest.IsolatedAsyncioTestCase):
    async def _setup_hustle(self, args: list[str], register: bool = True):
        temp_dir = TemporaryDirectory()
        database = Database(str(Path(temp_dir.name) / "hustles.sqlite3"))
        await database.initialize()
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=args,
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        chat = SimpleNamespace(id=-100888, type="supergroup")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=888,
                username="hustle_player",
                full_name="Hustle Player",
                is_bot=False,
            ),
            effective_chat=chat,
            effective_message=message,
        )
        if register:
            await database.ensure_player(888, "hustle_player", chat.id)
        return temp_dir, database, context, update, message, chat

    async def test_dedicated_hustle_commands_dispatch_the_matching_mission(self) -> None:
            update = SimpleNamespace()
            context = SimpleNamespace()
            commands = (
                mission_colis,
                mission_phishing,
                mission_catalyseur,
                mission_vapote,
                mission_depanneur,
                mission_sneakers,
            )
            with patch("bot.hustle_command", new_callable=AsyncMock) as hustle_command:
                for command, hustle in zip(commands, HUSTLES):
                    await command(update, context)
                    hustle_command.assert_awaited_with(update, context, hustle)
                    hustle_command.reset_mock()

    async def test_mission_menu_lists_hustles_and_cooldowns(self) -> None:
        temp_dir, _, context, update, message, _ = await self._setup_hustle([])
        self.addCleanup(temp_dir.cleanup)

        await missions(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn("/mission_colis", text)
        self.assertIn("/mission_phishing", text)
        self.assertIn("Cooldown: 20s", text)

    async def test_hustle_pays_fixed_reward_and_enforces_global_cooldown(self) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_hustle(
            ["colis"]
        )
        self.addCleanup(temp_dir.cleanup)

        with (
            patch("bot.random.random", return_value=0.0),
            patch("bot.random.randint", side_effect=AssertionError("cash must be fixed")),
        ):
            await missions(update, context)
            self.assertEqual(await database.get_player_balance(888, chat.id), 615)

            message.reply_text.reset_mock()
            context.args = ["vapote"]
            await missions(update, context)

        self.assertRegex(
            message.reply_text.await_args.args[0],
            r"Réessaie dans \d+ secondes\.",
        )
        self.assertEqual(await database.get_player_balance(888, chat.id), 615)

    async def test_failed_hustle_can_lose_cash_and_increase_heat(self) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_hustle(
            ["depanneur"]
        )
        self.addCleanup(temp_dir.cleanup)

        with (
            patch("bot.random.random", side_effect=[0.99, 0.0, 0.0]),
            patch("bot.random.randint", side_effect=AssertionError("cash must be fixed")),
        ):
            await missions(update, context)

        player = await database.get_player(888, chat.id)
        self.assertEqual(player["cash"], 450)
        self.assertEqual(player["heat"], 1)
        text, = message.reply_text.await_args.args
        self.assertIn("Cash: -50$ | ✨ XP: +0", text)
        self.assertIn("Heat: +1 (1/100)", text)
        self.assertIn("Recherche: +1 (1/100)", text)
        self.assertEqual(player["search_level"], 1)
        self.assertEqual(message.reply_text.await_args.kwargs["parse_mode"], "HTML")

    async def test_successful_hustle_uses_scenario_and_reports_all_game_deltas(
        self,
    ) -> None:
        temp_dir, _, context, update, message, _ = await self._setup_hustle(
            ["phishing"]
        )
        self.addCleanup(temp_dir.cleanup)
        scenario = HUSTLES[1].success_message.format(
            cash=HUSTLES[1].success_reward
        )

        with (
            patch("bot.random.random", return_value=0.0),
            patch("bot.random.randint", side_effect=AssertionError("cash must be fixed")),
        ):
            await missions(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn(scenario, text)
        self.assertIn("Cash: +275$ | ✨ XP: +0", text)
        self.assertIn("Heat: +0 (0/100)", text)
        self.assertIn("Recherche: +0 (0/100)", text)
        self.assertIn("Prochain hustle dans 25s", text)

    async def test_hustle_requires_existing_profile_and_supports_anonymous_admin(
        self,
    ) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_hustle(
            []
        )
        self.addCleanup(temp_dir.cleanup)

        await database.ensure_player(chat.id, "Admin anonyme", chat.id)
        message.sender_chat = SimpleNamespace(id=chat.id, title="Admin anonyme")
        update.effective_user = None
        context.args = []
        with (
            patch("bot.random.random", return_value=0.0),
            patch("bot.random.randint", side_effect=AssertionError("cash must be fixed")),
        ):
            await mission_colis(update, context)

        player = await database.get_player(chat.id, chat.id)
        self.assertEqual(player["cash"], 615)
        self.assertIn("Défi de livraison express", message.reply_text.await_args.args[0])

    async def test_hustle_handlers_require_start_for_unregistered_player(self) -> None:
        temp_dir, _, context, update, message, _ = await self._setup_hustle(
            [], register=False
        )
        self.addCleanup(temp_dir.cleanup)

        await mission_colis(update, context)

        self.assertIn("/start", message.reply_text.await_args.args[0])

    async def test_hustle_definitions_match_requested_rates_rewards_and_cooldowns(
        self,
    ) -> None:
        from bot import HUSTLES

        self.assertEqual(
            [
                (
                    hustle.mission_id,
                    hustle.success_chance,
                    hustle.success_reward,
                    hustle.failure_penalty,
                    hustle.cooldown_seconds,
                )
                for hustle in HUSTLES
            ],
            [
                ("colis", 0.85, 115, 25, 20),
                ("phishing", 0.70, 275, 30, 25),
                ("catalyseur", 0.60, 475, 35, 30),
                ("vapote", 0.90, 65, 20, 15),
                ("depanneur", 0.50, 900, 50, 30),
                ("sneakers", 0.75, 400, 25, 25),
            ],
        )
        self.assertTrue(
            all(15 <= hustle.cooldown_seconds <= 30 for hustle in HUSTLES)
        )
        self.assertTrue(all(hustle.success_message for hustle in HUSTLES))


class BlackjackHandTests(unittest.TestCase):
    def test_ace_counts_as_eleven_or_one_to_avoid_busting(self) -> None:
        self.assertEqual(blackjack_hand_value(["A", "K"]), 21)
        self.assertEqual(blackjack_hand_value(["A", "9", "5"]), 15)


class BraquerChatTypeTests(unittest.IsolatedAsyncioTestCase):
    async def _invoke_braquer(self, chat_type: str) -> tuple[AsyncMock, AsyncMock]:
        database = AsyncMock()
        database.get_player.return_value = {
            "user_id": 101,
            "username": "joueur",
            "level": 1,
            "in_prison_until": 0,
        }
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=["@cible"],
        )
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=101, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1001, type=chat_type),
            effective_message=message,
        )

        await braquer(update, context)
        return database, message.reply_text

    async def test_braquer_is_rejected_in_private_chat(self) -> None:
        database, reply_text = await self._invoke_braquer("private")
        reply_text.assert_awaited_once_with(
            "❌ Les braquages entre joueurs se font uniquement dans les groupes !"
        )
        database.get_player.assert_not_awaited()

    async def test_braquer_is_allowed_to_reach_game_rules_in_groups(self) -> None:
        for chat_type in ("group", "supergroup"):
            with self.subTest(chat_type=chat_type):
                database, reply_text = await self._invoke_braquer(chat_type)
                database.get_player.assert_awaited_once_with(101, -1001)
                reply_text.assert_awaited_once_with(
                    "Le défi PVP est verrouillé jusqu'au niveau 20."
                )


class JuggCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_anonymous_owner_callback_uses_group_id(self) -> None:
        database = AsyncMock()
        database.get_player.return_value = {
            "user_id": -100303,
            "username": "Groupe Montréal",
            "level": 1,
            "in_prison_until": 0,
        }
        database.get_item_effects.return_value = {
            "has_obd": False,
            "success_bonus": 0.0,
            "xp_bonus": 0,
        }
        database.apply_action_result.return_value = True
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="game:-100303:bixi")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=303, username="clicker", full_name="Clicker", is_bot=False
            ),
            effective_chat=SimpleNamespace(
                id=-100303, title="Groupe Montréal", type="supergroup"
            ),
            effective_message=object(),
        )

        with (
            patch("bot.asyncio.sleep", new_callable=AsyncMock),
            patch("game.random.random", return_value=0.1),
        ):
            await handle_game_action(update, context)

        database.ensure_player.assert_awaited_once_with(
            -100303, "Groupe Montréal", -100303
        )
        database.apply_action_result.assert_awaited_once_with(
            -100303, 90, 8, None, None, 30, chat_id=-100303
        )

    async def test_callback_passes_equipment_bonus_to_random_resolver(self) -> None:
        database = AsyncMock()
        database.get_player.return_value = {
            "user_id": 303,
            "username": "jugg_player",
            "level": 1,
            "in_prison_until": 0,
        }
        database.get_item_effects.return_value = {
            "has_obd": False,
            "success_bonus": 0.05,
            "xp_bonus": 0,
        }
        database.apply_action_result.return_value = True
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="game:303:bixi")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=303, username="jugg_player", full_name="Jugg Player", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-100303, type="supergroup"),
            effective_message=object(),
        )

        with (
            patch("bot.asyncio.sleep", new_callable=AsyncMock) as sleep,
            patch("game.random.random", return_value=0.71),
        ):
            await handle_game_action(update, context)

        sleep.assert_awaited_once_with(2)
        query.answer.assert_awaited_once()
        database.get_item_effects.assert_awaited_once_with(303, -100303)
        database.apply_action_result.assert_awaited_once_with(
            303, 90, 8, None, None, 30, chat_id=-100303
        )
        self.assertIn("réussi", query.edit_message_text.await_args.args[0])


class ConfigurationTests(unittest.TestCase):
    def test_missing_botfather_token_fails_with_clear_configuration_error(self) -> None:
        with patch("config.BOT_TOKEN", ""):
            with self.assertRaisesRegex(
                RuntimeError, "BOT_TOKEN est absente"
            ):
                validate_config()


if __name__ == "__main__":
    unittest.main()
