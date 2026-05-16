export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:8000";

export type Prompts = {
  filter: string;
  digest: string;
  story: string;
  linkedin: string;
  newsletter_subject: string;
  newsletter_html: string;
};

export type RunListItem = {
  id: string;
  created_at: string;
  hours_back: number;
  num_total: number;
  num_kept: number;
};

export type RunDetail = {
  id: string;
  created_at: string;
  hours_back: number;
  num_total: number;
  num_kept: number;
  digest: string;
  story: string;
  linkedin: string;
  newsletter_subject?: string;
  newsletter_html?: string;
  useful_links?: UsefulLink[];
  elapsed_seconds?: number;
};

export type UsefulLink = {
  url: string;
  text?: string;
  source_sender?: string;
  source_subject?: string;
  nearby_text?: string;
};

export type StreamEvent =
  | { type: "status"; message: string }
  | { type: "folder"; name: string; count: number }
  | { type: "mail"; subject: string; sender: string; date: string; folder: string }
  | { type: "fetch_done"; total: number }
  | { type: "filter_start" }
  | { type: "decision"; subject: string; sender: string; kept: boolean }
  | { type: "links_done"; count: number; links: UsefulLink[] }
  | { type: "filter_done"; kept: number; total: number; run_id: string }
  | { type: "step"; name: "digest" | "story" | "linkedin" | "newsletter"; status: "start" | "done"; text?: string }
  | { type: "newsletter_done"; subject: string; html: string }
  | { type: "complete"; run_id: string; num_total: number; num_kept: number; digest: string; story: string; linkedin: string; newsletter_subject?: string; newsletter_html?: string; useful_links?: UsefulLink[]; elapsed_seconds: number }
  | { type: "error"; message: string };

export type UserSettings = {
  email: string;
  owner_token?: string;
  app_password?: string;
  hours_back?: number;
  make_webhook_url?: string;
  automation_enabled?: boolean;
  timezone?: string;
  post_time?: string;
  last_run_at?: string | null;
  last_linkedin_run_at?: string | null;
  last_newsletter_run_at?: string | null;
  last_automation_error?: string | null;
  prompts?: Prompts;
  imap_server?: string;
  imap_port?: number;
  story_enabled?: boolean;
  linkedin_enabled?: boolean;
  newsletter_enabled?: boolean;
  linkedin_auto_post_enabled?: boolean;
  linkedin_post_time?: string;
  linkedin_timezone?: string;
  newsletter_auto_send_enabled?: boolean;
  newsletter_send_time?: string;
  newsletter_timezone?: string;
  newsletter_sending_method?: "mailbox" | "ses";
  ses_smtp_host?: string;
  ses_smtp_port?: number;
  ses_smtp_username?: string;
  ses_smtp_password?: string;
  ses_verified_sender_email?: string;
  ses_from_name?: string;
  ses_reply_to_email?: string;
  found?: boolean;
};

export type SaveSettingsPayload = {
  email: string;
  app_password: string;
  hours_back: number;
  make_webhook_url?: string;
  automation_enabled: boolean;
  timezone: string;
  post_time: string;
  prompts: Prompts;
  imap_server: string;
  imap_port: number;
  story_enabled: boolean;
  linkedin_enabled: boolean;
  newsletter_enabled: boolean;
  linkedin_auto_post_enabled: boolean;
  linkedin_post_time: string;
  linkedin_timezone: string;
  newsletter_auto_send_enabled: boolean;
  newsletter_send_time: string;
  newsletter_timezone: string;
  newsletter_sending_method: "mailbox" | "ses";
  ses_smtp_host?: string;
  ses_smtp_port?: number;
  ses_smtp_username?: string;
  ses_smtp_password?: string;
  ses_verified_sender_email?: string;
  ses_from_name?: string;
  ses_reply_to_email?: string;
};

export type ManualPostResponse = {
  ok: boolean;
  status_code: number;
};

export type SaveSettingsResponse = {
  ok: boolean;
  automation_run_reset: boolean;
  owner_token?: string;
};

export type AllowedDomain = {
  id: string;
  owner_email: string;
  domain: string;
  created_at: string;
};

