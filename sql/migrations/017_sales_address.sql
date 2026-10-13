-- /ventes : adresse facultative de chaque vente (« 12 rue de la Paix : 2 US 60 »), écrite dans la colonne
-- Adresse de la feuille Dispatch (« Récap ventes » quand il n'y en a pas).
alter table sales add column if not exists address text;
