"""Fictional, non-instructional game mechanics and progression catalogue."""

from dataclasses import dataclass
import math
import random
import time


MAX_LEVEL = 60
BAIL_COST = 300
jail_duration = 45
PRISON_DURATION_SECONDS = jail_duration
BASE_SUCCESS_RATE = 0.70
MAX_SUCCESS_RATE = 0.95


@dataclass(frozen=True)
class GameResult:
    message: str
    cash_delta: int
    heat_delta: int
    gamos: str | None = None
    prison_seconds: int | None = None
    xp_gain: int = 0
    success: bool = False


@dataclass(frozen=True)
class ActionSpec:
    title: str
    min_level: int
    success_chance: float
    reward: int
    xp: int
    heat: int
    success_message: str
    failure_message: str
    prison_seconds: int
    gamos: str | None = None
    requires_obd: bool = False


@dataclass(frozen=True)
class HustleSpec:
    mission_id: str
    name: str
    success_chance: float
    success_reward: int
    failure_penalty: int
    cooldown_seconds: int
    xp: int
    success_message: str


@dataclass(frozen=True)
class JuggTierSpec:
    tier_id: str
    title: str
    success_reward: int
    success_chance: float
    failure_fine: int
    cooldown_seconds: int
    success_heat: int
    xp: int


@dataclass(frozen=True)
class JuggResult:
    success: bool
    cash_delta: int
    heat_delta: int
    prison_seconds: int
    message: str


JUGG_TIERS = (
    JuggTierSpec("bloc", "⚡ Petit Hustle de Bloc", 1200, 0.85, 150, 15, 1, 30),
    JuggTierSpec(
        "centre_ville", "🔥 Gros Business Centre-Ville", 4500, 0.60, 700, 25, 3, 75
    ),
    JuggTierSpec(
        "international",
        "👑 Opération Internationale / Masterclass",
        15000,
        0.35,
        2500,
        30,
        5,
        150,
    ),
)


HUSTLES = (
    HustleSpec(
        "colis",
        "Mission logistique urbaine",
        0.85,
        115,
        25,
        20,
        5,
        "📦 Parcours logistique validé. Récompense : {cash}$ et progression créditée.",
    ),
    HustleSpec(
        "phishing",
        "Audit de sécurité numérique",
        0.70,
        275,
        30,
        25,
        10,
        "📲 Audit complété. Les indicateurs du scénario ont été vérifiés; "
        "récompense : {cash}$.",
    ),
    HustleSpec(
        "catalyseur",
        "Contrôle technique automobile",
        0.60,
        475,
        35,
        30,
        15,
        "🔧 Contrôle technique terminé dans le temps imparti. "
        "Récompense : {cash}$.",
    ),
    HustleSpec(
        "vapote",
        "Défi de service — kiosque",
        0.90,
        65,
        20,
        15,
        3,
        "🧃 Objectif de service atteint. Récompense : {cash}$.",
    ),
    HustleSpec(
        "depanneur",
        "Gestion des stocks — dépanneur",
        0.50,
        900,
        50,
        30,
        25,
        "🏪 Parcours de gestion complété. Tous les objectifs sont validés; "
        "récompense : {cash}$.",
    ),
    HustleSpec(
        "sneakers",
        "Circuit de collection — sneakers",
        0.75,
        400,
        25,
        25,
        15,
        "👟 Défi de collection terminé. Récompense : {cash}$.",
    ),
)


@dataclass(frozen=True)
class ShopItem:
    item_id: str
    name: str
    category: str
    price: int
    min_level: int
    xp_bonus: int = 0
    success_bonus: float = 0.0
    description: str = ""


@dataclass(frozen=True)
class PvpResult:
    outcome: str
    message: str
    prison_seconds: int = 0
    fine: int = 0
    xp_gain: int = 0
    transfer_rate: float = 0.0


@dataclass(frozen=True)
class ShootoutResult:
    winner: str
    attacker_chance: float
    police_intervention: bool
    xp_gain: int


