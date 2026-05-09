"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  API_BASE,
  getRun,
  listRuns,
  streamRun,
  verify,
  getSettings,
  saveSettings,
  type Prompts,
  type RunDetail,
  type RunListItem,
  type StreamEvent,
  type UserSettings,
} from "../lib/api";
import { load, save } from "../lib/storage";

// ---- Hardcoded defaults (kept in sync with backend) ----
const DEFAULT_PROMPTS: Prompts = {
  filter:
    "You are deciding which emails are 'newsletters' worth aggregating into a daily digest. " +
    "KEEP an email if it is: a regular newsletter, a curated digest, an industry roundup, " +
    "a Substack/Beehiiv/Medium-style publication, an editorial brief, a 'this week in X' " +
    "update, or a long-form informational broadcast email. " +
    "DROP: personal mail, transactional notifications (receipts, password resets, shipping, " +
    "calendar invites, OTPs, social-network alerts), marketing promos that are mostly a " +
    "coupon or discount code, automated system mail, and anything that looks like a 1:1 " +
    "conversation. When unsure, prefer to KEEP.",
  digest:
    "You are summarising a batch of newsletters into one cohesive digest for a busy reader " +
    "who does not want to read the originals. " +
    "Write a clean, scannable digest grouped by theme (not by source). " +
    "Keep every concrete fact: company names, product names, numbers, dates, names of " +
    "people, and links to the original story when present. Drop fluff, intros, signoffs, " +
    "and self-promotion. Use short bullet points under bold theme headings. " +
    "Aim for ~400-700 words depending on volume. Output plain text/markdown — no preamble.",
  story:
    "You are turning the digest below into a single flowing narrative — a 'what happened " +
    "today in this world' story. Write in connected paragraphs, not bullets. Keep it " +
    "factual and grounded; do not invent details. Weave related items together so the " +
    "reader gets the arc of the day across topics. ~300-500 words. Output plain text " +
    "with no preamble.",
  linkedin:
    "Turn the digest below into an engaging LinkedIn post.\n\n" +
    "Goal:\n" +
    "Create a LinkedIn-ready post that captures the full value of the digest without losing important details.\n\n" +
    "Hard rules:\n" +
    "- Do NOT reduce this to only 2–3 themes.\n" +
    "- Preserve all important concrete facts: company names, product names, people, numbers, prices, funding amounts, dates, percentages, APYs, viewership numbers, policy changes, and major claims.\n" +
    "- You may combine related points, but do not drop major items.\n" +
    "- Write it as a cohesive post, not a source-by-source summary.\n" +
    "- Use theme-based grouping if needed.\n" +
    "- Add light interpretation, but do not invent facts.\n" +
    "- Avoid generic lines like “innovation is accelerating” or “the future is here.”\n" +
    "- Start with a specific hook based on the strongest pattern in the digest.\n" +
    "- Use short paragraphs and clean bullets.\n" +
    "- End with a useful takeaway or discussion question.\n" +
    "- Length: 350–600 words depending on digest size.\n" +
    "- No emojis unless genuinely useful.\n" +
    "- Add 3–5 relevant hashtags on the final line.\n" +
    "- Output only the LinkedIn post. No preamble.\n\n" +
    "Style:\n" +
    "Conversational, sharp, professional, founder/investor/operator voice.\n" +
    "Make it detailed enough to feel valuable, but clean enough that someone would actually read it on LinkedIn.",
};

type View = "main" | "history";

type CurrentResult = {
  run_id: string;
  num_total: number;
  num_kept: number;
  digest: string;
  story: string;
  linkedin: string;
};

type ProgressLine =
  | { kind: "status"; text: string }
  | { kind: "folder"; name: string; count: number }
  | { kind: "mail"; subject: string; sender: string }
  | { kind: "decision"; subject: string; sender: string; kept: boolean }
  | { kind: "step"; name: string; status: "start" | "done" };

