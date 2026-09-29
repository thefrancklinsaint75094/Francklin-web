-- Moyen de déplacement déclaré par le livreur (/dispo) et mode détecté pendant une course.
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table users add column if not exists transport_mode text
  check (transport_mode in ('transport','deux_roues','voiture'));
alter table courses add column if not exists detected_mode text
  check (detected_mode in ('metro','vehicule','pied'));
