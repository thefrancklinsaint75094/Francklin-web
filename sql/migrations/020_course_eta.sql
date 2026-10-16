-- Délai annoncé par le livreur quand il prend une course (boutons 5 min … 1 h) : le franchisé est prévenu.
alter table courses add column if not exists eta_minutes integer;     -- délai choisi
alter table courses add column if not exists eta_at timestamptz;      -- heure d'arrivée prévue
