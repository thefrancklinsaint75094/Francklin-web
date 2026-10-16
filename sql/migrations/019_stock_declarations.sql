-- Stock déclaré par le livreur : au début de son service (une fois par nuit) et à sa fin de service.
-- Il écrit ce qu'il a sur lui (ou confirme le stock du tableau), puis confirme ; le dispatch voit les écarts.
create table if not exists stock_declarations (
  id            serial primary key,
  livreur_id    uuid not null references users(id) on delete cascade,
  livreur_name  text,                                    -- nom de la feuille au moment de la déclaration
  kind          text not null check (kind in ('start','end')),
  night         date not null,                           -- nuit « du D au D+1 »
  status        text not null default 'asked' check (status in ('asked','confirmed')),
  expected      jsonb,                                   -- stock d'après le tableau quand on lui a demandé
  items         jsonb,                                   -- ce qu'il a confirmé avoir sur lui
  reminded      boolean not null default false,          -- relance (livreur + dispatch) déjà faite
  asked_at      timestamptz not null default now(),
  confirmed_at  timestamptz
);
create unique index if not exists stock_declarations_once on stock_declarations (livreur_id, kind, night);
alter table stock_declarations enable row level security;
