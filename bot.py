"""Telegram entry point for Rabza Jugg V2."""

import asyncio
from html import escape
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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

from config import ADMIN_USER_IDS, BOT_TOKEN, DATABASE_PATH, validate_config
from database import (
    MARKET_UPDATE_INTERVAL_SECONDS,
    MAX_ADMIN_BALANCE,
    MAX_ADMIN_XP,
    Database,
)
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
    MissionSpec("audi", "Course express — secteur Sainte-Catherine", 3, 350, 50, 0.70),
    MissionSpec(
        "entrepot",
        "Trafic logistique — gestion de stock",
        5,
        900,
        120,
        0.55,
    ),
    MissionSpec("depot", "Braquage arcade du dépanneur", 5, 800, 100, 0.50),
    MissionSpec("port", "Contrebande portuaire", 7, 1800, 180, 0.45),
    MissionSpec(
        "blindee",
        "Convoi blindé — sécurisation logistique",
        10,
        2500,
        250,
        0.40,
    ),
    MissionSpec("crypto", "Crypto-arnaque — audit de portefeuille", 10, 3000, 300, 0.40),
    MissionSpec("bijouterie", "Casse de bijouterie — Plaza", 15, 5000, 500, 0.30),
    MissionSpec(
        "helico",
        "Coordination de transport — niveau expert",
        15,
        7500,
        600,
        0.25,
    ),
    MissionSpec(
        "banque_centrale",
        "Casse de la Banque Centrale — opération de haut niveau",
        20,
        15000,
        1200,
        0.15,
    ),
)

LONG_DISTANCE_MISSION_IDS = {
    "audi",
    "entrepot",
    "port",
    "blindee",
    "helico",
    "banque_centrale",
}

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
    application.bot_data["market_price_task"] = application.create_task(
        market_price_loop(application), name="market-price-updater"
    )


async def market_price_loop(application: Application) -> None:
    while True:
        await asyncio.sleep(MARKET_UPDATE_INTERVAL_SECONDS)
        database = cast(Database, application.bot_data["database"])
        await database.update_market_prices()


async def post_shutdown(application: Application) -> None:
    task = application.bot_data.pop("market_price_task", None)
    if task is not None:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    actor = await player_context(update, context, ensure_player=False)
    if actor is None:
        return
    actor_info, chat, message = actor
    created = await database_for(context).ensure_player(
        actor_info["id"], actor_info["username"], chat.id
    )
    if not created:
        await message.reply_text(
            f"👋 T'es déjà enregistré dans le quartier, "
            f"<b>{escape(actor_info['name'])}</b>, reuf.\n"
            "Tape /profil pour voir tes bails.",
            parse_mode="HTML",
        )
        return
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
        "/invest — consulter le marché et acheter/vendre des parts\n"
        "/invest buy <actif> <parts> — acheter un actif avec du cash liquide\n"
        "/invest sell <actif> <parts/all> — vendre une partie ou toutes ses parts\n"
        "/drive — faire un trajet rémunéré; véhicule requis, cooldown 60s\n"
        "/repair — payer les réparations avant de reprendre une location endommagée\n"
        "/return — restituer une location et récupérer la caution restante\n"
        "/braquer @username — braquage PvP au niveau 20+; échec: amende et peine aléatoires\n"
        "/shoot @username — duel armé; cible en riposte, butin et cooldown par cible\n"
        "/shop — parcourir les sapes, protections balistiques, véhicules et locations\n"
        "/missions — consulter les missions, leurs niveaux, gains et prérequis\n"
        "/mission <id> — lancer une mission ou un défi par identifiant\n"
        "/jugg — ouvrir les activités d’arcade et leurs paliers\n"
        "/casino — ouvrir les jeux et consulter leur syntaxe\n\n"
        "/admin — ouvrir le panneau réservé aux administrateurs\n"
        "/devmode — basculer le mode Joueur / Développeur (admin seulement)\n"
        "/give_cash [@username] [montant|max] [cash|bank] — sans montant, porter le solde au plafond admin\n"
        "/give_xp [@username] [montant|max] — sans montant, porter l'XP au plafond admin\n"
        "/set_level [@username] [niveau] — sans niveau, passer au niveau maximum\n"
        "/give_item [@username] [id_item|nom|all] — sans item ou avec all, donner le catalogue complet\n"
        "/revoke_cash [@username] [montant] [cash|bank] — sans montant, vider le compte choisi\n"
        "/revoke_xp [@username] [montant] — sans montant, remettre l'XP à zéro\n"
        "/revoke_level [@username] [niveaux] — sans nombre, repasser au niveau 1\n"
        "/revoke_item [@username] [id_item|nom|all] — sans item ou avec all, vider l'inventaire\n"
        "/admin_reset [@username] [repair|full CONFIRMER] — réparer ou réinitialiser un profil\n\n"
        "Dans les groupes, les commandes fonctionnent aussi avec la mention du bot."
    )


ADMIN_DENIED_MESSAGE = (
    "🚫 T'as cru t'étais le maire du quartier ? Passe ton chemin."
)
ADMIN_PANEL_TEXT = (
    "🔐 Panneau de contrôle\n"
    "Les actions sont limitées au groupe où tu lances la commande."
)


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and not user.is_bot and user.id in ADMIN_USER_IDS


async def developer_mode_enabled(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> bool:
    if user_id not in ADMIN_USER_IDS:
        return False
    return await database_for(context).get_admin_dev_mode(user_id)


async def require_admin_dev_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
) -> bool:
    if not is_admin(update):
        await message.reply_text(ADMIN_DENIED_MESSAGE)
        return False
    user = update.effective_user
    if user is None or not await developer_mode_enabled(context, user.id):
        await message.reply_text(
            "🔒 Outil verrouillé en Mode Joueur. Active le Mode Développeur avec /devmode."
        )
        return False
    return True


