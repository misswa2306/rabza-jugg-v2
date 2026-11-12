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
    VEHICLE_RENTAL_TERMS,
    HustleSpec,
    ShopItem,
    actions_available,
    item_by_id,
    prison_deadline,
    resolve_action,
    resolve_jugg,
    resolve_pvp,
    resolve_shootout,
    xp_with_bonus,
    xp_to_next_level,
    vehicle_rental_quote,
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
    MissionSpec("civic", "Mission logistique urbaine", 1, 100, 20, 0.85),
    MissionSpec(
        "audi",
        "Coordination de flotte — secteur Sainte-Catherine",
        3,
        350,
        50,
        0.70,
    ),
    MissionSpec(
        "entrepot",
        "Gestion de stock — secteur portuaire",
        5,
        900,
        120,
        0.55,
    ),
    MissionSpec(
        "blindee",
        "Audit des flux et sécurisation logistique",
        7,
        2500,
        250,
        0.40,
    ),
    MissionSpec(
        "helico",
        "Coordination de transport — niveau expert",
        10,
        7500,
        600,
        0.25,
    ),
)

LONG_DISTANCE_MISSION_IDS = {"audi", "entrepot", "blindee", "helico"}

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
        "/start — créer ton profil et rejoindre le quartier\n"
        "/help — afficher toutes les commandes et leurs usages\n"
        "/profil — consulter cash, banque, niveau, XP et statut\n"
        "/inventory — voir tes articles, bonus et location active\n"
        "/top — afficher le classement des joueurs de cette conversation\n"
        "/prison — voir le temps restant et l’option de caution\n"
        "/evade — tenter l’évasion; réussite libre, échec et +30s de peine\n"
        "/bank — consulter cash et banque; boutons pour tout déposer ou retirer\n"
        "/deposit <montant/all> — déposer du cash liquide en banque\n"
        "/withdraw <montant/all> — retirer du cash de la banque\n"
        "/give <montant> — transférer du cash en répondant à un joueur\n"
        "/give @username <montant> — transférer du cash à un joueur du groupe\n"
        "/drive — faire un trajet rémunéré; véhicule requis, cooldown 60s\n"
        "/ride — alias de /drive\n"
        "/repair — payer les réparations avant de reprendre une location endommagée\n"
        "/return — restituer une location et récupérer la caution restante\n"
        "/braquer @username — tenter un braquage PvP; niveau 20+, groupe seulement\n"
        "/shoot @username — duel armé; cible en riposte, butin et cooldown par cible\n"
        "/shop — parcourir les sapes, l’équipement, les véhicules et locations\n"
        "/missions — consulter les missions, leurs niveaux, gains et prérequis\n"
        "/mission <id> — lancer une mission (civic, audi, entrepot, blindee, helico)\n"
        "/hustle <id> — lancer un défi (colis, phishing, catalyseur, vapote, etc.)\n"
        "/mission_colis — défi logistique; véhicule requis, cooldown 20s\n"
        "/mission_phishing — défi d’audit numérique; cooldown 25s\n"
        "/mission_catalyseur — défi de contrôle automobile; cooldown 30s\n"
        "/mission_vapote — défi de service au kiosque; cooldown 15s\n"
        "/mission_depanneur — défi de gestion des stocks; cooldown 30s\n"
        "/mission_sneakers — défi de collection; cooldown 25s\n"
        "/jugg — ouvrir les activités d’arcade et leurs paliers\n"
        "/juggling — alias de /jugg\n"
        "/casino — ouvrir les jeux et consulter leur syntaxe\n\n"
        "Dans les groupes, les commandes fonctionnent aussi avec la mention du bot."
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


