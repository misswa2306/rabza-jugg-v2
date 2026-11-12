from datetime import datetime, timezone
import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Bot, Update, User
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler

from bot import (
    HUSTLES,
    blackjack_hand_value,
    braquer,
    bank,
    casino,
    admin,
    admin_callback,
    admin_reset,
    deposit,
    devmode,
    drive,
    edit_callback_message,
    error_handler,
    give_cash,
    give_item,
    give_xp,
    help_command,
    handle_game_action,
    player_context,
    missions,
    missions_command,
    mission_command,
    casino_command,
    profil,
    give_command,
    revoke_cash,
    revoke_xp,
    revoke_level,
    revoke_item,
    invest,
    handle_jugg_tier,
    handle_bank_callback,
    main as bot_main,
    jugg,
    prison_remaining_seconds,
    shop,
    shop_callback,
    set_level,
    shoot,
    vehicle_rental_callback,
    start,
    withdraw,
)
import config as config_module
from config import ADMIN_USER_IDS, validate_config
from database import MAX_ADMIN_BALANCE, MAX_ADMIN_XP, Database


class HelpCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_handler_errors_are_logged_and_reported(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        update = Update(update_id=1, message=message)
        error = RuntimeError("sqlite unavailable")
        context = SimpleNamespace(error=error)

        with self.assertLogs("bot", level="ERROR") as captured:
            await error_handler(update, context)

        self.assertIn("Erreur inattendue", captured.output[0])
        message.reply_text.assert_awaited_once_with(
            "Oups, le bot a eu un pépin. Réessaie dans un instant."
        )

    async def test_start_uses_montreal_welcome_and_escapes_player_name(self) -> None:
        database = AsyncMock()
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            sender_chat=None,
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=101,
                username="joueur",
                full_name="Joueur <Chef>",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=message,
        )

        await start(update, context)

        database.ensure_player.assert_awaited_once_with(101, "joueur", -1001)
        text = message.reply_text.await_args.args[0]
        self.assertIn("Montréal", text)
        self.assertIn("Joueur &lt;Chef&gt;", text)
        self.assertIn("kichta", text)
        self.assertIn("gamos", text)
        self.assertIn("taule arcade", text)
        for command in ("/shop", "/jugg", "/help"):
            self.assertIn(command, text)
        self.assertEqual(
            message.reply_text.await_args.kwargs["parse_mode"],
            "HTML",
        )

    async def test_start_existing_group_profile_is_preserved(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "start.sqlite3"))
            await database.initialize()
            await database.ensure_player(101, "joueur", -1001)
            async with database.transaction() as connection:
                await connection.execute(
                    """
                    UPDATE players
                    SET cash = 321, bank = 654, level = 5, xp = 432
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (101, -1001),
                )
            self.assertEqual(await database.purchase_item(101, "montreal_toque", -1001), "ok")

            context = SimpleNamespace(
                application=SimpleNamespace(bot_data={"database": database})
            )
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                sender_chat=None,
            )
            update = SimpleNamespace(
                effective_user=SimpleNamespace(
                    id=101,
                    username="joueur",
                    full_name="Joueur",
                    is_bot=False,
                ),
                effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
                effective_message=message,
            )

            await start(update, context)

            text = message.reply_text.await_args.args[0]
            self.assertIn("déjà enregistré", text)
            self.assertIn("/profil", text)
            player = await database.get_player(101, -1001)
            inventory = await database.get_inventory(101, -1001)
            self.assertEqual(
                (player["cash"], player["bank"], player["level"], player["xp"]),
                (236, 654, 5, 432),
            )
            self.assertEqual(
                [item["item_id"] for item in inventory], ["montreal_toque"]
            )

            other_group_message = SimpleNamespace(reply_text=AsyncMock())
            other_group_update = SimpleNamespace(
                effective_user=update.effective_user,
                effective_chat=SimpleNamespace(id=-1002, type="supergroup"),
                effective_message=other_group_message,
            )
            await start(other_group_update, context)
            self.assertIn(
                "Bienvenue",
                other_group_message.reply_text.await_args.args[0],
            )
            new_group_player = await database.get_player(101, -1002)
            self.assertEqual(new_group_player["cash"], 500)
            self.assertEqual(new_group_player["bank"], 0)
            self.assertEqual(await database.get_inventory(101, -1002), [])

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
            "/bank",
            "/deposit",
            "/withdraw",
            "/give",
            "/casino",
            "/missions",
            "/mission",
            "/help",
            "/jugg",
            "/braquer",
            "/shoot",
            "/shop",
            "/drive",
            "/repair",
            "/return",
            "/prison",
            "/evade",
            "/inventory",
            "/top",
            "/admin",
            "/devmode",
            "/give_cash",
            "/give_xp",
            "/set_level",
            "/give_item",
            "/admin_reset",
            "/revoke_cash",
            "/revoke_xp",
            "/revoke_level",
            "/revoke_item",
            "/invest",
        ):
            with self.subTest(command=command):
                self.assertIn(command, text)
        for alias in (
            "/bourse",
            "/buy",
            "/sell",
            "/ride",
            "/juggling",
            "/hustle",
            "/mission_colis",
            "/mission_phishing",
            "/mission_catalyseur",
            "/mission_vapote",
            "/mission_depanneur",
            "/mission_sneakers",
            "/give_infinite",
        ):
            with self.subTest(alias=alias):
                self.assertNotIn(alias, text)

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
                "bank",
                "deposit",
                "withdraw",
                "give",
                "casino",
                "missions",
                "mission",
                "admin",
                "devmode",
                "give_cash",
                "give_xp",
                "set_level",
                "give_item",
                "admin_reset",
                "revoke_cash",
                "revoke_xp",
                "revoke_level",
                "revoke_item",
                "invest",
                "help",
                "jugg",
                "braquer",
                "shoot",
                "shop",
                "drive",
                "repair",
                "return",
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
        builder.post_shutdown.return_value = builder
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
        for command in (
            "bank", "deposit", "withdraw", "drive", "repair",
            "return", "shoot", "admin", "admin_reset",
            "devmode", "give_cash", "give_xp", "set_level", "give_item",
        ):
            self.assertTrue(
                any(
                    isinstance(handler, CommandHandler)
                    and command in handler.commands
                    for handler in handlers
                )
            )
        registered_commands = {
            command
            for handler in handlers
            if isinstance(handler, CommandHandler)
            for command in handler.commands
        }
        self.assertNotIn("bourse", registered_commands)
        self.assertNotIn("buy", registered_commands)
        self.assertNotIn("sell", registered_commands)
        self.assertNotIn("ride", registered_commands)
        self.assertNotIn("juggling", registered_commands)
        self.assertNotIn("hustle", registered_commands)
        self.assertNotIn("mission_colis", registered_commands)
        self.assertNotIn("give_infinite", registered_commands)
        self.assertTrue(
            any(
                isinstance(handler, CallbackQueryHandler)
                and handler.pattern.pattern == r"^bank:"
                for handler in handlers
            )
        )
        self.assertTrue(
            any(
                isinstance(handler, CallbackQueryHandler)
                and handler.pattern.pattern == r"^jugg:"
                for handler in handlers
            )
        )
        self.assertTrue(
            any(
                isinstance(handler, CallbackQueryHandler)
                and handler.pattern.pattern == r"^admin:"
                for handler in handlers
            )
        )


class BankCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_bank_menu_and_commands_move_money_in_both_directions(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "bank.sqlite3"))
        await database.initialize()
        await database.ensure_player(111, "bank_player", -1001)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=[],
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=111, username="bank_player", full_name="Bank Player", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=message,
        )

        await bank(update, context)

        menu_text = message.reply_text.await_args.args[0]
        self.assertIn("Cash liquide: 500$", menu_text)
        self.assertIn("Solde bancaire: 0$", menu_text)
        buttons = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, "bank:111:deposit_all")
        self.assertEqual(buttons[1][0].callback_data, "bank:111:withdraw_all")

        context.args = ["all"]
        await deposit(update, context)
        player = await database.get_player(111, -1001)
        self.assertEqual((player["cash"], player["bank"]), (0, 500))
        self.assertIn("Dépôt de 500$", message.reply_text.await_args.args[0])

        context.args = ["100"]
        await withdraw(update, context)
        player = await database.get_player(111, -1001)
        self.assertEqual((player["cash"], player["bank"]), (100, 400))

        query = AsyncMock(data="bank:111:withdraw_all")
        callback_update = SimpleNamespace(
            callback_query=query,
            effective_user=update.effective_user,
            effective_chat=update.effective_chat,
            effective_message=message,
        )
        await handle_bank_callback(callback_update, context)
        player = await database.get_player(111, -1001)
        self.assertEqual((player["cash"], player["bank"]), (500, 0))
        self.assertIn("Retrait de 400$", query.edit_message_text.await_args.args[0])


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


class AdminCommandTests(unittest.IsolatedAsyncioTestCase):
    ADMIN_ID = 8970668955
    GROUP_ID = -1009001

    def _context(
        self, database: Database | AsyncMock, args: list[str]
    ) -> SimpleNamespace:
        return SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=args,
        )

    def _update(
        self,
        user_id: int,
        message: SimpleNamespace,
        chat_id: int = GROUP_ID,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            effective_user=SimpleNamespace(
                id=user_id,
                username="admin" if user_id == self.ADMIN_ID else "player",
                full_name="Admin" if user_id == self.ADMIN_ID else "Player",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
            effective_message=message,
        )

    async def test_non_admin_is_denied_from_all_admin_commands(self) -> None:
        database = AsyncMock()
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            reply_to_message=None,
        )
        update = self._update(101, message)
        context = self._context(database, [])

        with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
            await admin(update, context)
            await give_cash(update, context)
            await admin_reset(update, context)
            await give_xp(update, context)
            await set_level(update, context)
            await give_item(update, context)
            await revoke_cash(update, context)
            await revoke_xp(update, context)
            await revoke_level(update, context)
            await revoke_item(update, context)

        self.assertEqual(message.reply_text.await_count, 10)
        self.assertTrue(
            all(
                "Passe ton chemin" in call.args[0]
                for call in message.reply_text.await_args_list
            )
        )
        database.admin_credit_player.assert_not_awaited()
        database.admin_reset_player.assert_not_awaited()

    async def test_admin_panel_is_owner_bound_and_has_control_buttons(self) -> None:
        database = AsyncMock()
        database.get_admin_dev_mode.return_value = False
        message = SimpleNamespace(reply_text=AsyncMock())
        update = self._update(self.ADMIN_ID, message)
        context = self._context(database, [])

        with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
            await admin(update, context)

        markup = message.reply_text.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertEqual(
            callbacks,
            [f"admin:{self.ADMIN_ID}:toggle"],
        )

        query = AsyncMock(data=f"admin:{self.ADMIN_ID}:credit")
        unauthorized_update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101, is_bot=False, username="player", full_name="Player"
            ),
        )
        with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
            await admin_callback(unauthorized_update, context)
        query.answer.assert_awaited_once()
        self.assertTrue(query.answer.await_args.kwargs["show_alert"])
        query.edit_message_text.assert_not_awaited()

    async def test_admin_panel_exposes_all_controls_only_in_developer_mode(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_panel.sqlite3"))
            await database.initialize()
            await database.toggle_admin_dev_mode(self.ADMIN_ID)
            context = self._context(database, [])
            message = SimpleNamespace(reply_text=AsyncMock())
            update = self._update(self.ADMIN_ID, message)

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await admin(update, context)

            keyboard = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
            actions = {
                button.callback_data.rsplit(":", 1)[-1]
                for row in keyboard
                for button in row
            }
            self.assertEqual(
                actions,
                {"toggle", "credit", "xp", "level", "item", "revoke", "repair", "reset"},
            )
            for action, command in (
                ("credit", "/give_cash"),
                ("xp", "/give_xp"),
                ("level", "/set_level"),
                ("item", "/give_item"),
                ("revoke", "/revoke_cash"),
                ("repair", "/admin_reset"),
                ("reset", "/admin_reset"),
            ):
                with self.subTest(action=action):
                    query = AsyncMock(data=f"admin:{self.ADMIN_ID}:{action}")
                    callback_update = SimpleNamespace(
                        callback_query=query,
                        effective_user=update.effective_user,
                    )
                    with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                        await admin_callback(callback_update, context)
                    self.assertIn(command, query.edit_message_text.await_args.args[0])

    async def test_dev_tools_credit_cash_xp_level_and_items_for_self_and_player(self) -> None:
        from game import ITEMS

        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_tools.sqlite3"))
            await database.initialize()
            await database.ensure_player(self.ADMIN_ID, "admin", self.GROUP_ID)
            await database.ensure_player(204, "recipient", self.GROUP_ID)
            await database.ensure_player(204, "recipient", self.GROUP_ID - 1)
            await database.toggle_admin_dev_mode(self.ADMIN_ID)
            context = self._context(database, ["2500", "bank"])
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                reply_to_message=None,
            )
            update = self._update(self.ADMIN_ID, message)

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await give_cash(update, context)
                context.args = []
                await give_cash(update, context)
                context.args = ["@recipient", "100000", "bank"]
                await give_cash(update, context)
                self.assertEqual(
                    (await database.get_player(204, self.GROUP_ID))["bank"],
                    100_000,
                )
                context.args = ["@recipient"]
                await give_cash(update, context)
                context.args = ["@recipient", "20000"]
                await give_xp(update, context)
                self.assertEqual(
                    (await database.get_player(204, self.GROUP_ID))["xp"],
                    20_000,
                )
                context.args = ["@recipient"]
                await give_xp(update, context)
                context.args = []
                await give_xp(update, context)
                context.args = ["@recipient", "25"]
                await set_level(update, context)
                self.assertEqual(
                    (await database.get_player(204, self.GROUP_ID))["level"],
                    25,
                )
                context.args = ["@recipient"]
                await set_level(update, context)
                context.args = ["@recipient", "bixi"]
                await give_item(update, context)
                context.args = ["@recipient", "audi"]
                await give_item(update, context)
                context.args = ["@recipient", "Audi", "RS", "6", "Avant"]
                await give_item(update, context)
                context.args = ["@recipient"]
                await give_item(update, context)

            admin_player = await database.get_player(self.ADMIN_ID, self.GROUP_ID)
            recipient = await database.get_player(204, self.GROUP_ID)
            other_group_recipient = await database.get_player(204, self.GROUP_ID - 1)
            inventory = await database.get_inventory(204, self.GROUP_ID)
            self.assertEqual(
                (admin_player["cash"], admin_player["bank"]),
                (1_000_000_000_000, 2500),
            )
            self.assertEqual(
                (admin_player["xp"], admin_player["level"]),
                (MAX_ADMIN_XP, 60),
            )
            self.assertEqual(recipient["cash"], 1_000_000_000_000)
            self.assertEqual((recipient["xp"], recipient["level"]), (177_000, 60))
            self.assertEqual(recipient["bank"], 100_000)
            self.assertEqual((other_group_recipient["cash"], other_group_recipient["xp"]), (500, 0))
            self.assertEqual(len(inventory), len(ITEMS))
            self.assertIn("bixi", {item["item_id"] for item in inventory})
            self.assertIn("audi_rs3_nardo", {item["item_id"] for item in inventory})
            self.assertIn("audi_rs6_avant", {item["item_id"] for item in inventory})
            self.assertEqual(message.reply_text.await_count, 13)

    async def test_dev_mode_revocation_commands_remove_exact_or_clear_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_revoke.sqlite3"))
            await database.initialize()
            await database.ensure_player(self.ADMIN_ID, "admin", self.GROUP_ID)
            await database.ensure_player(205, "recipient", self.GROUP_ID)
            await database.ensure_player(205, "recipient", self.GROUP_ID - 1)
            await database.toggle_admin_dev_mode(self.ADMIN_ID)
            async with database.transaction() as connection:
                await connection.execute(
                    """
                    UPDATE players SET cash = 1000, bank = 500, xp = 20000, level = 20
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (205, self.GROUP_ID),
                )
            await database.admin_give_item(205, self.GROUP_ID, "bixi")
            await database.admin_give_item(205, self.GROUP_ID, "bixi")
            context = self._context(database, ["@recipient", "150", "bank"])
            message = SimpleNamespace(reply_text=AsyncMock(), reply_to_message=None)
            update = self._update(self.ADMIN_ID, message)

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await revoke_cash(update, context)
                context.args = ["@recipient", "5000"]
                await revoke_xp(update, context)
                context.args = ["@recipient", "3"]
                await revoke_level(update, context)
                context.args = ["@recipient", "bixi"]
                await revoke_item(update, context)

                context.args = ["@recipient"]
                await revoke_cash(update, context)
                await revoke_xp(update, context)
                await revoke_level(update, context)
                await revoke_item(update, context)

            player = await database.get_player(205, self.GROUP_ID)
            other_group_player = await database.get_player(205, self.GROUP_ID - 1)
            self.assertEqual((player["cash"], player["bank"]), (0, 350))
            self.assertEqual((player["xp"], player["level"]), (0, 1))
            self.assertEqual(await database.get_inventory(205, self.GROUP_ID), [])
            self.assertEqual(
                (other_group_player["cash"], other_group_player["bank"],
                 other_group_player["xp"], other_group_player["level"]),
                (500, 0, 0, 1),
            )
            self.assertEqual(message.reply_text.await_count, 8)

    async def test_god_mode_ignores_existing_prison_on_mission_and_player_mode_does_not(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_god_mode.sqlite3"))
            await database.initialize()
            await database.ensure_player(self.ADMIN_ID, "admin", self.GROUP_ID)
            async with database.transaction() as connection:
                await connection.execute(
                    """
                    UPDATE players SET in_prison_until = ?
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (datetime.now(timezone.utc).timestamp() + 300, self.ADMIN_ID, self.GROUP_ID),
                )
            context = self._context(database, ["civic"])
            message = SimpleNamespace(reply_text=AsyncMock())
            update = self._update(self.ADMIN_ID, message)

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await missions(update, context)
            self.assertIn("taule", message.reply_text.await_args.args[0])

            await database.toggle_admin_dev_mode(self.ADMIN_ID)
            message.reply_text.reset_mock()
            with (
                patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})),
                patch("bot.random.random", return_value=0.0),
            ):
                await missions(update, context)

            self.assertIn("Mission réussie", message.reply_text.await_args.args[0])
            player = await database.get_player(self.ADMIN_ID, self.GROUP_ID)
            self.assertEqual(player["in_prison_until"], 0)

    async def test_devmode_command_and_button_toggle_persist_and_mark_profile(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_mode.sqlite3"))
            await database.initialize()
            await database.ensure_player(self.ADMIN_ID, "admin", self.GROUP_ID)
            self.assertEqual(
                await database.purchase_item(
                    self.ADMIN_ID, "montreal_toque", self.GROUP_ID
                ),
                "ok",
            )
            async with database.transaction() as connection:
                await connection.execute(
                    """
                    UPDATE players SET bank = 321, xp = 444, level = 4
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (self.ADMIN_ID, self.GROUP_ID),
                )
            player_before = await database.get_player(self.ADMIN_ID, self.GROUP_ID)
            inventory_before = await database.get_inventory(
                self.ADMIN_ID, self.GROUP_ID
            )
            context = self._context(database, [])
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                sender_chat=None,
                reply_to_message=None,
            )
            update = self._update(self.ADMIN_ID, message)

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await profil(update, context)
            self.assertNotIn(
                "[DEV]", message.reply_text.await_args.args[0]
            )

            message.reply_text.reset_mock()
            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await devmode(update, context)
            self.assertTrue(await database.get_admin_dev_mode(self.ADMIN_ID))
            self.assertIn("Mode Développeur", message.reply_text.await_args.args[0])

            message.reply_text.reset_mock()
            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await profil(update, context)
            self.assertIn("[DEV]", message.reply_text.await_args.args[0])

            query = AsyncMock(data=f"admin:{self.ADMIN_ID}:toggle")
            callback_update = SimpleNamespace(
                callback_query=query,
                effective_user=update.effective_user,
            )
            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await admin_callback(callback_update, context)
            self.assertFalse(await database.get_admin_dev_mode(self.ADMIN_ID))
            self.assertIn(
                "Mode Joueur", query.edit_message_text.await_args.args[0]
            )
            player_after = await database.get_player(self.ADMIN_ID, self.GROUP_ID)
            self.assertEqual(
                (
                    player_after["cash"],
                    player_after["bank"],
                    player_after["xp"],
                    player_after["level"],
                ),
                (
                    player_before["cash"],
                    player_before["bank"],
                    player_before["xp"],
                    player_before["level"],
                ),
            )
            self.assertEqual(
                await database.get_inventory(self.ADMIN_ID, self.GROUP_ID),
                inventory_before,
            )
            blocked_action = AsyncMock(data=f"admin:{self.ADMIN_ID}:xp")
            blocked_callback_update = SimpleNamespace(
                callback_query=blocked_action,
                effective_user=update.effective_user,
            )
            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await admin_callback(blocked_callback_update, context)
                context.args = ["100"]
                await give_xp(update, context)
            self.assertTrue(blocked_action.answer.await_args.kwargs["show_alert"])
            self.assertIn(
                "verrouillé en Mode Joueur",
                message.reply_text.await_args.args[0],
            )
            unchanged_admin = await database.get_player(
                self.ADMIN_ID, self.GROUP_ID
            )
            self.assertEqual(unchanged_admin["xp"], player_before["xp"])

    async def test_cheat_commands_are_locked_while_admin_is_in_player_mode(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_lock.sqlite3"))
            await database.initialize()
            await database.ensure_player(204, "recipient", self.GROUP_ID)
            context = self._context(
                database, ["@recipient", "100000"]
            )
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                reply_to_message=None,
            )
            update = self._update(self.ADMIN_ID, message)

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await give_cash(update, context)
                await admin_reset(update, context)
                await give_xp(update, context)
                await set_level(update, context)
                await give_item(update, context)

            self.assertEqual(message.reply_text.await_count, 5)
            self.assertTrue(
                all(
                    "verrouillé en Mode Joueur" in call.args[0]
                    for call in message.reply_text.await_args_list
                )
            )
            player = await database.get_player(204, self.GROUP_ID)
            self.assertEqual((player["cash"], player["bank"]), (500, 0))

    async def test_admin_credit_by_username_targets_only_current_chat(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin.sqlite3"))
            await database.initialize()
            await database.ensure_player(202, "recipient", self.GROUP_ID)
            await database.ensure_player(202, "recipient", self.GROUP_ID - 1)
            await database.toggle_admin_dev_mode(self.ADMIN_ID)
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                reply_to_message=None,
            )
            update = self._update(self.ADMIN_ID, message)
            context = self._context(database, ["@recipient", "100000", "bank"])

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await give_cash(update, context)

            player = await database.get_player(202, self.GROUP_ID)
            other_group_player = await database.get_player(202, self.GROUP_ID - 1)
            self.assertEqual((player["cash"], player["bank"]), (500, 100000))
            self.assertEqual(
                (other_group_player["cash"], other_group_player["bank"]),
                (500, 0),
            )
            self.assertIn("Crédit admin appliqué", message.reply_text.await_args.args[0])

    async def test_admin_credit_can_target_a_replied_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_reply.sqlite3"))
            await database.initialize()
            await database.ensure_player(204, "reply_target", self.GROUP_ID)
            await database.toggle_admin_dev_mode(self.ADMIN_ID)
            replied_message = SimpleNamespace(
                from_user=SimpleNamespace(
                    id=204,
                    username="reply_target",
                    full_name="Reply Target",
                    is_bot=False,
                )
            )
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                reply_to_message=replied_message,
            )
            update = self._update(self.ADMIN_ID, message)
            context = self._context(database, ["250"])

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await give_cash(update, context)

            player = await database.get_player(204, self.GROUP_ID)
            self.assertEqual(player["cash"], 750)
            self.assertIn("250$", message.reply_text.await_args.args[0])

    async def test_give_cash_without_amount_uses_cap_and_amount_is_exact(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_max_credit.sqlite3"))
            await database.initialize()
            await database.ensure_player(self.ADMIN_ID, "admin", self.GROUP_ID)
            await database.toggle_admin_dev_mode(self.ADMIN_ID)
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                reply_to_message=None,
            )
            update = self._update(self.ADMIN_ID, message)
            context = self._context(database, [])

            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                context.args = ["250"]
                await give_cash(update, context)
                player = await database.get_player(self.ADMIN_ID, self.GROUP_ID)
                self.assertEqual(player["cash"], 750)
                self.assertIn("250$", message.reply_text.await_args.args[0])

                context.args = []
                await give_cash(update, context)
                player = await database.get_player(self.ADMIN_ID, self.GROUP_ID)
                self.assertEqual(player["cash"], MAX_ADMIN_BALANCE)
                self.assertIn(
                    f"+{MAX_ADMIN_BALANCE - 750}$",
                    message.reply_text.await_args.args[0],
                )

    async def test_full_reset_requires_confirmation_and_is_group_scoped(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "admin_reset.sqlite3"))
            await database.initialize()
            await database.toggle_admin_dev_mode(self.ADMIN_ID)
            for chat_id in (self.GROUP_ID, self.GROUP_ID - 1):
                await database.ensure_player(303, "reset_target", chat_id)
                async with database.transaction() as connection:
                    await connection.execute(
                        """
                        UPDATE players
                        SET cash = 1000, bank = 500, level = 5, xp = 1000
                        WHERE user_id = ? AND chat_id = ?
                        """,
                        (303, chat_id),
                    )
                self.assertEqual(
                    await database.purchase_item(303, "montreal_toque", chat_id),
                    "ok",
                )

            context = self._context(
                database, ["@reset_target", "full"]
            )
            message = SimpleNamespace(
                reply_text=AsyncMock(),
                reply_to_message=None,
            )
            update = self._update(self.ADMIN_ID, message)
            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await admin_reset(update, context)
            self.assertIn("confirmer", message.reply_text.await_args.args[0])
            still_unchanged = await database.get_player(303, self.GROUP_ID)
            self.assertEqual(
                (still_unchanged["cash"], still_unchanged["bank"]),
                (915, 500),
            )

            message.reply_text.reset_mock()
            context.args = ["@reset_target", "full", "CONFIRMER"]
            with patch("bot.ADMIN_USER_IDS", frozenset({self.ADMIN_ID})):
                await admin_reset(update, context)

            reset_player = await database.get_player(303, self.GROUP_ID)
            other_group_player = await database.get_player(303, self.GROUP_ID - 1)
            self.assertEqual(
                (reset_player["cash"], reset_player["bank"],
                 reset_player["level"], reset_player["xp"]),
                (500, 0, 1, 0),
            )
            self.assertEqual(
                (other_group_player["cash"], other_group_player["bank"],
                 other_group_player["level"], other_group_player["xp"]),
                (915, 500, 5, 1000),
            )
            self.assertEqual(await database.get_inventory(303, self.GROUP_ID), [])


class ShopCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_vehicle_rental_shop_flow_charges_and_persists_rental(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "rental_shop.sqlite3"))
        await database.initialize()
        await database.ensure_player(101, "joueur", -1001)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=101, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=message,
        )
        query = AsyncMock(data="shop:101:section:gamos")
        update.callback_query = query
        await shop_callback(update, context)

        buttons = [
            button.callback_data
            for row in query.edit_message_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("shop:101:rentcatalog:vehicules", buttons)

        query.data = "shop:101:rentcatalog:vehicules"
        await shop_callback(update, context)
        buttons = [
            button.callback_data
            for row in query.edit_message_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("shop:101:rentcar:bixi", buttons)

        query.data = "shop:101:rentcar:bixi"
        await shop_callback(update, context)
        buttons = [
            button.callback_data
            for row in query.edit_message_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn("rent:101:bixi:1h", buttons)

        query.data = "rent:101:bixi:1h"
        await vehicle_rental_callback(update, context)
        self.assertIn("Location confirmée", query.edit_message_text.await_args.args[0])
        rental = await database.get_vehicle_rental(101, -1001)
        self.assertEqual(rental["item_id"], "bixi")
        self.assertEqual((await database.get_player(101, -1001))["cash"], 480)

    async def test_owned_vehicle_can_be_used_without_rental_risk(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "owned_vehicle.sqlite3"))
        await database.initialize()
        await database.ensure_player(102, "owner", -1002)
        self.assertEqual(await database.purchase_item(102, "bixi", -1002), "ok")
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=102, username="owner", full_name="Owner", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1002, type="supergroup"),
            effective_message=message,
        )

        with patch("bot.random.SystemRandom") as system_random:
            system_random.return_value.random.return_value = 0.99
            await drive(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn("véhicule de propriété", text)
        self.assertIn("+75$", text)
        self.assertIn("+20 XP", text)
        player = await database.get_player(102, -1002)
        self.assertEqual((player["cash"], player["xp"]), (525, 20))
        self.assertIsNone(await database.get_vehicle_rental(102, -1002))

    async def test_shop_navigation_brands_types_back_buttons_and_purchase(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "shop.sqlite3"))
        await database.initialize()
        await database.ensure_player(101, "joueur", -1001)
        async with database.transaction() as connection:
            await connection.execute(
                """
                UPDATE players SET cash = 10000, level = 60
                WHERE user_id = ? AND chat_id = ?
                """,
                (101, -1001),
            )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=101, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=message,
        )

        await shop(update, context)
        root_markup = message.reply_text.await_args.kwargs["reply_markup"]
        root_callbacks = [
            button.callback_data
            for row in root_markup.inline_keyboard
            for button in row
        ]
        self.assertIn("shop:101:section:sapes", root_callbacks)
        self.assertIn("shop:101:section:arsenal", root_callbacks)
        self.assertIn("shop:101:section:gamos", root_callbacks)
        self.assertIn("shop:101:section:equipment", root_callbacks)

        query = AsyncMock(data="shop:101:section:sapes")
        update.callback_query = query
        await shop_callback(update, context)
        brand_buttons = [
            button
            for row in query.edit_message_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        brand_callbacks = [button.callback_data for button in brand_buttons]
        self.assertIn("shop:101:brand:nike", brand_callbacks)
        self.assertIn("shop:101:brand:prada", brand_callbacks)
        self.assertIn("shop:101:home", brand_callbacks)
        self.assertEqual(
            sum(button.text == "⬅️ Retour" for button in brand_buttons), 1
        )

        query.data = "shop:101:brand:nike"
        await shop_callback(update, context)
        type_buttons = [
            button
            for row in query.edit_message_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        type_callbacks = [button.callback_data for button in type_buttons]
        self.assertIn("shop:101:type:nike:footwear", type_callbacks)
        self.assertIn("shop:101:section:sapes", type_callbacks)
        self.assertEqual(
            sum(button.text == "⬅️ Retour" for button in type_buttons), 1
        )

        query.data = "shop:101:type:nike:footwear"
        await shop_callback(update, context)
        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Nike Air Max", text)
        item_buttons = [
            button
            for row in query.edit_message_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        item_callbacks = [button.callback_data for button in item_buttons]
        self.assertIn("buy:101:nike_air_max_tech", item_callbacks)
        self.assertIn("shop:101:brand:nike", item_callbacks)
        self.assertEqual(
            sum(button.text == "⬅️ Retour" for button in item_buttons), 1
        )

        query.data = "buy:101:nike_air_max_tech"
        await shop_callback(update, context)
        inventory = await database.get_inventory(101, -1001)
        self.assertEqual(
            [item["item_id"] for item in inventory],
            ["nike_air_max_tech"],
        )
        buy_buttons = [
            button
            for row in query.edit_message_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        ]
        self.assertIn(
            "shop:101:type:nike:footwear",
            [button.callback_data for button in buy_buttons],
        )
        self.assertEqual(
            sum(button.text == "⬅️ Retour" for button in buy_buttons), 1
        )

    def test_expanded_fashion_catalog_has_unique_ids_and_preserves_game_rates(self) -> None:
        from collections import Counter

        from bot import SHOP_CLOTHING_CATEGORIES, shop_item_brand
        from game import ITEMS

        ids = [item.item_id for item in ITEMS]
        self.assertEqual(len(ids), len(set(ids)))
        fashion = [
            item for item in ITEMS if item.category in SHOP_CLOTHING_CATEGORIES
        ]
        self.assertGreaterEqual(len(fashion), 250)
        self.assertTrue(all(item.xp_bonus >= 0 for item in fashion))
        brand_counts = Counter(shop_item_brand(item)[0] for item in fashion)
        for brand_id in (
            "nike",
            "prada",
            "louis_vuitton",
            "dior",
            "gucci",
            "balenciaga",
            "lacoste",
            "trapstar",
            "corteiz",
            "adidas",
            "chanel",
            "hermes",
        ):
            with self.subTest(brand=brand_id):
                self.assertGreaterEqual(brand_counts[brand_id], 14)

        category_counts = Counter(item.category for item in fashion)
        for category, minimum in {
            "vetements_accessoires": 48,
            "vetement": 33,
            "corps_vestes": 42,
            "tete_visage": 33,
            "pieds": 40,
            "accessoires_sacoches": 48,
            "accessoire": 19,
            "luxe": 16,
        }.items():
            with self.subTest(category=category):
                self.assertGreaterEqual(category_counts[category], minimum)

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
        self.assertIn("Protections balistiques", text)
        self.assertIn("Gilet pare-balles léger classe IIIA", text)
        self.assertIn("Shield anti-émeute / bouclier balistique", text)
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn("buy:101:gilet_leger", callback_data)
        self.assertIn("buy:101:gilet_lourd", callback_data)
        self.assertIn("buy:101:shield_tactique", callback_data)


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
                    "bank": 0,
                    "balance": 500,
                    "heat": 0,
                    "in_prison_until": 0,
                    "gamos": "Aucun",
                    "level": 1,
                    "xp": 0,
                    "search_level": 0,
                    "last_hustle_time": 0,
                    "last_hustle_cooldown": 0,
                    "last_jugg_time": 0.0,
                    "last_jugg_cooldown": 0,
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
        self.assertEqual((await database.get_player(chat_id, chat_id))["xp"], 5)
        text = message.reply_text.await_args.args[0]
        self.assertIn("Roulette pour Admin de Montréal", text)
        self.assertIn("Nouveau solde: 600$", text)
        self.assertIn("+5 XP", text)

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
        self.assertEqual((await database.get_player(44, chat_id))["xp"], 5)
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
        self.assertIn("Mission logistique urbaine", text)
        self.assertIn("Course express", text)
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

    async def test_mission_notifies_immediately_when_xp_reaches_level_threshold(self) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_mission(
            ["civic"]
        )
        self.addCleanup(temp_dir.cleanup)
        await database.apply_action_result(
            777, 0, 0, None, None, xp_gain=90, chat_id=chat.id
        )

        with patch("bot.random.random", return_value=0.0):
            await missions(update, context)

        player = await database.get_player(777, chat.id)
        self.assertEqual((player["xp"], player["level"]), (110, 2))
        self.assertIn("TU PRENDS DU GALON", message.reply_text.await_args.args[0])

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
            if "colis" in args:
                await database.purchase_item(888, "bixi", chat.id)
        return temp_dir, database, context, update, message, chat

    async def test_mission_menu_lists_hustles_and_cooldowns(self) -> None:
        temp_dir, _, context, update, message, _ = await self._setup_hustle([])
        self.addCleanup(temp_dir.cleanup)

        await missions(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn("/mission colis", text)
        self.assertIn("/mission phishing", text)
        self.assertIn("Cooldown: 20s", text)
        self.assertIn("Gain fixe: 115$ | XP: 5", text)
        self.assertIn("véhicule requis", text)

    async def test_delivery_hustle_requires_a_vehicle(self) -> None:
        temp_dir, _, context, update, message, _ = await self._setup_hustle([])
        self.addCleanup(temp_dir.cleanup)
        context.args = ["colis"]

        await missions(update, context)

        self.assertIn("T’es à pied", message.reply_text.await_args.args[0])


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
            self.assertEqual(await database.get_player_balance(888, chat.id), 565)

            message.reply_text.reset_mock()
            context.args = ["vapote"]
            await missions(update, context)

        self.assertRegex(
            message.reply_text.await_args.args[0],
            r"Réessaie dans \d+ secondes\.",
        )
        self.assertEqual(await database.get_player_balance(888, chat.id), 565)

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
        temp_dir, database, context, update, message, _ = await self._setup_hustle(
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
        self.assertIn("Cash: +275$ | ✨ XP: +10", text)
        player = await database.get_player(888, -100888)
        self.assertEqual((player["xp"], player["level"]), (10, 1))
        self.assertIn("Heat: +0 (0/100)", text)
        self.assertIn("Recherche: +0 (0/100)", text)
        self.assertIn("Prochain hustle dans 25s", text)

    async def test_successful_mission_notifies_level_up(self) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_hustle(
            ["colis"]
        )
        self.addCleanup(temp_dir.cleanup)
        await database.apply_action_result(
            888, 0, 0, None, None, xp_gain=95, chat_id=chat.id
        )

        with patch("bot.random.random", return_value=0.0):
            await missions(update, context)

        player = await database.get_player(888, chat.id)
        self.assertEqual((player["xp"], player["level"]), (100, 2))
        self.assertIn("TU PRENDS DU GALON", message.reply_text.await_args.args[0])

    async def test_hustle_requires_existing_profile_and_supports_anonymous_admin(
        self,
    ) -> None:
        temp_dir, database, context, update, message, chat = await self._setup_hustle(
            []
        )
        self.addCleanup(temp_dir.cleanup)

        await database.ensure_player(chat.id, "Admin anonyme", chat.id)
        await database.purchase_item(chat.id, "bixi", chat.id)
        message.sender_chat = SimpleNamespace(id=chat.id, title="Admin anonyme")
        update.effective_user = None
        context.args = []
        with (
            patch("bot.random.random", return_value=0.0),
            patch("bot.random.randint", side_effect=AssertionError("cash must be fixed")),
        ):
            context.args = ["colis"]
            await missions(update, context)

        player = await database.get_player(chat.id, chat.id)
        self.assertEqual(player["cash"], 565)
        self.assertIn("Mission logistique urbaine", message.reply_text.await_args.args[0])

    async def test_hustle_handlers_require_start_for_unregistered_player(self) -> None:
        temp_dir, _, context, update, message, _ = await self._setup_hustle(
            [], register=False
        )
        self.addCleanup(temp_dir.cleanup)

        context.args = ["colis"]
        await missions(update, context)

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

    async def test_braquer_failure_uses_random_fine_and_jail_duration(self) -> None:
        from game import PvpResult

        with TemporaryDirectory() as temp_dir:
            database = Database(str(Path(temp_dir) / "braquer_random.sqlite3"))
            await database.initialize()
            await database.ensure_player(101, "attaquant", -1001)
            await database.ensure_player(102, "cible", -1001)
            async with database.transaction() as connection:
                await connection.execute(
                    """
                    UPDATE players SET cash = 3000, xp = 19000, level = 20
                    WHERE user_id = ? AND chat_id = ?
                    """,
                    (101, -1001),
                )
            context = SimpleNamespace(
                application=SimpleNamespace(bot_data={"database": database}),
                args=["@cible"],
            )
            message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
            update = SimpleNamespace(
                effective_user=SimpleNamespace(
                    id=101, username="attaquant", full_name="Attaquant", is_bot=False
                ),
                effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
                effective_message=message,
            )
            with (
                patch(
                    "bot.resolve_pvp",
                    return_value=PvpResult(
                        "failed", "Le défi échoue.", prison_seconds=45
                    ),
                ),
                patch("bot.random.randint", side_effect=[75, 600]) as randint,
                patch("bot.prison_deadline", return_value=4_000_000_000) as deadline,
            ):
                await braquer(update, context)

            player = await database.get_player(101, -1001)
            self.assertEqual(player["cash"], 2400)
            self.assertEqual(player["in_prison_until"], 4_000_000_000)
            self.assertEqual(randint.call_args_list[0].args, (30, 120))
            self.assertEqual(randint.call_args_list[1].args, (250, 1000))
            deadline.assert_called_once_with(75)
            response = message.reply_text.await_args.args[0]
            self.assertIn("75 secondes", response)
            self.assertIn("600$", response)

    async def test_braquer_applies_best_protection_and_reports_armor_block(self) -> None:
        from game import PvpResult

        for item_id, reduction, expected_text in (
            ("gilet_lourd", 0.20, "encaisse le braquage grâce à son gilet lourd"),
            ("shield_tactique", 0.35, "Le shield de @cible bloque tout"),
        ):
            with self.subTest(item_id=item_id), TemporaryDirectory() as temp_dir:
                database = Database(str(Path(temp_dir) / "braquer_armor.sqlite3"))
                await database.initialize()
                await database.ensure_player(101, "attaquant", -1001)
                await database.ensure_player(102, "cible", -1001)
                async with database.transaction() as connection:
                    await connection.execute(
                        """
                        UPDATE players SET cash = 3000, xp = 19000, level = 20
                        WHERE user_id = ? AND chat_id = ?
                        """,
                        (101, -1001),
                    )
                    await connection.execute(
                        """
                        UPDATE players SET cash = 20000, xp = 19000, level = 20
                        WHERE user_id = ? AND chat_id = ?
                        """,
                        (102, -1001),
                    )
                self.assertEqual(
                    await database.purchase_item(102, item_id, -1001), "ok"
                )
                context = SimpleNamespace(
                    application=SimpleNamespace(bot_data={"database": database}),
                    args=["@cible"],
                )
                message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
                update = SimpleNamespace(
                    effective_user=SimpleNamespace(
                        id=101,
                        username="attaquant",
                        full_name="Attaquant",
                        is_bot=False,
                    ),
                    effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
                    effective_message=message,
                )
                with (
                    patch(
                        "bot.resolve_pvp",
                        return_value=PvpResult(
                            "failed",
                            "Échec de test.",
                            prison_seconds=45,
                            protection_blocked=True,
                            protection_item_id=item_id,
                            protection_name=item_id,
                        ),
                    ) as resolve,
                    patch("bot.random.randint", side_effect=[75, 600]),
                    patch("bot.prison_deadline", return_value=4_000_000_000),
                ):
                    await braquer(update, context)

                resolve.assert_called_once()
                self.assertEqual(
                    resolve.call_args.kwargs["victim_protection"], reduction
                )
                self.assertEqual(
                    resolve.call_args.kwargs["victim_protection_item_id"], item_id
                )
                response = message.reply_text.await_args.args[0]
                self.assertIn(expected_text, response)
                self.assertIn("SPVM arrive en trombe", response)


class ShootCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_shoot_requires_inventory_weapon(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "shoot_unarmed.sqlite3"))
        await database.initialize()
        await database.ensure_player(701, "attacker", -100701)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=["@victim"],
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=701, username="attacker", full_name="Attaquant", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-100701, type="supergroup"),
            effective_message=message,
        )

        await shoot(update, context)

        self.assertIn("Il te faut une arme", message.reply_text.await_args.args[0])

    async def test_shoot_resolves_armed_riposte_and_applies_cash_xp_and_cooldown(
        self,
    ) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "shoot.sqlite3"))
        await database.initialize()
        await database.ensure_player(702, "attacker", -100702)
        await database.ensure_player(703, "victim", -100702)
        await database.apply_action_result(
            702, 20000, 0, None, None, chat_id=-100702
        )
        await database.apply_action_result(
            703, 20000, 0, None, None, chat_id=-100702
        )
        await database.purchase_item(702, "glock_19", -100702)
        await database.purchase_item(703, "ak_47", -100702)
        async with database.transaction() as connection:
            await connection.execute(
                """
                UPDATE players SET cash = 10000, bank = 5000
                WHERE chat_id = ? AND user_id IN (?, ?)
                """,
                (-100702, 702, 703),
            )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=["@victim"],
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=702, username="attacker", full_name="Attaquant", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-100702, type="supergroup"),
            effective_message=message,
        )

        with patch("game.random.random", side_effect=[0.99, 0.99]):
            await shoot(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn("riposte", text)
        self.assertIn("victim prend l’avantage", text)
        self.assertIn("cash liquide", text)
        self.assertIn("récupère", text)
        attacker = await database.get_player(702, -100702)
        victim = await database.get_player(703, -100702)
        self.assertEqual(attacker["cash"], 7500)
        self.assertEqual(victim["cash"], 12400)
        self.assertEqual((attacker["bank"], victim["bank"]), (5000, 5000))
        self.assertEqual(victim["xp"], 176)

        message.reply_text.reset_mock()
        with patch("game.random.random", side_effect=[0.0, 0.99]):
            await shoot(update, context)
        self.assertIn("Attends encore", message.reply_text.await_args.args[0])
        self.assertEqual((await database.get_player(703, -100702))["xp"], 176)

    async def test_shoot_supports_targeting_a_replied_to_user(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "shoot_reply.sqlite3"))
        await database.initialize()
        await database.ensure_player(704, "attacker", -100704)
        await database.ensure_player(705, "victim", -100704)
        await database.apply_action_result(
            704, 2000, 0, None, None, chat_id=-100704
        )
        await database.purchase_item(704, "beretta_m9", -100704)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=[],
        )
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            sender_chat=None,
            reply_to_message=SimpleNamespace(
                from_user=SimpleNamespace(
                    id=705, username="victim", full_name="Cible", is_bot=False
                )
            ),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=704, username="attacker", full_name="Attaquant", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-100704, type="supergroup"),
            effective_message=message,
        )

        with patch("game.random.random", side_effect=[0.0, 0.99]):
            await shoot(update, context)

        self.assertIn("Attaquant prend l’avantage", message.reply_text.await_args.args[0])


class JuggCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_jugg_menu_contains_unlocked_tiers_and_legacy_actions(self) -> None:
        database = AsyncMock()
        database.get_player.return_value = {"level": 60, "in_prison_until": 0}
        database.get_item_effects.return_value = {"has_obd": False}
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=101, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=message,
        )

        await jugg(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn("progression par niveau", text)
        markup = message.reply_text.await_args.kwargs["reply_markup"]
        buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
        ]
        callbacks = [button.callback_data for button in buttons]
        labels = [button.text for button in buttons]
        self.assertIn("jugg:101:bloc", callbacks)
        self.assertIn("jugg:101:centre_ville", callbacks)
        self.assertIn("jugg:101:international", callbacks)
        self.assertIn("jugg:101:elite", callbacks)
        self.assertTrue(any("1200$" in label for label in labels))
        self.assertTrue(any("4500$" in label for label in labels))
        self.assertTrue(any("15000$" in label for label in labels))
        self.assertIn("game:101:berri", callbacks)

    async def test_jugg_callback_applies_payout_and_reports_new_balance(self) -> None:
        from game import JuggResult

        database = AsyncMock()
        database.get_player.return_value = {"level": 1, "in_prison_until": 0}
        database.get_item_effects.return_value = {"xp_bonus": 0}
        database.apply_jugg_result.return_value = {
            "status": "ok",
            "balance": 1700,
            "cash_delta": 1200,
            "heat_delta": 1,
            "heat": 1,
            "search_level": 1,
            "xp_gain": 30,
            "previous_level": 1,
            "level": 1,
            "prison_seconds": 0,
            "cooldown_seconds": 15,
        }
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="jugg:101:bloc")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=SimpleNamespace(sender_chat=None),
        )

        with patch(
            "bot.resolve_jugg",
            return_value=JuggResult(True, 1200, 1, 0, "Succès fictif."),
        ):
            await handle_jugg_tier(update, context)

        database.apply_jugg_result.assert_awaited_once_with(
            101, -1001, 15, 1200, 1, 0, 30,
            bypass_cooldown=False, god_mode=False
        )
        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Cash: +1200$ | Solde: 1700$", text)
        self.assertIn("XP: +30", text)
        self.assertIn("Prochain palier dans 15s", text)

    async def test_jugg_callback_shows_precise_remaining_cooldown(self) -> None:
        from game import JuggResult

        database = AsyncMock()
        database.get_player.return_value = {"level": 1, "in_prison_until": 0}
        database.apply_jugg_result.return_value = {
            "status": "cooldown",
            "remaining": 12,
        }
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )
        query = AsyncMock(data="jugg:101:bloc")
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101, username="joueur", full_name="Joueur", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
            effective_message=SimpleNamespace(sender_chat=None),
        )

        with patch(
            "bot.resolve_jugg",
            return_value=JuggResult(True, 1200, 1, 0, "Succès fictif."),
        ):
            await handle_jugg_tier(update, context)

        self.assertIn(
            "disponible dans 12 secondes",
            query.edit_message_text.await_args.args[0],
        )
        self.assertEqual(database.apply_jugg_result.await_count, 1)

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
            -100303, 90, 8, None, None, 30,
            chat_id=-100303, god_mode=False
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
            303, 90, 8, None, None, 30,
            chat_id=-100303, god_mode=False
        )
        self.assertIn("réussi", query.edit_message_text.await_args.args[0])