def admin_panel_text(dev_mode: bool) -> str:
    mode = "🧪 Mode Développeur [DEV]" if dev_mode else "🎮 Mode Joueur"
    return f"{ADMIN_PANEL_TEXT}\nÉtat actuel : {mode}."


def admin_panel_keyboard(
    admin_id: int, dev_mode: bool
) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "🎮 Revenir au Mode Joueur"
                if dev_mode
                else "🧪 Activer le Mode Développeur",
                callback_data=f"admin:{admin_id}:toggle",
            )
        ]
    ]
    if dev_mode:
        keyboard.extend(
            [
                [
                    InlineKeyboardButton(
                        "💸 Donner du cash",
                        callback_data=f"admin:{admin_id}:credit",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✨ Donner de l'XP",
                        callback_data=f"admin:{admin_id}:xp",
                    ),
                    InlineKeyboardButton(
                        "⭐ Fixer le niveau",
                        callback_data=f"admin:{admin_id}:level",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🎁 Donner un item / véhicule",
                        callback_data=f"admin:{admin_id}:item",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🧹 Révoquer cash, XP, niveaux ou items",
                        callback_data=f"admin:{admin_id}:revoke",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🩹 Réparer sans perte",
                        callback_data=f"admin:{admin_id}:repair",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "♻️ Réinitialisation complète",
                        callback_data=f"admin:{admin_id}:reset",
                    )
                ],
            ]
        )
    return InlineKeyboardMarkup(keyboard)


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    user = update.effective_user
    if user is None or not is_admin(update):
        await message.reply_text(ADMIN_DENIED_MESSAGE)
        return
    dev_mode = await developer_mode_enabled(context, user.id)
    await message.reply_text(
        admin_panel_text(dev_mode),
        reply_markup=admin_panel_keyboard(user.id, dev_mode),
    )


async def devmode(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return
    if user is None or not is_admin(update):
        await message.reply_text(ADMIN_DENIED_MESSAGE)
        return
    enabled = await database_for(context).toggle_admin_dev_mode(user.id)
    await message.reply_text(
        f"Mode {'Développeur [DEV]' if enabled else 'Joueur'} activé.",
        reply_markup=admin_panel_keyboard(user.id, enabled),
    )


async def admin_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None:
        return
    if user is None or user.is_bot or user.id not in ADMIN_USER_IDS:
        await query.answer(ADMIN_DENIED_MESSAGE, show_alert=True)
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "admin" or parts[1] != str(user.id):
        await query.answer(ADMIN_DENIED_MESSAGE, show_alert=True)
        return

    database = database_for(context)
    if parts[2] == "toggle":
        dev_mode = await database.toggle_admin_dev_mode(user.id)
        await query.answer(
            "Mode Développeur activé." if dev_mode else "Mode Joueur activé."
        )
        await edit_callback_message(
            query,
            admin_panel_text(dev_mode),
            reply_markup=admin_panel_keyboard(user.id, dev_mode),
        )
        return
    if parts[2] == "home":
        dev_mode = await developer_mode_enabled(context, user.id)
        await query.answer()
        await edit_callback_message(
            query,
            admin_panel_text(dev_mode),
            reply_markup=admin_panel_keyboard(user.id, dev_mode),
        )
        return
    if not await developer_mode_enabled(context, user.id):
        await query.answer(
            "Active le Mode Développeur avec /devmode pour utiliser ces outils.",
            show_alert=True,
        )
        return
    await query.answer()
    instructions = {
        "credit": (
            "💸 Donner du cash ou remplir un compte\n"
            "Pour toi : /give_cash [montant|max] [cash|bank].\n"
            "Pour un joueur : /give_cash @username [montant|max] [cash|bank], "
            "ou réponds à son message avec /give_cash [montant|max] [cash|bank].\n"
            f"Sans montant ou avec max, le solde sélectionné passe à "
            f"{MAX_ADMIN_BALANCE}$."
        ),
        "xp": (
            "✨ Donner de l'XP\n"
            f"Pour toi : /give_xp [montant|max] (sans montant: {MAX_ADMIN_XP} XP).\n"
            "Pour un joueur : /give_xp @username [montant|max], "
            "ou réponds à son message avec /give_xp [montant|max]."
        ),
        "level": (
            "⭐ Fixer le niveau\n"
            f"Pour toi : /set_level [niveau 1-{MAX_LEVEL}] (sans niveau: maximum).\n"
            f"Pour un joueur : /set_level @username [niveau 1-{MAX_LEVEL}], "
            "ou réponds à son message. L'XP est ajustée au seuil du niveau."
        ),
        "item": (
            "🎁 Donner un item ou véhicule du catalogue\n"
            "Pour toi : /give_item [id_item|nom|all].\n"
            "Pour un joueur : /give_item @username [id_item|nom|all], "
            "ou réponds à son message avec /give_item [id_item|nom|all].\n"
            "Sans item ou avec all, le catalogue entier est ajouté."
        ),
        "revoke": (
            "🧹 Révoquer une ressource\n"
            "/revoke_cash [@username] [montant] [cash|bank] — sans montant, "
            "remet le compte choisi à zéro.\n"
            "/revoke_xp [@username] [montant] — sans montant, remet l'XP à zéro.\n"
            "/revoke_level [@username] [niveaux] — retire le nombre indiqué; "
            "sans valeur, repasse au niveau 1.\n"
            "/revoke_item [@username] [id_item|nom|all] — retire un exemplaire; "
            "sans item ou avec all, vide l'inventaire."
        ),
        "repair": (
            "🩹 Réparation non destructive\n"
            "Réponds au joueur avec /admin_reset repair, ou utilise "
            "/admin_reset @username repair.\n"
            "Ses soldes, son niveau, son XP et son inventaire sont conservés."
        ),
        "reset": (
            "♻️ Réinitialisation complète\n"
            "Réponds au joueur avec /admin_reset full CONFIRMER, ou utilise "
            "/admin_reset @username full CONFIRMER.\n"
            "Cash, banque, progression, inventaire et location active seront remis à zéro."
        ),
    }
    instruction = instructions.get(parts[2])
    if instruction is None:
        await edit_callback_message(query, "Cette action admin n’existe pas.")
        return
    await edit_callback_message(
        query,
        instruction,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⬅️ Panneau admin",
                        callback_data=f"admin:{user.id}:home",
                    )
                ]
            ]
        ),
    )


