-- Goûts (variantes) d'un produit : même coût, même produit en compta (« MSX banane » = MSX).
-- Le bot retient seulement quels goûts chaque livreur a encore (oui / non), jamais de quantité.
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table products add column if not exists variants text[] not null default '{}';
create table if not exists livreur_variants (
  livreur_name  text not null,                       -- nom dans les feuilles (Livreur A…)
  product       text not null,                       -- nom du produit (MSX)
  variant       text not null,                       -- goût (banane)
  updated_at    timestamptz not null default now(),
  primary key (livreur_name, product, variant)
);
alter table livreur_variants enable row level security;