SHOP_CLOTHING_CATEGORIES = {
    "vetements_accessoires",
    "tete_visage",
    "corps_vestes",
    "accessoires_sacoches",
    "pieds",
    "vetement",
    "accessoire",
    "luxe",
}
SHOP_BRANDS = (
    ("louis_vuitton", "Louis Vuitton", ("louis vuitton", "louis v", " lv ")),
    ("audemars_piguet", "Audemars Piguet", ("audemars piguet",)),
    ("under_armour", "Under Armour", ("under armour",)),
    ("moose_knuckles", "Moose Knuckles", ("moose knuckles",)),
    ("the_north_face", "The North Face", ("the north face",)),
    ("palm_angels", "Palm Angels", ("palm angels",)),
    ("stone_island", "Stone Island", ("stone island",)),
    ("new_balance", "New Balance", ("new balance",)),
    ("off_white", "Off-White", ("off-white",)),
    ("balenciaga", "Balenciaga", ("balenciaga",)),
    ("carhartt", "Carhartt", ("carhartt",)),
    ("moncler", "Moncler", ("moncler",)),
    ("burberry", "Burberry", ("burberry",)),
    ("supreme", "Supreme", ("supreme",)),
    ("patek_philippe", "Patek Philippe", ("patek philippe",)),
    ("jacquemus", "Jacquemus", ("jacquemus",)),
    ("corteiz", "Corteiz", ("corteiz",)),
    ("chanel", "Chanel", ("chanel",)),
    ("hermes", "Hermès", ("hermès", "hermes")),
    ("versace", "Versace", ("versace",)),
    ("adidas", "Adidas", ("adidas",)),
    ("lacoste", "Lacoste", ("lacoste",)),
    ("prada", "Prada", ("prada",)),
    ("gucci", "Gucci", ("gucci",)),
    ("dior", "Dior", ("dior",)),
    ("nike", "Nike", ("nike", "tech fleece")),
    ("rolex", "Rolex", ("rolex",)),
    ("cartier", "Cartier", ("cartier",)),
    ("trapstar", "Trapstar", ("trapstar",)),
    ("montreal", "Collection Montréal", ("montréal", "montreal")),
)
SHOP_TYPES = {
    "tops": "T-shirts & chandails",
    "pants": "Pantalons",
    "outerwear": "Vestes & manteaux",
    "headwear": "Casquettes & accessoires tête",
    "footwear": "Chaussures",
    "accessories": "Accessoires",
    "dresses": "Robes",
}


def shop_item_brand(item: ShopItem) -> tuple[str, str]:
    name = f"{item.name} {item.item_id.replace('_', ' ')}".casefold()
    for brand_id, label, markers in SHOP_BRANDS:
        if any(marker in name for marker in markers):
            return brand_id, label
    return "other", "Autres marques"


def shop_item_type(item: ShopItem) -> str:
    name = item.name.casefold()
    category = item.category
    if category == "pieds" or any(
        word in name
        for word in ("sneaker", "basket", "air force", "tn requin", "claquette", "bottes")
    ):
        return "footwear"
    if category == "tete_visage" or any(
        word in name
        for word in ("casquette", "bonnet", "tuque", "toque", "lunettes", "cagoule")
    ):
        return "headwear"
    if category in {"accessoire", "accessoires_sacoches", "luxe"} or any(
        word in name
        for word in (
            "sacoche", "sac banane", "porte-cartes", "montre", "chaîne", "bague",
            "ceinture", "écharpe", "créoles",
        )
    ):
        return "accessories"
    if "robe" in name or "dress" in name:
        return "dresses"
    if category == "corps_vestes" or any(
        word in name
        for word in (
            "veste", "parka", "doudoune", "fleece", "hoodie", "survêtement",
            "survetement", "manteau", "surchemise", "teddy",
        )
    ):
        return "outerwear"
    if any(word in name for word in ("pantalon", "jean")):
        return "pants"
    return "tops"


def shop_is_clothing_item(item: ShopItem) -> bool:
    return item.category in SHOP_CLOTHING_CATEGORIES


def shop_item_route(item: ShopItem) -> str:
    if shop_is_clothing_item(item):
        brand_id, _ = shop_item_brand(item)
        return f"type:{brand_id}:{shop_item_type(item)}"
    if item.category == "vehicules":
        return "category:vehicules"
    if item.category in {
        "armes", "armes_poing", "armes_lourdes", "armes_longues",
    }:
        return f"category:{item.category}"
    return f"category:{item.category}"


def shop_back_button(user_id: int, payload: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            "⬅️ Retour",
            callback_data=f"shop:{user_id}:{payload}",
        )
    ]


def shop_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👕 Vêtements / Sapes",
                    callback_data=f"shop:{user_id}:section:sapes",
                )
            ],
            [
                InlineKeyboardButton(
                    "🛡️ Équipement & collections d’arcade",
                    callback_data=f"shop:{user_id}:section:arsenal",
                )
            ],
            [
                InlineKeyboardButton(
                    "🚘 Véhicules / Gamos",
                    callback_data=f"shop:{user_id}:section:gamos",
                )
            ],
            [
                InlineKeyboardButton(
                    "🧩 Équipement de jeu",
                    callback_data=f"shop:{user_id}:section:equipment",
                )
            ],
        ]
    )


