-- Table for storing user-specific automation settings
create table if not exists public.user_settings (
  email               text primary key,
  app_password        text not null, -- Encrypted or plain? User said "just stick to current approach", which seems to be plain in the current code's runs table context, but we should be careful.
  timezone            text not null default 'UTC',
  post_time           text not null default '07:00', -- Format: HH:MM
  make_webhook_url    text,
  automation_enabled  boolean not null default false,
  filter_prompt       text,
  digest_prompt       text,
  story_prompt        text,
  linkedin_prompt     text,
  imap_server         text not null default 'imap.gmail.com',
  imap_port           int not null default 993,
  last_run_at         timestamptz,
  created_at          timestamptz not null default now()
);

-- RLS for safety (though service key bypasses it)
alter table public.user_settings enable row level security;
