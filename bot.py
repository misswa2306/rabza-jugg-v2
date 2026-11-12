"""Telegram entry point for Rabza Jugg V2."""

import asyncio
from html import escape
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import TypedDict, cast

from telegram import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import BOT_TOKEN, DATABASE_PATH, validate_config
from database import Database
from game import (
    ACTION_SPECS,
    BAIL_COST,
    HUSTLES,
    ITEMS,
    JUGG_TIERS,
    MAX_LEVEL,
    PRISON_DURATION_SECONDS,
    SHOP_CATEGORIES,
    HustleSpec,
    actions_available,
    item_by_id,
    prison_deadline,
    resolve_action,
    resolve_jugg,
    resolve_pvp,
    xp_with_bonus,
    xp_to_next_level,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class ActorInfo(TypedDict):
    id: int
    name: str
    username: str | None


@dataclass(frozen=True)
class MissionSpec:
    mission_id: str
    name: str
    min_level: int
    reward: int
    xp: int
    success_chance: float


MISSIONS = (
    MissionSpec("civic", "Vol de civic rouillée dans une ruelle", 1, 100, 20, 0.85),
    MissionSpec(
        "audi",
        "Carjacking d'une Audi sur Sainte-Catherine",
        3,
        350,
        50,
        0.70,
    ),
    MissionSpec(
        "entrepot",
        "Pillage d'un entrepôt de pièces au port de Montréal",
        5,
        900,
        120,
        0.55,
    ),
    MissionSpec(
        "blindee",
        "Braquage d'un transport de fonds blindé",
        7,
        2500,
        250,
        0.40,
    ),
    MissionSpec(
        "helico",
        "Vol masterclass d'un hélico survolant le centre-ville",
        10,
        7500,
        600,
        0.25,
    ),
)

ROULETTE_RED_NUMBERS = frozenset(
    {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
)
CASINO_WIN_XP = 5


def level_up_notice(previous_level: int, new_level: int) -> str:
    if new_level <= previous_level:
        return ""
    return (
        "\n\n🎖️✨ TU PRENDS DU GALON! ✨🎖️\n"
        f"⭐ Niveau {previous_level} → {new_level} — le game évolue!"
    )


async def edit_callback_message(
    query: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit the callback's message and tolerate Telegram's no-op response only."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        logger.debug("Telegram message already contains the requested text.")


def database_for(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return cast(Database, context.application.bot_data["database"])


async def player_context(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    actor_override: ActorInfo | None = None,
    ensure_player: bool = True,
) -> tuple[ActorInfo, Chat, Message] | None:
    """Resolve this message's actor and optionally ensure its player account."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return None
    sender_chat = getattr(message, "sender_chat", None)

    if actor_override is not None:
        actor_info = actor_override
    elif user is not None and not user.is_bot:
        actor_info: ActorInfo = {
            "id": user.id,
            "name": user.full_name,
            "username": user.username,
        }
    elif sender_chat is not None:
        actor_info = {
            "id": sender_chat.id,
            "name": sender_chat.title or "Admin Anonyme",
            "username": sender_chat.title or "Admin Anonyme",
        }
    else:
        return None

    if ensure_player:
        await database_for(context).ensure_player(
            actor_info["id"], actor_info["username"], chat.id
        )
    return actor_info, chat, message


def owned_callback_payload(
    data: str | None,
    namespace: str,
    user_id: int,
    anonymous_chat_id: int | None = None,
) -> str | None:
    """Return a callback payload only for the player who opened its menu."""
    if data is None:
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != namespace:
        return None
    try:
        owner_id = int(parts[1])
    except ValueError:
        return None
    return parts[2] if owner_id in {user_id, anonymous_chat_id} else None


def callback_actor_info(
    update: Update,
    data: str | None,
    namespace: str,
) -> tuple[ActorInfo, str] | None:
    """Resolve the player who owns a callback, including group-anonymous menus."""
    user = update.effective_user
    chat = update.effective_chat
    if chat is None:
        return None

    if user is not None:
        payload = owned_callback_payload(data, namespace, user.id)
        if payload is not None and not user.is_bot:
            return (
                {"id": user.id, "name": user.full_name, "username": user.username},
                payload,
            )

    if chat.type in {"group", "supergroup"}:
        payload = owned_callback_payload(data, namespace, chat.id)
        if payload is not None:
            title = chat.title or "Admin Anonyme"
            return (
                {"id": chat.id, "name": title, "username": title},
                payload,
            )
    return None


def prison_remaining_seconds(player: dict[str, object]) -> int:
    """Return the remaining whole seconds until the Unix jail deadline."""
    end_time = player.get("in_prison_until")
    if not end_time:
        return 0
    if isinstance(end_time, str):
        try:
            end_time = float(end_time)
        except ValueError:
            legacy_datetime = datetime.fromisoformat(end_time)
            if legacy_datetime.tzinfo is None:
                legacy_datetime = legacy_datetime.replace(tzinfo=timezone.utc)
            end_time = legacy_datetime.timestamp()
    elif isinstance(end_time, datetime):
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        end_time = end_time.timestamp()
    if not isinstance(end_time, (int, float)):
        raise TypeError("in_prison_until doit être un timestamp Unix")
    return max(0, int(end_time - time.time()))


def blackjack_hand_value(hand: list[str]) -> int:
    value = sum(
        11 if card == "A" else 10 if card in {"J", "Q", "K"} else int(card)
        for card in hand
    )
    aces = hand.count("A")
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def deal_blackjack() -> tuple[list[str], list[str]]:
    deck = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"] * 4
    random.shuffle(deck)
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]
    while blackjack_hand_value(dealer_hand) < 17:
        dealer_hand.append(deck.pop())
    return player_hand, dealer_hand


def find_mission(query: str) -> MissionSpec | None:
    normalized_query = query.casefold().strip()
    return next(
        (
            mission
            for mission in MISSIONS
            if normalized_query in {
                mission.mission_id,
                mission.name.casefold(),
            }
        ),
        None,
    )


def find_hustle(query: str) -> HustleSpec | None:
    normalized_query = query.casefold().strip()
    return next(
        (
            hustle
            for hustle in HUSTLES
            if normalized_query in {
                hustle.mission_id,
                hustle.name.casefold(),
            }
        ),
        None,
    )


async def post_init(application: Application) -> None:
    database = Database(DATABASE_PATH)
    await database.initialize()
    application.bot_data["database"] = database


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, _chat, message = actor
    await message.reply_text(
        f"🎮 Bienvenue dans les rues de Montréal, <b>{escape(actor_info['name'])}</b>!\n"
        "Ici, tu montes ta kichta, tu fais évoluer ta collection de gamos et "
        "tu grimpes les niveaux. Chaque bail est un mini-jeu fictif.\n\n"
        "🚨 Garde l'œil ouvert: un mauvais résultat peut te coûter du cash, "
        "faire monter ton heat ou t'envoyer en taule arcade. Check /prison "
        "si la chance tourne contre toi.\n\n"
        "🛍️ Fais ton style avec /shop\n"
        "⚡ Lance un palier avec /jugg\n"
        "📖 Toutes les commandes: /help\n"
        "💸 Tes stats et ta kichta: /profil",
        parse_mode="HTML",
    )


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        "🎮 Rabza Jugg V2 — commandes disponibles\n\n"
        "👤 Profil et progression\n"
        "/start — démarrer le bot\n"
        "/profil — consulter cash, niveau, XP et statut\n"
        "/bank — consulter la banque et déposer/retirer tout ton cash\n"
        "/deposit <montant/all> — déposer du cash à la banque\n"
        "/withdraw <montant/all> — retirer du cash de la banque\n"
        "/top — classement du groupe\n"
        "/give <montant> — envoyer du cash en répondant à un joueur\n"
        "/give @username <montant> — envoyer du cash à un joueur\n"
        "/prison — consulter sa peine et payer la caution (avocat)\n"
        "/evade — tenter une évasion fictive si tu es en taule\n\n"
        "🎯 Jeux d'arcade\n"
        "/jugg ou /juggling — ouvrir le menu des paliers d'arcade\n"
        "/missions — afficher les missions et les niveaux requis\n"
        "/mission <id> — lancer une mission (ex. /mission civic)\n"
        "/hustle <id> — lancer un défi d'arcade (ex. /hustle colis)\n"
        "/mission_colis — défi de livraison (cooldown 20s, 115$)\n"
        "/mission_phishing — défi de sensibilisation numérique (cooldown 25s, 275$)\n"
        "/mission_catalyseur — défi mécanique (cooldown 30s, 475$)\n"
        "/mission_vapote — défi de kiosque (cooldown 15s, 65$)\n"
        "/mission_depanneur — défi chronométré (cooldown 30s, 900$)\n"
        "/mission_sneakers — défi de collection (cooldown 25s, 400$)\n"
        "/casino — afficher les règles et les jeux\n"
        "/casino blackjack <mise> — jouer au blackjack\n"
        "/casino roulette <choix> <mise> — jouer à la roulette\n\n"
        "🛍️ Autres commandes\n"
        "/shop — Vêtements & Accessoires, véhicules et équipement\n"
        "/inventory — consulter son inventaire\n"
        "/braquer @username — défi PVP fictif (niveau 20+)\n\n"
        "Dans un groupe, tu peux aussi mentionner le bot, par exemple "
        "/casino@Rabza_Jugg_V2_bot ou /help@Rabza_Jugg_V2_bot."
    )


async def profil(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    database = database_for(context)
    player = await database.get_player(actor_info["id"], chat.id)
    if player is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après son enregistrement."
        )

    items = await database.get_inventory(actor_info["id"], chat.id)
    inventory_text = (
        ", ".join(f"{item['name']} ×{item['quantity']}" for item in items)
        if items
        else "vide"
    )
    remaining_seconds = prison_remaining_seconds(player)
    if remaining_seconds:
        status = f"En taule à Bordeaux/RDP — encore {remaining_seconds} s"
    elif player["in_prison_until"]:
        status = "Libre (ta peine est finie, c'est carré)"
    else:
        status = "Libre"

    await message.reply_text(
        f"📍 Profil de {actor_info['name']}\n"
        f"💵 Kob: {player['cash']}$\n"
        f"🏦 Banque: {player['bank']}$\n"
        f"🚘 Gamos: {player['gamos']}\n"
        f"🔥 Heat: {player['heat']}/100\n"
        f"🚨 Recherche: {player['search_level']}/100\n"
        f"⭐ Rang MTL: niveau {player['level']}/{MAX_LEVEL}\n"
        f"✨ XP: {player['xp']} (prochain niveau: "
        f"{xp_to_next_level(player['xp'], player['level'])} XP)\n"
        f"🎒 Inventaire: {inventory_text}\n"
        f"🚔 Statut: {status}"
    )


async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    player = await database_for(context).get_player(actor_info["id"], chat.id)
    if player is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après son enregistrement."
        )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🏦 Déposer tout le cash",
                    callback_data=f"bank:{actor_info['id']}:deposit_all",
                )
            ],
            [
                InlineKeyboardButton(
                    "💵 Retirer tout",
                    callback_data=f"bank:{actor_info['id']}:withdraw_all",
                )
            ],
        ]
    )
    await message.reply_text(
        f"🏦 Banque sécurisée\n"
        f"💵 Cash liquide: {player['cash']}$\n"
        f"🏦 Solde bancaire: {player['bank']}$\n\n"
        "L'argent en banque est protégé contre les pertes de jeu. "
        "Utilise /deposit <montant/all> ou /withdraw <montant/all>.",
        reply_markup=keyboard,
    )


def bank_transfer_text(
    result: dict[str, int | str], deposit: bool
) -> str:
    status = result["status"]
    action = "dépôt" if deposit else "retrait"
    if status == "missing":
        return "Profil introuvable. Tape /start."
    if status == "invalid":
        return "Le montant doit être un entier strictement positif."
    if status == "empty":
        return (
            "Aucun cash liquide à déposer."
            if deposit
            else "Ton compte bancaire est vide."
        )
    if status == "insufficient":
        return "Solde insuffisant pour effectuer ce virement."
    if status != "ok":
        raise RuntimeError(f"Résultat bancaire inattendu: {status!r}")
    return (
        f"✅ {action.capitalize()} de {result['amount']}$ effectué.\n"
        f"💵 Cash: {result['cash']}$ | 🏦 Banque: {result['bank']}$"
    )


async def bank_transfer_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    deposit: bool,
) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    command = "/deposit" if deposit else "/withdraw"
    if len(context.args) != 1:
        await message.reply_text(f"Usage: {command} <montant/all>")
        return
    value = context.args[0].casefold()
    if value == "all":
        amount = None
    elif value.isdecimal():
        amount = int(value)
    else:
        await message.reply_text(f"Usage: {command} <montant/all>")
        return
    result = await database_for(context).move_bank_funds(
        actor_info["id"], chat.id, amount, deposit
    )
    await message.reply_text(bank_transfer_text(result, deposit))


async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await bank_transfer_command(update, context, deposit=True)


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await bank_transfer_command(update, context, deposit=False)


async def handle_bank_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    callback_actor = callback_actor_info(update, query.data, "bank")
    if callback_actor is None:
        return
    actor_info, action = callback_actor
    if action not in {"deposit_all", "withdraw_all"}:
        await edit_callback_message(query, "Cette opération bancaire n'existe pas.")
        return
    actor = await player_context(update, context, actor_info)
    if actor is None:
        return
    chat = actor[1]
    deposit_funds = action == "deposit_all"
    result = await database_for(context).move_bank_funds(
        actor_info["id"], chat.id, None, deposit_funds
    )
    await edit_callback_message(query, bank_transfer_text(result, deposit_funds))


async def jugg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor

    database = database_for(context)
    player = await database.get_player(actor_info["id"], chat.id)
    effects = await database.get_item_effects(actor_info["id"], chat.id)
    if player is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après son enregistrement."
        )
    jail_seconds = prison_remaining_seconds(player)
    if jail_seconds:
        await message.reply_text(
            f"T'es encore à Bordeaux/RDP pour {jail_seconds} seconde(s). "
            "Réessaie quand le compteur tombe à zéro."
        )
        return
    available = actions_available(player["level"], bool(effects["has_obd"]))
    keyboard = [
        [
            InlineKeyboardButton(
                f"{tier.title} — jusqu'à {tier.success_reward}$ "
                f"(+{tier.xp} XP, risque {1 - tier.success_chance:.0%}, "
                f"cooldown {tier.cooldown_seconds}s)",
                callback_data=f"jugg:{actor_info['id']}:{tier.tier_id}",
            )
        ]
        for tier in JUGG_TIERS
    ] + [
        [
            InlineKeyboardButton(
                spec.title,
                callback_data=f"game:{actor_info['id']}:{action_id}",
            )
        ]
        for action_id, spec in available
    ]
    locked_specs = [
        spec for spec in ACTION_SPECS.values()
        if player["level"] < spec.min_level
        or (spec.requires_obd and not effects["has_obd"])
    ]
    if locked_specs:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🔒 Débloque d'autres moves: /profil et /shop",
                    callback_data=f"game:{actor_info['id']}:locked",
                )
            ]
        )
    await message.reply_text(
        f"Choisis ton défi d'arcade, chef — niveau {player['level']}.\n"
        "Paliers Business: mini-jeux fictifs, récompenses fixes et risques "
        "simulés. Les anciens défis d'arcade restent disponibles:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_jugg_tier(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    callback_actor = callback_actor_info(update, query.data, "jugg")
    if callback_actor is None:
        return
    actor_info, tier_id = callback_actor
    actor = await player_context(update, context, actor_info)
    if actor is None:
        return
    actor_info, chat = actor[0], actor[1]
    tier = next((item for item in JUGG_TIERS if item.tier_id == tier_id), None)
    if tier is None:
        await edit_callback_message(query, "Ce palier de jugg n'existe pas.")
        return

    outcome = resolve_jugg(tier_id)
    database = database_for(context)
    effects = await database.get_item_effects(actor_info["id"], chat.id)
    xp_gain = (
        xp_with_bonus(tier.xp, int(effects["xp_bonus"]))
        if outcome.success
        else 0
    )
    applied = await database.apply_jugg_result(
        actor_info["id"],
        chat.id,
        tier.cooldown_seconds,
        outcome.cash_delta,
        outcome.heat_delta,
        outcome.prison_seconds,
        xp_gain,
    )
    status = applied["status"]
    if status == "missing":
        await edit_callback_message(query, "Profil introuvable. Tape /start.")
        return
    if status == "prison":
        await edit_callback_message(
            query,
            f"🚔 {actor_info['name']}, il te reste "
            f"{applied['remaining']} seconde(s) de pause arcade.",
        )
        return
    if status == "cooldown":
        remaining = int(applied["remaining"])
        unit = "seconde" if remaining == 1 else "secondes"
        await edit_callback_message(
            query,
            f"⏳ {actor_info['name']}, ton prochain bail arcade est "
            f"disponible dans {remaining} {unit}.",
        )
        return

    cash_delta = int(applied["cash_delta"])
    heat_delta = int(applied["heat_delta"])
    formatted_result = outcome.message
    if not outcome.success and outcome.prison_seconds == 0:
        formatted_result = formatted_result.replace(
            f"{tier.failure_fine}$", f"{-cash_delta}$"
        )
    await edit_callback_message(
        query,
        f"🎮 {tier.title}\n"
        f"{formatted_result}\n"
        f"💵 Cash: {cash_delta:+d}$ | Solde: {applied['balance']}$\n"
        f"✨ XP: +{applied['xp_gain']}\n"
        f"🔥 Heat: {heat_delta:+d} ({applied['heat']}/100) | "
        f"Recherche: {applied['search_level']}/100\n"
        f"⏱️ Prochain palier dans {applied['cooldown_seconds']}s."
        + level_up_notice(
            int(applied["previous_level"]), int(applied["level"])
        ),
    )


async def handle_game_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    callback_actor = callback_actor_info(update, query.data, "game")
    if callback_actor is None:
        return
    actor_info, action = callback_actor
    actor = await player_context(update, context, actor_info)
    if actor is None:
        return
    actor_info, chat = actor[0], actor[1]

    allowed_actions = set(ACTION_SPECS)
    if action not in allowed_actions:
        await edit_callback_message(query, "Ce move-là n'existe pas, chef.")
        return

    database = database_for(context)
    player = await database.get_player(actor_info["id"], chat.id)
    effects = await database.get_item_effects(actor_info["id"], chat.id)
    if player is None:
        raise RuntimeError(f"Le joueur {actor_info['id']} est introuvable.")
    spec = ACTION_SPECS[action]
    jail_seconds = prison_remaining_seconds(player)
    if jail_seconds:
        await edit_callback_message(
            query,
            f"T'es encore à Bordeaux/RDP pour {jail_seconds} seconde(s). "
            "Réessaie quand le compteur tombe à zéro.",
        )
        return
    if player["level"] < spec.min_level:
        await edit_callback_message(
            query,
            f"Move verrouillé: niveau {spec.min_level} requis; "
            f"t'es niveau {player['level']}. Continue à gagner de l'XP.",
        )
        return
    if spec.requires_obd and not effects["has_obd"]:
        await edit_callback_message(
            query, "Le défi gamos est verrouillé: le boîtier OBD fictif est requis. /shop"
        )
        return

    suspense_message = f"🎮 Défi d'arcade en cours: {spec.title}... Le suspense est à son max."

    await edit_callback_message(query, suspense_message)
    await asyncio.sleep(2)

    result = resolve_action(
        action,
        success_bonus=float(effects["success_bonus"]),
    )
    xp_gain = (
        xp_with_bonus(result.xp_gain, int(effects["xp_bonus"]))
        if result.success
        else 0
    )
    prison_until = (
        prison_deadline(result.prison_seconds)
        if result.prison_seconds is not None
        else None
    )
    applied = await database.apply_action_result(
        actor_info["id"],
        result.cash_delta,
        result.heat_delta,
        result.gamos,
        prison_until,
        xp_gain,
        chat_id=chat.id,
    )
    if not applied:
        await edit_callback_message(
            query,
            "T'es déjà en taule à Bordeaux/RDP, chef. "
            "Check /prison avant de retenter un jugg."
        )
        return
    updated_player = await database.get_player(actor_info["id"], chat.id)
    if updated_player is None:
        raise RuntimeError(f"Le joueur {actor_info['id']} est introuvable.")
    await edit_callback_message(
        query,
        f"{result.message}"
        + (
            f"\n✨ +{xp_gain} XP total."
            if result.success
            else ""
        )
        + level_up_notice(int(player["level"]), int(updated_player["level"]))
        + "\n\nPour voir tes stats: /profil"
    )


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, message = actor[0], actor[2]
    keyboard = [
        [
            InlineKeyboardButton(
                SHOP_CATEGORIES[category],
                callback_data=f"shop:{actor_info['id']}:{category}",
            )
        ]
        for category in SHOP_CATEGORIES
    ]
    await message.reply_text(
        "🛍️ Boutique MTL — Vêtements & Accessoires, véhicules et équipement "
        "de jeu. Choisis une catégorie:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    if data.startswith("buy:"):
        namespace = "buy"
    else:
        namespace = "shop"
    callback_actor = callback_actor_info(update, data, namespace)
    if callback_actor is None:
        return
    actor_info, payload = callback_actor
    actor = await player_context(update, context, actor_info)
    if actor is None:
        return
    actor_info, chat = actor[0], actor[1]
    database = database_for(context)

    if data.startswith("shop:") and payload != "home":
        category = payload
        if category not in SHOP_CATEGORIES:
            await edit_callback_message(query, "Cette catégorie n'existe pas.")
            return
        player = await database.get_player(actor_info["id"], chat.id)
        if player is None:
            raise RuntimeError(f"Le joueur {actor_info['id']} est introuvable.")
        category_items = [item for item in ITEMS if item.category == category]
        keyboard = [
            [
                InlineKeyboardButton(
                    f"{item.name} — {item.price}$ (niv. {item.min_level})",
                    callback_data=f"buy:{actor_info['id']}:{item.item_id}",
                )
            ]
            for item in category_items
        ]
        keyboard.append(
            [
                InlineKeyboardButton(
                    "↩️ Catégories",
                    callback_data=f"shop:{actor_info['id']}:home",
                )
            ]
        )
        item_details = "\n\n".join(
            f"• {item.name} — {item.price}$ (niveau {item.min_level})\n"
            f"{item.description or 'Objet de style et de progression en jeu.'}\n"
            f"Bonus: +{item.xp_bonus}% XP, "
            f"+{item.success_bonus:.0%} réussite"
            for item in category_items
        )
        await edit_callback_message(
            query,
            f"🛍️ {SHOP_CATEGORIES[category]}\n"
            f"Niveau: {player['level']} | Solde: {player['cash']}$\n\n"
            f"{item_details}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("shop:") and payload == "home":
        keyboard = [
            [
                InlineKeyboardButton(
                    SHOP_CATEGORIES[category],
                    callback_data=f"shop:{actor_info['id']}:{category}",
                )
            ]
            for category in SHOP_CATEGORIES
        ]
        await edit_callback_message(
            query,
            "Boutique MTL — Vêtements & Accessoires et équipement de jeu. "
            "Choisis une catégorie:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("buy:"):
        item_id = payload
        item = item_by_id(item_id)
        if item is None:
            await edit_callback_message(query, "Cet item n'existe pas.")
            return
        outcome = await database.purchase_item(actor_info["id"], item_id, chat.id)
        if outcome == "ok":
            player = await database.get_player(actor_info["id"], chat.id)
            text = (
                f"✅ {item.name} ajouté à ton inventaire! "
                f"Kob restant: {player['cash']}$.\n{item.description}"
            )
        elif outcome == "level":
            text = f"Niveau {item.min_level} requis pour {item.name}."
        elif outcome == "cash":
            text = f"Pas assez de kob pour {item.name}."
        else:
            text = "Achat impossible: joueur ou item introuvable."
        await edit_callback_message(
            query,
            text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"↩️ {SHOP_CATEGORIES[item.category]}",
                            callback_data=f"shop:{actor_info['id']}:{item.category}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Toutes les catégories",
                            callback_data=f"shop:{actor_info['id']}:home",
                        )
                    ],
                ]
            ),
        )
        return

    await edit_callback_message(query, "Action de marketplace invalide.")


async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    database = database_for(context)
    items = await database.get_inventory(actor_info["id"], chat.id)
    if not items:
        await message.reply_text("Ton inventaire est vide. Fais /shop pour magasiner.")
        return

    categories: dict[str, list[str]] = {}
    for item in items:
        categories.setdefault(item["category"], []).append(
            f"• {item['name']} ×{item['quantity']} — {item['description']}"
        )
    lines = ["🎒 Inventaire MTL (effets fictifs):"]
    for category, entries in categories.items():
        lines.append(f"\n{SHOP_CATEGORIES.get(category, category.title())}:")
        lines.extend(entries)

    effects = await database.get_item_effects(actor_info["id"], chat.id)
    lines.append(
        f"\nBonus XP max Vêtements & Accessoires: +{effects['xp_bonus']}%"
    )
    lines.append(f"Bonus de réussite abstrait: +{effects['success_bonus']:.0%}")
    lines.append(
        "Boîtier OBD de jeu: "
        + ("oui" if effects["has_obd"] else "non")
    )
    await message.reply_text("\n".join(lines))


async def braquer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    if chat.type not in {"group", "supergroup"}:
        await message.reply_text(
            "❌ Les braquages entre joueurs se font uniquement dans les groupes !"
        )
        return
    database = database_for(context)
    attacker = await database.get_player(actor_info["id"], chat.id)
    if attacker is None:
        raise RuntimeError(f"Le joueur {actor_info['id']} est introuvable.")
    jail_seconds = prison_remaining_seconds(attacker)
    if jail_seconds:
        await message.reply_text(
            f"T'es encore à Bordeaux pour {jail_seconds} seconde(s). "
            "Le défi PVP sera dispo quand ta peine sera finie."
        )
        return
    if attacker["level"] < 20:
        await message.reply_text("Le défi PVP est verrouillé jusqu'au niveau 20.")
        return
    if len(context.args) != 1 or not context.args[0].startswith("@"):
        await message.reply_text("Usage: /braquer @username (défi fictif entre joueurs)")
        return

    victim = await database.get_player_by_username(context.args[0], chat.id)
    if victim is None:
        await message.reply_text(
            "Joueur introuvable. Il doit avoir lancé /start et avoir un username Telegram."
        )
        return
    if victim["user_id"] == actor_info["id"]:
        await message.reply_text("Tu peux pas lancer un défi contre toi-même.")
        return

    attacker_effects = await database.get_item_effects(actor_info["id"], chat.id)
    victim_effects = await database.get_item_effects(victim["user_id"], chat.id)
    result = resolve_pvp(
        attacker_has_weapon=bool(attacker_effects["has_weapon"]),
        victim_has_weapon=bool(victim_effects["has_weapon"]),
        victim_has_armor=bool(victim_effects["has_armor"]),
    )
    deadline = (
        prison_deadline(result.prison_seconds)
        if result.prison_seconds
        else None
    )
    applied = await database.apply_pvp_result(
        actor_info["id"],
        victim["user_id"],
        result.outcome,
        result.fine,
        deadline,
        xp_with_bonus(result.xp_gain, int(attacker_effects["xp_bonus"])),
        result.transfer_rate,
        chat_id=chat.id,
    )
    if applied is None:
        await message.reply_text("Défi PVP impossible: cible invalide.")
    elif applied["status"] == "level":
        await message.reply_text("Le défi PVP est réservé aux joueurs niveau 20+.")
    elif applied["status"] == "prison":
        await message.reply_text("T'es en taule; aucun défi PVP possible pour le moment.")
    elif applied["status"] == "counter":
        await message.reply_text(
            f"💥 Riposte de jeu! @{victim['username']} bloque le braquage avec "
            f"son équipement "
            f"({victim_effects['armor_name'] or victim_effects['weapon_name'] or 'de défense'}). "
            f"Tu prends {PRISON_DURATION_SECONDS} secondes à Bordeaux et verses "
            f"{applied['fine']}$ à "
            f"@{victim['username']}."
        )
    elif applied["status"] == "success":
        await message.reply_text(
            f"🎯 Tu as braqué @{victim['username']} dans le jeu et tu es reparti "
            f"avec {applied['amount']}$ ({applied['rate']:.0%} du cash) ! "
            f"+{applied['xp']} XP. Niveau actuel: {applied['level']}."
            + level_up_notice(int(attacker["level"]), int(applied["level"]))
        )
    else:
        await message.reply_text(
            f"{result.message} Direction Bordeaux pour {PRISON_DURATION_SECONDS} secondes. "
            "Aucun cash n'a changé de mains."
        )


async def give_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    actor = await player_context(update, context, ensure_player=False)
    if actor is None:
        return
    actor_info, chat, message = actor
    database = database_for(context)
    reply_to = message.reply_to_message

    recipient_id: int
    recipient_name: str
    if len(context.args) == 1 and reply_to is not None:
        amount_text = context.args[0]
        recipient_user = reply_to.from_user
        recipient_chat = reply_to.sender_chat
        if recipient_user is not None and not recipient_user.is_bot:
            recipient_id = recipient_user.id
            recipient_name = recipient_user.full_name
        elif recipient_chat is not None:
            recipient_id = recipient_chat.id
            recipient_name = recipient_chat.title or "Admin anonyme"
        elif recipient_user is not None:
            await message.reply_text(
                "❌ Tu peux seulement envoyer du cash à un joueur."
            )
            return
        else:
            await message.reply_text(
                "❌ Destinataire introuvable. Réponds au message d'un joueur."
            )
            return
    elif len(context.args) == 2 and context.args[0].startswith("@"):
        username = context.args[0][1:]
        amount_text = context.args[1]
        if not username:
            await message.reply_text(
                "Usage: /give <montant> en réponse à un joueur, "
                "ou /give @username <montant>."
            )
            return
        recipient = await database.get_player_by_username(username, chat.id)
        if recipient is None:
            await message.reply_text(
                "❌ Joueur introuvable. Il doit être inscrit dans ce groupe avec "
                "un username Telegram."
            )
            return
        recipient_id = int(recipient["user_id"])
        recipient_name = str(recipient.get("username") or username)
    else:
        await message.reply_text(
            "Usage: /give <montant> en réponse à un joueur, "
            "ou /give @username <montant>."
        )
        return

    if not amount_text.isdecimal():
        await message.reply_text("❌ Le montant doit être un entier positif.")
        return
    try:
        amount = int(amount_text)
    except ValueError:
        await message.reply_text("❌ Le montant indiqué est invalide.")
        return
    if amount <= 0:
        await message.reply_text("❌ Le montant doit être supérieur à 0$.")
        return
    if recipient_id == actor_info["id"]:
        await message.reply_text("❌ Tu ne peux pas t'envoyer du cash à toi-même.")
        return

    result = await database.transfer_cash(
        actor_info["id"], recipient_id, amount, chat.id
    )
    status = result["status"]
    if status == "sender_missing":
        await message.reply_text("❌ Ton profil est introuvable. Tape /start d'abord.")
        return
    if status == "recipient_missing":
        await message.reply_text(
            "❌ Ce joueur n'est pas inscrit dans ce groupe. "
            "Il doit d'abord utiliser /start."
        )
        return
    if status == "insufficient":
        await message.reply_text(
            f"❌ Pas assez de cash: tu as {result['sender_balance']}$ "
            f"et tu veux envoyer {amount}$."
        )
        return
    if status != "ok":
        await message.reply_text("❌ Le virement n'a pas pu être effectué.")
        return

    await message.reply_text(
        f"💸 Virement effectué ! Tu as envoyé {amount}$ à "
        f"{escape(recipient_name)}.\n"
        f"💰 Ton nouveau solde: {result['sender_balance']}$",
        parse_mode="HTML",
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    chat, message = actor[1], actor[2]
    players = await database_for(context).get_top_players(chat_id=chat.id)
    if not players:
        await message.reply_text("Y'a personne dans le classement pour l'instant.")
        return

    lines = ["🏆 Top 10 des plus grosses kichtas de Montréal:"]
    for rank, player in enumerate(players, start=1):
        username = player["username"] or "Joueur sans blaze"
        lines.append(f"{rank}. {username} — {player['cash']}$")
    await message.reply_text("\n".join(lines))


async def prison(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    database = database_for(context)
    player = await database.get_player(actor_info["id"], chat.id)
    if player is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après son enregistrement."
        )
    prison_until = player["in_prison_until"]
    remaining_seconds = prison_remaining_seconds(player)
    if not prison_until:
        await message.reply_text("T'es libre, chef. Pas de cellule pour toi, c'est carré.")
        return

    if remaining_seconds <= 0:
        await message.reply_text(
            "Ta peine est finie, t'es libre! Retour dans les rues de Montréal."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                f"Payer la caution ({BAIL_COST}$)",
                callback_data=f"bail:{actor_info['id']}:pay",
            )
        ]
    ]
    await message.reply_text(
        f"T'es encore à Bordeaux/RDP pour {remaining_seconds} seconde(s). "
        f"T'as {player['cash']}$; la caution coûte {BAIL_COST}$. "
        "Un avocat peut payer la caution; sinon attends ou tente /evade "
        "(issue aléatoire, 100% dans le jeu).",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def evade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    success = random.random() < 0.25
    result, remaining, search_level = await database_for(context).try_escape(
        actor_info["id"], chat.id, success
    )
    if result == "free":
        await message.reply_text("T'es déjà libre, chef. /prison pour vérifier.")
        return
    if result == "expired":
        await message.reply_text("Ta peine est finie. Retour dans le quartier!")
        return
    if result == "missing":
        await message.reply_text("Ton profil n'est pas encore créé. Tape /start!")
        return
    if result == "escaped":
        await message.reply_text(
            "🗝️ Évasion réussie — scénario fictif! T'es libre, "
            "mais le quartier reste risqué. /profil"
        )
        return
    await message.reply_text(
        f"🚨 Évasion ratée dans le jeu: +30 secondes de peine "
        f"({remaining}s restantes) et recherche à {search_level}/100."
    )


async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context, ensure_player=False)
    if actor is None:
        return
    actor_info, chat, message = actor
    database = database_for(context)
    args = context.args
    player = await database.get_player(actor_info["id"], chat.id)
    if player is None:
        await message.reply_text("Ton profil n'est pas encore créé. Tape /start!")
        return

    if not args:
        lines = [
            "🚘 Missions d'arcade — tout est fictif\n",
            f"Ton niveau: {player['level']}. Choisis une mission avec "
            "/mission <id>.",
        ]
        lines.extend(
            f"\n{'✅ Disponible' if player['level'] >= mission.min_level else '🔒 Verrouillée'}"
            f" — niv. {mission.min_level} — {mission.name} "
            f"({mission.mission_id})\n"
            f"Réussite: {mission.success_chance:.0%} | "
            f"Gain: {mission.reward}$ | XP: {mission.xp}"
            for mission in MISSIONS
        )
        lines.extend(
            f"\n💼 {hustle.name} (/hustle {hustle.mission_id} ou "
            f"/mission_{hustle.mission_id})\n"
            f"Réussite: {hustle.success_chance:.0%} | "
            f"Gain fixe: {hustle.success_reward}$ | XP: {hustle.xp} | "
            f"Échec: -{hustle.failure_penalty}$ | "
            f"Cooldown: {hustle.cooldown_seconds}s"
            for hustle in HUSTLES
        )
        await message.reply_text("\n".join(lines))
        return

    query = " ".join(args)
    hustle = find_hustle(query)
    if hustle is not None:
        await run_hustle(actor_info, chat.id, message, database, hustle)
        return

    mission = find_mission(query)
    if mission is None:
        await message.reply_text(
            "Mission inconnue. Utilise /missions pour voir les missions et leurs IDs."
        )
        return

    player = await database.get_player(actor_info["id"], chat.id)
    if player is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après son enregistrement."
        )
    jail_seconds = prison_remaining_seconds(player)
    if jail_seconds:
        await message.reply_text(
            f"{actor_info['name']}, tu es encore en taule pour "
            f"{jail_seconds} seconde(s)."
        )
        return
    if player["level"] < mission.min_level:
        await message.reply_text(
            f"Mission verrouillée: niveau {mission.min_level} requis. "
            f"Tu es niveau {player['level']}."
        )
        return

    succeeded = random.random() < mission.success_chance
    if succeeded:
        effects = await database.get_item_effects(actor_info["id"], chat.id)
        xp_gain = xp_with_bonus(mission.xp, int(effects["xp_bonus"]))
        applied = await database.apply_action_result(
            actor_info["id"],
            mission.reward,
            0,
            None,
            None,
            xp_gain=xp_gain,
            chat_id=chat.id,
        )
    else:
        fine_limit = min(250, int(player["cash"]))
        fine = random.randint(1, fine_limit) if fine_limit else 0
        jailed = random.random() < 0.20
        applied = await database.apply_action_result(
            actor_info["id"],
            -fine,
            0,
            None,
            prison_deadline(PRISON_DURATION_SECONDS) if jailed else None,
            chat_id=chat.id,
        )

    if not applied:
        latest_player = await database.get_player(actor_info["id"], chat.id)
        if latest_player is None:
            raise RuntimeError(
                f"Le joueur {actor_info['id']} a disparu pendant la mission."
            )
        await message.reply_text(
            f"{actor_info['name']}, la mission n'a pas démarré: tu es "
            f"maintenant en taule pour "
            f"{prison_remaining_seconds(latest_player)} seconde(s)."
        )
        return

    updated_player = await database.get_player(actor_info["id"], chat.id)
    if updated_player is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après la mission."
        )
    if succeeded:
        await message.reply_text(
            f"🏁💨 Mission réussie, {actor_info['name']}!\n"
            f"🚘 {mission.name}\n"
            f"💵 +{mission.reward}$ | ✨ +{updated_player['xp'] - player['xp']} XP\n"
            f"⭐ Niveau: {updated_player['level']} | "
            f"Solde: {updated_player['cash']}$"
            + level_up_notice(int(player["level"]), int(updated_player["level"]))
        )
        return

    if jailed:
        penalty = (
            f"🚔 Tu prends {PRISON_DURATION_SECONDS} secondes de taule "
            "dans le jeu."
        )
    else:
        penalty = "Tu as réussi à filer, sans taule."
    await message.reply_text(
        f"🚨 Mission échouée, {actor_info['name']}.\n"
        f"🚘 {mission.name}\n"
        f"💸 Amende: -{fine}$ | {penalty}\n"
        f"Solde: {updated_player['cash']}$"
    )


async def run_hustle(
    actor_info: ActorInfo,
    chat_id: int,
    message: Message,
    database: Database,
    hustle: HustleSpec,
) -> None:
    """Resolve a fictional hustle and atomically apply its cooldown and outcome."""
    player = await database.get_player(actor_info["id"], chat_id)
    if player is None:
        await message.reply_text("Ton profil n'est pas encore créé. Tape /start!")
        return
    succeeded = random.random() < hustle.success_chance
    if succeeded:
        cash_delta = hustle.success_reward
        search_delta = 0
    else:
        cash_delta = -hustle.failure_penalty
        search_delta = 1

    effects = await database.get_item_effects(actor_info["id"], chat_id)
    xp_reward = (
        xp_with_bonus(hustle.xp, int(effects["xp_bonus"]))
        if succeeded
        else 0
    )
    status, remaining, balance, _search_level, applied_cash_delta = (
        await database.apply_hustle_result(
            actor_info["id"],
            chat_id,
            hustle.cooldown_seconds,
            cash_delta,
            search_delta,
            xp_gain=xp_reward,
        )
    )
    if status == "missing":
        await message.reply_text("Ton profil n'est pas encore créé. Tape /start!")
        return
    if status == "prison":
        await message.reply_text(
            f"🚔 {actor_info['name']}, tu es encore en taule pour {remaining}s."
        )
        return
    if status == "cooldown":
        time_unit = "seconde" if remaining == 1 else "secondes"
        await message.reply_text(
            f"⏳ {actor_info['name']}, le quartier a besoin d'une pause. "
            f"Réessaie dans {remaining} {time_unit}."
        )
        return

    updated_player = await database.get_player(actor_info["id"], chat_id)
    if updated_player is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après le hustle."
        )
    heat_delta = int(updated_player["heat"]) - int(player["heat"])
    search_delta = int(updated_player["search_level"]) - int(
        player["search_level"]
    )
    if succeeded:
        story = hustle.success_message.format(cash=applied_cash_delta)
    else:
        story = (
            f"❌ Le défi échoue: l'arcade applique une pénalité fixe de "
            f"<b>{-applied_cash_delta}$</b>."
        )
    xp_gain = max(0, int(updated_player["xp"]) - int(player["xp"]))
    await message.reply_text(
        f"💼 <b>{escape(hustle.name)}</b>\n"
        f"Salut {escape(actor_info['name'])}, le quartier n'attend pas!\n\n"
        f"{story}\n"
        f"💵 Cash: {applied_cash_delta:+d}$ | "
        f"✨ XP: +{xp_gain}\n"
        f"🔥 Heat: {heat_delta:+d} ({updated_player['heat']}/100) | "
        f"🚨 Recherche: {search_delta:+d} "
        f"({updated_player['search_level']}/100)\n"
        f"💰 Solde: {balance}$\n"
        f"⏱️ Prochain hustle dans {hustle.cooldown_seconds}s."
        + level_up_notice(int(player["level"]), int(updated_player["level"])),
        parse_mode="HTML",
    )


async def hustle_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    hustle: HustleSpec,
) -> None:
    actor = await player_context(update, context, ensure_player=False)
    if actor is None:
        return
    actor_info, chat, message = actor
    await run_hustle(actor_info, chat.id, message, database_for(context), hustle)


async def hustle_alias(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await missions(update, context)


async def mission_colis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await hustle_command(update, context, HUSTLES[0])


async def mission_phishing(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await hustle_command(update, context, HUSTLES[1])


async def mission_catalyseur(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await hustle_command(update, context, HUSTLES[2])


async def mission_vapote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await hustle_command(update, context, HUSTLES[3])


async def mission_depanneur(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await hustle_command(update, context, HUSTLES[4])


async def mission_sneakers(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await hustle_command(update, context, HUSTLES[5])


async def casino(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    args = context.args
    if not args:
        await message.reply_text(
            "🎰 Casino de Montréal — jeux fictifs\n\n"
            "Jeux disponibles:\n"
            "• Blackjack: /casino blackjack <mise>\n"
            "• Roulette: /casino roulette <rouge|noir|pair|impair|0-36> <mise>\n\n"
            "La mise doit être un entier positif et ne peut pas dépasser ton solde. "
            "Une victoire couleur/parité ou au blackjack rapporte la mise en gain "
            "net (la mise est également rendue). Un numéro exact rapporte 35× la "
            "mise en gain net. Une égalité au blackjack rembourse la mise."
        )
        return

    game_name = args[0].casefold()
    if game_name == "blackjack":
        if len(args) != 2:
            await message.reply_text("Usage: /casino blackjack <mise>")
            return
        choice = ""
        stake_text = args[1]
    elif game_name == "roulette":
        if len(args) != 3:
            await message.reply_text(
                "Usage: /casino roulette <rouge|noir|pair|impair|0-36> <mise>"
            )
            return
        choice = args[1].casefold()
        stake_text = args[2]
        if choice not in {"rouge", "noir", "pair", "impair"}:
            try:
                number = int(choice)
            except ValueError:
                await message.reply_text(
                    "Choix invalide. Mise sur rouge, noir, pair, impair ou un "
                    "nombre de 0 à 36."
                )
                return
            if not 0 <= number <= 36:
                await message.reply_text("Le numéro doit être compris entre 0 et 36.")
                return
            choice = str(number)
    else:
        await message.reply_text(
            "Jeu inconnu. Choisis blackjack ou roulette avec /casino."
        )
        return

    if not stake_text.isdecimal():
        await message.reply_text("La mise doit être un entier positif, par exemple 500.")
        return
    stake = int(stake_text)
    if stake <= 0:
        await message.reply_text("La mise doit être supérieure à 0$.")
        return

    database = database_for(context)
    balance = await database.get_player_balance(actor_info["id"], chat.id)
    if balance is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après son enregistrement."
        )
    if stake > balance:
        await message.reply_text(
            f"{actor_info['name']}, ta mise ({stake}$) dépasse ton solde "
            f"({balance}$)."
        )
        return
    player_before = await database.get_player(actor_info["id"], chat.id)
    if player_before is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable avant le pari."
        )
    effects = await database.get_item_effects(actor_info["id"], chat.id)

    net_change = -stake
    if game_name == "blackjack":
        player_hand, dealer_hand = deal_blackjack()
        player_total = blackjack_hand_value(player_hand)
        dealer_total = blackjack_hand_value(dealer_hand)
        player_blackjack = len(player_hand) == 2 and player_total == 21
        dealer_blackjack = len(dealer_hand) == 2 and dealer_total == 21
        if player_blackjack and not dealer_blackjack:
            net_change = stake * 3 // 2
            outcome = f"Blackjack! Tu gagnes {net_change}$ net."
        elif dealer_blackjack and not player_blackjack:
            outcome = "Le croupier a un blackjack. Tu perds ta mise."
        elif player_total > 21:
            outcome = "Tu dépasses 21. Tu perds ta mise."
        elif dealer_total > 21 or player_total > dealer_total:
            net_change = stake
            outcome = f"Tu remportes la manche: +{net_change}$ net."
        elif player_total == dealer_total:
            net_change = 0
            outcome = "Égalité: ta mise est remboursée."
        else:
            outcome = "Le croupier gagne. Tu perds ta mise."

        result_text = (
            f"🃏 Blackjack pour {actor_info['name']}\n"
            f"Tes cartes: {', '.join(player_hand)} ({player_total})\n"
            f"Croupier: {', '.join(dealer_hand)} ({dealer_total})\n"
            f"Mise: {stake}$\n{outcome}"
        )
    else:
        result_number = random.randint(0, 36)
        if result_number == 0:
            result_color = "vert"
        elif result_number in ROULETTE_RED_NUMBERS:
            result_color = "rouge"
        else:
            result_color = "noir"

        if choice in {"rouge", "noir"}:
            won = result_number != 0 and choice == result_color
            multiplier = 1
        elif choice in {"pair", "impair"}:
            won = result_number != 0 and (
                "pair" if result_number % 2 == 0 else "impair"
            ) == choice
            multiplier = 1
        else:
            won = result_number == int(choice)
            multiplier = 35

        if won:
            net_change = stake * multiplier
            outcome = f"Tu gagnes {net_change}$ net!"
        else:
            outcome = "La bille n'est pas tombée sur ton choix: tu perds ta mise."
        result_text = (
            f"🎰 Roulette pour {actor_info['name']}\n"
            f"Résultat: {result_number} ({result_color})\n"
            f"Ton choix: {choice}\n"
            f"Mise: {stake}$\n{outcome}"
        )

    new_balance = await database.settle_casino_bet(
        actor_info["id"],
        chat.id,
        stake,
        net_change,
        xp_gain=(
            xp_with_bonus(CASINO_WIN_XP, int(effects["xp_bonus"]))
            if net_change > 0
            else 0
        ),
    )
    if new_balance is None:
        current_balance = await database.get_player_balance(actor_info["id"], chat.id)
        if current_balance is None:
            raise RuntimeError(
                f"Le joueur {actor_info['id']} a disparu pendant le règlement du pari."
            )
        await message.reply_text(
            f"Solde insuffisant au moment du règlement: {current_balance}$ "
            f"disponibles pour une mise de {stake}$."
        )
        return

    player_after = await database.get_player(actor_info["id"], chat.id)
    if player_after is None:
        raise RuntimeError(
            f"Le joueur {actor_info['id']} est introuvable après le pari."
        )
    xp_gain = max(0, int(player_after["xp"]) - int(player_before["xp"]))
    await message.reply_text(
        f"{result_text}\n💵 Nouveau solde: {new_balance}$."
        + (f"\n✨ +{xp_gain} XP" if xp_gain else "")
        + level_up_notice(int(player_before["level"]), int(player_after["level"]))
    )


async def missions_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await missions(update, context)


async def mission_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await missions(update, context)


async def casino_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await casino(update, context)


async def pay_bail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    callback_actor = callback_actor_info(update, query.data, "bail")
    if callback_actor is None or callback_actor[1] != "pay":
        return
    actor_info = callback_actor[0]
    actor = await player_context(update, context, actor_info)
    if actor is None:
        return
    actor_info, chat = actor[0], actor[1]
    released = await database_for(context).pay_bail(
        actor_info["id"], BAIL_COST, chat.id
    )
    if released:
        await edit_callback_message(
            query,
            f"Caution payée: -{BAIL_COST}$. T'es libre, c'est carré. "
            "Va checker ton kob avec /profil."
        )
    else:
        await edit_callback_message(
            query,
            "La caution peut pas passer: soit ta peine est finie, "
            "soit t'as pas assez de kob. Check /prison et /profil."
        )


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.error(
        "Erreur inattendue lors du traitement de la mise à jour",
        exc_info=context.error,
    )
    if isinstance(update, Update) and update.effective_message is not None:
        await update.effective_message.reply_text(
            "Oups, le bot a eu un pépin. Réessaie dans un instant."
        )


def main() -> None:
    validate_config()
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profil", profil))
    application.add_handler(CommandHandler("bank", bank))
    application.add_handler(CommandHandler("deposit", deposit))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CommandHandler("give", give_command))
    application.add_handler(CommandHandler("jugg", jugg))
    application.add_handler(CommandHandler("juggling", jugg))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("inventory", inventory))
    application.add_handler(CommandHandler("braquer", braquer))
    application.add_handler(CommandHandler("top", top))
    application.add_handler(CommandHandler("prison", prison))
    application.add_handler(CommandHandler("evade", evade))
    application.add_handler(CommandHandler("missions", missions_command))
    application.add_handler(CommandHandler("mission", mission_command))
    application.add_handler(CommandHandler("hustle", hustle_alias))
    application.add_handler(CommandHandler("mission_colis", mission_colis))
    application.add_handler(CommandHandler("mission_phishing", mission_phishing))
    application.add_handler(CommandHandler("mission_catalyseur", mission_catalyseur))
    application.add_handler(CommandHandler("mission_vapote", mission_vapote))
    application.add_handler(CommandHandler("mission_depanneur", mission_depanneur))
    application.add_handler(CommandHandler("mission_sneakers", mission_sneakers))
    application.add_handler(CommandHandler("casino", casino_command))
    application.add_handler(
        CallbackQueryHandler(
            handle_bank_callback,
            pattern=r"^bank:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_jugg_tier,
            pattern=r"^jugg:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_game_action,
            pattern=r"^game:",
        )
    )
    application.add_handler(
        CallbackQueryHandler(shop_callback, pattern=r"^(shop:|buy:)")
    )
    application.add_handler(CallbackQueryHandler(pay_bail, pattern=r"^bail:"))
    application.add_error_handler(error_handler)
    logger.info("Rabza Jugg V2 démarre.")
    application.run_polling()


if __name__ == "__main__":
    main()
