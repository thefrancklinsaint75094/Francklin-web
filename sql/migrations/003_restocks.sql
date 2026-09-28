-- Rechargements des livreurs (rôle « ravitailleur » et commande /recharge).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table users drop constraint if exists users_role_check;
alter table users add constraint users_role_check
  check (role in ('dispatch','franchise','livreur','ravitailleur'));

create table if not exists restocks (
  id          serial primary key,
  livreur_id  uuid not null references users(id),
  by_user_id  uuid not null references users(id),     -- ravitailleur ou dispatch qui a saisi
  kind        text not null check (kind in ('load','unload','cash')),  -- chargement, reprise, cash seul
  box         text,                                   -- « Box 1 »… (null pour le cash seul)
  items       jsonb not null default '[]',            -- [{"p": "DIV", "q": 12}] quantités positives
  cash        numeric(8,2) not null default 0,        -- cash récupéré auprès du livreur
  created_at  timestamptz not null default now()
);
create index if not exists restocks_created_at_idx on restocks (created_at);
create index if not exists restocks_livreur_id_idx on restocks (livreur_id);
create index if not exists restocks_by_user_id_idx on restocks (by_user_id);
alter table restocks enable row level security;