export type Subscriber = {
  id: string;
  subscriber_email: string;
  status: string;
  source?: string;
  source_domain?: string;
  created_at: string;
  confirmed_at?: string | null;
  unsubscribed_at?: string | null;
};

export type SubscribeResponse = {
  ok: boolean;
  status: string;
  confirmation_required: boolean;
};

async function readError(res: Response): Promise<string> {
  let msg = `HTTP ${res.status}`;
  try {
    const data = await res.json();
    if (data?.detail) {
      msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    }
  } catch {
    try {
      const text = await res.text();
      if (text) msg = text;
    } catch {}
  }
  return msg;
}

export async function verify(payload: {
  email: string;
  app_password: string;
}): Promise<void> {
  const r = await fetch(`${API_BASE}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await readError(r));
}

export async function streamRun(
  payload: {
    email: string;
    app_password: string;
    hours_back: number;
    prompts: Prompts;
    story_enabled: boolean;
    linkedin_enabled: boolean;
    newsletter_enabled: boolean;
  },
  onEvent: (evt: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`${API_BASE}/run/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(await readError(r));

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLines = frame
        .split("\n")
        .filter(l => l.startsWith("data:"))
        .map(l => l.slice(5).trimStart());
      if (!dataLines.length) continue;
      const json = dataLines.join("\n");
      try {
        onEvent(JSON.parse(json) as StreamEvent);
      } catch (e) {
        // Ignore unparseable frames; the server only sends JSON.
        console.warn("bad SSE frame:", json);
      }
    }
  }
}

export async function listRuns(email: string): Promise<RunListItem[]> {
  const r = await fetch(`${API_BASE}/runs?email=${encodeURIComponent(email)}`);
  if (!r.ok) throw new Error(await readError(r));
  return r.json();
}

export async function getRun(runId: string, email: string): Promise<RunDetail> {
  const r = await fetch(
    `${API_BASE}/runs/${encodeURIComponent(runId)}?email=${encodeURIComponent(email)}`,
  );
  if (!r.ok) throw new Error(await readError(r));
  return r.json();
}

export async function getSettings(email: string): Promise<UserSettings> {
  const r = await fetch(`${API_BASE}/settings?email=${encodeURIComponent(email)}`);
  if (!r.ok) throw new Error(await readError(r));
  return r.json();
}

export async function saveSettings(payload: SaveSettingsPayload): Promise<SaveSettingsResponse> {
  const r = await fetch(`${API_BASE}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await readError(r));
  return r.json();
}

export async function manualPost(webhookUrl: string, content: string): Promise<ManualPostResponse> {
  const r = await fetch(`${API_BASE}/post`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ webhook_url: webhookUrl, content }),
  });
  if (!r.ok) throw new Error(await readError(r));
  return r.json();
}

export async function listAllowedDomains(email: string): Promise<AllowedDomain[]> {
  const r = await fetch(`${API_BASE}/allowed-domains?email=${encodeURIComponent(email)}`);
  if (!r.ok) throw new Error(await readError(r));
  const data = await r.json();
  return data.domains || [];
}

export async function addAllowedDomain(payload: {
  email: string;
  app_password: string;
  domain: string;
}): Promise<AllowedDomain> {
  const r = await fetch(`${API_BASE}/allowed-domains`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await readError(r));
  return r.json();
}

export async function deleteAllowedDomain(payload: {
  email: string;
  app_password: string;
  domain: string;
}): Promise<void> {
  const params = new URLSearchParams({
    email: payload.email,
    app_password: payload.app_password,
  });
  const r = await fetch(`${API_BASE}/allowed-domains/${encodeURIComponent(payload.domain)}?${params.toString()}`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(await readError(r));
}

export async function listSubscribers(email: string): Promise<{ count: number; subscribers: Subscriber[] }> {
  const r = await fetch(`${API_BASE}/subscribers?email=${encodeURIComponent(email)}`);
  if (!r.ok) throw new Error(await readError(r));
  return r.json();
}

export async function subscribeToNewsletter(payload: {
  owner_token: string;
  subscriber_email: string;
  source_domain?: string;
  trusted_email_provided?: boolean;
}): Promise<SubscribeResponse> {
  const r = await fetch(`${API_BASE}/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await readError(r));
  return r.json();
}
