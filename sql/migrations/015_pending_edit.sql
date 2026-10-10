-- Modification de la commande par le livreur (« Modif ») : en attente de la décision du franchisé,
-- qui a le dernier mot. La course livrée n'est écrite dans la feuille qu'une fois la décision prise.
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table courses add column if not exists pending_edit jsonb;
