-- Table for storing user-specific saved settings
create table if not exists public.user_settings (
  email               text primary key,
  app_password        text not null,
  hours_back          int not null default 24,
  make_webhook_url    text,
  automation_enabled  boolean not null default false,
  timezone            text not null default 'UTC',
  post_time           text not null default '07:00',
  story_enabled       boolean not null default true,
  linkedin_enabled    boolean not null default true,
  newsletter_enabled  boolean not null default false,
  linkedin_auto_post_enabled boolean not null default false,
  linkedin_post_time  text not null default '07:00',
  linkedin_timezone   text not null default 'UTC',
  filter_prompt       text,
  digest_prompt       text,
  story_prompt        text,
  linkedin_prompt     text,
  newsletter_subject_prompt text,
  newsletter_html_prompt text,
  imap_server         text not null default 'imap.gmail.com',
  imap_port           int not null default 993,
  last_run_at         timestamptz,
  last_linkedin_run_at timestamptz,
  last_automation_error text,
  created_at          timestamptz not null default now()
);

alter table public.user_settings
  add column if not exists hours_back int not null default 24,
  add column if not exists automation_enabled boolean not null default false,
  add column if not exists timezone text not null default 'UTC',
  add column if not exists post_time text not null default '07:00',
  add column if not exists story_enabled boolean not null default true,
  add column if not exists linkedin_enabled boolean not null default true,
  add column if not exists newsletter_enabled boolean not null default false,
  add column if not exists linkedin_auto_post_enabled boolean not null default false,
  add column if not exists linkedin_post_time text not null default '07:00',
  add column if not exists linkedin_timezone text not null default 'UTC',
  add column if not exists newsletter_subject_prompt text,
  add column if not exists newsletter_html_prompt text,
  add column if not exists last_run_at timestamptz,
  add column if not exists last_linkedin_run_at timestamptz,
  add column if not exists last_automation_error text;

-- RLS for safety (though service key bypasses it)
alter table public.user_settings enable row level security;
