from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram import Bot, Update, User
from telegram.error import BadRequest
from telegram.ext import CommandHandler

from bot import (
    blackjack_hand_value,
    braquer,
    casino,
    edit_callback_message,
    help_command,
    handle_game_action,
    player_context,
    missions,
    profil,
    prison_remaining_seconds,
)
from config import validate_config
from database import Database


class HelpCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_lists_all_registered_command_families(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        await help_command(SimpleNamespace(effective_message=message), None)

        text = message.reply_text.await_args.args[0]
        for command in (
            "/start",
            "/profil",
            "/casino",
            "/missions",
            "/mission",
            "/help",
            "/jugg",
            "/braquer",
            "/shop",
            "/prison",
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
            username="rabza_destroy_bot",
        )
        command_handlers = [
            CommandHandler(command, help_command)
            for command in (
                "start",
                "profil",
                "casino",
                "missions",
                "mission",
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
            for mention in ("", "@rabza_destroy_bot"):
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
                RuntimeError, "TELEGRAM_BOT_TOKEN est absente"
            ):
                validate_config()


if __name__ == "__main__":
    unittest.main()
