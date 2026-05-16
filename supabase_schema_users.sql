-- Table for storing user-specific saved settings
create table if not exists public.user_settings (
  email               text primary key,
  app_password        text not null,
  owner_token         text unique,
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
  newsletter_auto_send_enabled boolean not null default false,
  newsletter_send_time text not null default '07:00',
  newsletter_timezone text not null default 'UTC',
  newsletter_sending_method text not null default 'mailbox',
  ses_smtp_host       text,
  ses_smtp_port       int,
  ses_smtp_username   text,
  ses_smtp_password   text,
  ses_verified_sender_email text,
  ses_from_name       text,
  ses_reply_to_email  text,
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
  last_newsletter_run_at timestamptz,
  last_automation_error text,
  created_at          timestamptz not null default now()
);

alter table public.user_settings
  add column if not exists owner_token text unique,
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
  add column if not exists newsletter_auto_send_enabled boolean not null default false,
  add column if not exists newsletter_send_time text not null default '07:00',
  add column if not exists newsletter_timezone text not null default 'UTC',
  add column if not exists newsletter_sending_method text not null default 'mailbox',
  add column if not exists ses_smtp_host text,
  add column if not exists ses_smtp_port int,
  add column if not exists ses_smtp_username text,
  add column if not exists ses_smtp_password text,
  add column if not exists ses_verified_sender_email text,
  add column if not exists ses_from_name text,
  add column if not exists ses_reply_to_email text,
  add column if not exists newsletter_subject_prompt text,
  add column if not exists newsletter_html_prompt text,
  add column if not exists last_run_at timestamptz,
  add column if not exists last_linkedin_run_at timestamptz,
  add column if not exists last_newsletter_run_at timestamptz,
  add column if not exists last_automation_error text;

create table if not exists public.owner_allowed_domains (
  id          uuid primary key default gen_random_uuid(),
  owner_email text not null references public.user_settings(email) on delete cascade,
  domain      text not null,
  created_at  timestamptz not null default now(),
  unique(owner_email, domain)
);

create table if not exists public.newsletter_subscribers (
  id                 uuid primary key default gen_random_uuid(),
  owner_email        text not null references public.user_settings(email) on delete cascade,
  owner_token        text not null,
  subscriber_email   text not null,
  status             text not null default 'pending',
  source             text,
  source_domain      text,
  confirmation_token text,
  unsubscribe_token  text,
  confirmed_at       timestamptz,
  unsubscribed_at    timestamptz,
  created_at         timestamptz not null default now(),
  unique(owner_email, subscriber_email)
);

create table if not exists public.newsletter_send_queue (
  id               uuid primary key default gen_random_uuid(),
  owner_email      text not null references public.user_settings(email) on delete cascade,
  run_id           uuid,
  subscriber_email text not null,
  status           text not null default 'pending',
  attempts         int not null default 0,
  last_error       text,
  scheduled_at     timestamptz,
  sent_at          timestamptz,
  created_at       timestamptz not null default now()
);

create index if not exists owner_allowed_domains_owner_idx
  on public.owner_allowed_domains(owner_email);

create index if not exists newsletter_subscribers_owner_status_idx
  on public.newsletter_subscribers(owner_email, status);

create index if not exists newsletter_subscribers_confirm_idx
  on public.newsletter_subscribers(confirmation_token);

create index if not exists newsletter_subscribers_unsub_idx
  on public.newsletter_subscribers(unsubscribe_token);

create index if not exists newsletter_send_queue_owner_status_idx
  on public.newsletter_send_queue(owner_email, status);

-- RLS for safety (though service key bypasses it)
alter table public.user_settings enable row level security;
alter table public.owner_allowed_domains enable row level security;
alter table public.newsletter_subscribers enable row level security;
alter table public.newsletter_send_queue enable row level security;
