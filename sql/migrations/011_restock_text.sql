-- Rechargements saisis en texte (/ravi) : livreur de la feuille sans compte bot possible,
-- ravitailleur nommé dans la commande (« /ravi 1 » → Ravitailleur 1).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table restocks alter column livreur_id drop not null;
alter table restocks add column if not exists livreur_name text;
alter table restocks add column if not exists ravitailleur_name text;