export default function Home() {
  // ---- persisted state ----
  const [email, setEmail] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [hoursBack, setHoursBack] = useState(24);
  const [prompts, setPrompts] = useState<Prompts>(DEFAULT_PROMPTS);
  const [timezone, setTimezone] = useState("UTC");
  const [postTime, setPostTime] = useState("07:00");
  const [makeWebhookUrl, setMakeWebhookUrl] = useState("");
  const [automationEnabled, setAutomationEnabled] = useState(false);

  // ---- transient state ----
  const [verifying, setVerifying] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CurrentResult | null>(null);
  const [progress, setProgress] = useState<ProgressLine[]>([]);
  const [activeTab, setActiveTab] = useState<"digest" | "story" | "linkedin">("digest");
  const [showSettings, setShowSettings] = useState(true);
  const [showPrompts, setShowPrompts] = useState(false);
  const [showMakeModal, setShowMakeModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  // ---- view + history ----
  const [view, setView] = useState<View>("main");
  const [history, setHistory] = useState<RunListItem[]>([]);
  const [openFolders, setOpenFolders] = useState<Record<string, boolean>>({});
  const [historyDetail, setHistoryDetail] = useState<RunDetail | null>(null);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(false);
  const [historyTab, setHistoryTab] = useState<"digest" | "story" | "linkedin">("digest");

  const progressBoxRef = useRef<HTMLDivElement | null>(null);

  // ---- hydrate from localStorage ----
  useEffect(() => {
    setEmail(load<string>("email", ""));
    setAppPassword(load<string>("app_password", ""));
    setHoursBack(load<number>("hours_back", 24));
    const saved = load<Prompts | null>("prompts", null);
    const usable = saved && saved.filter && saved.digest && saved.story && saved.linkedin;
    if (usable) setPrompts(saved as Prompts);
    setHydrated(true);

    // Warm up the server (e.g. Render free tier sleep)
    fetch(`${API_BASE}/`).catch(() => {});
  }, []);

  async function loadRemoteSettings() {
    if (!email) return;
    try {
      const s = await getSettings(email);
      if (s.found) {
        setAppPassword(s.app_password);
        setTimezone(s.timezone);
        setPostTime(s.post_time);
        setMakeWebhookUrl(s.make_webhook_url || "");
        setAutomationEnabled(s.automation_enabled);
        setPrompts(s.prompts);
      }
    } catch (e) {
      console.warn("loadRemoteSettings failed:", e);
    }
  }

  useEffect(() => {
    if (hydrated && email) {
      loadRemoteSettings();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email, hydrated]);

  useEffect(() => { if (hydrated) save("email", email); }, [email, hydrated]);
  useEffect(() => { if (hydrated) save("app_password", appPassword); }, [appPassword, hydrated]);
  useEffect(() => { if (hydrated) save("hours_back", hoursBack); }, [hoursBack, hydrated]);
  useEffect(() => { if (hydrated) save("prompts", prompts); }, [prompts, hydrated]);

  // ---- history loader ----
  async function refreshHistory() {
    if (!email) { setHistory([]); return; }
    try {
      const rows = await listRuns(email);
      setHistory(rows);
    } catch (e: any) {
      console.warn("history failed:", e.message);
    }
  }
  useEffect(() => { refreshHistory(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [email]);

  // auto-scroll progress box
  useEffect(() => {
    if (progressBoxRef.current) {
      progressBoxRef.current.scrollTop = progressBoxRef.current.scrollHeight;
    }
  }, [progress]);

  // group history by date string
  const historyByDate = useMemo(() => {
    const groups: Record<string, RunListItem[]> = {};
    for (const h of history) {
      const d = new Date(h.created_at);
      const key = d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
      (groups[key] ??= []).push(h);
    }
    // sort runs within each date oldest-first so -1 is first run of the day
    for (const k of Object.keys(groups)) {
      groups[k].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    }
    // return as array sorted desc by date
    return Object.entries(groups).sort((a, b) => {
      const da = new Date(a[1][0].created_at).getTime();
      const db = new Date(b[1][0].created_at).getTime();
      return db - da;
    });
  }, [history]);

  // ---- handlers ----
  async function onVerify() {
    setVerifyMsg(null);
    setError(null);
    if (!email || !appPassword) {
      setVerifyMsg("Enter email and app password first.");
      return;
    }
    setVerifying(true);
    try {
      await verify({ email, app_password: appPassword });
      setVerifyMsg("✓ IMAP connection works.");
    } catch (e: any) {
      setVerifyMsg(`✗ ${e.message}`);
    } finally {
      setVerifying(false);
    }
  }

  async function onSaveSettings() {
    setError(null);
    if (!email || !appPassword) {
      setError("Enter email and app password to save settings.");
      return;
    }
    setSaving(true);
    try {
      await saveSettings({
        email,
        app_password: appPassword,
        timezone,
        post_time: postTime,
        make_webhook_url: makeWebhookUrl,
        automation_enabled: automationEnabled,
        prompts,
        imap_server: "imap.gmail.com",
        imap_port: 993,
      });
      setVerifyMsg("✓ Settings saved and automation status updated.");
    } catch (e: any) {
      setError(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  function downloadBlueprint() {
    fetch("/make_blueprint.json")
      .then(r => r.json())
      .then(data => {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "linkedin_autoposter_blueprint.json";
        a.click();
      });
  }

  async function onRun() {
    setError(null);
    setResult(null);
    setProgress([]);
    if (!email || !appPassword) {
      setError("Enter email and app password first.");
      return;
    }
    if (!prompts.filter || !prompts.digest || !prompts.story || !prompts.linkedin) {
      setError("All four prompts must be filled. Click 'Reset to defaults' if you cleared them.");
      return;
    }
    setRunning(true);
    setShowSettings(false);
    setView("main");

    let final: CurrentResult | null = null;

    try {
      await streamRun(
        { email, app_password: appPassword, hours_back: hoursBack, prompts },
        (evt: StreamEvent) => {
          switch (evt.type) {
            case "status":
              setProgress(p => [...p, { kind: "status", text: evt.message }]);
              break;
            case "folder":
              setProgress(p => [...p, { kind: "folder", name: evt.name, count: evt.count }]);
              break;
            case "mail":
              setProgress(p => [...p, { kind: "mail", subject: evt.subject, sender: evt.sender }]);
              break;
            case "fetch_done":
              setProgress(p => [...p, { kind: "status", text: `Fetched ${evt.total} mail(s) total. Starting filter…` }]);
              break;
            case "filter_start":
              setProgress(p => [...p, { kind: "step", name: "filter", status: "start" }]);
              break;
            case "decision":
              setProgress(p => [...p, { kind: "decision", subject: evt.subject, sender: evt.sender, kept: evt.kept }]);
              break;
            case "filter_done":
              setProgress(p => [...p, { kind: "status", text: `Filter complete: kept ${evt.kept} / ${evt.total}.` }]);
              setResult({
                run_id: evt.run_id,
                num_total: evt.total,
                num_kept: evt.kept,
                digest: "",
                story: "",
                linkedin: "",
              });
              break;
            case "step":
              setProgress(p => [...p, { kind: "step", name: evt.name, status: evt.status }]);
              if (evt.status === "done" && evt.text) {
                setResult(prev => prev ? { ...prev, [evt.name]: evt.text } : null);
                setActiveTab(evt.name as any);
              }
              break;
            case "complete":
              final = {
                run_id: evt.run_id,
                num_total: evt.num_total,
                num_kept: evt.num_kept,
                digest: evt.digest,
                story: evt.story,
                linkedin: evt.linkedin,
              };
              setProgress(p => [...p, { kind: "status", text: `Done in ${evt.elapsed_seconds}s.` }]);
              break;
            case "error":
              setError(evt.message);
              break;
          }
        },
      );
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setRunning(false);
      if (final) {
        setResult(final);
        refreshHistory();
      }
    }
  }

  async function openHistoryRun(id: string) {
    setHistoryDetail(null);
    setHistoryDetailLoading(true);
    setHistoryTab("digest");
    try {
      const row = await getRun(id, email);
      setHistoryDetail(row);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setHistoryDetailLoading(false);
    }
  }

  function resetPrompts() { setPrompts(DEFAULT_PROMPTS); }

  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); } catch {}
  }

  // ---- render ----
  return (
    <main className="max-w-3xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900">Newsletter Digest</h1>
          <p className="text-sm text-zinc-500 mt-1">
            Pull newsletters from the last <span className="font-medium text-zinc-700">{hoursBack}h</span>,
            aggregate, then generate a digest, story, and LinkedIn post.
          </p>
        </div>
        <nav className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => { setView("main"); }}
            className={tabBtn(view === "main")}
          >
            Run
          </button>
          <button
            onClick={() => { setView("history"); refreshHistory(); setHistoryDetail(null); }}
            className={tabBtn(view === "history")}
          >
            History
          </button>
        </nav>
      </header>

      {view === "main" && (
        <>
          {/* Settings */}
          <section className="rounded-xl border border-zinc-200 bg-white mb-4 shadow-sm">
            <button
              onClick={() => setShowSettings(s => !s)}
              className="w-full flex items-center justify-between px-4 py-3 text-left"
            >
              <span className="font-medium text-zinc-800">Settings</span>
              <span className="text-zinc-400 text-sm">{showSettings ? "hide" : "show"}</span>
            </button>
            {showSettings && (
              <div className="px-4 pb-4 space-y-4 border-t border-zinc-100 pt-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label="Email">
                    <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                           placeholder="you@gmail.com" className={inputCls} />
                  </Field>
                  <Field label={
                    <span>
                      Gmail app password: <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer" className="text-emerald-600 hover:underline">get it from here</a>
                    </span>
                  }>
                    <input type="password" value={appPassword} onChange={e => setAppPassword(e.target.value)}
                           placeholder="xxxx xxxx xxxx xxxx" className={inputCls} />
                  </Field>
                  <Field label="Hours to look back">
                    <input type="number" min={1} max={720} value={hoursBack}
                           onChange={e => setHoursBack(Math.max(1, Math.min(720, Number(e.target.value) || 1)))}
                           className={inputCls} />
                  </Field>
                  <div className="flex items-end">
                    <button onClick={onVerify} disabled={verifying}
                            className="px-3 py-2 rounded-md border border-zinc-300 bg-white hover:bg-zinc-50 text-sm text-zinc-700 disabled:opacity-50">
                      {verifying ? "Verifying…" : "Verify connection"}
                    </button>
                  </div>
                </div>
                {verifyMsg && (
                  <div className={`text-sm ${verifyMsg.startsWith("✓") ? "text-emerald-700" : "text-rose-700"}`}>
                    {verifyMsg}
                  </div>
                )}

                <div className="pt-2 border-t border-zinc-100">
                  <button onClick={() => setShowPrompts(s => !s)}
                          className="text-sm text-zinc-700 hover:text-zinc-900">
                    {showPrompts ? "▾" : "▸"} Prompts
                  </button>
                  {showPrompts && (
                    <div className="mt-3 space-y-3">
                      <PromptArea label="Filter prompt — decides which mails are 'newsletters'"
                                  value={prompts.filter}
                                  onChange={v => setPrompts({ ...prompts, filter: v })} />
                      <PromptArea label="Digest prompt"
                                  value={prompts.digest}
                                  onChange={v => setPrompts({ ...prompts, digest: v })} />
                      <PromptArea label="Story prompt"
                                  value={prompts.story}
                                  onChange={v => setPrompts({ ...prompts, story: v })} />
                      <PromptArea label="LinkedIn prompt"
                                  value={prompts.linkedin}
                                  onChange={v => setPrompts({ ...prompts, linkedin: v })} />
                      <button onClick={resetPrompts}
                              className="text-xs text-zinc-500 hover:text-zinc-900 underline">
                        Reset all four prompts to defaults
                      </button>
                    </div>
                  )}
                </div>

                <div className="pt-4 border-t border-zinc-100 space-y-4">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-zinc-800">Automatic Posting</span>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input type="checkbox" className="sr-only peer" checked={automationEnabled}
                             onChange={e => setAutomationEnabled(e.target.checked)} />
                      <div className="w-11 h-6 bg-zinc-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-zinc-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-emerald-600"></div>
                    </label>
                  </div>
                  
                  {automationEnabled && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 animate-in fade-in slide-in-from-top-2 duration-300">
                      <Field label="Post time (Daily)">
                        <input type="time" value={postTime} onChange={e => setPostTime(e.target.value)}
                               className={inputCls} />
                      </Field>
                      <Field label="Timezone">
                        <select value={timezone} onChange={e => setTimezone(e.target.value)} className={inputCls}>
                          <option value="UTC">UTC</option>
                          <option value="America/New_York">Eastern Time</option>
                          <option value="America/Chicago">Central Time</option>
                          <option value="America/Denver">Mountain Time</option>
                          <option value="America/Los_Angeles">Pacific Time</option>
                          <option value="Europe/London">London</option>
                          <option value="Europe/Berlin">Berlin</option>
                          <option value="Asia/Kolkata">India (IST)</option>
                          <option value="Asia/Singapore">Singapore</option>
                          <option value="Australia/Sydney">Sydney</option>
                        </select>
                      </Field>
                      <div className="sm:col-span-2">
                        <Field label={
                          <div className="flex justify-between">
                            <span>Make.com Webhook URL</span>
                            <button onClick={() => setShowMakeModal(true)} className="text-emerald-600 hover:underline">How to setup?</button>
                          </div>
                        }>
                          <input type="text" value={makeWebhookUrl} onChange={e => setMakeWebhookUrl(e.target.value)}
                                 placeholder="https://hook.make.com/..." className={inputCls} />
                        </Field>
                      </div>
                    </div>
                  )}

                  <button
                    onClick={onSaveSettings}
                    disabled={saving}
                    className="w-full py-2 bg-zinc-900 text-white rounded-md text-sm font-medium hover:bg-zinc-800 transition-colors disabled:opacity-50"
                  >
                    {saving ? "Saving..." : "Save Settings & Update Schedule"}
                  </button>
                </div>
              </div>
            )}
          </section>

          {/* Run button */}
          <div className="flex items-center gap-3 mb-4">
            <button onClick={onRun} disabled={running}
                    className="px-5 py-2.5 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white font-medium disabled:opacity-60">
              {running ? <span className="inline-flex items-center gap-2"><Spinner /> Running…</span> : "Run"}
            </button>
            {result && !running && (
              <span className="text-sm text-zinc-500">
                kept {result.num_kept} / {result.num_total} mails
              </span>
            )}
          </div>

          {error && <ErrorBox message={error} />}

          {/* Progress log — visible while running, and stays visible until next run */}
          {progress.length > 0 && (
            <section className="rounded-xl border border-zinc-200 bg-white mb-4 shadow-sm">
              <div className="px-4 py-2 border-b border-zinc-100 text-xs font-medium uppercase text-zinc-500 tracking-wide flex items-center justify-between">
                <span>Activity</span>
                {running && <span className="text-emerald-700 inline-flex items-center gap-1.5"><Spinner small /> live</span>}
              </div>
              <div ref={progressBoxRef}
                   className="max-h-72 overflow-auto px-4 py-3 text-sm font-mono text-zinc-800 space-y-1">
                {progress.map((p, i) => <ProgressRow key={i} p={p} />)}
              </div>
            </section>
          )}

          {/* Result tabs */}
          {result && (
            <ResultTabs result={result} active={activeTab} setActive={setActiveTab} onCopy={copy} />
          )}
        </>
      )}

      {view === "history" && (
        <section>
          {historyDetail ? (
            <div>
              <button onClick={() => setHistoryDetail(null)}
                      className="text-sm text-zinc-600 hover:text-zinc-900 mb-3">
                ← Back to history
              </button>
              <div className="text-xs text-zinc-500 mb-2">
                {new Date(historyDetail.created_at).toLocaleString()} · {historyDetail.hours_back}h
                lookback · kept {historyDetail.num_kept}/{historyDetail.num_total}
              </div>
              <ResultTabs
                result={{
                  run_id: historyDetail.id,
                  num_total: historyDetail.num_total,
                  num_kept: historyDetail.num_kept,
                  digest: historyDetail.digest || "",
                  story: historyDetail.story || "",
                  linkedin: historyDetail.linkedin || "",
                }}
                active={historyTab}
                setActive={setHistoryTab}
                onCopy={copy}
              />
            </div>
          ) : historyDetailLoading ? (
            <div className="text-sm text-zinc-500">Loading…</div>
          ) : history.length === 0 ? (
            <div className="text-sm text-zinc-500">
              No past runs for <code className="text-zinc-700">{email || "—"}</code>.
            </div>
          ) : (
            <ul className="space-y-2">
              {historyByDate.map(([date, runs]) => {
                const open = openFolders[date] ?? true;
                return (
                  <li key={date} className="rounded-xl border border-zinc-200 bg-white shadow-sm">
                    <button
                      onClick={() => setOpenFolders(o => ({ ...o, [date]: !open }))}
                      className="w-full flex items-center justify-between px-4 py-3 text-left"
                    >
                      <span className="font-medium text-zinc-800">📁 {date}</span>
                      <span className="text-xs text-zinc-500">{runs.length} run{runs.length === 1 ? "" : "s"}</span>
                    </button>
                    {open && (
                      <ul className="divide-y divide-zinc-100 border-t border-zinc-100">
                        {runs.map((r, i) => (
                          <li key={r.id}>
                            <button onClick={() => openHistoryRun(r.id)}
                                    className="w-full text-left px-4 py-2.5 hover:bg-zinc-50 flex items-center justify-between">
                              <div className="text-sm">
                                <div className="text-zinc-800">
                                  Run -{i + 1} ·{" "}
                                  <span className="text-zinc-500">
                                    {new Date(r.created_at).toLocaleTimeString()}
                                  </span>
                                </div>
                                <div className="text-xs text-zinc-500">
                                  {r.hours_back}h lookback · kept {r.num_kept}/{r.num_total}
                                </div>
                              </div>
                              <span className="text-zinc-400 text-xs">→</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      {showMakeModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50 animate-in fade-in duration-200">
          <div className="bg-white rounded-2xl max-w-xl w-full p-6 shadow-2xl">
            <h2 className="text-xl font-bold text-zinc-900 mb-4">Setup Automated LinkedIn Posting</h2>
            <div className="space-y-4 text-sm text-zinc-700">
              <p>Follow these steps to connect your LinkedIn Company Page via Make.com (free):</p>
              <ol className="list-decimal pl-5 space-y-3">
                <li>
                  <strong>Download Blueprint:</strong> 
                  <button onClick={downloadBlueprint} className="ml-2 text-emerald-600 hover:underline font-medium">Click here to download</button>
                </li>
                <li>
                  <strong>Import to Make.com:</strong> Go to <a href="https://www.make.com" target="_blank" className="text-emerald-600 underline">Make.com</a>, create a scenario, and click "Import Blueprint" (under the three-dot menu at the bottom).
                </li>
                <li>
                  <strong>Connect LinkedIn:</strong> Click the LinkedIn module in the scenario, click "Add", and authorize your LinkedIn account.
                </li>
                <li>
                  <strong>Copy Webhook:</strong> Click the "Webhooks" module, click "Add", and copy the new Webhook URL.
                </li>
                <li>
                  <strong>Paste Here:</strong> Paste that URL into the settings on this page and click "Save".
                </li>
              </ol>
            </div>
            <button
              onClick={() => setShowMakeModal(false)}
              className="mt-6 w-full py-2 bg-zinc-100 text-zinc-900 rounded-md font-medium hover:bg-zinc-200 transition-colors"
            >
              Got it
            </button>
          </div>
        </div>
      )}
    </main>
  );
}

// ============ subcomponents ============

const inputCls =
  "w-full bg-white border border-zinc-300 rounded-md px-3 py-2 text-sm text-zinc-900 " +
  "placeholder:text-zinc-400 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500";

function tabBtn(active: boolean) {
  return [
    "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
    active
      ? "bg-zinc-900 text-white"
      : "bg-white text-zinc-700 border border-zinc-300 hover:bg-zinc-50",
  ].join(" ");
}

function Field({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-zinc-600 mb-1">{label}</div>
      {children}
    </label>
  );
}

function PromptArea({
  label, value, onChange,
}: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <div className="text-xs text-zinc-600 mb-1">{label}</div>
      <textarea value={value} onChange={e => onChange(e.target.value)} rows={5}
                className={inputCls + " resize-y"} />
    </label>
  );
}

function Spinner({ small = false }: { small?: boolean }) {
  const cls = small ? "w-3 h-3 border" : "w-4 h-4 border-2";
  return (
    <span className={`inline-block ${cls} border-emerald-600/30 border-t-emerald-600 rounded-full animate-spin`} aria-hidden />
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 whitespace-pre-wrap">
      <div className="font-semibold mb-1">Error</div>
      {message}
    </div>
  );
}

function ProgressRow({ p }: { p: ProgressLine }) {
  switch (p.kind) {
    case "status":
      return <div className="text-zinc-700">{p.text}</div>;
    case "folder":
      return (
        <div className="text-zinc-600">
          📂 <span className="text-zinc-800">{p.name}</span>{" "}
          <span className="text-zinc-500">— {p.count} message(s) in window</span>
        </div>
      );
    case "mail":
      return (
        <div className="text-zinc-700">
          ✉️ <span className="text-zinc-500">{p.sender}:</span>{" "}
          <span className="text-zinc-800">{p.subject}</span>
        </div>
      );
    case "decision":
      return (
        <div className={p.kept ? "text-emerald-700" : "text-zinc-400"}>
          {p.kept ? "✓ KEEP" : "✗ DROP"}{" "}
          <span className="text-zinc-500">— {p.sender}:</span>{" "}
          <span>{p.subject}</span>
        </div>
      );
    case "step":
      return (
        <div className="text-blue-700">
          {p.status === "start" ? "▶" : "✓"} {p.name} {p.status === "start" ? "started…" : "done"}
        </div>
      );
  }
}

function ResultTabs({
  result, active, setActive, onCopy,
}: {
  result: CurrentResult;
  active: "digest" | "story" | "linkedin";
  setActive: (t: "digest" | "story" | "linkedin") => void;
  onCopy: (text: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const tabText =
    active === "digest" ? result.digest :
    active === "story"  ? result.story  :
    result.linkedin;

  function handleCopy() {
    if (!tabText) return;
    onCopy(tabText);
    setCopied(true);
    setTimeout(() => setCopied(false), 750);
  }

  return (
    <section className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <div className="flex border-b border-zinc-200">
        {(["digest", "story", "linkedin"] as const).map(t => (
          <button
            key={t}
            onClick={() => { setActive(t); setCopied(false); }}
            className={`px-4 py-2.5 text-sm capitalize ${
              active === t
                ? "text-zinc-900 border-b-2 border-emerald-600 font-medium"
                : "text-zinc-500 hover:text-zinc-800"
            }`}
          >
            {t === "linkedin" ? "LinkedIn post" : t}
          </button>
        ))}
        <div className="ml-auto pr-2 flex items-center">
          <button
            onClick={handleCopy}
            className={`text-xs px-2 py-1 transition-colors ${
              copied ? "text-emerald-600 font-medium" : "text-zinc-500 hover:text-zinc-900"
            }`}
          >
            {copied ? "✓ Copied!" : "Copy"}
          </button>
        </div>
      </div>
      <div className="p-5 text-sm leading-6 text-zinc-800 font-sans">
        <ReactMarkdown
          components={{
            h1: ({ ...props }) => <h1 className="text-xl font-bold mt-6 mb-4 text-zinc-900 border-b border-zinc-100 pb-2" {...props} />,
            h2: ({ ...props }) => <h2 className="text-lg font-bold mt-6 mb-3 text-zinc-900" {...props} />,
            h3: ({ ...props }) => <h3 className="text-base font-bold mt-5 mb-2 text-zinc-900" {...props} />,
            p: ({ ...props }) => <p className="mb-4 last:mb-0" {...props} />,
            ul: ({ ...props }) => <ul className="list-disc pl-5 mb-4 space-y-2" {...props} />,
            ol: ({ ...props }) => <ol className="list-decimal pl-5 mb-4 space-y-2" {...props} />,
            li: ({ ...props }) => <li className="pl-1" {...props} />,
            a: ({ ...props }) => <a className="text-emerald-600 hover:underline font-medium" target="_blank" rel="noopener noreferrer" {...props} />,
            strong: ({ ...props }) => <strong className="font-semibold text-zinc-950" {...props} />,
            hr: () => <hr className="my-6 border-zinc-100" />,
            blockquote: ({ ...props }) => <blockquote className="border-l-4 border-zinc-200 pl-4 italic text-zinc-600 mb-4" {...props} />,
            code: ({ ...props }) => <code className="bg-zinc-100 px-1.5 py-0.5 rounded text-xs font-mono text-zinc-700" {...props} />,
          }}
        >
          {tabText || ""}
        </ReactMarkdown>
      </div>
    </section>
  );
}
