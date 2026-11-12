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
Les commandes admin sont réservées aux deux ID Telegram intégrés dans
`config.py`. Pour remplacer cette liste, définis `ADMIN_USER_IDS` avec des ID
numériques séparés par des virgules; `ADMIN_USER_ID` est aussi accepté pour un
seul administrateur. Les ID ne doivent pas être des usernames.

Le fichier SQLite `rabza_jugg_bot.sqlite3` est créé automatiquement au premier
lancement, dans le dossier du projet. `DB_PATH` accepte un chemin absolu; un
chemin relatif est résolu depuis le dossier du projet, indépendamment du
dossier courant du processus. `DATABASE_PATH` reste accepté comme ancien alias.
Les dossiers parents manquants sont créés au démarrage et le journal SQLite
fonctionne en mode WAL avec des commits synchrones.

Sur Railway (ou tout hébergeur à système de fichiers éphémère), ces réglages
ne remplacent pas un volume persistant : attache un volume au service, monte-le
par exemple sur `/data`, puis définis `DB_PATH` à
`/data/rabza_jugg_bot.sqlite3`. Garde le même chemin de montage lors des
redéploiements. Sans volume persistant, les données locales au conteneur ne
peuvent pas être garanties après un redéploiement.

Les migrations gardent les anciennes tables `players_legacy` et
`inventory_legacy` comme sauvegardes après copie. Ne les supprime qu'après
avoir vérifié une sauvegarde externe et les données migrées.
Les comptes, inventaires et peines sont isolés par conversation Telegram:
un même utilisateur a donc un état distinct en privé et dans chaque groupe.
`/braquer @username` est disponible uniquement dans un groupe ou supergroupe
et demande le niveau 20, sauf en Mode Développeur. Un échec entraîne une amende
et une peine aléatoires (30 à 120 secondes); les autres activités conservent
leur peine standard de 45 secondes. Les échéances sont stockées en timestamp
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

- `/start` — créer ton profil et rejoindre le quartier
- `/help` — afficher toutes les commandes et leurs usages
- `/profil` — consulter cash, banque, niveau, XP et statut
- `/inventory` — voir tes articles, bonus et location active
- `/top` — afficher le classement des joueurs de cette conversation
- `/prison` — voir le temps restant et l’option de caution
- `/evade` — tenter l’évasion; réussite libre, échec et +30s de peine
- `/bank` — consulter cash et banque; boutons pour tout déposer ou retirer
- `/deposit <montant/all>` — déposer du cash liquide en banque
- `/withdraw <montant/all>` — retirer du cash de la banque
- `/give <montant>` — transférer du cash en répondant à un joueur
- `/give @username <montant>` — transférer du cash à un joueur du groupe
- `/invest` — consulter le marché, ses actifs et son portefeuille
- `/invest buy <actif> <parts>` — acheter des parts au cours actuel
- `/invest sell <actif> <parts/all>` — vendre des parts d'un actif
- `/drive` — faire un trajet rémunéré; véhicule requis, cooldown 60s
- `/repair` — payer les réparations avant de reprendre une location endommagée
- `/return` — restituer une location et récupérer la caution restante
- `/braquer @username` — braquage PvP niveau 20+; risque d'amende et de peine aléatoires
- `/shoot @username` — duel armé; cible en riposte, butin et cooldown par cible
- `/shop` — parcourir les sapes, l’équipement, les véhicules et locations
- `/missions` — consulter les missions, leurs niveaux, gains et prérequis
- `/mission <id>` — lancer une mission ou un défi par identifiant
- `/jugg` — ouvrir les activités d’arcade et leurs paliers
- `/casino` — ouvrir les jeux et consulter leur syntaxe
- `/admin` — ouvrir le panneau de contrôle réservé aux administrateurs
- `/devmode` — basculer entre Mode Joueur et Mode Développeur
- `/give_cash [@username] [montant|max] [cash|bank]` — ajouter le montant exact ou, sans montant, porter le compte au plafond
- `/give_xp [@username] [montant|max]` — ajouter l'XP exacte ou, sans montant, la porter au plafond
- `/set_level [@username] [niveau]` — fixer le niveau exact ou, sans niveau, atteindre le maximum
- `/give_item [@username] [id_item|nom|all]` — donner un article ou, sans item/avec `all`, tout le catalogue
- `/revoke_cash [@username] [montant] [cash|bank]` — retirer un montant ou vider le compte choisi sans montant
- `/revoke_xp [@username] [montant]` — retirer de l'XP ou remettre l'XP à zéro sans montant
- `/revoke_level [@username] [niveaux]` — retirer des niveaux ou repasser au niveau 1 sans nombre
- `/revoke_item [@username] [id_item|nom|all]` — retirer un exemplaire ou vider l'inventaire sans item/avec `all`
- `/admin_reset [@username] [repair|full CONFIRMER]` — réparer ou réinitialiser un profil du groupe

