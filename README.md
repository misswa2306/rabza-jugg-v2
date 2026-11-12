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
- `/profil` — cash, banque, gamos, heat et statut
- `/bank` — consulter cash et banque; boutons pour déposer ou retirer tout
- `/deposit <montant/all>` et `/withdraw <montant/all>` — gérer le compte bancaire
- `/give <montant>` — envoyer du cash en répondant au message d'un joueur
- `/give @username <montant>` — envoyer du cash à un joueur inscrit dans le groupe
- `/jugg` ou `/juggling` — menu d'arcade avec trois paliers à récompenses fixes,
  risques simulés et cooldowns de 15 à 30 secondes
- `/shop` — boutique Vêtements & Accessoires (tête, vestes, accessoires, pieds),
  véhicules et équipement de jeu
- `/inventory` — inventaire et bonus des items
- `/braquer @username` — défi PVP fictif (niveau 20+)
- `/top` — classement des dix joueurs les plus riches du contexte courant
- `/prison` — temps restant et option de caution (300$)
- `/evade` — tentative d'évasion fictive; un échec prolonge la peine
- `/missions`, `/mission` ou `/hustle` — catalogue et défis fictifs avec cash et XP
- `/mission_colis`, `/mission_phishing`, `/mission_catalyseur`,
  `/mission_vapote`, `/mission_depanneur` et `/mission_sneakers` — défis
  d'arcade à récompenses fixes, avec amende de jeu et hausse de l'indice de
  recherche en cas d'échec
- `/casino` — menu du casino fictif (blackjack et roulette)

Le menu `/jugg` inclut les paliers fictifs Petit Hustle de Bloc (jusqu'à
1 200$), Gros Business Centre-Ville (4 500$) et Opération Internationale /
Masterclass (15 000$). Chaque palier a une chance de réussite, une amende ou
une courte peine simulée en cas d'échec, et son propre cooldown de 15, 25 ou
30 secondes, persisté par joueur et conversation.

Dans les groupes, les commandes acceptent aussi la mention du bot, par exemple
`/help@Rabza_Jugg_V2_bot` et `/casino@Rabza_Jugg_V2_bot`.

Les missions se lancent avec un identifiant, par exemple `/mission civic`.
Elles sont débloquées par niveau, accordent du cash et de l'XP en cas de
réussite, et peuvent entraîner une amende ou une peine de jeu en cas d'échec.
Le menu `/missions` affiche aussi les défis disponibles. Chaque défi a
son taux de réussite, une récompense fixe en cash et en XP, une pénalité fixe
en cas d'échec et son cooldown. Les succès des activités et des missions
créditent l'XP et mettent à jour le niveau; un message annonce chaque niveau
gagné. Le cooldown global persistant est enregistré avec le profil du joueur
dans chaque conversation.
Les profils existants sont migrés pour conserver le cash, le niveau et le XP,
et ajouter l'indice de recherche ainsi que l'horodatage du dernier hustle.
Les valeurs sont: colis (85%, +115$/−25$, 20 s), phishing (70%,
+275$/−30$, 25 s), catalyseur (60%, +475$/−35$, 30 s), vapote
(90%, +65$/−20$, 15 s), dépanneur (50%, +900$/−50$, 30 s) et sneakers
(75%, +400$/−25$, 25 s). Les cooldowns sont plafonnés à 30 secondes,
y compris pour les anciens profils. Les montants ne sont pas tirés au sort.

Exemples: `/casino blackjack 500`, `/casino roulette rouge 100` ou
`/casino roulette 17 20`. Une mise doit être un entier positif et ne peut pas
dépasser le solde. Les gains sont nets et la mise est rendue en cas de victoire:
la roulette paie 1× sur couleur/parité et 35× sur un numéro exact; le blackjack
paie 1× sur une victoire classique et 1,5× pour un blackjack naturel. Une
égalité au blackjack rembourse la mise.

Les niveaux vont de 1 à 60. Le prochain niveau coûte 100 × niveau XP
(par exemple 100 XP du niveau 1 au 2, puis 200 XP du niveau 2 au 3).
L'XP est persisté avec les joueurs; la base existante est migrée au lancement.
Le solde bancaire est persisté séparément du cash liquide et n'est pas touché
par les amendes, la taule ou les défis PVP simulés. Les dépôts et retraits sont
atomiques et peuvent être réalisés avec les boutons de `/bank` ou les commandes
`/deposit` et `/withdraw`.
Le catalogue Vêtements & Accessoires comprend plus de 20 pièces supplémentaires,
réparties entre tête et visage, corps et vestes, accessoires et sacoches, et
chaussures. Chaque fiche présente son prix, son niveau requis, sa description
et ses bonus de jeu. Les accessoires de style restent cosmétiques; aucune
capacité de dissimulation, de stockage ou d'appareil réel n'est simulée.
La boutique contient aussi une collection luxe (lunettes, vêtements, sacoches,
montres et bijoux), achetable dans les catégories Vêtements & Accessoires,
Accessoires et Luxe.
La catégorie Véhicules propose également douze nouvelles voitures allemandes,
japonaises, sportives et SUV. Les achats sont débités du solde et apparaissent
dans l'inventaire du joueur; les descriptions automobiles sont des éléments RP
et n'activent pas de capacité de conduite réelle.
Les catégories Armes — Poing, Collection lourde, Collection longue et Protection
proposent des objets d'arcade fictifs. L'équipement est acheté avec le solde et
conservé dans l'inventaire; les objets d'arme comptent comme équipement pour les
défis PVP simulés, et l'équipement de protection ajoute une chance de riposte
virtuelle. Ces effets sont exclusivement des règles de jeu.
Les bonus d'XP des vêtements et les bonus de réussite d'équipement sont des
valeurs de jeu abstraites; le bonus des vêtements est le plus élevé parmi les
vêtements possédés.
possédés. Le boîtier OBD est requis pour les défis gamos de niveau 20+.
Les juggs utilisent une réussite de base de 70%; les bonus d'objets du
marketplace s'ajoutent au taux, plafonné à 95%. Les niveaux débloquent les
actions et les items disponibles.

## Tests

```powershell
python -m unittest discover -s tests -v
```