class ConfigurationTests(unittest.TestCase):
    def test_only_configured_admin_ids_are_allowed_by_default(self) -> None:
        self.assertEqual(ADMIN_USER_IDS, frozenset({8970668955, 8742788260}))

    def test_missing_botfather_token_fails_with_clear_configuration_error(self) -> None:
        with patch("config.BOT_TOKEN", ""):
            with self.assertRaisesRegex(
                RuntimeError, "BOT_TOKEN est absente"
            ):
                validate_config()

    def test_db_path_environment_variable_takes_precedence_and_is_absolute(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DB_PATH": str(Path("C:\\persistent") / "rabza.sqlite3"),
                "DATABASE_PATH": str(Path("C:\\legacy") / "ignored.sqlite3"),
            },
        ):
            loaded_config = importlib.reload(config_module)
            self.assertEqual(
                loaded_config.DATABASE_PATH,
                str(Path("C:/persistent/rabza.sqlite3")),
            )
            self.assertTrue(Path(loaded_config.DATABASE_PATH).is_absolute())
        importlib.reload(config_module)


class InvestmentCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_market_display_partial_sale_and_sell_all(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "investment.sqlite3"))
        await database.initialize()
        await database.ensure_player(801, "investisseur", -100801)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=[],
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=801,
                username="investisseur",
                full_name="Investisseur",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=-100801, type="supergroup"),
            effective_message=message,
        )

        await invest(update, context)
        self.assertIn("BTC — Bitcoin", message.reply_text.await_args.args[0])
        self.assertIn("/invest sell all", message.reply_text.await_args.args[0])

        context.args = ["buy", "SOL", "2"]
        await invest(update, context)
        self.assertIn("Achat de 2 SOL", message.reply_text.await_args.args[0])
        self.assertEqual(
            (await database.get_player(801, -100801))["cash"], 210
        )

        context.args = ["sell", "SOL", "1"]
        await invest(update, context)
        self.assertIn("+0.00%", message.reply_text.await_args.args[0])
        self.assertEqual(
            (await database.get_player(801, -100801))["cash"], 355
        )

        context.args = ["sell", "all"]
        await invest(update, context)
        self.assertIn("Vente totale", message.reply_text.await_args.args[0])
        self.assertEqual(
            (await database.get_player(801, -100801))["cash"], 500
        )
        self.assertEqual(
            (await database.get_market_snapshot(801, -100801))["portfolio"]["value"],
            0,
        )

        context.args = ["SOL", "1"]
        context.args = ["buy", "SOL", "1"]
        await invest(update, context)
        self.assertIn("Achat de 1 SOL", message.reply_text.await_args.args[0])
        self.assertEqual((await database.get_player(801, -100801))["cash"], 355)


class ProgressionMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_level_sixty_has_no_lower_level_gates_in_missions_or_jugg(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "max_level.sqlite3"))
        await database.initialize()
        await database.ensure_player(802, "boss", -100802)
        async with database.transaction() as connection:
            await connection.execute(
                """
                UPDATE players SET xp = 177000, level = 60
                WHERE user_id = ? AND chat_id = ?
                """,
                (802, -100802),
            )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database}),
            args=[],
        )
        message = SimpleNamespace(reply_text=AsyncMock(), sender_chat=None)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=802, username="boss", full_name="Boss", is_bot=False
            ),
            effective_chat=SimpleNamespace(id=-100802, type="supergroup"),
            effective_message=message,
        )

        await missions(update, context)
        mission_text = message.reply_text.await_args.args[0]
        self.assertNotIn("Verrouillée", mission_text)
        self.assertIn("✅ Disponible — niv. 20", mission_text)

        await jugg(update, context)
        jugg_text = message.reply_text.await_args.args[0]
        self.assertNotIn("Prochain palier Jugg", jugg_text)
        callbacks = {
            button.callback_data
            for row in message.reply_text.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard
            for button in row
        }
        self.assertIn("jugg:802:elite", callbacks)

    async def test_locked_jugg_tier_is_rejected_server_side(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        database = Database(str(Path(temp_dir.name) / "locked_jugg.sqlite3"))
        await database.initialize()
        await database.ensure_player(803, "beginner", -100803)
        query = AsyncMock(data="jugg:803:elite")
        update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=803,
                username="beginner",
                full_name="Beginner",
                is_bot=False,
            ),
            effective_chat=SimpleNamespace(id=-100803, type="supergroup"),
            effective_message=SimpleNamespace(sender_chat=None),
            callback_query=query,
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"database": database})
        )

        await handle_jugg_tier(update, context)

        self.assertIn("niveau 15", query.edit_message_text.await_args.args[0])
        player = await database.get_player(803, -100803)
        self.assertEqual(player["last_jugg_time"], 0)


if __name__ == "__main__":
    unittest.main()