`/devmode` et les boutons de `/admin` basculent un état persistant par ID admin.
En Mode Joueur, toutes les commandes de crédit, progression, attribution et
réparation admin sont verrouillées. En Mode Développeur, les actions admin du
panneau sont disponibles; `max` porte le cash ou la banque à 1 000 000 000 000$.
Sans montant, `/give_cash` porte également le compte sélectionné à ce plafond.
L'XP sans montant est portée à 999 999 999 999 XP; un niveau fixé sans argument
est réglé au niveau maximum. `/give_item` sans argument ou avec `all` ajoute une
référence du catalogue à l'inventaire sans dupliquer les entrées déjà possédées.
Les commandes `/revoke_*` sont réservées au Mode Développeur: un montant précis
retire jusqu'à cette quantité sans passer sous zéro; l'absence de valeur remet
le cash/banque ou l'XP à zéro, le niveau à 1 ou vide l'inventaire. La révocation
d'un article retire un exemplaire et les changements restent limités au groupe
courant.
L'admin ignore les cooldowns de hustle, jugg, conduite et duel, peut agir malgré
une peine, ne peut pas être envoyé en prison par les missions/PVP et bénéficie
de réparations gratuites (les réparations de location déjà dues sont faites
automatiquement avant un trajet). Les règles standard s'appliquent aux autres
joueurs ciblés. Le badge `[DEV]` n'apparaît que dans le `/profil` de l'admin
concerné.

Les commandes admin vérifient l'ID Telegram configuré. `/admin_reset repair`
préserve l'économie, le niveau, l'XP et l'inventaire; le mode `full` exige
`CONFIRMER` et remet à zéro le profil ainsi que son inventaire dans la
conversation visée. Un crédit ou une réinitialisation ne touche jamais le même
joueur dans les autres groupes.

Le menu `/jugg` débloque ses paliers aux niveaux 1, 3, 7 et 15. Chaque palier
a une chance de réussite, une amende ou une courte peine simulée en cas
d'échec, et son propre cooldown de 15, 25, 30 ou 35 secondes, persisté par
joueur et conversation. Les missions suivent
également leurs prérequis jusqu'au niveau 20+; au niveau maximal, aucun palier
inférieur ne reste indiqué comme verrouillé.

Le marché BTC/ETH/SOL et les actions 514-TEL, ANJOU-MOTORS, HYDRO-MTL et
RUE-STE-CATH évolue à intervalles de trois minutes. Les cours et positions sont
persistés; les achats utilisent le cash liquide, et l'affichage donne les
variations en pourcentage, le coût moyen, la valeur et le gain/perte net du
portefeuille. Les parts peuvent être vendues individuellement ou avec
`/invest sell all`.

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
La boutique contient aussi des collections luxe et streetwear (vêtements,
sacs, montres, bijoux et chaussures), accessibles par marque et type.
Les véhicules peuvent être achetés définitivement ou loués pour 1 heure,
24 heures ou 7 jours. Le loyer représente respectivement 10%, 25% ou 50% du
prix d'achat; la caution représente 30%, 50% ou 75%. Les frais sont débités
du cash puis de la banque. La caution est restituée à /return ou à l'échéance,
moins les réparations issues d'incidents aléatoires (25% de risque par trajet).
Un incident mineur ou modéré immobilise la location jusqu'au paiement des frais
de remise en état avec `/repair`; un incident majeur termine la location. Une
caution épuisée entraîne la confiscation. Les locations sont isolées par joueur
et groupe et apparaissent dans /inventory. `/drive` rapporte
75$ et 20 XP quand le trajet est clean; l'entretien d'un véhicule possédé est
rare. Le défi `/mission colis` et les missions longues exigent un véhicule et
vérifient le risque de trajet; aucune vérification passive n'est faite.
Les catégories Armes — Poing, Collection lourde, Collection longue et Protections
balistiques proposent des objets d'arcade fictifs. Les gilets léger IIIA, lourd
IV et le shield tactique réduisent respectivement de 10, 20 et 35 points le
taux de réussite d'un braquage contre leur propriétaire; seul l'équipement le
plus puissant compte, avec un plancher de réussite de 10%. Les anciennes
protections possédées sont converties au démarrage sans perdre les quantités.
Les gadgets de mission (dont le boîtier OBD) restent disponibles pour préserver
les prérequis existants. Ces effets sont exclusivement des règles de jeu.
Les braquages utilisent la meilleure arme de l'inventaire dans le groupe:
équipement de poing (puissance 1), équipement lourd (2) et équipement long (3).
Une cible mieux armée peut déclencher une riposte; ses protections balistiques
réduisent aussi directement le taux de réussite adverse sans se cumuler. Une
protection qui fait échouer le tirage augmente la probabilité d'une arrestation
dans les règles fictives du jeu.
`/shoot` applique les mêmes puissances à un duel aléatoire; son cooldown est de
60 secondes par attaquant, cible et groupe. Le perdant peut perdre une part de
son cash liquide, payer des frais de soins et être envoyé en taule; la banque
reste intacte. Le gagnant reçoit le butin disponible et de l'XP.
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
