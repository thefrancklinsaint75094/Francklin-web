-- Ventes saisies (/ventes) : 0 € accepté (produit offert).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table sales drop constraint if exists sales_price_check;
alter table sales add constraint sales_price_check check (price >= 0);
