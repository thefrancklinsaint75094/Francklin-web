# Bot Telegram de dispatch

Bot Telegram qui remplace les groupes WhatsApp entre franchisés et livreurs : il reçoit les commandes
(texte, vocal ou capture d'écran), les propose aux livreurs les plus proches, attribue chaque course à un
seul livreur et tient le compte de ce que chacun a encaissé. Il ne touche jamais à l'argent.

Rôles : **dispatch** (toi), **franchisés** (qui ont aussi tous les pouvoirs d'admin), **livreurs** et
**ravitailleurs**. Tout se passe dans Telegram.

---

## 1. Ce qu'il te faut

| Service | Pourquoi | Coût |
|---|---|---|
| Un compte Telegram | pour créer le bot et être le dispatch | gratuit |
| [Supabase](https://supabase.com) | la base de données | gratuit (plan Free) |
| [Railway](https://railway.app) | héberger le bot 24h/24 | ~5 $/mois |
| [Anthropic](https://console.anthropic.com) *(optionnel)* | lire les commandes écrites librement (mode `ia`) | quelques centimes par nuit |
| [OpenAI](https://platform.openai.com) *(optionnel)* | transcrire les vocaux | quelques centimes par nuit |

---

## 2. Créer le bot Telegram (@BotFather)

1. Dans Telegram, ouvre **@BotFather** et envoie `/newbot`.
2. Choisis un nom (« Dispatch IDF ») puis un identifiant qui finit par `bot` (`dispatch_idf_bot`).
3. BotFather te donne un **token** du type `7412345678:AAH…` → c'est `TELEGRAM_BOT_TOKEN`.

## 3. Récupérer ton identifiant Telegram (@userinfobot)

Ouvre **@userinfobot**, envoie `/start` : il te répond ton `Id` (un nombre, ex. `123456789`)
→ c'est `DISPATCH_TELEGRAM_ID`. C'est ce compte-là qui sera le dispatch.

## 4. Préparer Supabase

1. Crée un projet Supabase.
2. **SQL Editor** → *New query* → colle tout le contenu de [`sql/schema.sql`](sql/schema.sql) → **Run**.
   À faire **une seule fois**.
3. **Project Settings → API** :
   - *Project URL* → `SUPABASE_URL`
   - clé **`service_role`** (secrète, pas la clé `anon`) → `SUPABASE_SERVICE_KEY`

Le schéma active la sécurité RLS sans aucune règle : seule la clé `service_role` (celle du bot) peut lire
et écrire. Ne mets jamais cette clé dans une page web ou une appli.

## 5. Lancer en local (pour essayer)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # puis remplis les valeurs
python -m bot.main
```

Le bot tourne en *polling* : pas de domaine, pas de port à ouvrir. Arrête-le (Ctrl+C) avant de le déployer :
**deux instances avec le même token se font la guerre** (erreurs `Conflict 409`, messages en double).

## 6. Déployer sur Railway

1. Pousse ce dépôt sur GitHub.
2. Railway → **New Project → Deploy from GitHub repo** → choisis le dépôt.
3. Onglet **Variables** : ajoute toutes les variables de [`.env.example`](.env.example)
   (au minimum les 4 obligatoires, plus `ANTHROPIC_API_KEY` en mode `ia`).
4. Onglet **Settings → Deploy** : **Replicas = 1**. C'est indispensable (voir plus haut).
5. Aucun domaine ni port à configurer : c'est un simple *worker*. `railway.toml` donne déjà la commande
   de démarrage (`python -m bot.main`) et le redémarrage automatique.

Les logs (onglet **Deployments → View logs**) affichent chaque événement horodaté.
Au démarrage, le dispatch reçoit « 🟢 Bot démarré ».

### Variables d'environnement

| Variable | Obligatoire | Défaut | Rôle |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | oui | | token @BotFather |
| `SUPABASE_URL` | oui | | URL du projet |
| `SUPABASE_SERVICE_KEY` | oui | | clé `service_role` |
| `EXTRACTION_MODE` | non | `ia` si une clé Anthropic est fournie, sinon `regles` | `regles` : lecture par règles fixes, sans IA (vocaux et captures refusés) ; `ia` : lecture par Anthropic |
| `ANTHROPIC_API_KEY` | si `EXTRACTION_MODE=ia` | | lecture des commandes par l'IA |
| `DISPATCH_TELEGRAM_ID` | oui | | ton identifiant numérique |
| `OPENAI_API_KEY` | non | | vocaux, en mode `ia` seulement ; sans elle, ils sont refusés poliment |
| `DEPARTEMENTS_AUTORISES` | non | `75,77,78,91,92,93,94,95` | zone acceptée |
| `BROADCAST_WAVE_SIZE` | non | `3` | livreurs sollicités par vague |
| `BROADCAST_WAVE_SECONDS` | non | `120` | délai avant d'élargir |
| `POSITION_STALE_MINUTES` | non | `30` | position trop vieille → hors service |
| `SOON_FREE_RADIUS_METERS` | non | `400` | rayon « bientôt libre » automatique |
| `DRAFT_EXPIRY_MINUTES` | non | `15` | durée de vie d'une fiche non confirmée |
| `MAX_DISTANCE_KM` | non | `25` | au-delà, un livreur ne reçoit jamais la course |
| `STUCK_COURSE_MINUTES` | non | `75` | course en cours trop longtemps → alerte |
| `PRICE_MAX` | non | `2000` | au-delà, « ⚠️ Prix élevé, vérifie » |
| `DATA_RETENTION_DAYS` | non | `90` | effacement des digicodes et messages |
| `NIGHT_START_HOUR` / `NIGHT_END_HOUR` | non | `18` / `6` | découpage des nuits (voir DECISIONS.md) |
| `ANTHROPIC_MODEL` | non | `claude-haiku-4-5-20251001` | modèle d'extraction |
| `BAN_URL` | non | API Adresse | à changer seulement si l'API Adresse déménage |
| `GOOGLE_SHEETS_WEBHOOK_URL` | non | | adresse du script Google Sheets (voir plus haut) |
| `GOOGLE_SHEETS_SECRET` | non | | secret partagé avec ce script |
| `BOXES` | non | `Box 1,Box 2,Box 3` | box proposés au ravitailleur (noms du tableau Rechargement) |
| `ARRIVAL_NOTIFY_METERS` | non | `500` | alerte « le livreur arrive » sous cette distance (0 = désactivé) |
| `ARRIVAL_NOTIFY_MINUTES` | non | `5` | … ou sous ce nombre de minutes estimées (0 = désactivé) |
| `LIVREUR_NAMES` | non | `Livreur A,Livreur B,Livreur C,Livreur D,Livreur R,Livreur X` | noms des livreurs dans les feuilles (`-` : « Livreur 1 », « Livreur 2 »…) |
| `RAVITAILLEUR_NAMES` | non | `Ravitailleur 1,Ravitailleur 2` | idem pour les ravitailleurs |
| `STOCK_ALERTS` | non | `1` | alertes de stock du livreur (lu dans SOLDES du tableau Rechargement) ; `0` pour couper |

---

### Lire les commandes avec ou sans IA

- **`EXTRACTION_MODE=regles`** : le bot découpe le message aux virgules, retours à la ligne, « / », « ; »
  et reconnaît l'adresse (mot de voie ou code postal), le prix (nombre suivi de €, ou nombre seul à la fin),
  le digicode ou l'étage, l'heure (« vers 23h ») ; le reste devient les produits. Rien n'est inventé :
  s'il manque une information, il le dit. Les vocaux et les captures d'écran sont refusés poliment.
- **`EXTRACTION_MODE=ia`** : lecture par Anthropic, qui comprend les messages les plus désordonnés,
  les vocaux (avec `OPENAI_API_KEY`) et les captures d'écran.

Dans les deux cas, le franchisé voit une fiche et la confirme avant que la course parte.

### Modèle de commande et catalogue des produits

Les franchisés peuvent écrire leurs commandes selon ce modèle, lu sans IA dans les deux modes :

```
12 rue de Rivoli 75004 Paris
2 vodka 60
1 coca 10

Digicode 45A32, 3e étage
```

1re ligne l'adresse ; une ligne par produit : **quantité, produit, prix total de la ligne** (toujours un
multiple de 10 €) ; une ligne vide,
puis le commentaire (vu par le livreur seulement). Le bot fait le total. `/modele` le rappelle.

Le dispatch gère le catalogue avec `/produits` (bouton ➕ pour ajouter, 🗑 pour supprimer) ou
`/ajouter`, un produit par ligne, avec après « : » les autres façons de l'écrire :

```
Vodka Absolut : absolut, abso
Coca-Cola : coca
```

Le bot reconnaît le produit malgré les majuscules, accents, pluriels, contenances (« 70cl ») et petites
fautes, et affiche le nom du catalogue sur la fiche. Un produit inconnu ne bloque pas la commande : la
fiche porte « ⚠️ Produit pas dans le catalogue ». Les franchisés voient la liste avec `/produits`.

**Prix de 10 en 10 €** : une commande dont le total, ou le prix d'une ligne, n'est pas un multiple de 10 €
est refusée avec un message qui dit quoi corriger ; le franchisé la renvoie avec le bon prix.

Base créée avant l'ajout du catalogue : exécuter une fois `sql/migrations/002_products.sql`.

### Alerte « le livreur arrive »

Grâce à la position en direct du livreur, le franchisé de la course et le dispatch reçoivent **une fois par
course** : « 📍 Livreur 1 arrive — course #142 : à ~300 m (≈ 2 min) », dès que le livreur est à moins de
`ARRIVAL_NOTIFY_METERS` (500 m) de l'adresse **ou** à moins de `ARRIVAL_NOTIFY_MINUTES` (5 min). Le temps est
estimé avec la vitesse réelle du livreur entre ses deux dernières positions (sinon 15 km/h). Il faut que le
livreur partage sa position **en direct**.

### Où sont les livreurs (`/livreurs`)

Chaque livreur a une ligne avec l'état de sa position :

- **🟢 en service · 📍 il y a 2 min · en direct** : tout va bien ;
- **⚠️ position fixe** : il a envoyé une position qui ne se met pas à jour (le dispatch est prévenu tout de
  suite) — il doit partager sa position **en direct** ;
- **position perdue (dernière il y a 42 min)** / **sans position** (mis en service par un admin).

Boutons : **📍** à côté de chaque livreur → son point sur une carte Telegram (ouvrable dans Maps), avec
l'adresse la plus proche, l'âge de la position, en direct ou fixe, et l'heure de fin du partage choisie dans
Telegram ; **🗺 Tous les livreurs** → un point par livreur en service.

Alertes au dispatch : un livreur en service qui n'envoie plus sa position depuis `POSITION_ALERT_MINUTES`
(10 min) → « ⚠️ Livreur A : plus de position depuis 12 min (dernière : près de …) », et le livreur reçoit un
rappel ; à `POSITION_STALE_MINUTES` (30 min) il sort du service → « ⏸ Livreur A retiré du service ».

Base créée avant cette version : exécuter une fois `sql/migrations/008_positions_live.sql`.

### Retirer quelqu'un : `/bannir` ou `/supprimer`

- **`/bannir`** (ancien nom `/exclure`, toujours accepté) : plus aucun accès, et la personne ne peut pas se
  réinscrire avec le même compte Telegram. Le compte est gardé : `/reactiver` le rend.
- **`/supprimer`** : définitif. Le compte est détaché de son Telegram : la personne peut se réinscrire de zéro
  avec `/start`, dans le rôle de son choix (utile quand quelqu'un s'est inscrit dans le mauvais rôle pour
  tester), et tu la valides comme un nouveau. Son nom de feuille est libéré ; récap, journal et feuilles gardent
  ses anciennes courses.

Dans les deux cas : plus de service, ses courses en cours sont rendues (livreur) ou annulées si elles attendent
encore (franchisé), son menu de commandes disparaît, le bot ne lui envoie plus rien et **efface chez lui tous
les messages qu'il lui a envoyés ces dernières 48 h** (fiches, adresses, digicodes). Limite fixée par
Telegram : un bot ne peut pas effacer plus ancien ; ce qui a plus de 48 h reste dans son historique, mais
les boutons ne répondent plus. Pense aussi à lui retirer le partage des feuilles Google s'il y avait accès.

Base créée avant cette version : exécuter une fois `sql/migrations/007_removed_users.sql`.

### Moyen de déplacement des livreurs

Trois modes : **🚶 Transport** (à pied, métro, bus : c'est pareil), **🛵 Deux-roues**, **🚗 Voiture**. Le livreur
le choisit au `/dispo` (boutons sous le message ; retenu jusqu'au prochain changement). À quoi il sert :

- **dispatch** : les livreurs sont classés par **temps de trajet estimé** selon leur mode, plus seulement par
  distance ; un livreur en transport ne reçoit pas de course à plus de `TRANSPORT_MAX_KM` (5 km) ;
- **alerte « le livreur arrive »** : sans vitesse mesurable, un livreur en transport est compté à pied ;
- **vérification** avec la position en direct pendant la course :
  - 🚇 **métro** : plus aucune position pendant au moins 2 min 30 (sous terre), puis réapparition à plus de
    800 m, près d'une autre station, à une vitesse de métro (12 à 45 km/h). Les stations de métro et de RER
    viennent des données ouvertes d'IDFM, téléchargées au démarrage (`STATIONS_URL`) ;
  - 🛵 **véhicule** : trois positions de suite à plus de 25 km/h, reçues sans interruption ;
  - 🚶 **à pied** : jamais plus de 8 km/h pendant toute la course (constaté à la livraison).

  Quand c'est contraire à ce que le livreur a déclaré, le dispatch reçoit par exemple : « 🚇 #142 — Livreur A
  semble avoir pris le métro (Bastille → Nation, 2 km en 7 min) · déclaré 🛵 deux-roues ».
- **`/livreurs`** montre le mode de chacun (❔ = pas encore choisi) ; **`/close`** l'affiche par livreur, avec la
  liste des déplacements détectés (⚠️ quand ça ne correspond pas à la déclaration).

Il faut que le livreur partage sa position **en direct**. Limites : un RER qui roule en surface peut passer
pour un véhicule ; un téléphone qui perd le réseau hors du métro peut ressembler à un trajet en métro (la
vérification des stations limite ce cas). Ce sont des indices (« semble »), pas des preuves.

Base créée avant cette version : exécuter une fois `sql/migrations/006_transport.sql`.

### Consulter le stock (`/stock`)

Pour les admins et les ravitailleurs. `/stock` affiche des boutons : **📦 Box 1 / Box 2 / Box 3**, **📦 Tous les
box** et **🚴 Livreurs** ; `/stock box 1` répond directement.

- **Box** : onglet ORGA du tableau Rechargement, section ④ (stock initial + mouvements du Compta − sorties vers
  les livreurs), moins les rechargements du bot pas encore écrits dans la feuille ;
- **Tous les box** : chaque box, plus les produits sous leur seuil (🟠 alerte, 🔴 rupture, section ⑤) ;
- **Livreurs** : stock de chaque livreur, calculé en direct (chargé net − ventes OK de la feuille Dispatch).

### Noms des livreurs = noms des feuilles

Le nom d'un livreur dans le bot est **exactement** son nom dans les feuilles Dispatch et Rechargement
(« Livreur A », « Livreur B »… liste `LIVREUR_NAMES`). À la validation d'une inscription, le livreur reçoit le
premier nom libre et l'admin reçoit des boutons pour en choisir un autre ; plus tard : `/livreurs` → **🏷 Nom**.
Un nom n'est porté que par un seul livreur (🔒 = déjà pris). Même principe pour les ravitailleurs
(`RAVITAILLEUR_NAMES`). Les tables de correspondance des onglets PARAMETRES ne servent plus pour eux.

### Alertes de stock du livreur

Le stock du livreur est calculé **en direct**, sans attendre les IMPORTRANGE entre les fichiers :

- le script Google (action `stock`) prend le **chargé net** de SOLDES (section ②, calculée dans le tableau
  Rechargement lui-même) et retire les **ventes au statut OK lues directement dans les 7 onglets de la feuille
  Dispatch** — saisies à la main comprises ;
- le bot y ajoute ses propres mouvements **pas encore écrits** dans les feuilles (livraison ou rechargement en
  cours d'envoi, ou dont l'envoi a échoué) ;
- à l'attribution, il retire aussi les **autres courses déjà attribuées** au livreur, pas encore livrées.

Alertes :

- **à l'attribution** : « ⚠️ Stock — course #142 : • Ce sont les 2 derniers US de Livreur 1 (Livreur A) », ou
  « n'a que 1 US pour 2 commandés », ou « n'a plus de US sur lui » ;
- **à la livraison** : « 📭 Livreur 1 (Livreur A) n'a plus de US sur lui (course #142 livrée) ». Le stock est lu
  avant d'envoyer la vente à la feuille, puis la quantité livrée est déduite.

Prévenus : le franchisé de la course, le livreur, les ravitailleurs et le dispatch. Sans réponse du script (ou
livreur absent de SOLDES), pas d'alerte : la course n'est jamais bloquée.

### Pouvoirs des admins (dispatch et franchisés)

Le dispatch **et les franchisés** ont les pleins pouvoirs :

- `/livreurs` : mettre un livreur **🟢 en service** ou **⏸ en pause**. Mis en service ainsi, il reçoit les courses
  même sans position partagée (« distance inconnue ») ;
- **👤 Attribuer** une course en attente à un livreur précis (dans `/encours`, ou sur le message de course du
  franchisé) : le livreur reçoit directement sa fiche ;
- sur son message de course, le franchisé a aussi **✏️ Modifier** et **📦 Livrée** ;
- toutes les commandes du dispatch : `/encours`, `/recap`, `/journal`, `/users`, `/recharge`, `/stock`,
  `/caisse`, `/depense`, `/synchro`, `/close`, `/reset`, `/bannir`, `/reactiver`, `/supprimer`, `/produits`.

Base créée avant cette version : exécuter une fois `sql/migrations/004_duty_forced.sql`.

### Modification de la commande par le livreur

Sur place, le client prend parfois plus, moins ou autre chose. Sur sa course, le livreur appuie sur
**✏️ Modifier la commande** et corrige tout avec des boutons, sans rien taper :

- **➖ / ➕** sur chaque produit : seule la quantité change, **le prix ne bouge pas tout seul** (pas de calcul
  automatique) ; à 0, le produit disparaît ;
- **➕ Ajouter un produit** : la liste du catalogue s'affiche, un appui ajoute le produit ;
- toucher un produit ouvre son **prix** : boutons −50, −20, −10, +10, +20, +50 € (ou taper le prix, au choix) ;
  les prix vont toujours de 10 en 10 € ;
- **✅ Valider** enregistre, **↩️ Annuler** ne change rien.

À la validation, la course prend les nouveaux produits et le nouveau total (« à encaisser »), le franchisé et
le dispatch sont prévenus (ancien → nouveau prix), et c'est cette version qui part dans Google Sheets à la
livraison. Possible tant que la course n'est pas livrée.

### Google Sheets (facultatif)

Chaque course livrée peut s'écrire automatiquement dans la feuille « Dispatch » (onglets Lundi à Dimanche),
via un petit script Google ([`integrations/google_sheets/Code.gs`](integrations/google_sheets/Code.gs)).
Tout se fait depuis un téléphone, dans Chrome :

1. Ouvre **script.google.com** → **Nouveau projet**. Remplace le contenu par `Code.gs`.
   Mets à la place de `SPREADSHEET_ID` l'identifiant de la feuille (la partie entre `/d/` et `/edit` de son
   lien) et à la place de `A_REMPLACER` ton secret.
2. **Déployer → Nouveau déploiement → Application Web** ; « Exécuter en tant que : moi » ;
   « Qui a accès : Tout le monde ». Autorise l'accès demandé par Google.
3. Dans Railway : `GOOGLE_SHEETS_WEBHOOK_URL` = l'adresse obtenue (finit par `/exec`),
   `GOOGLE_SHEETS_SECRET` = le même secret.
4. Dans l'onglet **PARAMETRES**, colonnes **Q** et **R** à partir de la ligne 4 : en Q le nom du bot
   (« Livreur 2 ») ou le vrai nom, en R le nom de la feuille (« Livreur A »).

La colonne Vendeur vaut toujours « TOTAL » (constante `VENDEUR` du script) : une ligne dont la seule case remplie
est Vendeur = « TOTAL » compte comme vide, et après chaque écriture les cases Vendeur vidées repassent à « TOTAL ».

Chaque course livrée va dans l'onglet de sa nuit (livrée à 2h dans la nuit de lundi à mardi → « Lundi »),
sur la première ligne vide entre 2 et 41 : Vendeur « TOTAL », Livreur, Statut « OK », Adresse, puis jusqu'à 3 produits
avec leur quantité et leur prix (au-delà, la suite va sur la ligne vide suivante). Paiement : « Espèces » ou
« Virement », choisi par le livreur à la livraison. Le digicode et le commentaire ne vont jamais dans la feuille.

Le numéro de course est gardé dans une note sur la cellule Vendeur : le script n'écrit jamais deux fois la même
course et ne touche pas aux lignes remplies à la main. `/synchro` (dispatch) renvoie les courses de la nuit,
sans risque de doublon. Si un onglet est plein, le dispatch le voit dans la réponse de `/synchro`.

### Paiement à la livraison

Quand le livreur appuie sur **📦 Livré**, le bot demande **💵 Espèces** ou **💳 Virement** (↩️ Retour pour revenir
à la fiche). Un admin qui marque une course livrée choisit aussi le mode. Le mode est enregistré, affiché au
dispatch et dans le journal (colonne « paiement » du CSV), et écrit dans la colonne **Paiement** de la feuille.

### Caisse des livreurs (`/depense`, `/caisse`, `/macaisse`)

- **`/depense`** (livreur pour lui-même, admin pour un livreur) : **🧾 Charges** (essence, repas… à ses frais)
  ou **💸 Avance sur paye**, puis le montant (boutons ±5/±10/±50 € ou montant tapé, centimes acceptés), puis le
  motif (boutons ou texte, facultatif), puis ✅ Enregistrer. La dépense s'écrit dans la zone **DÉPENSES
  LIVREURS** (lignes 46 à 57) de l'onglet de la nuit ; le dispatch (ou le livreur, si c'est un admin qui saisit)
  est prévenu.
- **`/caisse`** (admins et ravitailleurs) : cash à récupérer chez chaque livreur = ventes OK en espèces −
  dépenses − cash déjà récupéré, calculé **en direct** par le script (comme SOLDES ④, sans attendre les
  IMPORTRANGE), plus les mouvements du bot pas encore écrits. **💶 Récupérer** ouvre `/recharge` en « cash
  seulement », prérempli avec le montant dû.
- **`/macaisse`** (livreur) : ce qu'il doit remettre, avec le détail.

### Dispatch selon le stock

Avec `STOCK_ALERTS=1`, une course part **d'abord aux livreurs qui ont tout en stock** (stock de la feuille +
mouvements en vol − leurs autres courses en cours), puis à ceux dont le stock est inconnu, puis aux autres — la
distance départage dans chaque groupe. Si **aucun** livreur éligible n'a tout, la course part quand même au plus
proche et les ravitailleurs et le dispatch reçoivent une alerte (une fois par course) avec ce qui manque. Les
produits absents des colonnes de la feuille (coca…) sont ignorés. Le stock des livreurs est relu au plus une
fois par minute.

### Débrief de la journée (`/close`)

Pour les admins, en fin de nuit : un message « 🔒 Journée close » qui résume la nuit, sans rien changer —
courses livrées et total (💵 espèces / 💳 virement), annulations, courses encore ouvertes, chiffres par livreur
(avec ses dépenses) et par franchisé, dépenses et rechargements de la nuit, puis, si Google Sheets est relié,
le cash à récupérer (cumul de la semaine) et les produits sous le seuil. Lancé le matin, il porte sur la nuit
qui vient de finir. Le dispatch reçoit le débrief quand c'est un franchisé qui clôt la journée.

### Remise à zéro de la semaine (`/reset`)

(Ancien nom `/cloture`, qui marche toujours.)

Pour les admins, le lundi matin (rappel automatique au dispatch le lundi à 6h05). Le bot montre d'abord ce qui
va se passer : stock encore chez les livreurs (**reporté**), cash pas encore récupéré et reste chez le
ravitailleur (**remis à zéro** : récupère-les avant), courses encore ouvertes. Puis **✅ Reset de la semaine** :

1. copie des 3 fichiers dans Google Drive, dossier **Archives bot / Reset du …** (si la copie échoue, rien
   n'est effacé) ;
2. le stock actuel des box (ORGA ④) devient le **stock initial** (COMPTA, onglet STOCK, colonnes B à P) ;
3. remise à zéro : lignes de commande et dépenses des 7 onglets de la feuille Dispatch (Vendeur repasse à
   TOTAL), lignes des 7 onglets du tableau Rechargement, MOUVEMENTS et RAVI du COMPTA ;
4. le stock encore chez chaque livreur est reporté par une ligne « Report clôture » (sans box) dans l'onglet
   du jour du tableau Rechargement.

Il faut `COMPTA_SPREADSHEET_ID` dans le script et l'accès à Google Drive : après avoir collé le script, choisis
la fonction **autoriser**, **▶ Exécuter**, accepte, puis **Déployer → Gérer les déploiements → ✏️ → Version :
Nouvelle version** (l'adresse `/exec` ne change pas).

Base créée avant ces fonctions : exécuter une fois `sql/migrations/005_payment_expenses.sql`.

### Rechargement des livreurs (ravitailleur)

Un **ravitailleur** s'inscrit comme les autres (`/start` → Ravitailleur) et le dispatch le valide. Avec
`/recharge` (aussi disponible pour le dispatch), tout se fait par boutons dans un seul message :

1. le **livreur** ;
2. **📦 Chargement**, **↩️ Reprise** ou **💶 Cash seulement** ;
3. le **box** (liste `BOXES`) ;
4. les **produits** du catalogue et leurs quantités (−1, +1, +5) ;
5. le **cash récupéré** (boutons ±10, ±50, ±100 €, ou montant tapé) ;
6. **✅ Valider**.

Le livreur reçoit le détail (« 📦 Chargement reçu : +12 DIV, +6 KT »), le dispatch est prévenu, et la ligne
part dans le tableau **Rechargement** (même script Google que la feuille Dispatch, voir `RECHARGE_SPREADSHEET_ID`
dans `Code.gs`) : onglet de la nuit, première ligne libre entre 3 et 20, livreur, box, cash, quantités
(+ chargé, − repris) dans la colonne du produit, heure et ravitailleur. Noms : table **PARAMETRES J/K** du
tableau Rechargement (nom du bot → nom de la feuille). `/synchro` renvoie aussi les rechargements de la nuit.

Base créée avant le rechargement : exécuter une fois `sql/migrations/003_restocks.sql`.

## 7. Premier démarrage

1. Toi (compte dispatch) : envoie `/start` au bot → « Dispatch actif ».
2. Chaque franchisé et chaque livreur envoie `/start`, choisit son rôle, donne son nom.
3. Tu reçois une carte « 🆕 Nouvelle inscription » avec ✅ / ❌. Valide.
   Les noms visibles par les autres sont automatiques : « Franchisé 1 », « Livreur 1 »…
   Le vrai nom n'est visible que par toi.

### Comment tester seul

Un compte Telegram = un seul rôle. Pour tout tester il te faut **au moins trois comptes** :
dispatch, un franchisé, un livreur (et idéalement un second livreur pour tester la course
« déjà prise »). Utilise un second téléphone, ou ajoute des comptes dans l'appli Telegram
(Paramètres → *Ajouter un compte*, jusqu'à 3 par appli) avec d'autres numéros.

Le livreur doit partager sa **position en direct** : 📎 → Position → *Partager ma position en direct*.

---

## 8. Tests automatiques

```bash
pip install -r requirements.txt
pytest                       # tests unitaires (aucun réseau, aucune base)
```

Sans base, les tests `test_lock.py` et `test_scenarios.py` sont ignorés. Ils ont besoin d'une vraie base.
**Ils vident toutes les tables** : utilise un projet dédié aux tests, jamais la production.

**Option A — un projet Supabase de test** (schéma appliqué) :

```bash
SUPABASE_URL_TEST=https://xxxx.supabase.co SUPABASE_SERVICE_KEY_TEST=… pytest
```

**Option B — PostgreSQL + PostgREST en local** (ce qu'utilise Supabase sous le capot) :

```bash
createdb dispatch_test && psql -d dispatch_test -f sql/schema.sql
cat > postgrest.conf <<'CONF'
db-uri = "postgres://postgres@localhost:5432/dispatch_test"
db-schemas = "public"
db-anon-role = "postgres"
server-port = 3055
CONF
postgrest postgrest.conf &          # binaire : github.com/PostgREST/postgrest/releases
POSTGREST_URL_TEST=http://localhost:3055 pytest
```

`test_scenarios.py` fait tourner **le vrai bot** (handlers, base, verrou) contre un faux Telegram :
inscription, commandes, vagues, prise simultanée, relais, annulations, récap, journal, CSV, exclusions,
mur de confidentialité. Anthropic, l'API Adresse et Whisper y sont simulés.

Ces tests ne remplacent pas un essai réel sur Telegram (voir la checklist ci-dessous).

## 9. Checklist d'essai sur Telegram

- [ ] **Étape 1** — un franchisé et un livreur s'inscrivent, le dispatch valide, chacun reçoit son message de bienvenue.
- [ ] **Étape 2** — les 4 exemples de commande produisent les bonnes fiches ; un vocal est transcrit ; une adresse hors zone (Lyon) est refusée.
- [ ] **Étape 3** — deux livreurs appuient en même temps sur « Je prends » : un seul obtient la course, l'autre voit « déjà prise ».
- [ ] **Étape 4** — aucun livreur en service → notification → un livreur fait `/dispo` et partage sa position → il reçoit la course.
- [ ] **Étape 5** — une nuit complète avec 2 franchisés et 2 livreurs, puis `/recap` et `/journal` (avec le CSV).

---

## 10. Structure

```
bot/
  main.py            point d'entrée, handlers, error_handler global
  config.py          variables d'environnement (validées au démarrage)
  db.py              toutes les requêtes Supabase (une fonction par requête)
  texts.py           tous les textes envoyés
  keyboards.py       boutons inline
  messaging.py       envois/éditions sûrs (bot bloqué, message trop ancien…)
  timeutil.py        heure de Paris, découpage des nuits
  jobs.py            tâches planifiées
  handlers/          onboarding, franchise, livreur, dispatch, relay, location, restock, stock_view, cash,
                     cloture, messages (aiguillage), common
  services/          extraction, geocoding, transcription, distance, broadcast, lifecycle, sheets, stock,
                     cash, restock, arrival, order_edit, names
sql/schema.sql       schéma à exécuter une fois
tests/               tests unitaires + verrou + scénarios de bout en bout
DECISIONS.md         choix faits là où la spécification ne tranchait pas
```
