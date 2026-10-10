-- Schéma de la base du bot de dispatch.
-- À exécuter une seule fois dans l'éditeur SQL de Supabase.

create extension if not exists "pgcrypto";

create table users (
  id              uuid primary key default gen_random_uuid(),
  telegram_id     bigint unique not null,
  telegram_username text,                       -- @pseudo, peut être null
  real_name       text,                          -- nom donné à l'inscription, visible du dispatch seulement
  display_name    text,                          -- « Franchisé 3 », « Livreur 4 » — visible de l'autre partie
  role            text not null check (role in ('dispatch','franchise','livreur','ravitailleur')),
  status          text not null default 'pending' check (status in ('pending','active','banned','deleted')),
  on_duty         boolean not null default false,   -- livreur en service
  soon_free       boolean not null default false,   -- livreur a signalé qu'il termine
  duty_forced     boolean not null default false,   -- mis en service par un admin (même sans position)
  transport_mode  text check (transport_mode in ('transport','deux_roues','voiture')),  -- déclaré au /dispo
  cancel_count    integer not null default 0,       -- annulations après acceptation
  conversation_state text,                          -- null | 'awaiting_role' | 'awaiting_name' | 'correcting' | 'relaying'
  state_payload   jsonb,                             -- ex : {"draft_id": "..."} ou {"course_id": 142}
  state_expires_at timestamptz,
  created_at      timestamptz not null default now()
);

create table drafts (
  id              uuid primary key default gen_random_uuid(),
  franchise_id    uuid not null references users(id),
  raw_message     text not null,
  extracted       jsonb,                             -- résultat de l'extraction
  status          text not null default 'awaiting' check (status in ('awaiting','correcting','confirmed','expired','error')),
  telegram_message_id bigint,                        -- message de la fiche, pour l'éditer
  created_at      timestamptz not null default now()
);

create table courses (
  id              serial primary key,                -- numéro affiché : « Course #142 »
  draft_id        uuid references drafts(id),
  franchise_id    uuid not null references users(id),
  livreur_id      uuid references users(id),
  raw_message     text not null,
  address         text not null,
  address_detail  text,
  postal_code     text not null,
  district        text not null,
  lat             double precision not null,
  lon             double precision not null,
  products        text not null,
  price           numeric(8,2) not null,
  requested_time  text,
  status          text not null default 'pending'
                  check (status in ('pending','assigned','delivered','cancelled','cancelled_on_site')),
  broadcast_round integer not null default 1,
  franchise_message_id bigint,                       -- message « Course #142 envoyée » côté franchisé, édité au fil de l'eau
  livreur_message_id   bigint,                       -- fiche complète côté livreur
  delivered_distance_m integer,                      -- distance livreur ↔ adresse au moment du « Livré » (null si position inconnue)
  possible_duplicate_of integer references courses(id), -- renseigné si le bot a détecté un doublon probable
  created_at      timestamptz not null default now(),
  assigned_at     timestamptz,
  delivered_at    timestamptz,
  closed_at       timestamptz,
  payment         text check (payment in ('especes','virement')),  -- mode de paiement à la livraison
  detected_mode   text check (detected_mode in ('metro','vehicule','pied')),  -- déplacement détecté pendant la course
  pending_edit    jsonb           -- « Modif » du livreur en attente du franchisé : {products, price, by, at}
);

create table livreur_positions (
  livreur_id      uuid primary key references users(id),
  lat             double precision not null,
  lon             double precision not null,
  updated_at      timestamptz not null default now(),
  live            boolean,                           -- position en direct (sinon fixe)
  live_until      timestamptz                        -- fin du partage en direct choisie dans Telegram
);

create table broadcasts (
  course_id       integer not null references courses(id),
  livreur_id      uuid not null references users(id),
  telegram_message_id bigint,                        -- pour éditer/retirer la proposition
  round           integer not null,
  sent_at         timestamptz not null default now(),
  primary key (course_id, livreur_id)
);

create table messages (
  id              serial primary key,
  course_id       integer not null references courses(id),
  sender_id       uuid not null references users(id),
  content         text not null,
  relayed_telegram_message_id bigint,                -- id du message relayé chez le destinataire (pour la réponse par « Répondre »)
  created_at      timestamptz not null default now()
);

create table events (
  id              serial primary key,
  course_id       integer references courses(id),
  user_id         uuid references users(id),
  type            text not null,                     -- ex : 'course_created', 'course_taken', 'livreur_cancelled'
  payload         jsonb,
  created_at      timestamptz not null default now()
);

