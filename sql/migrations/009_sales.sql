-- Ventes saisies par un admin (/ventes) : écrites dans la feuille Dispatch, renvoyées par /synchro.
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
create table if not exists sales (
  id            serial primary key,
  livreur_name  text not null,                         -- nom dans la feuille (Livreur A…)
  livreur_id    uuid references users(id),             -- compte du bot, s'il existe
  by_user_id    uuid not null references users(id),
  payment       text not null check (payment in ('especes','virement')),
  product       text not null,
  qty           integer not null check (qty > 0),
  price         numeric(8,2) not null check (price > 0),
  created_at    timestamptz not null default now()
);
create index if not exists sales_created_at_idx on sales (created_at);
create index if not exists sales_livreur_id_idx on sales (livreur_id);
create index if not exists sales_by_user_id_idx on sales (by_user_id);
alter table sales enable row level security;
