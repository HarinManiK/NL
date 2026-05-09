export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:8000";

export type Prompts = {
  filter: string;
  digest: string;
  story: string;
  linkedin: string;
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
  elapsed_seconds?: number;
};

export type StreamEvent =
  | { type: "status"; message: string }
  | { type: "folder"; name: string; count: number }
  | { type: "mail"; subject: string; sender: string; date: string; folder: string }
  | { type: "fetch_done"; total: number }
  | { type: "filter_start" }
  | { type: "decision"; subject: string; sender: string; kept: boolean }
  | { type: "filter_done"; kept: number; total: number; run_id: string }
  | { type: "step"; name: "digest" | "story" | "linkedin"; status: "start" | "done"; text?: string }
  | { type: "complete"; run_id: string; num_total: number; num_kept: number; digest: string; story: string; linkedin: string; elapsed_seconds: number }
  | { type: "error"; message: string };

export type UserSettings = {
  email: string;
  app_password: string;
  timezone: string;
  post_time: string;
  make_webhook_url?: string;
  automation_enabled: boolean;
  prompts: Prompts;
  imap_server: string;
  imap_port: number;
  found?: boolean;
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

export async function saveSettings(payload: UserSettings): Promise<void> {
  const r = await fetch(`${API_BASE}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await readError(r));
}