async def resolve_admin_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    arguments: list[str],
    create_replied_profile: bool = False,
    allow_self: bool = False,
    create_self_profile: bool = False,
) -> tuple[dict[str, object] | None, list[str], str | None]:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return None, arguments, "Impossible de déterminer la conversation."

    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is not None:
        target_user = getattr(reply_to, "from_user", None)
        if target_user is None or target_user.is_bot:
            return None, arguments, "La réponse doit viser le message d’un joueur."
        database = database_for(context)
        target = await database.get_player(target_user.id, chat.id)
        if target is None and create_replied_profile:
            await database.ensure_player(target_user.id, target_user.username, chat.id)
            target = await database.get_player(target_user.id, chat.id)
        if target is None:
            return (
                None,
                arguments,
                "Ce joueur n’a pas encore de profil dans cette conversation.",
            )
        return target, arguments, None

    if allow_self and (not arguments or not arguments[0].startswith("@")):
        user = update.effective_user
        if user is None or user.is_bot:
            return None, arguments, "Impossible de déterminer l'administrateur."
        database = database_for(context)
        target = await database.get_player(user.id, chat.id)
        if target is None and create_self_profile:
            await database.ensure_player(user.id, user.username, chat.id)
            target = await database.get_player(user.id, chat.id)
        if target is None:
            return None, arguments, "Ton profil n’existe pas dans cette conversation."
        return target, arguments, None

    if not arguments or not arguments[0].startswith("@"):
        return (
            None,
            arguments,
            "Indique @username ou réponds au message du joueur ciblé.",
        )
    target = await database_for(context).get_player_by_username(
        arguments[0][1:], chat.id
    )
    if target is None:
        return (
            None,
            arguments[1:],
            "Joueur introuvable dans cette conversation. Il doit avoir un profil actif.",
        )
    return target, arguments[1:], None


