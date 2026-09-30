-- Comptes supprimés (/supprimer) et messages envoyés par le bot (pour les effacer quand quelqu'un
-- perd son accès : Telegram ne laisse effacer que les messages de moins de 48 h).
-- À exécuter une fois sur une base créée avec une version antérieure de schema.sql.
alter table users drop constraint if exists users_status_check;
alter table users add constraint users_status_check check (status in ('pending','active','banned','deleted'));

create table if not exists bot_messages (
  chat_id     bigint not null,
  message_id  bigint not null,
  created_at  timestamptz not null default now(),
  primary key (chat_id, message_id)
);
create index if not exists bot_messages_created_at_idx on bot_messages (created_at);
alter table bot_messages enable row level security;
