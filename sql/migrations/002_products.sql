-- Catalogue des produits, géré par le dispatch depuis le bot (/produits).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
create table if not exists products (
  id          serial primary key,
  name        text not null,                    -- nom affiché : « Vodka Absolut »
  name_key    text not null unique,             -- nom normalisé, pour éviter les doublons
  aliases     text[] not null default '{}',     -- autres façons de l'écrire : {absolut, abso}
  created_at  timestamptz not null default now()
);
alter table products enable row level security;
