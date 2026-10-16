-- Mini-application Telegram : fil d'activité (tous les avis du dispatch, lus dans l'appli) et niveau de
-- notifications par personne (« essentiel » : seuls les avis importants arrivent en messages ; « tout »).
create table if not exists feed (
  id          bigserial primary key,
  created_at  timestamptz not null default now(),
  text        text not null,                         -- même texte que l'avis (HTML Telegram)
  team        boolean not null default true,         -- visible des franchisés (sinon dispatch seul)
  important   boolean not null default false
);
create index if not exists feed_created_at on feed (created_at desc);
alter table feed enable row level security;

alter table users add column if not exists notify_level text not null default 'essentiel'
  check (notify_level in ('essentiel', 'tout'));
