-- Prénoms des livreurs, affichés entre parenthèses dans le bot (« Livreur A (Ketur) »).
-- Par défaut : le prénom donné à l'inscription (users.real_name) ; cette table le fixe par /prenom,
-- y compris pour un livreur des feuilles sans compte bot.
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
create table if not exists prenoms (
  name        text primary key,                        -- nom dans les feuilles (Livreur A…)
  prenom      text not null,
  updated_at  timestamptz not null default now()
);
alter table prenoms enable row level security;
