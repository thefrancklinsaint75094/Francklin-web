-- Argent du ravitailleur (espace « Ravi » de l'appli Dispatch) : registre gardé d'une semaine à l'autre.
-- Le cash récupéré auprès des livreurs vient des rechargements (restocks.cash) ; ici, le reste :
-- charges, invest, remis à l'exploitant (perso), dépôts (donnés au ravitailleur), solde de départ, contrôles.
create table if not exists ravi_moves (
  id           serial primary key,
  ravitailleur text not null,                          -- nom de la feuille (Ravitailleur 1…)
  kind         text not null check (kind in ('charge', 'invest', 'perso', 'depot', 'depart', 'controle')),
  amount       numeric(10,2) not null,                 -- départ : solde fixé ; contrôle : montant compté
  expected     numeric(10,2),                          -- contrôle : ce qu'il devait avoir à ce moment
  motif        text,
  by_user_id   uuid not null references users(id),
  sheet_state  text not null default 'none'            -- copie dans COMPTA RAVI
               check (sheet_state in ('none', 'pending', 'done', 'remove', 'removed')),
  deleted_at   timestamptz,
  created_at   timestamptz not null default now()
);
create index if not exists ravi_moves_ravi on ravi_moves (ravitailleur, created_at);
alter table ravi_moves enable row level security;