ITEMS = (
    ShopItem(
        "tech_fleece",
        "Tech Fleece",
        "vetements_accessoires",
        250,
        1,
        xp_bonus=5,
        success_bonus=0.02,
        description="Ensemble confortable pour les longues soirées de jeu; +5% XP.",
    ),
    ShopItem(
        "moose_knuckles",
        "Moose Knuckles",
        "vetements_accessoires",
        800,
        10,
        xp_bonus=10,
        success_bonus=0.03,
        description="Manteau premium qui fait tourner les têtes au coin de rue; +10% XP.",
    ),
    ShopItem(
        "trapstar_bag",
        "Sacoche Trapstar",
        "vetements_accessoires",
        1500,
        20,
        xp_bonus=15,
        success_bonus=0.04,
        description="Sacoche de collection pour garder tes essentiels bien organisés; +15% XP.",
    ),
    ShopItem(
        "nike_tech_balaclava",
        "Cagoule Nike Tech noire",
        "vetements_accessoires",
        180,
        1,
        xp_bonus=2,
        success_bonus=0.01,
        description="Accessoire de sport noir au look sobre; +2% XP et +1% réussite.",
    ),
    ShopItem(
        "under_armour_balaclava",
        "Cagoule Under Armour noire",
        "vetements_accessoires",
        160,
        1,
        xp_bonus=2,
        success_bonus=0.01,
        description="Couche thermique légère pour les sessions dehors; +2% XP et +1% réussite.",
    ),
    ShopItem(
        "lacoste_go_fast_cap",
        "Casquette Lacoste Go Fast",
        "vetements_accessoires",
        220,
        2,
        xp_bonus=3,
        success_bonus=0.01,
        description="Casquette propre, visière courbée et logo qui ressort; +3% XP.",
    ),
    ShopItem(
        "adidas_tn_tracksuit",
        "Survêtement Adidas TN complet",
        "vetements_accessoires",
        850,
        10,
        xp_bonus=8,
        success_bonus=0.02,
        description="Ensemble coordonné pour arriver frais au spot; +8% XP.",
    ),
    ShopItem(
        "lacoste_live_tracksuit",
        "Survêtement Lacoste Live",
        "vetements_accessoires",
        950,
        12,
        xp_bonus=9,
        success_bonus=0.02,
        description="Coupe nette et finition premium pour le flex du week-end; +9% XP.",
    ),
    ShopItem(
        "gucci_crossbody",
        "Sacoche Gucci monogramme",
        "vetements_accessoires",
        1800,
        18,
        xp_bonus=12,
        success_bonus=0.03,
        description="Petite sacoche de marque pour transporter tes essentiels avec style; +12% XP.",
    ),
    ShopItem(
        "louis_vuitton_crossbody",
        "Sacoche Louis Vuitton",
        "vetements_accessoires",
        2400,
        22,
        xp_bonus=14,
        success_bonus=0.03,
        description="Pièce de collection au monogramme reconnaissable; +14% XP.",
    ),
    ShopItem(
        "rolex_watch",
        "Montre Rolex Submariner",
        "vetements_accessoires",
        12000,
        30,
        xp_bonus=18,
        success_bonus=0.04,
        description="Montre de prestige pour compléter le fit haut de gamme; +18% XP.",
    ),
    ShopItem(
        "cartier_replica_watch",
        "Montre style Cartier (réplique de jeu)",
        "vetements_accessoires",
        700,
        8,
        xp_bonus=5,
        success_bonus=0.01,
        description="Accessoire fantaisie de collection, sans valeur de marque; +5% XP.",
    ),
    ShopItem(
        "gold_plated_ring",
        'Bague "plaqué or" du Marché Jean-Talon',
        "vetements_accessoires",
        140,
        1,
        xp_bonus=2,
        success_bonus=0.01,
        description="Petit flash doré trouvé au marché; +2% XP.",
    ),
    ShopItem(
        "north_face_vest",
        "Doudoune sans manches The North Face",
        "vetements_accessoires",
        650,
        8,
        xp_bonus=7,
        success_bonus=0.02,
        description="La couche montréalaise parfaite pour les journées fraîches; +7% XP.",
    ),
    ShopItem(
        "tn_shark_sneakers",
        "TN Requin",
        "vetements_accessoires",
        480,
        6,
        xp_bonus=5,
        success_bonus=0.02,
        description="Paire sportive qui donne du caractère au fit; +5% XP.",
    ),
    ShopItem(
        "air_force_1_white",
        "Air Force 1 blanches",
        "vetements_accessoires",
        420,
        5,
        xp_bonus=4,
        success_bonus=0.02,
        description="Classique toute blanche, fraîche dès la sortie de boîte; +4% XP.",
    ),
    ShopItem(
        "prada_sunglasses",
        "Lunettes de soleil Prada Linea Rossa",
        "accessoire",
        850,
        14,
        xp_bonus=8,
        success_bonus=0.02,
        description="Design futuriste noir pour compléter ton look de soirée; accessoire cosmétique, sans effet de dissimulation; +8% XP.",
    ),
    ShopItem(
        "montreal_toque",
        "Tuque Montréal",
        "vetements_accessoires",
        85,
        1,
        xp_bonus=1,
        description="La tuque aux couleurs locales pour survivre au vent du fleuve; +1% XP.",
    ),
    ShopItem(
        "lacoste_live_hoodie",
        "Hoodie Lacoste Live",
        "vetements_accessoires",
        590,
        7,
        xp_bonus=6,
        success_bonus=0.02,
        description="Chandail doux avec une coupe clean pour le quotidien; +6% XP.",
    ),
    ShopItem(
        "gold_chain",
        "Chaîne dorée de quartier",
        "vetements_accessoires",
        320,
        4,
        xp_bonus=3,
        success_bonus=0.01,
        description="Chaîne brillante pour ajouter une touche au look; +3% XP.",
    ),
    ShopItem(
        "montreal_winter_parka",
        "Parka d'hiver Montréal",
        "vetements_accessoires",
        1350,
        16,
        xp_bonus=11,
        success_bonus=0.03,
        description="Grosse parka pour les journées sous zéro, style et chaleur; +11% XP.",
    ),
    ShopItem(
        "stealth_style_balaclava",
        "Cagoule Stealth noire (cosmétique)",
        "tete_visage",
        210,
        2,
        xp_bonus=2,
        success_bonus=0.01,
        description="Accessoire de look monochrome pour ton avatar; effet purement visuel, sans fonction anti-caméra.",
    ),
    ShopItem(
        "carhartt_paintball_beanie",
        "Bonnet Carhartt taché de paintball",
        "tete_visage",
        125,
        1,
        xp_bonus=2,
        description="Bonnet usé avec quelques traces de peinture de la dernière partie; +2% XP.",
    ),
    ShopItem(
        "gold_ny_cap",
        "Casquette NY dorée (accessoire de jeu)",
        "tete_visage",
        360,
        5,
        xp_bonus=4,
        success_bonus=0.01,
        description="Visière dorée façon collection pour un gros flex au spawn; +4% XP.",
    ),
    ShopItem(
        "cartier_buffs_sunglasses",
        'Lunettes "buffs" fumées style Cartier',
        "tete_visage",
        1100,
        15,
        xp_bonus=9,
        success_bonus=0.02,
        description="Lunettes de collection à verres fumés; aucun effet de vision nocturne; +9% XP.",
    ),
    ShopItem(
        "carhartt_canvas_cap",
        "Casquette Carhartt canvas",
        "tete_visage",
        145,
        1,
        xp_bonus=2,
        description="Casquette robuste pour les trajets dans le vent de Montréal; +2% XP.",
    ),
    ShopItem(
        "north_face_700_nuptse",
        "Doudoune The North Face 700 Nuptse",
        "corps_vestes",
        1900,
        20,
        xp_bonus=15,
        success_bonus=0.03,
        description="Grosse doudoune façon uniforme d'hiver montréalais; +15% XP et +3% réussite.",
    ),
    ShopItem(
        "nike_tech_full_black_windbreaker",
        "Coupe-vent Nike Tech Fleece full black",
        "corps_vestes",
        780,
        10,
        xp_bonus=8,
        success_bonus=0.02,
        description="Coupe-vent noir au look clean pour les soirées fraîches; +8% XP.",
    ),
    ShopItem(
        "lacoste_classic_tracksuit",
        "Survêtement complet Lacoste classique",
        "corps_vestes",
        1050,
        14,
        xp_bonus=10,
        success_bonus=0.02,
        description="Ensemble classique bien coordonné pour arriver frais au rendez-vous; +10% XP.",
    ),
    ShopItem(
        "vegan_leather_jacket",
        "Veste en cuir végane du Marché aux Puces",
        "corps_vestes",
        390,
        4,
        xp_bonus=4,
        success_bonus=0.01,
        description="Veste vintage chinée aux puces, cuir végane et attitude rétro; +4% XP.",
    ),
    ShopItem(
        "canadiens_retro_91_jersey",
        "Maillot rétro du Canadien floqué 91",
        "corps_vestes",
        525,
        6,
        xp_bonus=5,
        description="Maillot hommage aux soirées du Centre Bell; +5% XP.",
    ),
    ShopItem(
        "montreal_hockey_scarf",
        "Écharpe du Canadien de Montréal",
        "corps_vestes",
        95,
        1,
        xp_bonus=1,
        description="Écharpe de partisan pour les matchs et les nuits froides; +1% XP.",
    ),
    ShopItem(
        "gucci_lv_crossbody_game",
        "Sacoche monogramme Gucci / Louis V (jeu)",
        "accessoires_sacoches",
        2100,
        20,
        xp_bonus=13,
        success_bonus=0.03,
        description="Sacoche de collection pour le style; aucun espace de stockage supplémentaire en jeu; +13% XP.",
    ),
    ShopItem(
        "gucci_gold_buckle_belt",
        "Ceinture boucle dorée style Gucci",
        "accessoires_sacoches",
        680,
        9,
        xp_bonus=6,
        success_bonus=0.01,
        description="Grosse boucle dorée qui complète le fit du samedi soir; +6% XP.",
    ),
    ShopItem(
        "mile_end_silver_teardrop_chain",
        'Chaîne argent "goutte d\'eau" du Mile-End',
        "accessoires_sacoches",
        430,
        5,
        xp_bonus=4,
        success_bonus=0.01,
        description="Chaîne argentée au pendentif discret, dénichée dans une boutique du Mile-End; +4% XP.",
    ),
    ShopItem(
        "apple_watch_game_replica",
        "Montre connectée style Apple Watch (accessoire de jeu)",
        "accessoires_sacoches",
        550,
        7,
        xp_bonus=5,
        success_bonus=0.01,
        description="Réplique fantaisie pour avatar, sans marque ni fonction connectée réelle; +5% XP.",
    ),
    ShopItem(
        "north_face_crossbody_pouch",
        "Sacoche urbaine The North Face",
        "accessoires_sacoches",
        310,
        3,
        xp_bonus=3,
        description="Petite sacoche de style pour les essentiels de ton personnage; +3% XP.",
    ),
    ShopItem(
        "silver_hoop_earrings",
        "Créoles argentées du Plateau",
        "accessoires_sacoches",
        115,
        1,
        xp_bonus=1,
        description="Petit accessoire brillant pour peaufiner le look de l'avatar; +1% XP.",
    ),
    ShopItem(
        "tn_shark_blue_black",
        "Nike TN Requin dégradé bleu-noir",
        "pieds",
        560,
        8,
        xp_bonus=6,
        success_bonus=0.02,
        description="Dégradé bleu-noir qui ressort dans le métro comme au spot; +6% XP.",
    ),
    ShopItem(
        "air_force_1_pristine_white",
        "Air Force 1 blanches — zéro trace de boue",
        "pieds",
        445,
        5,
        xp_bonus=4,
        success_bonus=0.02,
        description="Paire blanche immaculée pour garder le fit propre; +4% XP.",
    ),
    ShopItem(
        "adidas_slides_socks",
        "Claquettes Adidas et chaussettes",
        "pieds",
        90,
        1,
        xp_bonus=1,
        description="Le combo confort pour chiller au bloc en mode détente; +1% XP.",
    ),
    ShopItem(
        "winter_boots_montreal",
        "Bottes d'hiver antidérapantes Montréal",
        "pieds",
        240,
        2,
        xp_bonus=2,
        success_bonus=0.01,
        description="Bottes de saison pour traverser les trottoirs enneigés du jeu; +2% XP.",
    ),
    ShopItem(
        "retro_basketball_sneakers",
        "Baskets rétro édition terrain",
        "pieds",
        350,
        4,
        xp_bonus=3,
        success_bonus=0.01,
        description="Paire rétro inspirée des terrains de quartier; +3% XP.",
    ),
    ShopItem(
        "cartier_buffs",
        "Lunettes Cartier Buffalo Wood Vintage",
        "accessoire",
        3500,
        20,
        xp_bonus=12,
        success_bonus=0.03,
        description="Monture vintage aux finitions dorées pour un flex remarquable au centre-ville; +12% XP.",
    ),
    ShopItem(
        "louis_vuitton_millionaire",
        "Lunettes Louis Vuitton Millionaires",
        "accessoire",
        1800,
        18,
        xp_bonus=10,
        success_bonus=0.02,
        description="Monture iconique et charnières dorées gravées pour une allure premium; +10% XP.",
    ),
    ShopItem(
        "gucci_oversized",
        "Lunettes aviateur Gucci dorées",
        "accessoire",
        950,
        14,
        xp_bonus=8,
        success_bonus=0.02,
        description="Monture aviateur dorée, style grand patron sur Sainte-Catherine; +8% XP.",
    ),
    ShopItem(
        "moncler_maya",
        "Doudoune brillante Moncler Maya",
        "vetement",
        2200,
        22,
        xp_bonus=16,
        success_bonus=0.03,
        description="Doudoune signature pour traverser l'hiver montréalais avec un fit haut de gamme; +16% XP.",
    ),
    ShopItem(
        "dior_oblique_puffer",
        "Doudoune réversible Dior Oblique",
        "vetement",
        4200,
        30,
        xp_bonus=20,
        success_bonus=0.04,
        description="Duvet réversible au motif tissé pour les soirées de la haute sphère; +20% XP.",
    ),
    ShopItem(
        "balenciaga_tracksuit",
        "Survêtement Balenciaga Destroyed",
        "vetement",
        2500,
        24,
        xp_bonus=17,
        success_bonus=0.03,
        description="Ensemble au fini volontairement usé, luxe décontracté sans faire trop propre; +17% XP.",
    ),
    ShopItem(
        "off_white_varsity",
        "Veste Teddy Varsity Off-White",
        "vetement",
        1900,
        20,
        xp_bonus=14,
        success_bonus=0.03,
        description="Laine épaisse, broderies graphiques et flèches dans le dos; +14% XP.",
    ),
    ShopItem(
        "palm_angels_track",
        "Ensemble Palm Angels violet",
        "vetement",
        1100,
        15,
        xp_bonus=10,
        success_bonus=0.02,
        description="Veste zippée et pantalon large pour flex au studio; +10% XP.",
    ),
    ShopItem(
        "lv_discovery_bag",
        "Sacoche Louis Vuitton Discovery",
        "accessoire",
        2100,
        20,
        xp_bonus=13,
        success_bonus=0.03,
        description="Sacoche de luxe pour compléter le fit; effet cosmétique, sans capacité de stockage en jeu; +13% XP.",
    ),
    ShopItem(
        "gucci_gg_belt_bag",
        "Sac banane Gucci Supreme GG",
        "accessoire",
        1400,
        17,
        xp_bonus=11,
        success_bonus=0.02,
        description="À porter en bandoulière pour un look streetwear bien monté; +11% XP.",
    ),
    ShopItem(
        "prada_re_edition",
        "Sac bandoulière Prada Re-Edition",
        "accessoire",
        1650,
        18,
        xp_bonus=12,
        success_bonus=0.03,
        description="Silhouette en nylon au triangle émaillé, la touche clean du quartier; +12% XP.",
    ),
    ShopItem(
        "rolex_datejust",
        "Rolex Datejust 41 mm acier et or",
        "luxe",
        14500,
        35,
        xp_bonus=22,
        success_bonus=0.05,
        description="Cadran bleu soleil, une pièce horlogère pour célébrer tes grosses manches; +22% XP.",
    ),
    ShopItem(
        "audemars_piguet_royaloak",
        "Audemars Piguet Royal Oak Offshore",
        "luxe",
        28000,
        45,
        xp_bonus=25,
        success_bonus=0.06,
        description="Lunette octogonale et présence imposante, le flex ultime de ta collection; +25% XP.",
    ),
    ShopItem(
        "cartier_tank",
        "Montre Cartier Tank Must",
        "luxe",
        4800,
        28,
        xp_bonus=18,
        success_bonus=0.04,
        description="Élégance intemporelle pour compléter une tenue soignée; +18% XP.",
    ),
    ShopItem(
        "patek_philippe_nautilus",
        "Patek Philippe Nautilus acier",
        "luxe",
        35000,
        50,
        xp_bonus=28,
        success_bonus=0.07,
        description="Le Saint Graal horloger, réservé aux joueurs qui ont fini le grind; +28% XP.",
    ),
    ShopItem("bixi", "BIXI", "vehicules", 50, 1, success_bonus=0.02),
    ShopItem("lime_scooter", "Scooter Lime", "vehicules", 400, 10, success_bonus=0.03),
    ShopItem("civic_2008", "Civic 2008", "vehicules", 2500, 20, success_bonus=0.04),
    ShopItem("rav4", "RAV4", "vehicules", 15000, 35, success_bonus=0.05),
    ShopItem("urus", "Urus", "vehicules", 120000, 50, success_bonus=0.06),
    ShopItem(
        "audi_rs3_nardo",
        "Audi RS3 Sportback (Nardo Grey)",
        "vehicules",
        45000,
        1,
        description="Le cinq-cylindres au grondement reconnaissable, finition Nardo Grey pour un look discret et sportif dans les rues de Montréal.",
    ),
    ShopItem(
        "bmw_m3_g80_competition",
        "BMW M3 Competition (G80 full black)",
        "vehicules",
        68000,
        1,
        description="Berline musclée entièrement noire, avec une présence qui impose le respect au rassemblement automobile.",
    ),
    ShopItem(
        "mercedes_c63_amg_w204",
        "Mercedes-Benz C63 AMG W204",
        "vehicules",
        38000,
        1,
        description="L'AMG au V8 atmosphérique et à la sonorité culte, un classique qui fait tourner les têtes sur le boulevard.",
    ),
    ShopItem(
        "bmw_m4_f82",
        "BMW M4 Coupé (F82)",
        "vehicules",
        55000,
        1,
        description="Coupé allemand propre et agressif, le choix des stratèges qui préfèrent le style net à l'ostentation.",
    ),
    ShopItem(
        "porsche_911_turbo_s_992",
        "Porsche 911 Turbo S (992)",
        "vehicules",
        195000,
        1,
        description="Icône sportive de prestige, réservée aux grosses économies et aux arrivées remarquées au centre-ville.",
    ),
    ShopItem(
        "honda_civic_si_k24",
        "Honda Civic Si (K24 swap)",
        "vehicules",
        12000,
        1,
        description="La compacte sport inspirée des builds de Cégep, avec une préparation fictive et un look tuning assumé.",
    ),
    ShopItem(
        "toyota_supra_mk4",
        "Toyota Supra MK4 biturbo",
        "vehicules",
        85000,
        1,
        description="Légende japonaise des années 90, silhouette intemporelle et présence de star au rassemblement.",
    ),
    ShopItem(
        "nissan_skyline_gtr_r34",
        "Nissan Skyline GT-R R34",
        "vehicules",
        120000,
        1,
        description="GT-R de légende, rareté japonaise convoitée par les collectionneurs de l'arcade montréalaise.",
    ),
    ShopItem(
        "subaru_impreza_wrx_sti",
        "Subaru Impreza WRX STI",
        "vehicules",
        28000,
        1,
        description="Sportive à transmission intégrale au look rallye, taillée pour les routes enneigées de l'hiver québécois.",
    ),
    ShopItem(
        "mercedes_amg_g63_brabus",
        "Mercedes-AMG G63 Brabus",
        "vehicules",
        160000,
        1,
        description="Le grand 4x4 carré au kit spectaculaire, mastodonte de luxe qui se remarque à chaque coin de rue.",
    ),
    ShopItem(
        "bmw_x5_m_competition",
        "BMW X5 M Competition",
        "vehicules",
        82000,
        1,
        description="SUV allemand spacieux et sportif, parfait pour rouler confortablement avec toute l'équipe dans le jeu.",
    ),
    ShopItem(
        "range_rover_sport_svr",
        "Range Rover Sport SVR",
        "vehicules",
        95000,
        1,
        description="SUV britannique au V8 sonore et à la silhouette imposante, un classique des arrivées VIP.",
    ),
    ShopItem("tear_gas", "Lacrymo (objet de jeu)", "armes", 150, 1, success_bonus=0.03),
    ShopItem(
        "glock_19",
        "Glock 19 Gen 5 — édition arcade",
        "armes_poing",
        1200,
        1,
        success_bonus=0.08,
        description="Objet fictif de collection, reconnu comme équipement dans les défis PVP simulés.",
    ),
    ShopItem(
        "beretta_m9",
        "Beretta M9 9 mm — édition arcade",
        "armes_poing",
        950,
        1,
        description="Pièce classique du catalogue d'arcade, sans effet hors des mécaniques du jeu.",
    ),
    ShopItem(
        "sw_357_magnum",
        "Smith & Wesson .357 Magnum — édition arcade",
        "armes_poing",
        1600,
        1,
        description="Objet de collection virtuel pour compléter ton inventaire de joueur.",
    ),
    ShopItem(
        "desert_eagle_50",
        "Desert Eagle .50 AE doré — édition arcade",
        "armes_poing",
        4500,
        1,
        description="Pièce dorée de prestige; son effet est limité aux défis PVP simulés.",
    ),
    ShopItem(
        "mini_uzi_9mm",
        "Mini Uzi 9 mm — édition arcade",
        "armes_lourdes",
        2800,
        1,
        description="Objet fictif de collection, reconnu comme équipement dans les défis PVP simulés.",
    ),
    ShopItem(
        "mac_10",
        "MAC-10 — édition arcade",
        "armes_lourdes",
        3200,
        1,
        description="Pièce de collection du catalogue arcade, sans détail d'utilisation réelle.",
    ),
    ShopItem(
        "mp5k",
        "MP5 K — édition arcade",
        "armes_lourdes",
        5000,
        1,
        description="Objet virtuel haut de gamme pour les défis PVP fictifs du quartier.",
    ),
    ShopItem(
        "thompson_tommy_gun",
        "Thompson vintage — édition arcade",
        "armes_lourdes",
        7500,
        1,
        description="Objet rétro de collection, apprécié pour son style dans le jeu.",
    ),
    ShopItem(
        "ak_47",
        "AK-47 — édition arcade",
        "armes_longues",
        8500,
        1,
        success_bonus=0.15,
        description="Objet fictif de collection, reconnu comme équipement dans les défis PVP simulés.",
    ),
    ShopItem(
        "ar15_custom",
        "AR-15 custom — édition arcade",
        "armes_longues",
        11000,
        1,
        description="Pièce custom de collection, avec un bonus limité aux mécaniques du jeu.",
    ),
    ShopItem(
        "remington_870",
        "Remington 870 — édition arcade",
        "armes_longues",
        2200,
        1,
        description="Objet virtuel classique du catalogue, destiné aux défis fictifs.",
    ),
    ShopItem(
        "tactical_vest_level_3",
        "Gilet de protection renforcé — niveau III+ (jeu)",
        "protection",
        1800,
        1,
        description="Équipement de défense fictif; améliore la riposte dans les défis PVP simulés.",
    ),
    ShopItem(
        "fast_ballistic_helmet",
        "Casque de protection FAST — édition arcade",
        "protection",
        1200,
        1,
        description="Équipement de protection de collection, avec effet uniquement dans le jeu.",
    ),
    ShopItem("bolt_cutter", "Coupe-boulon", "matos", 100, 1, success_bonus=0.02),
    ShopItem("obd_device", "Boîtier OBD Keyless", "matos", 4000, 20, success_bonus=0.03),
    ShopItem("gps_jammer", "Brouilleur GPS", "matos", 8000, 35, success_bonus=0.04),
)