create table products (
  id          serial primary key,
  name        text not null,                    -- nom affiché : « Vodka Absolut »
  name_key    text not null unique,             -- nom normalisé, pour éviter les doublons
  aliases     text[] not null default '{}',     -- autres façons de l'écrire : {absolut, abso}
  variants    text[] not null default '{}',     -- goûts, même coût : {noisette, banane} (/gouts)
  created_at  timestamptz not null default now()
);

create table livreur_variants (                  -- goûts qu'un livreur a encore (oui / non, sans quantité)
  livreur_name  text not null,                   -- nom dans les feuilles (Livreur A…)
  product       text not null,
  variant       text not null,
  updated_at    timestamptz not null default now(),
  primary key (livreur_name, product, variant)
);

create table restocks (
  id          serial primary key,
  livreur_id  uuid references users(id),              -- vide : livreur de la feuille sans compte bot
  livreur_name text,                                  -- nom dans la feuille (rechargement saisi par /ravi)
  by_user_id  uuid not null references users(id),     -- ravitailleur ou dispatch qui a saisi
  ravitailleur_name text,                             -- ravitailleur nommé dans /ravi (sinon by_user_id)
  kind        text not null check (kind in ('load','unload','cash','swipe')),  -- chargement, reprise, cash seul, transfert
  box         text,                                   -- « Box 1 »… (null pour le cash seul)
  items       jsonb not null default '[]',            -- [{"p": "DIV", "q": 12}] quantités positives
  cash        numeric(8,2) not null default 0,        -- cash récupéré auprès du livreur
  to_livreur_id uuid references users(id),            -- swipe : livreur qui reçoit (livreur_id donne)
  to_livreur_name text,                               -- swipe : son nom dans la feuille (colonne T)
  created_at  timestamptz not null default now()
);

create table expenses (
  id          serial primary key,
  livreur_id  uuid not null references users(id),
  by_user_id  uuid not null references users(id),     -- livreur lui-même ou admin
  kind        text not null check (kind in ('charges','paye')),  -- à tes frais / avance sur paye
  amount      numeric(8,2) not null check (amount > 0),
  motif       text,
  created_at  timestamptz not null default now()
);

create table sales (                                 -- ventes saisies par un admin (/ventes)
  id            serial primary key,
  livreur_name  text not null,                         -- nom dans la feuille (Livreur A…)
  livreur_id    uuid references users(id),             -- compte du bot, s'il existe
  by_user_id    uuid not null references users(id),
  payment       text not null check (payment in ('especes','virement')),
  product       text not null,
  qty           integer not null check (qty > 0),
  price         numeric(8,2) not null check (price >= 0),   -- 0 : produit offert
  night         date,                                  -- nuit choisie (/ventes lundi) ; vide : celle de created_at
  created_at    timestamptz not null default now()
);

create index on sales (created_at);
create index on sales (livreur_id);
create index on sales (by_user_id);

create table prenoms (                               -- prénom affiché « Livreur A (Ketur) » (/prenom)
  name        text primary key,                        -- nom dans les feuilles (Livreur A…)
  prenom      text not null,
  updated_at  timestamptz not null default now()
);

create table bot_messages (                          -- messages envoyés (48 h), effacés si l'accès est retiré
  chat_id     bigint not null,
  message_id  bigint not null,
  created_at  timestamptz not null default now(),
  primary key (chat_id, message_id)
);

create index on bot_messages (created_at);
create index on expenses (created_at);
create index on expenses (livreur_id);
create index on expenses (by_user_id);
create index on restocks (created_at);
create index on restocks (livreur_id);
create index on restocks (by_user_id);
create index on courses (status);
create index on courses (franchise_id, created_at);
create index on courses (livreur_id, created_at);
create index on events (course_id);

-- Sécurité : RLS activé sans aucune politique. Le bot utilise la clé
-- service_role, qui contourne RLS ; la clé publique « anon » ne peut rien lire.
alter table users enable row level security;
alter table drafts enable row level security;
alter table courses enable row level security;
alter table livreur_positions enable row level security;
alter table broadcasts enable row level security;
alter table messages enable row level security;
alter table events enable row level security;
alter table prenoms enable row level security;
alter table livreur_variants enable row level security;
alter table products enable row level security;
alter table restocks enable row level security;
alter table expenses enable row level security;
alter table bot_messages enable row level security;
alter table sales enable row level security;
