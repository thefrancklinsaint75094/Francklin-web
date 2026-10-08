-- /ventes lundi : la nuit choisie pour les ventes (onglet de la feuille Dispatch), qui peut être
-- antérieure au jour de saisie. Vide : la nuit de created_at (ventes saisies avant cette version).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table sales add column if not exists night date;