async def give_cash(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return

    target, remaining, error = await resolve_admin_target(
        update,
        context,
        list(context.args or []),
        allow_self=True,
        create_self_profile=True,
    )
    if target is None:
        await message.reply_text(
            f"{error}\nUsage: /give_cash [@username] [montant|max] [cash|bank]."
        )
        return

    account_names = {"cash": "cash", "bank": "bank", "banque": "bank"}
    account_tokens = [token.casefold() for token in remaining if token.casefold() in account_names]
    if len(account_tokens) > 1:
        await message.reply_text(
            "Usage: /give_cash [@username] [montant|max] [cash|bank]."
        )
        return
    account = account_names[account_tokens[0]] if account_tokens else "cash"
    amount_tokens = [
        token for token in remaining if token.casefold() not in account_names
    ]
    if len(amount_tokens) > 1:
        await message.reply_text(
            "Usage: /give_cash [@username] [montant|max] [cash|bank]."
        )
        return
    amount: int | None
    if not amount_tokens:
        amount = None
    elif amount_tokens[0].casefold() in {"max", "maximum", "plafond", "infini", "infinite"}:
        amount = None
    elif amount_tokens[0].isdecimal():
        amount = int(amount_tokens[0])
    else:
        await message.reply_text("Le montant doit être un entier positif ou « max ».")
        return
    if amount is not None and amount <= 0:
        await message.reply_text("Le montant doit être supérieur à zéro.")
        return

    result = await database_for(context).admin_credit_player(
        int(target["user_id"]), chat.id, amount, account
    )
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable dans cette conversation.")
    elif result["status"] == "invalid":
        await message.reply_text("Le montant ou le compte demandé est invalide.")
    elif result["status"] == "limit":
        await message.reply_text(
            f"Le compte est déjà au plafond admin de {MAX_ADMIN_BALANCE}$ "
            "ou le montant dépasserait la limite de stockage."
        )
    else:
        current_balance = int(target[account])
        credited = int(result[account]) - current_balance
        target_name = str(target.get("username") or target["user_id"])
        await message.reply_text(
            f"✅ Crédit admin appliqué à {target_name}: +{credited}$ en "
            f"{'banque' if account == 'bank' else 'cash'}."
        )


async def give_xp(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return
    target, remaining, error = await resolve_admin_target(
        update,
        context,
        list(context.args or []),
        allow_self=True,
        create_self_profile=True,
    )
    if target is None:
        await message.reply_text(
            f"{error}\nUsage: /give_xp [@username] [montant|max]."
        )
        return
    if len(remaining) > 1:
        await message.reply_text(
            "Usage: /give_xp [@username] [montant|max]."
        )
        return
    amount: int | None = None
    if remaining and remaining[0].casefold() not in {
        "max", "maximum", "plafond", "infini", "infinite"
    }:
        if not remaining[0].isdecimal():
            await message.reply_text(
                "L’XP doit être un entier positif ou « max »."
            )
            return
        amount = int(remaining[0])
    result = await database_for(context).admin_give_xp(
        int(target["user_id"]), chat.id, amount
    )
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable dans cette conversation.")
    elif result["status"] == "invalid":
        await message.reply_text("Le montant d’XP doit être supérieur à zéro.")
    elif result["status"] == "limit":
        await message.reply_text("Le montant dépasserait la limite de stockage XP.")
    else:
        target_name = str(target.get("username") or target["user_id"])
        await message.reply_text(
            f"✅ +{result['xp_added']} XP pour {target_name}. "
            f"Niveau actuel: {result['level']} (total: {result['xp']} XP)."
        )


async def set_level(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return
    target, remaining, error = await resolve_admin_target(
        update,
        context,
        list(context.args or []),
        allow_self=True,
        create_self_profile=True,
    )
    if target is None:
        await message.reply_text(
            f"{error}\nUsage: /set_level [@username] [niveau 1-{MAX_LEVEL}]."
        )
        return
    if len(remaining) > 1 or (remaining and not remaining[0].isdecimal()):
        await message.reply_text(
            f"Usage: /set_level [@username] [niveau 1-{MAX_LEVEL}]."
        )
        return
    level = int(remaining[0]) if remaining else MAX_LEVEL
    result = await database_for(context).admin_set_level(
        int(target["user_id"]), chat.id, level
    )
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable dans cette conversation.")
    elif result["status"] == "invalid":
        await message.reply_text(f"Le niveau doit être compris entre 1 et {MAX_LEVEL}.")
    else:
        target_name = str(target.get("username") or target["user_id"])
        await message.reply_text(
            f"✅ {target_name} est maintenant niveau {result['level']} "
            f"({result['xp']} XP)."
        )


async def give_item(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return
    target, remaining, error = await resolve_admin_target(
        update,
        context,
        list(context.args or []),
        allow_self=True,
        create_self_profile=True,
    )
    if target is None:
        await message.reply_text(
            f"{error}\nUsage: /give_item [@username] [id_item|nom|all]."
        )
        return
    result = await database_for(context).admin_give_item(
        int(target["user_id"]),
        chat.id,
        " ".join(remaining).casefold() if remaining else None,
    )
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable dans cette conversation.")
    elif result["status"] == "invalid_item":
        await message.reply_text(
            "Identifiant inconnu. Choisis un id_item présent dans le catalogue du /shop."
        )
    else:
        target_name = str(target.get("username") or target["user_id"])
        if result.get("item_id") == "all":
            await message.reply_text(
                f"✅ Catalogue complet: {result['count']} articles, véhicules et "
                f"sapes vérifiés dans l’inventaire de {target_name}."
            )
        else:
            await message.reply_text(
                f"✅ {result['name']} ajouté à l’inventaire de {target_name}."
            )


async def revoke_cash(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return
    target, remaining, error = await resolve_admin_target(
        update, context, list(context.args or []), allow_self=True
    )
    usage = "Usage: /revoke_cash [@username] [montant] [cash|bank]."
    if target is None:
        await message.reply_text(f"{error}\n{usage}")
        return

    account_names = {"cash": "cash", "bank": "bank", "banque": "bank"}
    account_tokens = [
        token.casefold() for token in remaining if token.casefold() in account_names
    ]
    amount_tokens = [
        token for token in remaining if token.casefold() not in account_names
    ]
    if len(account_tokens) > 1 or len(amount_tokens) > 1:
        await message.reply_text(usage)
        return
    account = account_names[account_tokens[0]] if account_tokens else "cash"
    amount: int | None = None
    if amount_tokens:
        if not amount_tokens[0].isdecimal() or int(amount_tokens[0]) <= 0:
            await message.reply_text("Le montant doit être un entier strictement positif.")
            return
        amount = int(amount_tokens[0])

    result = await database_for(context).admin_revoke_cash(
        int(target["user_id"]), chat.id, amount, account
    )
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable dans cette conversation.")
    elif result["status"] == "invalid":
        await message.reply_text(usage)
    else:
        target_name = str(target.get("username") or target["user_id"])
        await message.reply_text(
            f"✅ {result['removed']}$ retirés du {account} de {target_name}. "
            f"Solde restant : {result['balance']}$."
        )


async def revoke_xp(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return
    target, remaining, error = await resolve_admin_target(
        update, context, list(context.args or []), allow_self=True
    )
    usage = "Usage: /revoke_xp [@username] [montant]."
    if target is None:
        await message.reply_text(f"{error}\n{usage}")
        return
    if len(remaining) > 1 or (
        remaining and (not remaining[0].isdecimal() or int(remaining[0]) <= 0)
    ):
        await message.reply_text("L’XP à retirer doit être un entier strictement positif.")
        return
    amount = int(remaining[0]) if remaining else None
    result = await database_for(context).admin_revoke_xp(
        int(target["user_id"]), chat.id, amount
    )
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable dans cette conversation.")
    else:
        target_name = str(target.get("username") or target["user_id"])
        await message.reply_text(
            f"✅ {result['xp_removed']} XP retirés à {target_name}. "
            f"Niveau {result['level']} — {result['xp']} XP."
        )


async def revoke_level(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return
    target, remaining, error = await resolve_admin_target(
        update, context, list(context.args or []), allow_self=True
    )
    usage = "Usage: /revoke_level [@username] [nombre_de_niveaux]."
    if target is None:
        await message.reply_text(f"{error}\n{usage}")
        return
    if len(remaining) > 1 or (
        remaining and (not remaining[0].isdecimal() or int(remaining[0]) <= 0)
    ):
        await message.reply_text("Le nombre de niveaux à retirer doit être positif.")
        return
    levels = int(remaining[0]) if remaining else None
    result = await database_for(context).admin_revoke_level(
        int(target["user_id"]), chat.id, levels
    )
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable dans cette conversation.")
    else:
        target_name = str(target.get("username") or target["user_id"])
        await message.reply_text(
            f"✅ Progression de {target_name} ajustée au niveau {result['level']} "
            f"({result['xp']} XP)."
        )


async def revoke_item(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return
    target, remaining, error = await resolve_admin_target(
        update, context, list(context.args or []), allow_self=True
    )
    if target is None:
        await message.reply_text(
            f"{error}\nUsage: /revoke_item [@username] [id_item|nom|all]."
        )
        return
    result = await database_for(context).admin_revoke_item(
        int(target["user_id"]),
        chat.id,
        " ".join(remaining).casefold() if remaining else None,
    )
    if result["status"] == "missing":
        await message.reply_text("Profil introuvable dans cette conversation.")
    elif result["status"] == "missing_item":
        await message.reply_text("Cet article n’est pas dans l’inventaire du joueur.")
    elif result["item_id"] == "all":
        target_name = str(target.get("username") or target["user_id"])
        await message.reply_text(
            f"✅ Inventaire de {target_name} vidé ({result['count']} références retirées)."
        )
    else:
        target_name = str(target.get("username") or target["user_id"])
        await message.reply_text(
            f"✅ Un exemplaire de {result['name']} retiré de l’inventaire de {target_name}."
        )


async def admin_reset(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    if not await require_admin_dev_mode(update, context, message):
        return

    target, remaining, error = await resolve_admin_target(
        update, context, list(context.args or [])
    )
    if target is None:
        await message.reply_text(
            f"{error}\nUsage: réponds au joueur avec /admin_reset [repair|full CONFIRMER], "
            "ou /admin_reset @username [repair|full CONFIRMER]."
        )
        return

    mode = remaining[0].casefold() if remaining else "repair"
    if mode == "repair" and len(remaining) <= 1:
        result = await database_for(context).admin_repair_player(
            int(target["user_id"]), chat.id
        )
        if result == "missing":
            await message.reply_text("Profil introuvable dans cette conversation.")
            return
        await message.reply_text(
            "🩹 Profil réparé. Cash, banque, niveau, XP et inventaire sont préservés."
        )
        return
    if mode == "full":
        if len(remaining) != 2 or remaining[1] != "CONFIRMER":
            await message.reply_text(
                "⚠️ Cette action efface progression et inventaire. "
                "Pour confirmer, renvoie /admin_reset full CONFIRMER en réponse "
                "au joueur ciblé, ou /admin_reset @username full CONFIRMER."
            )
            return
        result = await database_for(context).admin_reset_player(
            int(target["user_id"]), chat.id
        )
        if result == "missing":
            await message.reply_text("Profil introuvable dans cette conversation.")
            return
        await message.reply_text(
            "♻️ Profil réinitialisé dans cette conversation uniquement."
        )
        return

    await message.reply_text(
        "Mode inconnu. Choisis repair, ou full CONFIRMER pour remettre le profil à zéro."
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
    god_mode = await developer_mode_enabled(context, actor_info["id"])
    dev_badge = " [DEV]" if god_mode else ""
    inventory_text = (
        ", ".join(f"{item['name']} ×{item['quantity']}" for item in items)
        if items
        else "vide"
    )
    remaining_seconds = prison_remaining_seconds(player)
    if god_mode:
        status = "Immunité active — aucune peine en Mode Développeur"
    elif remaining_seconds:
        status = f"En taule à Bordeaux/RDP — encore {remaining_seconds} s"
    elif player["in_prison_until"]:
        status = "Libre (ta peine est finie, c'est carré)"
    else:
        status = "Libre"

    await message.reply_text(
        f"📍 Profil de {actor_info['name']}{dev_badge}\n"
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


def market_rate_label(percent: float) -> str:
    if percent >= 0:
        return f"🟢 +{percent:.2f}%"
    return f"🔴 {percent:.2f}%"


def share_quantity_label(quantity: float) -> str:
    return f"{quantity:.6f}".rstrip("0").rstrip(".") or "0"


def format_market_snapshot(snapshot: dict[str, object]) -> str:
    assets = cast(list[dict[str, object]], snapshot["assets"])
    portfolio = cast(dict[str, float], snapshot["portfolio"])
    lines = [
        "📈 Bourse de Montréal — cours actualisés",
        (
            f"Portefeuille : valeur {portfolio['value']:.2f}$ / "
            f"investi {portfolio['cost_basis']:.2f}$ — "
            f"{market_rate_label(portfolio['profit_percent'])} "
            f"({portfolio['profit']:+.2f}$)"
        ),
    ]
    for asset in assets:
        event = str(asset["last_event"])
        lines.append(
            f"\n{asset['symbol']} — {asset['name']} : "
            f"{asset['current_price']:.2f}$ "
            f"({market_rate_label(asset['market_change_percent'])} depuis l’ouverture)"
            + (f" — 📣 {event}" if event else "")
        )
        quantity = float(asset["quantity"])
        if quantity:
            lines.append(
                f"  Tes parts : {share_quantity_label(quantity)} — "
                f"prix moyen {float(asset['average_cost']):.2f}$ — "
                f"{market_rate_label(float(asset['profit_percent']))} "
                f"({float(asset['profit']):+.2f}$)"
            )
    lines.extend(
        [
            "\nAchat : /invest buy BTC 0.01",
            "Vente : /invest sell BTC 0.005 (ou all pour cet actif)",
            "Tout vendre : /invest sell all",
        ]
    )
    return "\n".join(lines)


def parse_market_quantity(value: str) -> float | None:
    try:
        quantity = Decimal(value)
    except InvalidOperation:
        return None
    if (
        not quantity.is_finite()
        or quantity <= 0
        or quantity.as_tuple().exponent < -6
    ):
        return None
    return float(quantity)


async def execute_investment_trade(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    arguments: list[str],
) -> None:
    actor = await player_context(update, context)
    if actor is None:
        return
    actor_info, chat, message = actor
    if not arguments:
        snapshot = await database_for(context).get_market_snapshot(
            actor_info["id"], chat.id
        )
        await message.reply_text(format_market_snapshot(snapshot))
        return

    side = arguments[0].casefold()
    if side not in {"buy", "sell"}:
        await message.reply_text(
            "Usage : /invest pour les cours, /invest buy <actif> <parts>, "
            "/invest sell <actif> <parts/all> ou /invest sell all."
        )
        return
    if side == "sell" and len(arguments) == 2 and arguments[1].casefold() == "all":
        result = await database_for(context).sell_all_investments(
            actor_info["id"], chat.id
        )
        if result["status"] == "empty":
            await message.reply_text("Ton portefeuille ne contient aucune part à vendre.")
        elif result["status"] == "missing":
            await message.reply_text("Profil introuvable. Tape /start avant d’investir.")
        elif result["status"] == "limit":
            await message.reply_text("La vente dépasserait la limite de solde autorisée.")
        else:
            await message.reply_text(
                f"✅ Vente totale effectuée : {result['count']} actifs liquidés "
                f"pour {result['total']}$.\nCash disponible : {result['cash']} $."
            )
        return

    if side == "buy" and len(arguments) != 3:
        await message.reply_text("Usage : /invest buy <actif> <nombre_de_parts>.")
        return
    if side == "sell" and len(arguments) != 3:
        await message.reply_text(
            "Usage : /invest sell <actif> <nombre_de_parts/all>."
        )
        return
    symbol = arguments[1].upper()
    amount_text = arguments[2]
    quantity = (
        None
        if side == "sell" and amount_text.casefold() == "all"
        else parse_market_quantity(amount_text)
    )
    if quantity is None and not (side == "sell" and amount_text.casefold() == "all"):
        await message.reply_text("Le nombre de parts doit être positif (jusqu’à 6 décimales).")
        return

    result = await database_for(context).trade_investment(
        actor_info["id"], chat.id, symbol, side, quantity
    )
    status = result["status"]
    if status == "missing":
        await message.reply_text("Profil introuvable. Tape /start avant d’investir.")
    elif status == "unknown_asset":
        await message.reply_text("Actif inconnu. Utilise /invest pour voir le marché.")
    elif status == "funds":
        await message.reply_text(
            f"Cash insuffisant : il faut {result['total']}$ et tu as {result['cash']} $."
        )
    elif status == "no_position":
        await message.reply_text(f"Tu ne détiens aucune part de {symbol}.")
    elif status == "quantity":
        await message.reply_text(
            f"Quantité indisponible : tu détiens {share_quantity_label(float(result['available']))} part(s)."
        )
    elif status == "minimum":
        await message.reply_text(
            "Cette quantité est trop faible pour une transaction d’au moins 1$."
        )
    elif status == "limit":
        await message.reply_text("La transaction dépasserait la limite de solde autorisée.")
    elif status == "invalid":
        await message.reply_text("Transaction invalide.")
    elif side == "buy":
        await message.reply_text(
            f"✅ Achat de {share_quantity_label(float(result['quantity']))} "
            f"{symbol} à {float(result['price']):.2f}$ la part — "
            f"total {result['total']}$.\nCash disponible : {result['cash']} $."
        )
    else:
        profit = float(result["profit"])
        await message.reply_text(
            f"✅ Vente de {share_quantity_label(float(result['quantity']))} "
            f"{symbol} à {float(result['price']):.2f}$ la part — "
            f"encaissé {result['total']}$.\n"
            f"Résultat de ces parts : {market_rate_label(float(result['profit_percent']))} "
            f"({profit:+.2f}$). Cash : {result['cash']} $."
        )


async def invest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await execute_investment_trade(update, context, list(context.args or []))


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
    god_mode = await developer_mode_enabled(context, actor_info["id"])
    jail_seconds = prison_remaining_seconds(player)
    if jail_seconds and not god_mode:
        await message.reply_text(
            f"T'es encore à Bordeaux/RDP pour {jail_seconds} seconde(s). "
            "Réessaie quand le compteur tombe à zéro."
        )
        return
    available = actions_available(player["level"], bool(effects["has_obd"]))
    jugg_buttons = []
    for tier in JUGG_TIERS:
        if player["level"] < tier.min_level:
            continue
        cooldown_label = (
            "cooldown ignoré [DEV]"
            if god_mode
            else f"cooldown {tier.cooldown_seconds}s"
        )
        jugg_buttons.append(
            [
                InlineKeyboardButton(
                    f"{tier.title} — jusqu'à {tier.success_reward}$ "
                    f"(+{tier.xp} XP, risque {1 - tier.success_chance:.0%}, "
                    f"{cooldown_label})",
                    callback_data=f"jugg:{actor_info['id']}:{tier.tier_id}",
                )
            ]
        )
    keyboard = jugg_buttons + [
        [
            InlineKeyboardButton(
                spec.title,
                callback_data=f"game:{actor_info['id']}:{action_id}",
            )
        ]
        for action_id, spec in available
    ]
    next_jugg_tier = next(
        (tier for tier in JUGG_TIERS if player["level"] < tier.min_level),
        None,
    )
    next_action = min(
        (
            spec for spec in ACTION_SPECS.values()
            if player["level"] < spec.min_level
        ),
        key=lambda spec: spec.min_level,
        default=None,
    )
    notices = []
    if next_jugg_tier is not None:
        notices.append(
            f"🔒 Prochain palier Jugg : {next_jugg_tier.title} "
            f"(niveau {next_jugg_tier.min_level})."
        )
    if next_action is not None:
        notices.append(
            f"🔒 Prochain défi arcade : {next_action.title} "
            f"(niveau {next_action.min_level})."
        )
    equipment_locked = any(
        spec.requires_obd
        and player["level"] >= spec.min_level
        and not effects["has_obd"]
        for spec in ACTION_SPECS.values()
    )
    if equipment_locked:
        notices.append("🧰 Certains défis de véhicules exigent le module OBD du /shop.")
    await message.reply_text(
        f"Choisis ton défi d'arcade, chef — niveau {player['level']}.\n"
        "Paliers Business : progression par niveau, récompenses fixes et risques "
        "simulés."
        + ("\n" + "\n".join(notices) if notices else ""),
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

    database = database_for(context)
    player = await database.get_player(actor_info["id"], chat.id)
    if player is None:
        await edit_callback_message(query, "Profil introuvable. Tape /start.")
        return
    if player["level"] < tier.min_level:
        await edit_callback_message(
            query,
            f"🔒 {tier.title} est disponible au niveau {tier.min_level}. "
            f"Ton niveau actuel : {player['level']}.",
        )
        return
    outcome = resolve_jugg(tier_id)
    effects = await database.get_item_effects(actor_info["id"], chat.id)
    xp_gain = (
        xp_with_bonus(tier.xp, int(effects["xp_bonus"]))
        if outcome.success
        else 0
    )
    god_mode = await developer_mode_enabled(context, actor_info["id"])
    applied = await database.apply_jugg_result(
        actor_info["id"],
        chat.id,
        tier.cooldown_seconds,
        outcome.cash_delta,
        outcome.heat_delta,
        outcome.prison_seconds,
        xp_gain,
        bypass_cooldown=god_mode,
        god_mode=god_mode,
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
    if god_mode and outcome.prison_seconds:
        formatted_result = (
            "🧪 Mode Développeur: l’incident est neutralisé, aucune peine appliquée."
        )
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
        + (
            "⏱️ Cooldown ignoré en Mode Développeur."
            if god_mode
            else f"⏱️ Prochain palier dans {applied['cooldown_seconds']}s."
        )
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
    god_mode = await developer_mode_enabled(context, actor_info["id"])
    jail_seconds = prison_remaining_seconds(player)
    if jail_seconds and not god_mode:
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
        god_mode=god_mode,
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
        (
            "🧪 Mode Développeur: l’incident est neutralisé, aucune peine appliquée."
            if god_mode and result.prison_seconds
            else result.message
        )
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
                [shop_back_button(user_id, shop_item_route(item))]
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
            "protection": "Protections balistiques",
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
    god_mode = await developer_mode_enabled(context, actor_info["id"])
    result = await database.complete_vehicle_drive(
        actor_info["id"],
        chat.id,
        random.SystemRandom().random(),
        random.SystemRandom().random(),
        random.SystemRandom().random(),
        bypass_cooldown=god_mode,
        god_mode=god_mode,
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
    god_mode = await developer_mode_enabled(context, actor_info["id"])
    result = await database_for(context).repair_vehicle_rental(
        actor_info["id"], chat.id, free=god_mode
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
        if god_mode:
            await message.reply_text(
                f"🛠️ {result['name']} remis en état sans frais. "
                "Tu peux reprendre la route."
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
    protection_reduction = float(effects.get("protection_reduction", 0.0))
    if protection_reduction:
        lines.append(
            f"🛡️ Protection anti-braquage : "
            f"{effects.get('protection_name')} "
            f"(−{protection_reduction:.0%} au taux adverse)"
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
    god_mode = await developer_mode_enabled(context, actor_info["id"])
    jail_seconds = prison_remaining_seconds(attacker)
    if jail_seconds and not god_mode:
        await message.reply_text(
            f"T'es encore à Bordeaux pour {jail_seconds} seconde(s). "
            "Le défi PVP sera dispo quand ta peine sera finie."
        )
        return
    if attacker["level"] < 20 and not god_mode:
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
        victim_protection=float(victim_effects.get("protection_reduction", 0.0)),
        victim_protection_item_id=(
            str(victim_effects["protection_item_id"])
            if victim_effects.get("protection_item_id")
            else None
        ),
        victim_protection_name=(
            str(victim_effects["protection_name"])
            if victim_effects.get("protection_name")
            else None
        ),
    )
    jail_seconds = (
        random.randint(30, 120)
        if result.prison_seconds
        else None
    )
    deadline = prison_deadline(jail_seconds) if jail_seconds else None
    fine = (
        random.randint(250, 1_000)
        if result.outcome in {"counter", "failed"}
        else result.fine
    )
    applied = await database.apply_pvp_result(
        actor_info["id"],
        victim["user_id"],
        result.outcome,
        fine,
        deadline,
        xp_with_bonus(result.xp_gain, int(attacker_effects["xp_bonus"])),
        result.transfer_rate,
        chat_id=chat.id,
        god_mode=god_mode,
    )
    if applied is None:
        await message.reply_text("Défi PVP impossible: cible invalide.")
    elif applied["status"] == "level":
        await message.reply_text("Le défi PVP est réservé aux joueurs niveau 20+.")
    elif applied["status"] == "prison":
        await message.reply_text("T'es en taule; aucun défi PVP possible pour le moment.")
    elif applied["status"] == "counter":
        prison_notice = (
            f"Tu prends {jail_seconds} secondes à Bordeaux"
            if not god_mode
            else "La peine est neutralisée par le Mode Développeur"
        )
        defense_name = victim_weapon["name"] or "sa riposte"
        await message.reply_text(
            f"💥 Riposte de jeu! @{victim['username']} bloque le braquage avec "
            f"{defense_name}. "
            f"{prison_notice} et verses "
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
        prison_notice = (
            f"Direction Bordeaux pour {jail_seconds} secondes"
            f" avec une amende de {applied.get('fine', 0)}$."
            if not god_mode
            else "🧪 Mode Développeur: aucune peine appliquée."
        )
        if result.protection_blocked:
            if result.protection_item_id == "shield_tactique":
                failure_message = (
                    f"🛡️ Le shield de @{victim['username']} bloque tout; "
                    "t'as aucune ouverture, repli immédiat ! Le SPVM arrive en trombe !"
                )
            else:
                protection_label = {
                    "gilet_leger": "son gilet léger",
                    "gilet_lourd": "son gilet lourd",
                }.get(result.protection_item_id, "son équipement de protection")
                failure_message = (
                    f"💥 @{victim['username']} encaisse le braquage grâce à "
                    f"{protection_label}; le SPVM arrive en trombe !"
                )
        else:
            failure_message = result.message
        await message.reply_text(
            f"{failure_message} {prison_notice}"
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
    god_mode = await developer_mode_enabled(context, actor_info["id"])
    jail_seconds = prison_remaining_seconds(attacker)
    if jail_seconds and not god_mode:
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
        bypass_cooldown=god_mode,
        god_mode=god_mode,
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
        if applied["prison_seconds"]
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
    if await developer_mode_enabled(context, actor_info["id"]):
        await message.reply_text(
            "🧪 Immunité active en Mode Développeur : aucune peine ni caution à gérer."
        )
        return
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
    if await developer_mode_enabled(context, actor_info["id"]):
        await message.reply_text(
            "🧪 Immunité active en Mode Développeur. Pas besoin de tenter une évasion."
        )
        return
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
    god_mode = await developer_mode_enabled(context, actor_info["id"])

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
            f"\n💼 {hustle.name} (/mission {hustle.mission_id})\n"
            f"Réussite: {hustle.success_chance:.0%} | "
            f"Gain fixe: {hustle.success_reward}$ | XP: {hustle.xp} | "
            f"Échec: -{hustle.failure_penalty}$ | "
            + (
                "Cooldown ignoré [DEV]"
                if god_mode
                else f"Cooldown: {hustle.cooldown_seconds}s"
            )
            + (" | véhicule requis" if hustle.mission_id == "colis" else "")
            for hustle in HUSTLES
        )
        await message.reply_text("\n".join(lines))
        return

    query = " ".join(args)
    hustle = find_hustle(query)
    if hustle is not None:
        await run_hustle(
            actor_info,
            chat.id,
            message,
            database,
            hustle,
            god_mode,
            god_mode,
        )
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
    if jail_seconds and not god_mode:
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
    if (
        not god_mode
        and rental is not None
        and int(rental.get("repair_due", 0)) > 0
    ):
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
            god_mode=god_mode,
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
            god_mode=god_mode,
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
            free_repair=god_mode,
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
            if not god_mode
            else "🧪 Mode Développeur: la peine est neutralisée."
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
    bypass_cooldown: bool = False,
    god_mode: bool = False,
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
        if (
            not god_mode
            and rental is not None
            and int(rental.get("repair_due", 0)) > 0
        ):
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
            bypass_cooldown=bypass_cooldown,
            god_mode=god_mode,
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
            free_repair=god_mode,
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
        + (
            "⏱️ Cooldown ignoré en Mode Développeur."
            if god_mode
            else f"⏱️ Prochain hustle dans {hustle.cooldown_seconds}s."
        )
        + f"{vehicle_notice}"
        + level_up_notice(int(player["level"]), int(updated_player["level"])),
        parse_mode="HTML",
    )


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
    if await developer_mode_enabled(context, actor_info["id"]):
        await edit_callback_message(
            query,
            "🧪 Immunité active en Mode Développeur: aucune caution à payer.",
        )
        return
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
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("profil", profil))
    application.add_handler(CommandHandler("bank", bank))
    application.add_handler(CommandHandler("deposit", deposit))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CommandHandler("give", give_command))
    application.add_handler(CommandHandler("invest", invest))
    application.add_handler(CommandHandler("jugg", jugg))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("inventory", inventory))
    application.add_handler(CommandHandler("drive", drive))
    application.add_handler(CommandHandler("repair", repair_vehicle))
    application.add_handler(CommandHandler("return", return_vehicle))
    application.add_handler(CommandHandler("braquer", braquer))
    application.add_handler(CommandHandler("shoot", shoot))
    application.add_handler(CommandHandler("top", top))
    application.add_handler(CommandHandler("prison", prison))
    application.add_handler(CommandHandler("evade", evade))
    application.add_handler(CommandHandler("missions", missions_command))
    application.add_handler(CommandHandler("mission", mission_command))
    application.add_handler(CommandHandler("casino", casino_command))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("devmode", devmode))
    application.add_handler(CommandHandler("give_cash", give_cash))
    application.add_handler(CommandHandler("give_xp", give_xp))
    application.add_handler(CommandHandler("set_level", set_level))
    application.add_handler(CommandHandler("give_item", give_item))
    application.add_handler(CommandHandler("revoke_cash", revoke_cash))
    application.add_handler(CommandHandler("revoke_xp", revoke_xp))
    application.add_handler(CommandHandler("revoke_level", revoke_level))
    application.add_handler(CommandHandler("revoke_item", revoke_item))
    application.add_handler(CommandHandler("admin_reset", admin_reset))
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
        CallbackQueryHandler(admin_callback, pattern=r"^admin:")
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
