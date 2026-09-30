-- Position du livreur : partagée en direct ou fixe, et jusqu'à quand (durée choisie dans Telegram).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table livreur_positions add column if not exists live boolean;
alter table livreur_positions add column if not exists live_until timestamptz;
