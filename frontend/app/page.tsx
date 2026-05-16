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
  manualPost,
  listAllowedDomains,
  addAllowedDomain,
  deleteAllowedDomain,
  listSubscribers,
  type Prompts,
  type RunDetail,
  type RunListItem,
  type StreamEvent,
  type AllowedDomain,
  type Subscriber,
  type UsefulLink,
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
    "Aim for ~400-700 words depending on volume. Output plain text/markdown with no preamble. " +
    "When citing links, do not use markdown link syntax; write the raw URL after the sentence. " +
    "Do not create a final 'For more info' section; the system appends that section.",
  story:
    "You are turning the digest below into a single flowing narrative — a 'what happened " +
    "today in this world' story. Write in connected paragraphs, not bullets. Keep it " +
    "factual and grounded; do not invent details. Weave related items together so the " +
    "reader gets the arc of the day across topics. ~300-500 words. Output plain text " +
    "with no preamble. If you include links, write the raw URL instead of markdown links. " +
    "Do not create a final 'For more info' section; the system appends that section.",
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
    "- If you include links, write raw URLs. Do not use markdown link syntax.\n" +
    "- Do not create a final 'For more info' section; the system appends that section.\n" +
    "- Output only the LinkedIn post. No preamble.\n\n" +
    "Style:\n" +
    "Conversational, sharp, professional, founder/investor/operator voice.\n" +
    "Make it detailed enough to feel valuable, but clean enough that someone would actually read it on LinkedIn.",
  newsletter_subject:
    "Write a concise, specific email subject line for the newsletter below. " +
    "Make it useful and professional. Keep it under 80 characters. " +
    "Output only the subject line, no quotes and no preamble.",
  newsletter_html:
    "Turn the digest below into a polished HTML newsletter email. Use simple email-safe HTML only: " +
    "h1, h2, h3, p, ul, ol, li, strong, em, a, br, hr, div, and span. " +
    "Preserve important concrete facts and do not invent details. You may include useful source links near the relevant sections, " +
    "but do not create a final 'For more info' section because the system appends that section. " +
    "Do not include scripts, forms, external stylesheets, images, or an unsubscribe footer. Output only the HTML body.",
};

type View = "main" | "history";

type CurrentResult = {
  run_id: string;
  num_total: number;
  num_kept: number;
  digest: string;
  story: string;
  linkedin: string;
  newsletter_subject?: string;
  newsletter_html?: string;
  useful_links?: UsefulLink[];
};

type ProgressLine =
  | { kind: "status"; text: string }
  | { kind: "folder"; name: string; count: number }
  | { kind: "mail"; subject: string; sender: string }
  | { kind: "links"; count: number }
  | { kind: "decision"; subject: string; sender: string; kept: boolean }
  | { kind: "step"; name: string; status: "start" | "done" };

