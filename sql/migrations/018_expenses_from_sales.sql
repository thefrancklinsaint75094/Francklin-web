-- /ventes : « charge = 20 » / « paye = 100 » dans un récap → dépense du livreur du bloc, même s'il n'a pas
-- de compte dans le bot (nom de la feuille), dans l'onglet de la nuit choisie (/ventes lundi).
alter table expenses alter column livreur_id drop not null;
alter table expenses add column if not exists livreur_name text;   -- nom de la feuille quand livreur_id est vide
alter table expenses add column if not exists night date;          -- nuit choisie ; vide : celle de created_at