ITEMS += (
    ShopItem(
        "nike_air_max_tech",
        "Nike Air Max — édition urbaine",
        "pieds",
        620,
        8,
        xp_bonus=5,
        success_bonus=0.02,
        description="Sneakers de collection au coloris exclusif; bonus appliqués uniquement dans l'arcade.",
    ),
    ShopItem(
        "nike_crewneck",
        "Nike Sportswear Crewneck",
        "corps_vestes",
        540,
        6,
        xp_bonus=5,
        description="Chandail confortable au style sport classique; bonus d'XP en jeu.",
    ),
    ShopItem(
        "adidas_forum_low",
        "Adidas Forum Low",
        "pieds",
        480,
        5,
        xp_bonus=4,
        description="Silhouette rétro et finition soignée pour compléter ta collection.",
    ),
    ShopItem(
        "adidas_originals_hoodie",
        "Adidas Originals Hoodie",
        "corps_vestes",
        650,
        8,
        xp_bonus=6,
        success_bonus=0.01,
        description="Hoodie Originals à la coupe classique; bonus réservés aux mécaniques du jeu.",
    ),
    ShopItem(
        "prada_re_nylon_jacket",
        "Prada Re-Nylon — veste",
        "corps_vestes",
        2600,
        24,
        xp_bonus=16,
        success_bonus=0.03,
        description="Veste premium au design épuré; pièce cosmétique avec bonus d'arcade.",
    ),
    ShopItem(
        "prada_sport_cap",
        "Prada Sport — casquette",
        "tete_visage",
        1250,
        15,
        xp_bonus=9,
        success_bonus=0.02,
        description="Casquette de collection, finition sobre et logo discret.",
    ),
    ShopItem(
        "gucci_ace_sneakers",
        "Gucci Ace — sneakers",
        "pieds",
        2200,
        22,
        xp_bonus=15,
        success_bonus=0.03,
        description="Sneakers premium pour une collection haut de gamme.",
    ),
    ShopItem(
        "gucci_wool_scarf",
        "Gucci — écharpe en laine",
        "accessoires_sacoches",
        1450,
        18,
        xp_bonus=11,
        success_bonus=0.02,
        description="Écharpe de collection adaptée aux hivers montréalais.",
    ),
    ShopItem(
        "louis_vuitton_trainer",
        "Louis Vuitton Trainer",
        "pieds",
        3900,
        30,
        xp_bonus=20,
        success_bonus=0.04,
        description="Sneakers de luxe au design distinctif; bonus limités à l'arcade.",
    ),
    ShopItem(
        "louis_vuitton_card_holder",
        "Louis Vuitton — porte-cartes",
        "accessoires_sacoches",
        1150,
        15,
        xp_bonus=9,
        description="Petit accessoire de collection au fini premium.",
    ),
    ShopItem(
        "lacoste_polo_classic",
        "Lacoste — polo classique",
        "vetement",
        520,
        6,
        xp_bonus=5,
        success_bonus=0.01,
        description="Polo intemporel à porter en toute saison.",
    ),
    ShopItem(
        "lacoste_croco_cap",
        "Lacoste — casquette Croco",
        "tete_visage",
        350,
        4,
        xp_bonus=3,
        description="Casquette légère, style sport et finition nette.",
    ),
    ShopItem(
        "moncler_maya_vest",
        "Moncler Maya — veste",
        "corps_vestes",
        3200,
        28,
        xp_bonus=19,
        success_bonus=0.04,
        description="Doudoune de collection pour les sorties par temps froid.",
    ),
    ShopItem(
        "burberry_check_scarf",
        "Burberry — écharpe à carreaux",
        "accessoires_sacoches",
        1700,
        20,
        xp_bonus=13,
        success_bonus=0.02,
        description="Écharpe classique au motif reconnaissable.",
    ),
    ShopItem(
        "off_white_arrow_hoodie",
        "Off-White — hoodie Arrow",
        "corps_vestes",
        2100,
        22,
        xp_bonus=15,
        success_bonus=0.03,
        description="Hoodie streetwear de collection, bonus utilisables dans le jeu.",
    ),
    ShopItem(
        "balenciaga_runner",
        "Balenciaga Runner",
        "pieds",
        2800,
        26,
        xp_bonus=17,
        success_bonus=0.03,
        description="Sneakers au profil affirmé pour les amateurs de silhouettes originales.",
    ),
    ShopItem(
        "stone_island_overshirt",
        "Stone Island — surchemise",
        "corps_vestes",
        1600,
        18,
        xp_bonus=12,
        success_bonus=0.02,
        description="Surchemise robuste au style minimaliste.",
    ),
    ShopItem(
        "carhartt_wip_double_knee",
        "Carhartt WIP — pantalon Double Knee",
        "vetement",
        460,
        5,
        xp_bonus=4,
        description="Pantalon workwear solide au style décontracté.",
    ),
    ShopItem(
        "palm_angels_bear_cap",
        "Palm Angels — casquette Bear",
        "tete_visage",
        780,
        11,
        xp_bonus=7,
        success_bonus=0.01,
        description="Casquette streetwear de collection.",
    ),
    ShopItem(
        "dior_oblique_sweater",
        "Dior Oblique — chandail",
        "vetement",
        3600,
        32,
        xp_bonus=21,
        success_bonus=0.04,
        description="Pièce premium à intégrer à une collection haut de gamme.",
    ),
    ShopItem(
        "versace_barocco_shirt",
        "Versace — chemise Barocco",
        "vetement",
        2450,
        25,
        xp_bonus=16,
        success_bonus=0.03,
        description="Chemise de collection aux motifs audacieux.",
    ),
    ShopItem(
        "new_balance_990v6",
        "New Balance 990v6",
        "pieds",
        720,
        9,
        xp_bonus=6,
        success_bonus=0.01,
        description="Sneakers polyvalentes au confort reconnu.",
    ),
    ShopItem(
        "supreme_box_logo_hoodie",
        "Supreme — Box Logo Hoodie",
        "corps_vestes",
        1850,
        20,
        xp_bonus=14,
        success_bonus=0.03,
        description="Hoodie streetwear recherché par les collectionneurs.",
    ),
)

