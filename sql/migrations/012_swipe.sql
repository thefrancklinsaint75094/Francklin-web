-- Transfert entre livreurs (/swipe) : une seule ligne, comme dans le tableau Rechargement
-- (livreur de gauche = celui qui donne, colonne T = celui qui reçoit, box « Swipe »).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table restocks drop constraint if exists restocks_kind_check;
alter table restocks add constraint restocks_kind_check check (kind in ('load','unload','cash','swipe'));
alter table restocks add column if not exists to_livreur_id uuid references users(id);
alter table restocks add column if not exists to_livreur_name text;