export default function Home() {
  // ---- persisted state ----
  const [email, setEmail] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [hoursBack, setHoursBack] = useState(24);
  const [prompts, setPrompts] = useState<Prompts>(DEFAULT_PROMPTS);
  const [makeWebhookUrl, setMakeWebhookUrl] = useState("");
  const [automationEnabled, setAutomationEnabled] = useState(false);
  const [postTime, setPostTime] = useState("07:00");
  const [timezone, setTimezone] = useState("UTC");
  const [lastRunAt, setLastRunAt] = useState<string | null>(null);
  const [lastAutomationError, setLastAutomationError] = useState<string | null>(null);
  const [ownerToken, setOwnerToken] = useState("");
  const [storyEnabled, setStoryEnabled] = useState(true);
  const [linkedinEnabled, setLinkedinEnabled] = useState(true);
  const [newsletterEnabled, setNewsletterEnabled] = useState(false);
  const [linkedinAutoPostEnabled, setLinkedinAutoPostEnabled] = useState(false);
  const [linkedinPostTime, setLinkedinPostTime] = useState("07:00");
  const [linkedinTimezone, setLinkedinTimezone] = useState("UTC");
  const [newsletterAutoSendEnabled, setNewsletterAutoSendEnabled] = useState(false);
  const [newsletterSendTime, setNewsletterSendTime] = useState("07:00");
  const [newsletterTimezone, setNewsletterTimezone] = useState("UTC");
  const [newsletterSendingMethod, setNewsletterSendingMethod] = useState<"mailbox" | "ses">("mailbox");
  const [sesSmtpHost, setSesSmtpHost] = useState("");
  const [sesSmtpPort, setSesSmtpPort] = useState(587);
  const [sesSmtpUsername, setSesSmtpUsername] = useState("");
  const [sesSmtpPassword, setSesSmtpPassword] = useState("");
  const [sesVerifiedSenderEmail, setSesVerifiedSenderEmail] = useState("");
  const [sesFromName, setSesFromName] = useState("");
  const [sesReplyToEmail, setSesReplyToEmail] = useState("");
  const [allowedDomains, setAllowedDomains] = useState<AllowedDomain[]>([]);
  const [newDomain, setNewDomain] = useState("");
  const [subscribers, setSubscribers] = useState<Subscriber[]>([]);
  const [subscriberCount, setSubscriberCount] = useState(0);
  const [publicOrigin, setPublicOrigin] = useState("");

  // ---- transient state ----
  const [verifying, setVerifying] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CurrentResult | null>(null);
  const [progress, setProgress] = useState<ProgressLine[]>([]);
  const [activeTab, setActiveTab] = useState<"digest" | "story" | "linkedin" | "newsletter">("digest");
  const [showSettings, setShowSettings] = useState(true);
  const [showPrompts, setShowPrompts] = useState(false);
  const [showMakeModal, setShowMakeModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [posting, setPosting] = useState(false);
  const [postSuccess, setPostSuccess] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  // ---- view + history ----
  const [view, setView] = useState<View>("main");
  const [history, setHistory] = useState<RunListItem[]>([]);
  const [openFolders, setOpenFolders] = useState<Record<string, boolean>>({});
  const [historyDetail, setHistoryDetail] = useState<RunDetail | null>(null);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(false);
  const [historyTab, setHistoryTab] = useState<"digest" | "story" | "linkedin" | "newsletter">("digest");

  const progressBoxRef = useRef<HTMLDivElement | null>(null);
  const lastProgressAtRef = useRef(Date.now());

  function addProgress(line: ProgressLine) {
    lastProgressAtRef.current = Date.now();
    setProgress(p => [...p, line]);
  }

  // ---- hydrate from localStorage ----
  useEffect(() => {
    setEmail(load<string>("email", ""));
    setAppPassword(load<string>("app_password", ""));
    setHoursBack(load<number>("hours_back", 24));
    setMakeWebhookUrl(load<string>("make_webhook_url", ""));
    setAutomationEnabled(load<boolean>("automation_enabled", false));
    setPostTime(load<string>("post_time", "07:00"));
    setTimezone(load<string>("timezone", "UTC"));
    const saved = load<Prompts | null>("prompts", null);
    if (saved) {
      setPrompts({ ...DEFAULT_PROMPTS, ...saved });
    }
    setPublicOrigin(window.location.origin);
    setHydrated(true);

    // Warm up the server (e.g. Render free tier sleep)
    fetch(`${API_BASE}/`).catch(() => {});
  }, []);

  async function loadRemoteSettings() {
    if (!email) return;
    try {
      const s = await getSettings(email);
      if (s.found) {
        if (s.owner_token) setOwnerToken(s.owner_token);
        if (s.app_password) setAppPassword(s.app_password);
        if (s.hours_back) setHoursBack(s.hours_back);
        setMakeWebhookUrl(s.make_webhook_url || "");
        setAutomationEnabled(Boolean(s.automation_enabled));
        setStoryEnabled(s.story_enabled ?? true);
        setLinkedinEnabled(s.linkedin_enabled ?? true);
        setNewsletterEnabled(Boolean(s.newsletter_enabled));
        setLinkedinAutoPostEnabled(Boolean(s.linkedin_auto_post_enabled));
        setLinkedinPostTime(s.linkedin_post_time || "07:00");
        setLinkedinTimezone(s.linkedin_timezone || "UTC");
        setNewsletterAutoSendEnabled(Boolean(s.newsletter_auto_send_enabled));
        setNewsletterSendTime(s.newsletter_send_time || "07:00");
        setNewsletterTimezone(s.newsletter_timezone || "UTC");
        setNewsletterSendingMethod(s.newsletter_sending_method || "mailbox");
        setSesSmtpHost(s.ses_smtp_host || "");
        setSesSmtpPort(s.ses_smtp_port || 587);
        setSesSmtpUsername(s.ses_smtp_username || "");
        setSesSmtpPassword(s.ses_smtp_password || "");
        setSesVerifiedSenderEmail(s.ses_verified_sender_email || "");
        setSesFromName(s.ses_from_name || "");
        setSesReplyToEmail(s.ses_reply_to_email || "");
        setTimezone(s.timezone || "UTC");
        setPostTime(s.post_time || "07:00");
        setLastRunAt(s.last_run_at || null);
        setLastAutomationError(s.last_automation_error || null);
        if (s.prompts) {
          setPrompts({ ...DEFAULT_PROMPTS, ...s.prompts });
        }
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
  useEffect(() => { if (hydrated) save("make_webhook_url", makeWebhookUrl); }, [makeWebhookUrl, hydrated]);
  useEffect(() => { if (hydrated) save("automation_enabled", automationEnabled); }, [automationEnabled, hydrated]);
  useEffect(() => { if (hydrated) save("post_time", postTime); }, [postTime, hydrated]);
  useEffect(() => { if (hydrated) save("timezone", timezone); }, [timezone, hydrated]);
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

  async function refreshNewsletterAdmin() {
    if (!email) {
      setAllowedDomains([]);
      setSubscribers([]);
      setSubscriberCount(0);
      return;
    }
    try {
      const [domains, subs] = await Promise.all([
        listAllowedDomains(email),
        listSubscribers(email),
      ]);
      setAllowedDomains(domains);
      setSubscribers(subs.subscribers || []);
      setSubscriberCount(subs.count || 0);
    } catch (e: any) {
      console.warn("newsletter admin load failed:", e.message);
    }
  }

  useEffect(() => {
    if (hydrated && email) refreshNewsletterAdmin();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email, hydrated]);

  // auto-scroll progress box
  useEffect(() => {
    if (progressBoxRef.current) {
      progressBoxRef.current.scrollTop = progressBoxRef.current.scrollHeight;
    }
  }, [progress]);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      if (Date.now() - lastProgressAtRef.current > 12000) {
        addProgress({ kind: "status", text: "Still working... waiting for the next live update." });
      }
    }, 4000);
    return () => window.clearInterval(id);
  }, [running]);

  useEffect(() => {
    if (!showMakeModal) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setShowMakeModal(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showMakeModal]);

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
    if (linkedinAutoPostEnabled && !makeWebhookUrl.trim()) {
      setError("Add your LinkedIn Make.com webhook URL before enabling auto-post daily.");
      return;
    }
    setSaving(true);
    try {
      const saved = await saveSettings({
        email,
        app_password: appPassword,
        hours_back: hoursBack,
        make_webhook_url: makeWebhookUrl,
        automation_enabled: linkedinAutoPostEnabled || newsletterAutoSendEnabled,
        timezone,
        post_time: postTime,
        prompts,
        imap_server: "imap.gmail.com",
        imap_port: 993,
        story_enabled: storyEnabled,
        linkedin_enabled: linkedinEnabled,
        newsletter_enabled: newsletterEnabled,
        linkedin_auto_post_enabled: linkedinAutoPostEnabled,
        linkedin_post_time: linkedinPostTime,
        linkedin_timezone: linkedinTimezone,
        newsletter_auto_send_enabled: newsletterAutoSendEnabled,
        newsletter_send_time: newsletterSendTime,
        newsletter_timezone: newsletterTimezone,
        newsletter_sending_method: newsletterSendingMethod,
        ses_smtp_host: sesSmtpHost.trim() || undefined,
        ses_smtp_port: sesSmtpPort,
        ses_smtp_username: sesSmtpUsername.trim() || undefined,
        ses_smtp_password: sesSmtpPassword.trim() || undefined,
        ses_verified_sender_email: sesVerifiedSenderEmail.trim() || undefined,
        ses_from_name: sesFromName.trim() || undefined,
        ses_reply_to_email: sesReplyToEmail.trim() || undefined,
      });
      if (saved.owner_token) setOwnerToken(saved.owner_token);
      if (saved.automation_run_reset) {
        setLastRunAt(null);
      }
      setLastAutomationError(null);
      refreshNewsletterAdmin();
      setVerifyMsg((linkedinAutoPostEnabled || newsletterAutoSendEnabled) ? "✓ Settings saved. Automation is ON." : "✓ Settings saved. Automation is OFF.");
    } catch (e: any) {
      setError(`Save failed: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  async function onManualPost(content?: string) {
    const webhook = makeWebhookUrl.trim();
    if (!webhook) {
      setError("Add your LinkedIn Make.com webhook URL in Settings first.");
      setShowSettings(true);
      return;
    }
    const textToPost = content || result?.linkedin;
    if (!textToPost?.trim()) {
      setError("There is no LinkedIn post text to send yet.");
      return;
    }

    setPosting(true);
    setPostSuccess(false);
    setError(null);
    try {
      await manualPost(webhook, textToPost);
      setPostSuccess(true);
      setTimeout(() => setPostSuccess(false), 3000);
    } catch (e: any) {
      const msg = e.message || "Post failed";
      setError(`LinkedIn post failed: ${msg}`);
    } finally {
      setPosting(false);
    }
  }

  async function onAddDomain() {
    const domain = newDomain.trim();
    if (!email || !appPassword || !domain) return;
    setError(null);
    try {
      await addAllowedDomain({ email, app_password: appPassword, domain });
      setNewDomain("");
      refreshNewsletterAdmin();
    } catch (e: any) {
      setError(`Could not add domain: ${e.message}`);
    }
  }

  async function onDeleteDomain(domain: string) {
    if (!email || !appPassword) return;
    setError(null);
    try {
      await deleteAllowedDomain({ email, app_password: appPassword, domain });
      refreshNewsletterAdmin();
    } catch (e: any) {
      setError(`Could not remove domain: ${e.message}`);
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
    if (!prompts.filter || !prompts.digest || (storyEnabled && !prompts.story) || (linkedinEnabled && !prompts.linkedin) || (newsletterEnabled && (!prompts.newsletter_subject || !prompts.newsletter_html))) {
      setError("Enabled outputs must have prompts filled. Click 'Reset to defaults' if you cleared them.");
      return;
    }
    lastProgressAtRef.current = Date.now();
    setProgress([{ kind: "status", text: "Preparing run..." }]);
    setRunning(true);
    setShowSettings(false);
    setView("main");

    let final: CurrentResult | null = null;

    try {
      await streamRun(
        {
          email,
          app_password: appPassword,
          hours_back: hoursBack,
          prompts,
          story_enabled: storyEnabled,
          linkedin_enabled: linkedinEnabled,
          newsletter_enabled: newsletterEnabled,
        },
        (evt: StreamEvent) => {
          switch (evt.type) {
            case "status":
              addProgress({ kind: "status", text: evt.message });
              break;
            case "folder":
              addProgress({ kind: "folder", name: evt.name, count: evt.count });
              break;
            case "mail":
              addProgress({ kind: "mail", subject: evt.subject, sender: evt.sender });
              break;
            case "fetch_done":
              addProgress({ kind: "status", text: `Fetched ${evt.total} mail(s) total. Starting filter…` });
              break;
            case "filter_start":
              addProgress({ kind: "step", name: "filter", status: "start" });
              break;
            case "links_done":
              addProgress({ kind: "links", count: evt.count });
              setResult(prev => prev ? { ...prev, useful_links: evt.links } : prev);
              break;
            case "decision":
              addProgress({ kind: "decision", subject: evt.subject, sender: evt.sender, kept: evt.kept });
              break;
            case "filter_done":
              addProgress({ kind: "status", text: `Filter complete: kept ${evt.kept} / ${evt.total}.` });
              setResult({
                run_id: evt.run_id,
                num_total: evt.total,
                num_kept: evt.kept,
                digest: "",
                story: "",
                linkedin: "",
                newsletter_subject: "",
                newsletter_html: "",
              });
              break;
            case "step":
              addProgress({ kind: "step", name: evt.name, status: evt.status });
              if (evt.status === "done" && evt.text) {
                setResult(prev => prev ? { ...prev, [evt.name]: evt.text } : null);
                setActiveTab(evt.name as any);
              }
              break;
            case "newsletter_done":
              setResult(prev => prev ? { ...prev, newsletter_subject: evt.subject, newsletter_html: evt.html } : null);
              setActiveTab("newsletter");
              break;
            case "complete":
              final = {
                run_id: evt.run_id,
                num_total: evt.num_total,
                num_kept: evt.num_kept,
                digest: evt.digest,
                story: evt.story,
                linkedin: evt.linkedin,
                newsletter_subject: evt.newsletter_subject || "",
                newsletter_html: evt.newsletter_html || "",
                useful_links: evt.useful_links || [],
              };
              addProgress({ kind: "status", text: `Done in ${evt.elapsed_seconds}s.` });
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

                <div className="pt-3 border-t border-zinc-100 space-y-2">
                  <div className="text-xs font-medium text-zinc-600">Outputs</div>
                  <label className="flex items-center justify-between gap-3 text-sm">
                    <span>Digest (mandatory)</span>
                    <input type="checkbox" checked disabled className="h-4 w-4 accent-emerald-600" />
                  </label>
                  <label className="flex items-center justify-between gap-3 text-sm">
                    <span>Generate story</span>
                    <input type="checkbox" checked={storyEnabled} onChange={e => setStoryEnabled(e.target.checked)}
                           className="h-4 w-4 accent-emerald-600" />
                  </label>
                  <label className="flex items-center justify-between gap-3 text-sm">
                    <span>Generate LinkedIn post</span>
                    <input type="checkbox" checked={linkedinEnabled} onChange={e => setLinkedinEnabled(e.target.checked)}
                           className="h-4 w-4 accent-emerald-600" />
                  </label>
                  <label className="flex items-center justify-between gap-3 text-sm">
                    <span>Generate newsletter mail</span>
                    <input type="checkbox" checked={newsletterEnabled} onChange={e => setNewsletterEnabled(e.target.checked)}
                           className="h-4 w-4 accent-emerald-600" />
                  </label>
                </div>

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
                      <PromptArea label="Newsletter subject prompt"
                                  value={prompts.newsletter_subject}
                                  onChange={v => setPrompts({ ...prompts, newsletter_subject: v })} />
                      <PromptArea label="Newsletter HTML prompt"
                                  value={prompts.newsletter_html}
                                  onChange={v => setPrompts({ ...prompts, newsletter_html: v })} />
                      <button onClick={resetPrompts}
                              className="text-xs text-zinc-500 hover:text-zinc-900 underline">
                        Reset prompts to defaults
                      </button>
                    </div>
                  )}
                </div>

                <div className="pt-4 border-t border-zinc-100 space-y-4">
                  <Field label={
                    <div className="flex justify-between gap-3">
                      <span>LinkedIn Make.com webhook URL</span>
                      <button type="button" onClick={() => setShowMakeModal(true)}
                              className="text-emerald-600 hover:underline">
                        Setup guide
                      </button>
                    </div>
                  }>
                    <input type="url" value={makeWebhookUrl}
                           onChange={e => setMakeWebhookUrl(e.target.value)}
                           placeholder="https://hook.make.com/..." className={inputCls} />
                  </Field>

                  <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-3 space-y-3">
                    <label className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-zinc-800">Auto-post LinkedIn</span>
                      <input type="checkbox" checked={linkedinAutoPostEnabled}
                             onChange={e => setLinkedinAutoPostEnabled(e.target.checked)}
                             className="h-4 w-4 accent-emerald-600" />
                    </label>
                    {linkedinAutoPostEnabled && (
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <Field label="LinkedIn post time">
                          <input type="time" value={linkedinPostTime} onChange={e => setLinkedinPostTime(e.target.value)}
                                 className={inputCls} />
                        </Field>
                        <Field label="LinkedIn timezone">
                          <TimezoneSelect value={linkedinTimezone} onChange={setLinkedinTimezone} />
                        </Field>
                      </div>
                    )}
                  </div>

                  <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-3 space-y-3">
                    <label className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-zinc-800">Auto-send newsletter emails</span>
                      <input type="checkbox" checked={newsletterAutoSendEnabled}
                             onChange={e => setNewsletterAutoSendEnabled(e.target.checked)}
                             className="h-4 w-4 accent-emerald-600" />
                    </label>
                    {newsletterAutoSendEnabled && (
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <Field label="Newsletter send time">
                          <input type="time" value={newsletterSendTime} onChange={e => setNewsletterSendTime(e.target.value)}
                                 className={inputCls} />
                        </Field>
                        <Field label="Newsletter timezone">
                          <TimezoneSelect value={newsletterTimezone} onChange={setNewsletterTimezone} />
                        </Field>
                        <Field label="Sending method">
                          <select value={newsletterSendingMethod} onChange={e => setNewsletterSendingMethod(e.target.value as "mailbox" | "ses")} className={inputCls}>
                            <option value="mailbox">Connected mailbox</option>
                            <option value="ses">Amazon SES SMTP</option>
                          </select>
                        </Field>
                        {newsletterSendingMethod === "ses" && (
                          <>
                            <Field label="SES SMTP host">
                              <input value={sesSmtpHost} onChange={e => setSesSmtpHost(e.target.value)}
                                     placeholder="email-smtp.us-east-1.amazonaws.com" className={inputCls} />
                            </Field>
                            <Field label="SES SMTP port">
                              <input type="number" value={sesSmtpPort} onChange={e => setSesSmtpPort(Number(e.target.value) || 587)}
                                     className={inputCls} />
                            </Field>
                            <Field label="SES SMTP username">
                              <input value={sesSmtpUsername} onChange={e => setSesSmtpUsername(e.target.value)}
                                     className={inputCls} />
                            </Field>
                            <Field label="SES SMTP password">
                              <input type="password" value={sesSmtpPassword} onChange={e => setSesSmtpPassword(e.target.value)}
                                     className={inputCls} />
                            </Field>
                            <Field label="Verified sender email">
                              <input type="email" value={sesVerifiedSenderEmail} onChange={e => setSesVerifiedSenderEmail(e.target.value)}
                                     placeholder="founder@company.com" className={inputCls} />
                            </Field>
                            <Field label="From name">
                              <input value={sesFromName} onChange={e => setSesFromName(e.target.value)}
                                     placeholder="Company Newsletter" className={inputCls} />
                            </Field>
                            <Field label="Reply-to email">
                              <input type="email" value={sesReplyToEmail} onChange={e => setSesReplyToEmail(e.target.value)}
                                     placeholder="founder@company.com" className={inputCls} />
                            </Field>
                          </>
                        )}
                      </div>
                    )}
                  </div>

                  {newsletterEnabled && (
                    <div className="rounded-md border border-zinc-200 bg-white px-3 py-3 space-y-3">
                      <div>
                        <div className="text-sm font-medium text-zinc-800">Subscribe widget</div>
                        <div className="text-xs text-zinc-500 mt-1">Owner token: {ownerToken || "Save settings to generate one."}</div>
                      </div>
                      <div>
                        <div className="text-xs font-medium text-zinc-600 mb-1">Shareable subscribe link</div>
                        <CodeBox value={`${publicOrigin || "https://your-site.com"}/subscribe/${ownerToken || "OWNER_PUBLIC_TOKEN"}`} />
                      </div>
                      <CodeBox value={`<div data-nl-owner="${ownerToken || "OWNER_PUBLIC_TOKEN"}"></div>\n<script src="${API_BASE}/embed/subscribe.js"></script>`} />
                      <CodeBox value={`<div\n  data-nl-owner="${ownerToken || "OWNER_PUBLIC_TOKEN"}"\n  data-nl-email="{{ logged_in_user.email }}">\n</div>\n<script src="${API_BASE}/embed/subscribe.js"></script>`} />
                      <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2">
                        <input value={newDomain} onChange={e => setNewDomain(e.target.value)}
                               placeholder="allowed-domain.com" className={inputCls} />
                        <button type="button" onClick={onAddDomain}
                                className="px-3 py-2 rounded-md border border-zinc-300 bg-white hover:bg-zinc-50 text-sm">
                          Add domain
                        </button>
                      </div>
                      <div className="space-y-1">
                        {allowedDomains.length === 0 ? (
                          <div className="text-xs text-zinc-500">No allowed domains yet.</div>
                        ) : allowedDomains.map(d => (
                          <div key={d.id || d.domain} className="flex items-center justify-between text-sm border border-zinc-100 rounded-md px-2 py-1">
                            <span>{d.domain}</span>
                            <button type="button" onClick={() => onDeleteDomain(d.domain)}
                                    className="text-xs text-rose-600 hover:underline">Remove</button>
                          </div>
                        ))}
                      </div>
                      <div className="text-xs text-zinc-600">
                        Subscribers: {subscriberCount}
                      </div>
                      {subscribers.length > 0 && (
                        <div className="max-h-40 overflow-auto border border-zinc-100 rounded-md">
                          {subscribers.map(s => (
                            <div key={s.id} className="flex items-center justify-between gap-3 px-2 py-1 text-xs border-b border-zinc-50 last:border-0">
                              <span className="text-zinc-800">{s.subscriber_email}</span>
                              <span className="text-zinc-500">{s.status}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {lastAutomationError && (
                    <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                      Last automation error: {lastAutomationError}
                    </div>
                  )}

                  <button
                    onClick={onSaveSettings}
                    disabled={saving}
                    className="w-full py-2 bg-zinc-900 text-white rounded-md text-sm font-medium hover:bg-zinc-800 transition-colors disabled:opacity-50"
                  >
                    {saving ? "Saving..." : "Save Settings"}
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
            <ResultTabs 
              result={result} 
              active={activeTab} 
              setActive={setActiveTab} 
              onCopy={copy}
              onPost={(txt) => onManualPost(txt)}
              posting={posting}
              postSuccess={postSuccess}
            />
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
                  newsletter_subject: historyDetail.newsletter_subject || "",
                  newsletter_html: historyDetail.newsletter_html || "",
                  useful_links: historyDetail.useful_links || [],
                }}
                active={historyTab}
                setActive={setHistoryTab}
                onCopy={copy}
                onPost={(txt) => onManualPost(txt)}
                posting={posting}
                postSuccess={postSuccess}
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
          <div className="relative bg-white rounded-xl max-w-2xl w-full max-h-[90vh] overflow-auto p-6 shadow-2xl">
            <button
              type="button"
              onClick={() => setShowMakeModal(false)}
              aria-label="Close setup guide"
              className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-md text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900"
            >
              ×
            </button>
            <h2 className="text-xl font-bold text-zinc-900 mb-2 pr-10">Setup LinkedIn Posting</h2>
            <div className="space-y-5 text-sm text-zinc-700">
              <ol className="list-decimal pl-5 space-y-4">
                <li>
                  <button type="button" onClick={downloadBlueprint}
                          className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800">
                    Download the blueprint
                  </button>
                </li>
                <li>
                  Go to{" "}
                  <a href="https://www.make.com" target="_blank" rel="noopener noreferrer"
                     className="text-emerald-600 underline">
                    Make.com
                  </a>{" "}
                  and sign up.
                </li>
              </ol>

              <p className="font-medium text-zinc-900">Then follow these 4 steps:</p>

              <section>
                <h3 className="font-semibold text-zinc-900 mb-2">Step 1</h3>
                <ol className="list-decimal pl-5 space-y-2">
                  <li>Click on Create Scenario.</li>
                  <li>At the top right 3 dots menu, click on Import Blueprint and select the downloaded blueprint.</li>
                </ol>
              </section>

              <section>
                <h3 className="font-semibold text-zinc-900 mb-2">Step 2</h3>
                <ol className="list-decimal pl-5 space-y-2" start={3}>
                  <li>Click on the red webhook circle, click on Add, give it a name, and click Save.</li>
                  <li>Click on Copy address to clipboard, hit Save, and paste that address in the website.</li>
                </ol>
              </section>

              <section>
                <h3 className="font-semibold text-zinc-900 mb-2">Step 3</h3>
                <ol className="list-decimal pl-5 space-y-2" start={5}>
                  <li>
                    Click on the blue LinkedIn circle and click on Create a connection.
                    Connection type = LinkedIn, connection name = any of your choice, then connect your LinkedIn account.
                  </li>
                  <li>In the company field, choose your company.</li>
                </ol>
              </section>

              <section>
                <h3 className="font-semibold text-zinc-900 mb-2">Step 4</h3>
                <ol className="list-decimal pl-5 space-y-2" start={7}>
                  <li>
                    At the bottom, beside Run once, change the default value Every 15mins to Immediately.
                    For max runs per minute, choose how many as you want. Typically, 1 should be enough.
                    Then click Activate Scenario.
                  </li>
                </ol>
              </section>

              <p className="font-medium text-zinc-900">That's all.</p>
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

function TimezoneSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)} className={inputCls}>
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
  );
}

function CodeBox({ value }: { value: string }) {
  return (
    <pre className="whitespace-pre-wrap break-all rounded-md bg-zinc-950 px-3 py-2 text-xs text-zinc-50">
      {value}
    </pre>
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
    case "links":
      return <div className="text-zinc-700">Found {p.count} useful link(s) in kept newsletters.</div>;
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
  result, active, setActive, onCopy, onPost, posting, postSuccess,
}: {
  result: CurrentResult;
  active: "digest" | "story" | "linkedin" | "newsletter";
  setActive: (t: "digest" | "story" | "linkedin" | "newsletter") => void;
  onCopy: (text: string) => void;
  onPost?: (text: string) => void;
  posting?: boolean;
  postSuccess?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const tabText =
    active === "digest" ? result.digest :
    active === "story"  ? result.story  :
    active === "newsletter" ? `${result.newsletter_subject || ""}\n\n${result.newsletter_html || ""}` :
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
        {(["digest", "story", "linkedin", "newsletter"] as const).map(t => (
          <button
            key={t}
            onClick={() => { setActive(t); setCopied(false); }}
            className={`px-4 py-2.5 text-sm capitalize ${
              active === t
                ? "text-zinc-900 border-b-2 border-emerald-600 font-medium"
                : "text-zinc-500 hover:text-zinc-800"
            }`}
          >
            {t === "linkedin" ? "LinkedIn post" : t === "newsletter" ? "Newsletter mail" : t}
          </button>
        ))}
        <div className="ml-auto pr-2 flex items-center">
          <button
            onClick={handleCopy}
            className={`text-xs px-2 py-1 transition-colors ${
              copied ? "text-emerald-600 font-medium" : "text-zinc-500 hover:text-zinc-900"
            }`}
          >
            {copied ? "✓ Copied!" : active === "newsletter" ? "Copy HTML" : "Copy"}
          </button>
        </div>
      </div>
      {active === "newsletter" ? (
        <div className="p-5 text-sm leading-6 text-zinc-800 font-sans">
          <div className="mb-4 rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2">
            <div className="text-xs text-zinc-500 mb-1">Subject</div>
            <div className="font-medium text-zinc-900">{result.newsletter_subject || ""}</div>
          </div>
          <div className="rounded-md border border-zinc-200 bg-white p-4"
               dangerouslySetInnerHTML={{ __html: result.newsletter_html || "" }} />
        </div>
      ) : (
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
      )}

      {active === "linkedin" && onPost && (
        <div className="px-5 pb-6 pt-2 border-t border-zinc-50 flex flex-col items-center gap-3 bg-zinc-50/50 rounded-b-xl">
          <button
            onClick={() => onPost(tabText)}
            disabled={posting || !tabText}
            className={`w-full max-w-sm py-2.5 rounded-lg font-bold text-sm transition-all shadow-sm flex items-center justify-center gap-2 ${
              postSuccess 
                ? "bg-emerald-100 text-emerald-700 border border-emerald-200" 
                : "bg-[#0077B5] text-white hover:bg-[#006097] disabled:opacity-60"
            }`}
          >
            {posting ? (
              <>
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Posting...
              </>
            ) : postSuccess ? (
              "✓ Posted to LinkedIn"
            ) : (
              "Post to LinkedIn Now"
            )}
          </button>
          {postSuccess && <p className="text-[10px] text-emerald-600 font-medium">Webhook accepted the post.</p>}
          {!postSuccess && !posting && (
            <p className="text-[10px] text-zinc-500 text-center px-4">
              Clicking this will send today's LinkedIn post to your configured Make.com webhook.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