ITEMS += (
    ShopItem("chanel_classic_flap_bag", "Chanel Classic Flap — sac de collection", "accessoires_sacoches", 5200, 35, xp_bonus=22, success_bonus=0.04, description="Sac de collection matelassé; bonus réservés à l'arcade."),
    ShopItem("chanel_tweed_jacket", "Chanel — veste tweed", "corps_vestes", 4800, 34, xp_bonus=22, description="Veste de collection au style intemporel."),
    ShopItem("chanel_ballet_flats", "Chanel — ballerines", "pieds", 2450, 25, xp_bonus=16, description="Chaussures élégantes pour compléter ta collection."),
    ShopItem("hermes_birkin_25", "Hermès Birkin 25 — sac de collection", "accessoires_sacoches", 12500, 42, xp_bonus=24, success_bonus=0.04, description="Pièce de maroquinerie de collection; bonus d'arcade."),
    ShopItem("hermes_kelly_28", "Hermès Kelly 28 — sac de collection", "accessoires_sacoches", 11800, 40, xp_bonus=23, success_bonus=0.04, description="Sac de collection au format classique."),
    ShopItem("hermes_silk_shirt", "Hermès — chemise en soie", "vetements_accessoires", 3200, 30, xp_bonus=18, success_bonus=0.03, description="Chemise de collection aux finitions soignées."),
    ShopItem("jacquemus_le_chiquito", "Jacquemus Le Chiquito — sac", "accessoires_sacoches", 1250, 16, xp_bonus=10, description="Petit sac de collection au design graphique."),
    ShopItem("jacquemus_la_robe", "Jacquemus — robe de collection", "vetements_accessoires", 1850, 20, xp_bonus=14, success_bonus=0.02, description="Robe de collection à la coupe contemporaine."),
    ShopItem("jacquemus_le_cardigan", "Jacquemus — cardigan", "corps_vestes", 1400, 17, xp_bonus=11, description="Cardigan léger pour les tenues de saison."),
    ShopItem("corteiz_allstarz_tee", "Corteiz Allstarz — t-shirt", "vetements_accessoires", 420, 5, xp_bonus=4, description="T-shirt streetwear de collection."),
    ShopItem("corteiz_tracksuit", "Corteiz — ensemble tracksuit", "corps_vestes", 1150, 14, xp_bonus=9, success_bonus=0.02, description="Ensemble coordonné au style sportif."),
    ShopItem("corteiz_cargos", "Corteiz — pantalon cargo", "vetements_accessoires", 680, 9, xp_bonus=6, description="Pantalon cargo ample au style décontracté."),
    ShopItem("corteiz_4starz_sneakers", "Corteiz 4Starz — sneakers", "pieds", 890, 12, xp_bonus=8, success_bonus=0.01, description="Sneakers streetwear à ajouter à ta collection."),
    ShopItem("nike_womens_tech_set", "Nike — ensemble Tech femme", "corps_vestes", 780, 10, xp_bonus=7, description="Ensemble sport à la coupe ajustée."),
    ShopItem("nike_womens_dunk_low", "Nike Dunk Low — édition femme", "pieds", 610, 8, xp_bonus=5, description="Sneakers basses au coloris de collection."),
    ShopItem("dior_saddle_bag", "Dior Saddle — sac de collection", "accessoires_sacoches", 4100, 32, xp_bonus=20, success_bonus=0.04, description="Maroquinerie de collection au profil iconique."),
    ShopItem("dior_slingback_pumps", "Dior — escarpins Slingback", "pieds", 2250, 24, xp_bonus=15, description="Chaussures habillées pour les tenues de soirée."),
    ShopItem("gucci_jackie_1961", "Gucci Jackie 1961 — sac de collection", "accessoires_sacoches", 3650, 30, xp_bonus=18, success_bonus=0.03, description="Sac de collection au fermoir signature."),
    ShopItem("prada_cleo_bag", "Prada Cleo — sac de collection", "accessoires_sacoches", 3350, 29, xp_bonus=18, success_bonus=0.03, description="Sac de collection aux lignes épurées."),
    ShopItem("balenciaga_le_cagole", "Balenciaga Le Cagole — sac", "accessoires_sacoches", 2950, 27, xp_bonus=17, description="Sac de collection au style affirmé."),
    ShopItem("rolex_daytona", "Rolex Daytona — montre de collection", "accessoires_sacoches", 18000, 40, xp_bonus=25, success_bonus=0.05, description="Montre de collection; bonus d'arcade uniquement."),
    ShopItem("audemars_royal_oak", "Audemars Piguet Royal Oak — montre", "accessoires_sacoches", 26000, 48, xp_bonus=28, success_bonus=0.05, description="Montre de collection haut de gamme."),
    ShopItem("patek_nautilus", "Patek Philippe Nautilus — montre", "accessoires_sacoches", 32000, 52, xp_bonus=30, success_bonus=0.05, description="Montre de collection pour les hauts niveaux."),
    ShopItem("cartier_santos", "Cartier Santos — montre", "accessoires_sacoches", 6200, 36, xp_bonus=21, success_bonus=0.04, description="Montre de collection au bracelet distinctif."),
    ShopItem("chanel_pearl_earrings", "Chanel — boucles d'oreilles perles", "accessoires_sacoches", 980, 13, xp_bonus=8, description="Bijou de collection pour compléter une tenue."),
    ShopItem("hermes_silk_scarf", "Hermès — carré de soie", "accessoires_sacoches", 760, 10, xp_bonus=6, description="Carré de soie aux motifs de collection."),
    ShopItem("prada_sunglasses_archive", "Prada — lunettes de soleil archive", "tete_visage", 920, 13, xp_bonus=8, description="Lunettes de collection à monture géométrique."),
    ShopItem("jacquemus_le_bambino", "Jacquemus Le Bambino — sac", "accessoires_sacoches", 1390, 17, xp_bonus=11, description="Petit sac à bandoulière de collection."),
    ShopItem("moncler_womens_maya", "Moncler Maya — doudoune femme", "corps_vestes", 2800, 26, xp_bonus=17, success_bonus=0.03, description="Doudoune de collection à la coupe courte."),
    ShopItem("lv_womens_sneaker", "Louis Vuitton — sneaker Archlight", "pieds", 3150, 28, xp_bonus=18, success_bonus=0.03, description="Sneaker de collection au profil sculptural."),
)

