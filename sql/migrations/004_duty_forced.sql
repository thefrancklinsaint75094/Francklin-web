-- Livreur mis en service par un admin (dispatch ou franchisé) : il reçoit les courses
-- même sans position partagée, et n'est pas mis hors service faute de position.
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table users add column if not exists duty_forced boolean not null default false;
