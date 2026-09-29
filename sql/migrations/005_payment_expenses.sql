-- Mode de paiement des courses livrées et dépenses des livreurs (caisse).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table courses add column if not exists payment text check (payment in ('especes','virement'));

create table if not exists expenses (
  id          serial primary key,
  livreur_id  uuid not null references users(id),
  by_user_id  uuid not null references users(id),     -- livreur lui-même ou admin
  kind        text not null check (kind in ('charges','paye')),  -- à tes frais / avance sur paye
  amount      numeric(8,2) not null check (amount > 0),
  motif       text,
  created_at  timestamptz not null default now()
);
create index if not exists expenses_created_at_idx on expenses (created_at);
create index if not exists expenses_livreur_id_idx on expenses (livreur_id);
create index if not exists expenses_by_user_id_idx on expenses (by_user_id);
alter table expenses enable row level security;