ITEMS += (
    ShopItem("mercedes_amg_c63_s", "Mercedes-AMG C 63 S", "vehicules", 72000, 25, success_bonus=0.05, description="Berline sportive de collection, utilisable dans les activités compatibles du jeu."),
    ShopItem("mercedes_amg_cla45", "Mercedes-AMG CLA 45", "vehicules", 58000, 20, success_bonus=0.04, description="Coupé quatre portes compact au style sportif."),
    ShopItem("bmw_m4_competition", "BMW M4 Competition", "vehicules", 82000, 28, success_bonus=0.05, description="Coupé sportif pour la collection automobile."),
    ShopItem("bmw_x6m_competition", "BMW X6 M Competition", "vehicules", 105000, 34, success_bonus=0.06, description="SUV hautes performances au profil distinctif."),
    ShopItem("audi_rs6_avant", "Audi RS 6 Avant", "vehicules", 98000, 32, success_bonus=0.06, description="Wagon sportif polyvalent pour les déplacements en jeu."),
    ShopItem("audi_rsq8", "Audi RS Q8", "vehicules", 112000, 36, success_bonus=0.06, description="SUV de collection à la finition premium."),
    ShopItem("porsche_911_gt3_rs", "Porsche 911 GT3 RS", "vehicules", 230000, 48, success_bonus=0.07, description="Modèle sportif de prestige pour les collectionneurs."),
    ShopItem("porsche_cayenne_turbo", "Porsche Cayenne Turbo", "vehicules", 118000, 38, success_bonus=0.06, description="SUV de luxe à ajouter au garage."),
    ShopItem("lamborghini_urus_se", "Lamborghini Urus", "vehicules", 240000, 50, success_bonus=0.08, description="SUV de prestige pour les activités compatibles du jeu."),
    ShopItem("lamborghini_huracan", "Lamborghini Huracán", "vehicules", 285000, 52, success_bonus=0.08, description="Supercar de collection à la silhouette iconique."),
    ShopItem("ferrari_f8_tributo", "Ferrari F8 Tributo", "vehicules", 310000, 54, success_bonus=0.08, description="Supercar de prestige pour les hauts niveaux."),
    ShopItem("rolls_royce_cullinan", "Rolls-Royce Cullinan", "vehicules", 350000, 56, success_bonus=0.08, description="SUV de luxe aux finitions haut de gamme."),
    ShopItem("rolls_royce_ghost", "Rolls-Royce Ghost", "vehicules", 330000, 55, success_bonus=0.08, description="Berline grand luxe pour les collectionneurs."),
    ShopItem("range_rover_autobiography", "Range Rover Autobiography", "vehicules", 145000, 42, success_bonus=0.07, description="SUV de prestige au confort généreux."),
    ShopItem("brabus_800_g63", "Brabus 800 — G 63", "vehicules", 265000, 52, success_bonus=0.08, description="SUV de collection à la configuration exclusive."),
)

