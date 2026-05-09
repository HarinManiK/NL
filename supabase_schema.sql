-- Newsletter Digest — single table.
-- Run this in Supabase SQL Editor.

create table if not exists public.runs (
  id              uuid primary key,
  email           text not null,
  created_at      timestamptz not null default now(),
  hours_back      int  not null,
  num_total       int  not null default 0,
  num_kept        int  not null default 0,
  digest          text,
  story           text,
  linkedin        text,
  filter_prompt   text,
  digest_prompt   text,
  story_prompt    text,
  linkedin_prompt text,
  elapsed_seconds numeric
);

create index if not exists runs_email_created_idx
  on public.runs (email, created_at desc);

-- We use the service key from the backend, so RLS is not strictly required.
-- Keeping RLS enabled with no public policies is safest — only the service key
-- (which bypasses RLS) can read/write.
alter table public.runs enable row level security;
