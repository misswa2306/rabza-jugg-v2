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
    success_message: str


HUSTLES = (
    HustleSpec(
        "colis",
        "Défi de livraison express (Plateau → St-Léonard)",
        0.85,
        115,
        25,
        20,
        "📦 Livraison arcade terminée: tu as validé les étapes du parcours "
        "Plateau–St-Léonard. Le jeu crédite {cash}$ pour ce score.",
    ),
    HustleSpec(
        "phishing",
        "Défi de sensibilisation SMS à Saint-Michel",
        0.70,
        275,
        30,
        25,
        "📲 Simulation SMS Interac réussie: tu as repéré les signaux d'alerte "
        "dans le scénario d'entraînement. Les points de la manche valent "
        "{cash}$ dans le jeu.",
    ),
    HustleSpec(
        "catalyseur",
        "Défi mécanique de Civic",
        0.60,
        475,
        35,
        30,
        "🔧 Épreuve mécanique arcade terminée: tu as complété les contrôles "
        "virtuels de la Civic dans le temps imparti. Récompense fixe: {cash}$.",
    ),
    HustleSpec(
        "vapote",
        "Défi de kiosque au Cégep",
        0.90,
        65,
        20,
        15,
        "🧃 Défi de kiosque terminé au Cégep: tu as atteint l'objectif de "
        "service du mini-jeu. Le score ajoute {cash}$ à ta caisse d'arcade.",
    ),
    HustleSpec(
        "depanneur",
        "Défi chronométré au dépanneur",
        0.50,
        900,
        50,
        30,
        "🏪 Parcours chronométré du dépanneur complété dans le mini-jeu: "
        "tous les checkpoints sont validés. Récompense fixe: {cash}$.",
    ),
    HustleSpec(
        "sneakers",
        "Défi d'échange de baskets",
        0.75,
        400,
        25,
        25,
        "👟 Défi de collection terminé: tu as complété la tournée de baskets "
        "dans le marché virtuel. Le score de la partie rapporte {cash}$.",
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
) -> PvpResult:
    """Resolve a fictional, non-violent player-versus-player contest."""
    attack = random.random() if attack_roll is None else attack_roll
    counter = random.random() if counter_roll is None else counter_roll
    transfer = random.random() if transfer_roll is None else transfer_roll
    if not all(0 <= roll < 1 for roll in (attack, counter, transfer)):
        raise ValueError("Les tirages doivent être compris entre 0 inclus et 1 exclu")

    counter_chance = 0.60 if victim_has_weapon else 0.35 if victim_has_armor else 0.0
    if counter < counter_chance:
        return PvpResult(
            "counter",
            "Riposte fictive réussie: la victime bloque le braquage. "
            "Le braqueur prend 45 secondes à Bordeaux et une amende de 500$ versée à la victime.",
            prison_seconds=PRISON_DURATION_SECONDS,
            fine=500,
        )
    success_chance = 0.50 + (0.15 if attacker_has_weapon else 0.0)
    if attack < success_chance:
        return PvpResult(
            "success",
            "Défi PVP remporté dans le jeu: le transfert de kob est appliqué.",
            xp_gain=70,
            transfer_rate=0.15 + 0.20 * transfer,
        )
    return PvpResult(
        "failed",
        "Le braquage fictif échoue; les condés interviennent. "
        "Aucun cash n'est transféré.",
        prison_seconds=PRISON_DURATION_SECONDS,
    )


def prison_deadline(seconds: int = PRISON_DURATION_SECONDS) -> float:
    """Return the Unix timestamp at which the 45-second jail period expires."""
    if seconds != PRISON_DURATION_SECONDS:
        raise ValueError(
            f"La durée de prison est fixée à {PRISON_DURATION_SECONDS} secondes"
        )
    return time.time() + jail_duration
