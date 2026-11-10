# Rabza Jugg V2

Bot Telegram de mini-jeu textuel fictif à Montréal. Les actions, gains et risques
sont simulés et ne constituent pas des instructions pour agir dans la vraie vie.

## Prérequis

- Python 3.10 ou plus récent
- Un bot créé avec [@BotFather](https://t.me/BotFather) et son token

## Installation et lancement (Windows PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:BOT_TOKEN = "TOKEN_FOURNI_PAR_BOTFATHER"
python bot.py
```

`.env.example` documente les variables requises, mais n'est pas chargé
automatiquement. Définis `BOT_TOKEN` dans l'environnement avant de démarrer.
Ne partage pas le fichier `.env` ni le token.

Le fichier SQLite `rabza_jugg_bot.sqlite3` est créé automatiquement au premier
lancement. Pour choisir un autre chemin, définis `DATABASE_PATH`.
Les comptes, inventaires et peines sont isolés par conversation Telegram:
un même utilisateur a donc un état distinct en privé et dans chaque groupe.
`/braquer @username` est disponible uniquement dans un groupe ou supergroupe.
Une peine dure exactement 45 secondes et son échéance est stockée en timestamp
Unix; les anciennes bases sont migrées au démarrage. Les comptes historiques
sans `chat_id` sont conservés dans le contexte privé correspondant à leur
`user_id`.

## Installation et lancement (macOS/Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export BOT_TOKEN="TOKEN_FOURNI_PAR_BOTFATHER"
python bot.py
```

Ne publie jamais le token dans le code ou dans un dépôt.

## Commandes

- `/help` — liste des commandes disponibles
- `/start` — inscription et accueil
- `/profil` — cash, gamos, heat et statut
- `/jugg` — menu des actions du mini-jeu
- `/shop` — marketplace d'items de jeu, verrouillés par niveau
- `/inventory` — inventaire et bonus des items
- `/braquer @username` — défi PVP fictif (niveau 20+)
- `/top` — classement des dix joueurs les plus riches du contexte courant
- `/prison` — temps restant et option de caution (300$)
- `/evade` — tentative d'évasion fictive; un échec prolonge la peine
- `/missions` ou `/mission` — catalogue de missions fictives et récompenses
- `/mission_colis`, `/mission_phishing`, `/mission_catalyseur`,
  `/mission_vapote`, `/mission_depanneur` et `/mission_sneakers` — hustles
  fictifs avec gains aléatoires, amende possible au sens du jeu et hausse de
  l'indice de recherche en cas d'échec
- `/casino` — menu du casino fictif (blackjack et roulette)

Dans les groupes, les commandes acceptent aussi la mention du bot, par exemple
`/help@Rabza_Jugg_V2_bot` et `/casino@Rabza_Jugg_V2_bot`.

Les missions se lancent avec un identifiant, par exemple `/mission civic`.
Elles sont débloquées par niveau, accordent du cash et de l'XP en cas de
réussite, et peuvent entraîner une amende ou une peine de jeu en cas d'échec.
Le menu `/missions` affiche aussi les hustles disponibles. Chaque hustle a
son taux de réussite, sa plage de gains et son cooldown. Le cooldown global
persistant est enregistré avec le profil du joueur dans chaque conversation.
Les profils existants sont migrés pour conserver le cash, le niveau et le XP,
et ajouter l'indice de recherche ainsi que l'horodatage du dernier hustle.
Les gains et probabilités sont: colis (85%, 80–150$, 60 s), phishing fictif
(70%, 150–400$, 120 s), catalyseur (60%, 300–650$, 180 s), vapote (90%,
40–90$, 45 s), dépanneur (50%, 600–1200$, 300 s) et sneakers (75%,
250–550$, 150 s).

Exemples: `/casino blackjack 500`, `/casino roulette rouge 100` ou
`/casino roulette 17 20`. Une mise doit être un entier positif et ne peut pas
dépasser le solde. Les gains sont nets et la mise est rendue en cas de victoire:
la roulette paie 1× sur couleur/parité et 35× sur un numéro exact; le blackjack
paie 1× sur une victoire classique et 1,5× pour un blackjack naturel. Une
égalité au blackjack rembourse la mise.

Les niveaux vont de 1 à 60. Le prochain niveau coûte 100 × niveau XP
(par exemple 100 XP du niveau 1 au 2, puis 200 XP du niveau 2 au 3).
L'XP est persisté avec les joueurs; la base existante est migrée au lancement.
Les bonus d'XP des sapes et les bonus de réussite d'équipement sont des valeurs
de jeu abstraites; le bonus des sapes est le plus élevé parmi les vêtements
possédés. Le boîtier OBD est requis pour les défis gamos de niveau 20+.
Les juggs utilisent une réussite de base de 70%; les bonus d'objets du
marketplace s'ajoutent au taux, plafonné à 95%. Les niveaux débloquent les
actions et les items disponibles.

## Tests

```powershell
python -m unittest discover -s tests -v
```
