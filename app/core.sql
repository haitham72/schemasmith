-- Engine-owned auth, discussions, spec versions and migration history.

create table if not exists users (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (char_length(name) between 2 and 80),
  email       text not null,
  password_hash text not null,
  is_admin    boolean not null default false,
  status      text not null default 'pending' check (status in ('pending','approved','rejected')),
  created_at  timestamptz not null default now(),
  decided_at  timestamptz,
  decided_by  text
);
create unique index if not exists users_email on users (lower(email));

-- Reviewer role: read-only oversight of people and their approvals (no decisions, no catalog).
alter table users add column if not exists is_reviewer boolean not null default false;

-- Signup audit: where the account was created from.
alter table users add column if not exists signup_ip text not null default '';

-- Guest access (GUEST_LOGIN=1): one shared, non-admin, passwordless account.
-- Guests never count toward the "first signup becomes admin" rule.
alter table users add column if not exists is_guest boolean not null default false;

create table if not exists sessions (
  token      text primary key,
  user_id    uuid not null references users(id) on delete cascade,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '30 days'
);
create index if not exists sessions_user on sessions (user_id);

-- Emails an admin pre-approved: signing up with one is approved immediately.
create table if not exists allowed_emails (
  email      text primary key,
  added_by   text not null default '',
  created_at timestamptz not null default now()
);


-- A generic discussion belongs to an entity/record pair; ownership is checked
-- by the API because a static foreign key cannot target different entity tables.
create table if not exists comments (
  id uuid primary key default gen_random_uuid(),
  entity text not null,
  record_id uuid not null,
  author_name text not null,
  author_email text not null,
  is_admin boolean not null default false,
  body text not null check (char_length(body) between 1 and 2000),
  created_at timestamptz not null default now()
);
create index if not exists comments_record on comments (entity, record_id, created_at);

create table if not exists ss_spec (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  spec jsonb not null,
  spec_hash text not null,
  active boolean not null default true
);
create unique index if not exists ss_spec_active on ss_spec (active) where active;

create table if not exists ss_migrations (
  id bigserial primary key,
  ts timestamptz not null default now(),
  spec_hash text not null,
  ops jsonb not null,
  sql jsonb not null
);

-- NULL distinguishes pre-role accounts from deliberately assigned role arrays.
-- Backfill once; later spec switches do not silently grant a different owner role.
alter table users add column if not exists roles text[];
update users set roles = array(
  select distinct role from unnest(
    case when is_admin then array['admin']::text[] else '{}'::text[] end ||
    case when is_reviewer then array['reviewer']::text[] else '{}'::text[] end ||
    case when not is_admin then coalesce((
      select array_agg(distinct entity->>'owner')
      from ss_spec cross join lateral jsonb_array_elements(spec->'entities') entity
      where active and entity->>'kind' = 'record' and entity->>'owner' is not null
    ), '{}'::text[]) else '{}'::text[] end
  ) role order by role
) where roles is null;
alter table users alter column roles set default '{}'::text[];
alter table users alter column roles set not null;

-- Durable workflow-event metadata (WP6 parity). Every state transition and
-- every thread/note annotation appends one row in the engine's own
-- transaction, so adapters derive stable justification timestamps, decision
-- attribution, explicit note clearing and the thread-wide ask flag from
-- these events instead of line updated_at or discussion comments.
-- The ss_ namespace is already blocked to specs, so no generated table can
-- collide with this name and migrate.py needs no table-shape change.
create table if not exists ss_workflow_events (
  id          bigserial primary key,
  entity      text not null,
  record_id   uuid not null,
  line_id     uuid,
  from_state  text not null,
  to_state    text not null,
  actor_id    text not null default '',
  actor_email text not null default '',
  data        jsonb not null default '{}',
  kind        text not null default 'transition'
              check (kind in ('transition', 'note', 'ask', 'resolve')),
  created_at  timestamptz not null default now()
);
create index if not exists ss_workflow_events_record
  on ss_workflow_events (entity, record_id, created_at);