async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, message = actor[0], actor[2]
    await message.reply_text(
        "🛍️ Boutique MTL\nChoisis une section. Les bonus s’appliquent aux mécaniques d’arcade.",
        reply_markup=shop_main_keyboard(actor_info["id"]),
    )


async def show_shop_items(
    query: CallbackQuery,
    database: Database,
    actor_info: ActorInfo,
    chat_id: int,
    category_items: list[ShopItem],
    title: str,
    back_payload: str,
) -> None:
    player = await database.get_player(actor_info["id"], chat_id)
    if player is None:
        raise RuntimeError(f"Le joueur {actor_info['id']} est introuvable.")
    keyboard = [
        [
            InlineKeyboardButton(
                f"{item.name} — {item.price}$ (niv. {item.min_level})",
                callback_data=f"buy:{actor_info['id']}:{item.item_id}",
            )
        ]
        for item in category_items
    ]
    keyboard.append(shop_back_button(actor_info["id"], back_payload))
    keyboard.append(shop_back_button(actor_info["id"], "home"))
    item_details = "\n\n".join(
        f"• {item.name} — {item.price}$ (niveau {item.min_level})\n"
        f"{item.description or 'Article de collection avec effets réservés à l’arcade.'}\n"
        f"Bonus : +{item.xp_bonus}% XP, +{item.success_bonus:.0%} réussite"
        for item in category_items
    )
    await edit_callback_message(
        query,
        f"🛍️ {title}\nNiveau : {player['level']} | Solde : {player['cash']}$\n\n"
        f"{item_details}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    namespace = "buy" if data.startswith("buy:") else "shop"
    callback_actor = callback_actor_info(update, data, namespace)
    if callback_actor is None:
        return
    actor_info, payload = callback_actor
    actor = await player_context(update, context, actor_info)
    if actor is None:
        return
    actor_info, chat = actor[0], actor[1]
    database = database_for(context)
    user_id = actor_info["id"]

    if data.startswith("buy:"):
        item = item_by_id(payload)
        if item is None:
            await edit_callback_message(query, "Cet article n’existe pas.")
            return
        outcome = await database.purchase_item(user_id, item.item_id, chat.id)
        if outcome == "ok":
            player = await database.get_player(user_id, chat.id)
            if player is None:
                raise RuntimeError("Profil introuvable après l’achat.")
            text = (
                f"✅ {item.name} ajouté à ton inventaire.\n"
                f"Solde restant : {player['cash']}$.\n{item.description}"
            )
        elif outcome == "level":
            text = f"Niveau {item.min_level} requis pour {item.name}."
        elif outcome == "cash":
            text = f"Solde insuffisant pour {item.name}."
        else:
            text = "Achat impossible : profil ou article introuvable."
        await edit_callback_message(
            query,
            text,
            reply_markup=InlineKeyboardMarkup(
                [
                    shop_back_button(user_id, shop_item_route(item)),
                    shop_back_button(user_id, "home"),
                ]
            ),
        )
        return

    if payload == "home":
        await edit_callback_message(
            query, "🛍️ Boutique MTL\nChoisis une section.",
            reply_markup=shop_main_keyboard(user_id),
        )
        return

    if payload.startswith("rentcatalog:"):
        category = payload.removeprefix("rentcatalog:")
        if category != "vehicules":
            await edit_callback_message(query, "Cette sélection n’existe pas.")
            return
        vehicle_items = [item for item in ITEMS if item.category == "vehicules"]
        keyboard = [
            [
                InlineKeyboardButton(
                    f"{item.name} — valeur {item.price}$",
                    callback_data=f"shop:{user_id}:rentcar:{item.item_id}",
                )
            ]
            for item in vehicle_items
        ]
        keyboard.append(shop_back_button(user_id, "section:gamos"))
        await edit_callback_message(
            query,
            "🔑 Location courte, journée ou semaine.\n"
            "Le loyer et la caution sont calculés sur la valeur d’achat.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if payload.startswith("rentcar:"):
        item_id = payload.removeprefix("rentcar:")
        item = item_by_id(item_id)
        if item is None or item.category != "vehicules":
            await edit_callback_message(query, "Ce véhicule n’est pas disponible.")
            return
        keyboard = [
            [
                InlineKeyboardButton(
                    f"{label} — {vehicle_rental_quote(item, duration)[0]}$ + "
                    f"{vehicle_rental_quote(item, duration)[1]}$ caution",
                    callback_data=f"rent:{user_id}:{item_id}:{duration}",
                )
            ]
            for duration, (label, _, _, _) in VEHICLE_RENTAL_TERMS.items()
        ]
        keyboard.append(shop_back_button(user_id, "rentcatalog:vehicules"))
        await edit_callback_message(
            query,
            f"🔑 {item.name} — valeur {item.price}$\n"
            "La caution restante est rendue à la restitution ou à l’échéance.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if payload == "section:sapes":
        brands = sorted(
            {
                shop_item_brand(item)
                for item in ITEMS
                if shop_is_clothing_item(item)
            },
            key=lambda brand: brand[1].casefold(),
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    label, callback_data=f"shop:{user_id}:brand:{brand_id}"
                )
            ]
            for brand_id, label in brands
        ]
        keyboard.append(shop_back_button(user_id, "home"))
        await edit_callback_message(
            query,
            "👕 Sapes — choisis une marque ou une collection.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if payload.startswith("brand:"):
        brand_id = payload.removeprefix("brand:")
        brand_items = [
            item for item in ITEMS
            if shop_is_clothing_item(item) and shop_item_brand(item)[0] == brand_id
        ]
        if not brand_items:
            await edit_callback_message(query, "Cette marque n’est pas disponible.")
            return
        brand_label = shop_item_brand(brand_items[0])[1]
        types = sorted(
            {shop_item_type(item) for item in brand_items},
            key=lambda item_type: SHOP_TYPES[item_type],
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    SHOP_TYPES[item_type],
                    callback_data=f"shop:{user_id}:type:{brand_id}:{item_type}",
                )
            ]
            for item_type in types
        ]
        keyboard.append(shop_back_button(user_id, "section:sapes"))
        await edit_callback_message(
            query,
            f"👕 {brand_label} — choisis un type d’article.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if payload.startswith("type:"):
        parts = payload.split(":")
        if len(parts) != 3:
            await edit_callback_message(query, "Cette sélection n’est pas valide.")
            return
        _, brand_id, item_type = parts
        category_items = [
            item for item in ITEMS
            if shop_is_clothing_item(item)
            and shop_item_brand(item)[0] == brand_id
            and shop_item_type(item) == item_type
        ]
        if not category_items or item_type not in SHOP_TYPES:
            await edit_callback_message(query, "Aucun article dans cette sélection.")
            return
        await show_shop_items(
            query,
            database,
            actor_info,
            chat.id,
            category_items,
            SHOP_TYPES[item_type],
            f"brand:{brand_id}",
        )
        return

    if payload.startswith("section:"):
        section = payload.removeprefix("section:")
        section_categories = {
            "arsenal": ("armes", "armes_poing", "armes_lourdes", "armes_longues", "protection"),
            "equipment": ("matos",),
        }
        if section == "gamos":
            await edit_callback_message(
                query,
                "🚘 Véhicules / Gamos\nChoisis propriété ou location.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🏷️ Acheter — propriété définitive",
                                callback_data=f"shop:{user_id}:buycatalog:vehicules",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🔑 Louer — 1 h, 24 h ou 7 jours",
                                callback_data=f"shop:{user_id}:rentcatalog:vehicules",
                            )
                        ],
                        shop_back_button(user_id, "home"),
                    ]
                ),
            )
            return
        categories = section_categories.get(section)
        if categories is None:
            await edit_callback_message(query, "Cette section n’existe pas.")
            return
        category_labels = {
            "armes": "Objets de collection",
            "armes_poing": "Collection — catégorie A",
            "armes_lourdes": "Collection — catégorie B",
            "armes_longues": "Collection — catégorie C",
            "vehicules": "Tous les véhicules",
            "protection": "Protection (effets d’arcade)",
            "matos": "Équipement (effets d’arcade)",
        }
        keyboard = [
            [
                InlineKeyboardButton(
                    category_labels[category],
                    callback_data=f"shop:{user_id}:category:{category}",
                )
            ]
            for category in categories
            if any(item.category == category for item in ITEMS)
        ]
        keyboard.append(shop_back_button(user_id, "home"))
        title = {
            "arsenal": "Équipement & collections d’arcade",
            "equipment": "Équipement complémentaire",
        }[section]
        await edit_callback_message(
            query, title, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if payload.startswith("buycatalog:"):
        category = payload.removeprefix("buycatalog:")
        if category != "vehicules":
            await edit_callback_message(query, "Cette sélection n’existe pas.")
            return
        vehicle_items = [item for item in ITEMS if item.category == "vehicules"]
        await show_shop_items(
            query,
            database,
            actor_info,
            chat.id,
            vehicle_items,
            "Véhicules — propriété définitive",
            "section:gamos",
        )
        return

    category = payload.removeprefix("category:")
    if category in SHOP_CATEGORIES:
        category_items = [item for item in ITEMS if item.category == category]
        if not category_items:
            await edit_callback_message(query, "Aucun article dans cette catégorie.")
            return
        if category == "vehicules":
            parent = "section:gamos"
        elif category in {
            "armes", "armes_poing", "armes_lourdes", "armes_longues", "protection"
        }:
            parent = "section:arsenal"
        else:
            parent = "section:equipment"
        await show_shop_items(
            query,
            database,
            actor_info,
            chat.id,
            category_items,
            SHOP_CATEGORIES[category],
            parent,
        )
        return
    await edit_callback_message(query, "Cette catégorie n’existe pas.")


def rental_trip_message(result: dict[str, object]) -> str:
    status = result.get("status")
    if status == "none":
        return ""
    if status == "expired":
        refund = int(result.get("cash_refund", 0)) + int(
            result.get("bank_refund", 0)
        )
        return (
            f"\n\n⌛ Location expirée pour {result.get('name')}. "
            f"Caution rendue : {refund}$."
        )
    if status == "clear":
        return f"\n\n🚘 Trajet terminé avec {result.get('name')}; aucun dégât."
    if status == "light":
        return (
            f"\n\n⚠️ Petit accrochage sur {result.get('name')}. "
            f"Caution retenue : {result.get('damage_cost')}$; "
            f"remise en état : {result.get('repair_fee')}$. "
            "Commande /repair avant de reprendre la route."
        )
    if status == "moderate":
        return (
            f"\n\n🛠️ Dégâts modérés sur {result.get('name')}. "
            f"Caution retenue : {result.get('damage_cost')}$; "
            f"remise en état : {result.get('repair_fee')}$. "
            "Commande /repair pour continuer à rouler."
        )
    if status == "major":
        return (
            f"\n\n🚨 Dégâts majeurs sur {result.get('name')}. "
            f"Caution retenue : {result.get('damage_cost')}$. "
            "La location est terminée."
        )
    if status == "confiscated":
        return (
            f"\n\n🚨 La caution de {result.get('name')} est épuisée "
            f"({result.get('damage_cost')}$ retenus). La location est terminée."
        )
    if status == "repair_required":
        return (
            f"\n\n🛠️ {result.get('name')} est immobilisé. "
            f"Règle {result.get('fee')}$ de remise en état avec /repair."
        )
    return ""


async def vehicle_rental_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    callback_actor = callback_actor_info(update, query.data, "rent")
    if callback_actor is None:
        return
    actor_info, payload = callback_actor
    actor = await player_context(update, context, actor_info)
    if actor is None:
        return
    item_id, separator, duration = payload.partition(":")
    if not separator or duration not in VEHICLE_RENTAL_TERMS:
        await edit_callback_message(query, "Cette durée de location n’existe pas.")
        return
    result = await database_for(context).create_vehicle_rental(
        actor_info["id"], actor[1].id, item_id, duration
    )
    status = result["status"]
    if status == "ok":
        text = (
            f"✅ Location confirmée : {result['name']} pour {result['duration']}.\n"
            f"Loyer : {result['rent']}$ | Caution bloquée : {result['deposit']}$.\n"
            f"Échéance : {datetime.fromtimestamp(float(result['expires_at']), timezone.utc):%Y-%m-%d %H:%M UTC}.\n"
            "La caution restante sera rendue avec /return ou à l’échéance. "
            "Utilise /drive pour un trajet de jeu."
        )
    elif status == "active":
        text = "Tu as déjà une location active. Rends le véhicule avec /return."
    elif status == "level":
        item = item_by_id(item_id)
        text = (
            f"Niveau {item.min_level} requis pour louer ce véhicule."
            if item is not None
            else "Niveau requis pour louer ce véhicule."
        )
    elif status == "funds":
        text = "Solde cash + banque insuffisant pour le loyer et la caution."
    else:
        text = "Ce véhicule n’est pas disponible à la location."
    await edit_callback_message(query, text)


async def drive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    database = database_for(context)
    result = await database.complete_vehicle_drive(
        actor_info["id"],
        chat.id,
        random.SystemRandom().random(),
        random.SystemRandom().random(),
        random.SystemRandom().random(),
    )
    if result["status"] == "no_vehicle":
        await message.reply_text(
            "🚶 T’es à pied, chef. Passe au /shop pour acheter ou louer un véhicule."
        )
        return
    if result["status"] == "cooldown":
        await message.reply_text(
            f"⏳ Tu viens de faire un tour. Reprends la route dans "
            f"{result['remaining']}s."
        )
        return
    if result["status"] == "prison":
        await message.reply_text(
            f"🚔 Le trajet attendra la fin de ta peine ({result['remaining']}s)."
        )
        return
    if result["status"] == "repair_required":
        await message.reply_text(rental_trip_message(result).strip())
        return
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable. Tape /start avant de partir.")
        return

    if result["status"] in {"light", "moderate", "major", "confiscated"}:
        await message.reply_text(
            f"🚘 Trajet interrompu avec {result['name']}."
            + rental_trip_message(result)
        )
        return
    upkeep_notice = (
        f" Entretien léger : -{result['maintenance_fee']}$."
        if int(result["maintenance_fee"])
        else ""
    )
    vehicle_type = (
        "véhicule loué"
        if int(result["is_rental"])
        else "ton véhicule de propriété"
    )
    await message.reply_text(
        f"🚘 Trajet effectué avec {result['name']}, {vehicle_type}. "
        f"Le tour rapporte +{result['cash_delta']}$ et +{result['xp']} XP."
        f"{upkeep_notice}\n💰 Cash : {result['cash']}$."
        + level_up_notice(
            int(result["previous_level"]), int(result["level"])
        )
    )


async def repair_vehicle(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    result = await database_for(context).repair_vehicle_rental(
        actor_info["id"], chat.id
    )
    if result["status"] == "missing":
        await message.reply_text("Aucune location active à remettre en état.")
    elif result["status"] == "expired":
        await message.reply_text("La location a expiré; restitue-la avec /return.")
    elif result["status"] == "clean":
        await message.reply_text(f"{result['name']} est déjà en état de rouler.")
    elif result["status"] == "funds":
        await message.reply_text(
            f"Il te faut {result['fee']}$ de cash liquide pour les réparations."
        )
    else:
        await message.reply_text(
            f"🛠️ {result['name']} remis en état. "
            f"Frais réglés : {result['fee']}$. Tu peux reprendre la route."
        )


async def return_vehicle(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    result = await database_for(context).return_vehicle_rental(
        actor_info["id"], chat.id
    )
    status = result["status"]
    if status == "missing":
        await message.reply_text("Tu n’as aucune location active à restituer.")
        return
    refund = int(result["cash_refund"]) + int(result["bank_refund"])
    reason = (
        "La location avait atteint son échéance."
        if status == "expired"
        else "Véhicule restitué."
    )
    await message.reply_text(
        f"🔑 {reason} {result['name']}.\n"
        f"Caution restante rendue : {refund}$ "
        f"(cash {result['cash_refund']}$, banque {result['bank_refund']}$)."
    )


async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    database = database_for(context)
    items = await database.get_inventory(actor_info["id"], chat.id)
    rental = await database.get_vehicle_rental(actor_info["id"], chat.id)
    if not items and not rental:
        await message.reply_text("Ton inventaire est vide. Fais /shop pour magasiner.")
        return

    categories: dict[str, list[str]] = {}
    for item in items:
        categories.setdefault(item["category"], []).append(
            f"• {item['name']} ×{item['quantity']} — {item['description']}"
        )
    lines = ["🎒 Inventaire MTL :"]
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
    if rental and rental.get("status") == "expired":
        lines.append(rental_trip_message(rental).strip())
    elif rental:
        remaining = max(0, int(float(rental["expires_at"]) - time.time()))
        lines.append(
            f"\n🔑 Location active : {rental['name']} — "
            f"caution restante {int(rental['deposit_cash_remaining']) + int(rental['deposit_bank_remaining'])}$, "
            f"état {rental['damage_state']}, échéance dans {remaining}s. "
            "Commande /return pour restituer."
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
        await message.reply_text("Usage : /braquer @username")
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
    attacker_weapon = await database.get_weapon_profile(actor_info["id"], chat.id)
    victim_weapon = await database.get_weapon_profile(victim["user_id"], chat.id)
    result = resolve_pvp(
        attacker_has_weapon=bool(attacker_weapon["has_weapon"]),
        victim_has_weapon=bool(victim_weapon["has_weapon"]),
        victim_has_armor=bool(victim_effects["has_armor"]),
        attacker_weapon_power=int(attacker_weapon["power"]),
        victim_weapon_power=int(victim_weapon["power"]),
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
            f"({victim_effects['armor_name'] or victim_weapon['name'] or 'de défense'}). "
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


async def shoot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    if chat.type not in {"group", "supergroup"}:
        await message.reply_text("Cette commande est disponible dans les groupes.")
        return

    database = database_for(context)
    attacker = await database.get_player(actor_info["id"], chat.id)
    if attacker is None:
        raise RuntimeError(f"Le joueur {actor_info['id']} est introuvable.")
    jail_seconds = prison_remaining_seconds(attacker)
    if jail_seconds:
        await message.reply_text(
            f"🚔 Tu es en taule pour encore {jail_seconds}s; le duel est bloqué."
        )
        return

    attacker_weapon = await database.get_weapon_profile(actor_info["id"], chat.id)
    if not attacker_weapon["has_weapon"]:
        await message.reply_text(
            "Il te faut une arme de l’Arsenal dans ton inventaire pour lancer un duel."
        )
        return

    target_id: int | None = None
    target_name = "Joueur ciblé"
    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is not None:
        target_user = getattr(reply_to, "from_user", None)
        if target_user is not None and not target_user.is_bot:
            target_id = target_user.id
            target_name = target_user.full_name
    elif len(context.args) == 1 and context.args[0].startswith("@"):
        target = await database.get_player_by_username(context.args[0], chat.id)
        if target is not None:
            target_id = int(target["user_id"])
            target_name = str(target.get("username") or "Joueur ciblé")

    if target_id is None:
        await message.reply_text(
            "Usage : /shoot @username, ou réponds au message d’un joueur avec /shoot."
        )
        return
    if target_id == actor_info["id"]:
        await message.reply_text("Tu ne peux pas lancer un duel contre toi-même.")
        return

    victim = await database.get_player(target_id, chat.id)
    if victim is None:
        await message.reply_text(
            "Cette cible n’a pas encore de profil dans ce groupe."
        )
        return
    victim_weapon = await database.get_weapon_profile(target_id, chat.id)
    outcome = resolve_shootout(
        int(attacker_weapon["power"]),
        int(victim_weapon["power"]),
    )
    winner_id = actor_info["id"] if outcome.winner == "attacker" else target_id
    winner_effects = await database.get_item_effects(winner_id, chat.id)
    xp_gain = xp_with_bonus(outcome.xp_gain, int(winner_effects["xp_bonus"]))
    deadline = (
        prison_deadline(PRISON_DURATION_SECONDS)
        if outcome.police_intervention
        else None
    )
    applied = await database.apply_shootout_result(
        actor_info["id"],
        target_id,
        winner_id,
        xp_gain,
        outcome.police_intervention,
        deadline,
        chat_id=chat.id,
    )
    if applied is None:
        await message.reply_text("Le duel n’a pas pu être lancé.")
        return
    if applied["status"] == "cooldown":
        await message.reply_text(
            f"⏳ Attends encore {applied['remaining']}s avant de défier "
            "à nouveau ce joueur."
        )
        return
    if applied["status"] == "prison":
        await message.reply_text(
            f"🚔 Le duel est bloqué : un participant est en taule "
            f"pour encore {applied['remaining']}s."
        )
        return
    if applied["status"] == "missing":
        await message.reply_text(
            "Cette cible n’a pas encore de profil dans ce groupe."
        )
        return

    winner_name = actor_info["name"] if winner_id == actor_info["id"] else target_name
    loser_name = actor_info["name"] if winner_id == target_id else target_name
    riposte = (
        f" La riposte de {target_name} a été immédiate."
        if victim_weapon["has_weapon"]
        else ""
    )
    police_notice = (
        f" Intervention de police : {loser_name} part en taule "
        f"{applied['prison_seconds']}s."
        if applied["police_intervention"]
        else ""
    )
    await message.reply_text(
        f"⚡ Duel d’arcade : {winner_name} prend l’avantage.{riposte}\n"
        f"💵 {loser_name} perd {applied['cash_loss']}$ de cash liquide "
        f"(soins : {applied['hospital_fee']}$); "
        f"{winner_name} récupère {applied['loot']}$ de butin.\n"
        f"✨ {winner_name} gagne {applied['xp']} XP "
        f"(niveau {applied['level']}).{police_notice}"
        + level_up_notice(
            int(applied["previous_level"]), int(applied["level"])
        )
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
            + (
                " | véhicule requis"
                if mission.mission_id in LONG_DISTANCE_MISSION_IDS
                else ""
            )
            for mission in MISSIONS
        )
        lines.extend(
            f"\n💼 {hustle.name} (/hustle {hustle.mission_id} ou "
            f"/mission_{hustle.mission_id})\n"
            f"Réussite: {hustle.success_chance:.0%} | "
            f"Gain fixe: {hustle.success_reward}$ | XP: {hustle.xp} | "
            f"Échec: -{hustle.failure_penalty}$ | "
            f"Cooldown: {hustle.cooldown_seconds}s"
            + (" | véhicule requis" if hustle.mission_id == "colis" else "")
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
    uses_vehicle = mission.mission_id in LONG_DISTANCE_MISSION_IDS
    if uses_vehicle and not await database.has_available_vehicle(
        actor_info["id"], chat.id
    ):
        await message.reply_text(
            "🚶 Cette mission est trop éloignée pour y aller à pied. "
            "Achète ou loue un véhicule au /shop."
        )
        return
    rental = (
        await database.get_vehicle_rental(actor_info["id"], chat.id)
        if uses_vehicle
        else None
    )
    if rental is not None and int(rental.get("repair_due", 0)) > 0:
        await message.reply_text(
            f"🛠️ {rental['name']} attend sa remise en état. "
            f"Règle {rental['repair_due']}$ avec /repair avant la mission."
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
    vehicle_notice = ""
    if uses_vehicle and succeeded:
        trip_result = await database.apply_vehicle_trip(
            actor_info["id"],
            chat.id,
            random.SystemRandom().random(),
            random.SystemRandom().random(),
        )
        vehicle_notice = rental_trip_message(trip_result)
    if succeeded:
        await message.reply_text(
            f"🏁💨 Mission réussie, {actor_info['name']}!\n"
            f"🚘 {mission.name}\n"
            f"💵 +{mission.reward}$ | ✨ +{updated_player['xp'] - player['xp']} XP\n"
            f"⭐ Niveau: {updated_player['level']} | "
            f"Solde: {updated_player['cash']}$"
            f"{vehicle_notice}"
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
    if hustle.mission_id == "colis":
        if not await database.has_available_vehicle(actor_info["id"], chat_id):
            await message.reply_text(
                "🚶 T’es à pied, chef. Il te faut un véhicule pour la logistique. "
                "Passe au /shop pour acheter ou louer."
            )
            return
        rental = await database.get_vehicle_rental(actor_info["id"], chat_id)
        if rental is not None and int(rental.get("repair_due", 0)) > 0:
            await message.reply_text(
                f"🛠️ {rental['name']} attend sa remise en état. "
                f"Règle {rental['repair_due']}$ avec /repair avant le trajet."
            )
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
    vehicle_notice = ""
    if hustle.mission_id == "colis":
        trip_result = await database.apply_vehicle_trip(
            actor_info["id"],
            chat_id,
            random.SystemRandom().random(),
            random.SystemRandom().random(),
        )
        vehicle_notice = rental_trip_message(trip_result)
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
        f"{vehicle_notice}"
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
    application.add_handler(CommandHandler(("drive", "ride"), drive))
    application.add_handler(CommandHandler("repair", repair_vehicle))
    application.add_handler(CommandHandler("return", return_vehicle))
    application.add_handler(CommandHandler("braquer", braquer))
    application.add_handler(CommandHandler("shoot", shoot))
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
    application.add_handler(
        CallbackQueryHandler(vehicle_rental_callback, pattern=r"^rent:")
    )
    application.add_handler(CallbackQueryHandler(pay_bail, pattern=r"^bail:"))
    application.add_error_handler(error_handler)
    logger.info("Rabza Jugg V2 démarre.")
    application.run_polling()


if __name__ == "__main__":
    main()
