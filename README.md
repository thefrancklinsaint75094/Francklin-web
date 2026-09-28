# Bot Telegram de dispatch

Bot Telegram qui remplace les groupes WhatsApp entre franchisés et livreurs : il reçoit les commandes
(texte, vocal ou capture d'écran), les propose aux livreurs les plus proches, attribue chaque course à un
seul livreur et tient le compte de ce que chacun a encaissé. Il ne touche jamais à l'argent.

Trois rôles : **dispatch** (toi), **franchisés**, **livreurs**. Tout se passe dans Telegram.

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

---

### Lire les commandes avec ou sans IA

- **`EXTRACTION_MODE=regles`** : le bot découpe le message aux virgules, retours à la ligne, « / », « ; »
  et reconnaît l'adresse (mot de voie ou code postal), le prix (nombre suivi de €, ou nombre seul à la fin),
  le digicode ou l'étage, l'heure (« vers 23h ») ; le reste devient les produits. Rien n'est inventé :
  s'il manque une information, il le dit. Les vocaux et les captures d'écran sont refusés poliment.
- **`EXTRACTION_MODE=ia`** : lecture par Anthropic, qui comprend les messages les plus désordonnés,
  les vocaux (avec `OPENAI_API_KEY`) et les captures d'écran.

Dans les deux cas, le franchisé voit une fiche et la confirme avant que la course parte.

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
  handlers/          onboarding, franchise, livreur, dispatch, relay, location, messages (aiguillage), common
  services/          extraction, geocoding, transcription, distance, broadcast, lifecycle
sql/schema.sql       schéma à exécuter une fois
tests/               tests unitaires + verrou + scénarios de bout en bout
DECISIONS.md         choix faits là où la spécification ne tranchait pas
```