VEHICLE_RENTAL_TERMS = {
    "1h": ("1 heure", 3600, 10, 30),
    "24h": ("24 heures", 86400, 25, 50),
    "7d": ("7 jours", 604800, 50, 75),
}


def vehicle_rental_quote(item: ShopItem, duration: str) -> tuple[int, int]:
    """Return the fixed game rental fee and refundable deposit."""
    term = VEHICLE_RENTAL_TERMS.get(duration)
    if item.category != "vehicules" or term is None:
        raise ValueError("Durée ou véhicule de location non valide.")
    _, _, rent_percent, deposit_percent = term
    return item.price * rent_percent // 100, item.price * deposit_percent // 100


_FASHION_BRANDS = (
    ("nike", "Nike", 420, 1),
    ("prada", "Prada", 1700, 15),
    ("louis_vuitton", "Louis Vuitton", 2100, 18),
    ("dior", "Dior", 1900, 17),
    ("gucci", "Gucci", 1650, 15),
    ("balenciaga", "Balenciaga", 1600, 15),
    ("lacoste", "Lacoste", 460, 3),
    ("trapstar", "Trapstar", 520, 4),
    ("corteiz", "Corteiz", 500, 4),
    ("adidas", "Adidas", 390, 1),
    ("chanel", "Chanel", 2600, 22),
    ("hermes", "Hermès", 2800, 24),
)
_FASHION_STYLES = (
    ("stadium_tee", "T-shirt Stadium", "vetements_accessoires", 1.0),
    ("logo_tee", "T-shirt Signature", "vetements_accessoires", 1.2),
    ("straight_pants", "Pantalon droit", "vetement", 1.35),
    ("cargo_pants", "Pantalon cargo", "vetement", 1.55),
    ("logo_hoodie", "Hoodie logo", "corps_vestes", 2.0),
    ("light_jacket", "Veste légère", "corps_vestes", 2.6),
    ("embroidered_cap", "Casquette brodée", "tete_visage", 0.8),
    ("ribbed_beanie", "Bonnet côtelé", "tete_visage", 0.9),
    ("city_runner", "Sneakers City Runner", "pieds", 1.8),
    ("court_sneaker", "Sneakers Court", "pieds", 2.1),
    ("crossbody_bag", "Sacoche crossbody", "accessoires_sacoches", 1.5),
    ("leather_bag", "Sac de collection", "accessoires_sacoches", 2.3),
    ("signature_jewelry", "Bijou Signature", "accessoire", 1.3),
    ("premium_watch", "Montre de collection", "luxe", 3.2),
)
ITEMS += tuple(
    ShopItem(
        f"{brand_id}_{style_id}",
        f"{brand_name} — {style_name}",
        category,
        (int(base_price * multiplier) // 10) * 10,
        min(45, max(base_level, int(base_price * multiplier // 600))),
        xp_bonus=min(35, max(2, int(base_price * multiplier // 200))),
        success_bonus=min(0.05, 0.01 + base_price * multiplier / 200_000),
        description=(
            "Pièce de collection pour compléter ton style; "
            "bonus appliqués aux activités d’arcade."
        ),
    )
    for brand_id, brand_name, base_price, base_level in _FASHION_BRANDS
    for style_id, style_name, category, multiplier in _FASHION_STYLES
)

SHOP_CATEGORIES = {
    "vetements_accessoires": "Vêtements & Accessoires",
    "tete_visage": "Vêtements & Accessoires — Tête & Visage",
    "corps_vestes": "Vêtements & Accessoires — Corps & Vestes",
    "accessoires_sacoches": "Vêtements & Accessoires — Accessoires & Sacoches",
    "pieds": "Vêtements & Accessoires — Pieds",
    "vetement": "Vêtements & Accessoires — Vêtements",
    "accessoire": "Vêtements & Accessoires — Accessoires",
    "luxe": "Vêtements & Accessoires — Luxe",
    "vehicules": "Véhicules",
    "armes": "Armes — Objets de jeu",
    "armes_poing": "Armes — Poing (jeu)",
    "armes_lourdes": "Armes — Collection lourde (jeu)",
    "armes_longues": "Armes — Collection longue (jeu)",
    "protection": "Protection & Défense (jeu)",
    "matos": "Matos",
}


ACTION_SPECS = {
    "berri": ActionSpec(
        "Move fictif à Berri-UQAM", 1, BASE_SUCCESS_RATE, 120, 25, 5,
        "Petit gain de jeu à Berri-UQAM: +{reward}$ et +{xp} XP.",
        "La chance tourne: 45 secondes à Bordeaux/RDP.", PRISON_DURATION_SECONDS,
    ),
    "bixi": ActionSpec(
        "Défi BIXI", 1, BASE_SUCCESS_RATE, 90, 30, 8,
        "Défi BIXI réussi dans le jeu: +{reward}$ et +{xp} XP.",
        "Le défi BIXI échoue: 45 secondes à Bordeaux/RDP.", PRISON_DURATION_SECONDS,
    ),
    "dep": ActionSpec(
        "Défi dép de quartier", 10, BASE_SUCCESS_RATE, 220, 45, 12,
        "Défi du dép réussi dans le jeu: +{reward}$ et +{xp} XP.",
        "Le défi dérape: 45 secondes à Bordeaux/RDP.", PRISON_DURATION_SECONDS,
    ),
    "marketplace": ActionSpec(
        "Défi Marketplace Radisson", 10, BASE_SUCCESS_RATE, 250, 50, 8,
        "Le deal fictif passe: +{reward}$ et +{xp} XP.",
        "Le deal échoue: 45 secondes à Bordeaux/RDP.", PRISON_DURATION_SECONDS,
    ),
    "steal_rav4": ActionSpec(
        "Défi gamos RAV4", 20, BASE_SUCCESS_RATE, 350, 75, 12,
        "Défi RAV4 réussi dans le jeu: +{reward}$ et +{xp} XP.",
        "Le défi échoue: 45 secondes à Bordeaux/RDP.", PRISON_DURATION_SECONDS,
        gamos="RAV4", requires_obd=True,
    ),
    "steal_lexus": ActionSpec(
        "Défi gamos Lexus", 20, BASE_SUCCESS_RATE, 500, 85, 15,
        "Défi Lexus réussi dans le jeu: +{reward}$ et +{xp} XP.",
        "Le défi échoue: 45 secondes à Bordeaux/RDP.", PRISON_DURATION_SECONDS,
        gamos="Lexus", requires_obd=True,
    ),
    "jewelry": ActionSpec(
        "Défi Plaza St-Hubert", 35, BASE_SUCCESS_RATE, 1200, 120, 25,
        "Défi d'arcade Plaza réussi: +{reward}$ et +{xp} XP.",
        "Défi raté: 45 secondes à Bordeaux/RDP.", PRISON_DURATION_SECONDS,
    ),
    "port": ActionSpec(
        "Défi conteneur du Port", 50, BASE_SUCCESS_RATE, 2500, 160, 35,
        "Défi d'arcade du Port réussi: +{reward}$ et +{xp} XP.",
        "Défi raté: 45 secondes à Bordeaux/RDP.", PRISON_DURATION_SECONDS,
    ),
}


def level_for_xp(xp: int) -> int:
    """Each next level costs 100 × current level XP, capped at level 60."""
    if xp < 0:
        raise ValueError("XP ne peut pas être négatif")
    level = 1
    remaining = xp
    while level < MAX_LEVEL:
        required = 100 * level
        if remaining < required:
            break
        remaining -= required
        level += 1
    return level


def xp_to_next_level(xp: int, level: int) -> int:
    if level >= MAX_LEVEL:
        return 0
    return 100 * level - (xp - 50 * level * (level - 1))


def xp_with_bonus(xp: int, bonus_percent: int = 0) -> int:
    """Apply the player's XP bonus while preserving a positive reward."""
    if xp < 0 or bonus_percent < 0:
        raise ValueError("XP et bonus d'XP doivent être positifs ou nuls")
    if xp == 0:
        return 0
    return max(1, xp + xp * bonus_percent // 100)


def item_by_id(item_id: str) -> ShopItem | None:
    return next((item for item in ITEMS if item.item_id == item_id), None)


def actions_available(level: int, has_obd: bool) -> list[tuple[str, ActionSpec]]:
    return [
        (action_id, spec)
        for action_id, spec in ACTION_SPECS.items()
        if level >= spec.min_level and (has_obd or not spec.requires_obd)
    ]


def resolve_action(
    action: str,
    roll: float | None = None,
    success_bonus: float = 0.0,
) -> GameResult:
    """Resolve a fictional action without describing real-world methods."""
    spec = ACTION_SPECS.get(action)
    if spec is None:
        raise ValueError(f"Action inconnue: {action}")
    chance = random.random() if roll is None else roll
    if not 0 <= chance < 1:
        raise ValueError("roll doit être compris entre 0 inclus et 1 exclu")
    if not math.isfinite(success_bonus) or success_bonus < 0:
        raise ValueError("success_bonus doit être positif ou nul")

    success_rate = min(MAX_SUCCESS_RATE, BASE_SUCCESS_RATE + success_bonus)
    if chance < success_rate:
        return GameResult(
            spec.success_message.format(reward=spec.reward, xp=spec.xp),
            spec.reward,
            spec.heat,
            gamos=spec.gamos,
            xp_gain=spec.xp,
            success=True,
        )
    return GameResult(
        spec.failure_message,
        0,
        spec.heat,
        prison_seconds=spec.prison_seconds,
    )


def resolve_pvp(
    attacker_has_weapon: bool,
    victim_has_weapon: bool,
    attack_roll: float | None = None,
    counter_roll: float | None = None,
    transfer_roll: float | None = None,
    victim_has_armor: bool = False,
    attacker_weapon_power: int | None = None,
    victim_weapon_power: int | None = None,
) -> PvpResult:
    """Resolve a fictional, non-violent player-versus-player contest."""
    attack = random.random() if attack_roll is None else attack_roll
    counter = random.random() if counter_roll is None else counter_roll
    transfer = random.random() if transfer_roll is None else transfer_roll
    if not all(0 <= roll < 1 for roll in (attack, counter, transfer)):
        raise ValueError("Les tirages doivent être compris entre 0 inclus et 1 exclu")

    attacker_power = (
        max(0, attacker_weapon_power)
        if attacker_weapon_power is not None
        else 1 if attacker_has_weapon else 0
    )
    victim_power = (
        max(0, victim_weapon_power)
        if victim_weapon_power is not None
        else 1 if victim_has_weapon else 0
    )
    counter_chance = (
        min(0.82, 0.60 + 0.08 * (victim_power - 1))
        if victim_power
        else 0.35 if victim_has_armor else 0.0
    )
    if counter < counter_chance:
        return PvpResult(
            "counter",
            "Riposte fictive réussie: la victime bloque le braquage. "
            "Le braqueur prend 45 secondes à Bordeaux et une amende de 500$ versée à la victime.",
            prison_seconds=PRISON_DURATION_SECONDS,
            fine=500,
        )
    if attacker_weapon_power is None and victim_weapon_power is None:
        success_chance = 0.50 + (0.15 if attacker_has_weapon else 0.0)
    else:
        success_chance = min(
            0.88,
            max(
                0.25,
                0.50
                + 0.10 * attacker_power
                - 0.06 * victim_power
                + (0.05 if attacker_power else 0.0),
            ),
        )
    if attack < success_chance:
        if attacker_weapon_power is None:
            transfer_rate = 0.15 + 0.20 * transfer
        else:
            transfer_rate = min(
                0.50, 0.12 + 0.06 * attacker_power + 0.20 * transfer
            )
        return PvpResult(
            "success",
            "Défi PVP remporté dans le jeu: le transfert de kob est appliqué.",
            xp_gain=70,
            transfer_rate=transfer_rate,
        )
    return PvpResult(
        "failed",
        "Le braquage fictif échoue; les condés interviennent. "
        "Aucun cash n'est transféré.",
        prison_seconds=PRISON_DURATION_SECONDS,
    )


def resolve_shootout(
    attacker_weapon_power: int,
    victim_weapon_power: int,
    outcome_roll: float | None = None,
    police_roll: float | None = None,
) -> ShootoutResult:
    """Resolve an abstract arcade duel using inventory power ratings."""
    if attacker_weapon_power < 1 or victim_weapon_power < 0:
        raise ValueError("Puissance d'arme non valide.")
    outcome = random.random() if outcome_roll is None else outcome_roll
    police = random.random() if police_roll is None else police_roll
    if not 0 <= outcome < 1 or not 0 <= police < 1:
        raise ValueError("Les tirages doivent être compris entre 0 inclus et 1 exclu.")

    attacker_chance = min(
        0.90,
        max(0.20, 0.55 + 0.12 * (attacker_weapon_power - victim_weapon_power)),
    )
    winner = "attacker" if outcome < attacker_chance else "victim"
    winning_power = (
        attacker_weapon_power if winner == "attacker" else victim_weapon_power
    )
    return ShootoutResult(
        winner=winner,
        attacker_chance=attacker_chance,
        police_intervention=police < 0.22,
        xp_gain=100 + 25 * winning_power,
    )


def resolve_jugg(
    tier_id: str,
    success_roll: float | None = None,
    setback_roll: float | None = None,
) -> JuggResult:
    """Resolve a fictional arcade business tier without real-world instructions."""
    spec = next((tier for tier in JUGG_TIERS if tier.tier_id == tier_id), None)
    if spec is None:
        raise ValueError(f"Palier de jugg inconnu: {tier_id}")
    success = random.random() if success_roll is None else success_roll
    if not 0 <= success < 1:
        raise ValueError("Le tirage doit être compris entre 0 inclus et 1 exclu")
    if success < spec.success_chance:
        return JuggResult(
            True,
            spec.success_reward,
            spec.success_heat,
            0,
            f"✅ Le scénario d'arcade est maîtrisé: la manche crédite "
            f"{spec.success_reward}$ à ta caisse.",
        )

    setback = random.random() if setback_roll is None else setback_roll
    if not 0 <= setback < 1:
        raise ValueError("Le tirage doit être compris entre 0 inclus et 1 exclu")
    if setback < 0.5:
        return JuggResult(
            False,
            -spec.failure_fine,
            4,
            0,
            f"🚨 Contrôle fictif dans le mini-jeu: amende de "
            f"{spec.failure_fine}$.",
        )
    return JuggResult(
        False,
        0,
        8,
        20,
        "🚔 La manche est interrompue dans le scénario: courte pause en "
        "cellule arcade, 20 secondes.",
    )


def prison_deadline(seconds: int = PRISON_DURATION_SECONDS) -> float:
    """Return the Unix timestamp at which the 45-second jail period expires."""
    if seconds != PRISON_DURATION_SECONDS:
        raise ValueError(
            f"La durée de prison est fixée à {PRISON_DURATION_SECONDS} secondes"
        )
    return time.time() + jail_duration
