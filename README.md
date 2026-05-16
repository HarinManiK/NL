# Newsletter Digest

Pull recent newsletters from your Gmail, AI-filter them, then generate three text outputs:

1. **Digest** — themed summary of every newsletter in the window
2. **Story** — narrative version of the digest
3. **LinkedIn post** — short post derived from the story

Single-user: no auth, just don't share the URL. Email + IMAP password + lookback hours +
prompts persist in your browser. Past runs persist in Supabase, scoped by the email you
entered.

## Stack

- **Backend** — FastAPI on Render (`backend/`)
- **Frontend** — Next.js on Vercel (`frontend/`)
- **DB** — Supabase Postgres (one `runs` table)
- **LLM** — OpenRouter, model `meta-llama/llama-3.3-70b-instruct:free`

## 0. Prereqs

- A Gmail account with an **App Password** (16 chars). Generate at
  <https://myaccount.google.com/apppasswords>. You need 2-Step Verification on first.
- Accounts on: Supabase, Render, Vercel, OpenRouter.

## 1. Supabase

1. Create a project. Note the **Project URL** and the **service_role key**
   (Settings → API). The service key is secret — never put it in the frontend.
2. Open the SQL Editor and run the contents of [`supabase_schema.sql`](supabase_schema.sql).

## 2. Backend on Render

This repo includes [`render.yaml`](render.yaml) — Render will pick it up automatically.

1. Push the repo to GitHub.
2. Render → **New → Blueprint**, point at the repo. It will detect `render.yaml`
   and create the `newsletter-digest-api` web service.
3. Set these **Environment** variables on the service:
   - `OPENROUTER_API_KEY` — your OpenRouter key
   - `SUPABASE_URL` — `https://xxxx.supabase.co`
   - `SUPABASE_SERVICE_KEY` — the service_role key from step 1
   - `ALLOWED_ORIGINS` — your Vercel URL (e.g. `https://newsletter-digest.vercel.app`).
     Use `*` only while testing.
4. Deploy. After it's up, hit `https://<your-service>.onrender.com/` — should return
   `{"ok": true, ...}`.

> Render free tier sleeps after ~15 min idle. The first request after a sleep takes
> ~30s to wake. Subsequent runs are fast.

## 3. Frontend on Vercel

1. Vercel → **Import Project** → pick the repo.
2. **Root Directory**: `frontend`
3. **Environment variable**:
   - `NEXT_PUBLIC_API_BASE` = `https://<your-service>.onrender.com`
4. Deploy.

After Vercel gives you a URL, go back to Render and set `ALLOWED_ORIGINS` to that URL,
then redeploy the backend so CORS picks it up.

## 4. Use it

Open the Vercel URL.

1. Enter your Gmail address and 16-char app password.
2. Click **Verify connection** — confirms IMAP login works.
3. Set hours-back (default 24). Optionally edit prompts under "Prompts (advanced)".
4. Click **Run**. Spinner runs while the pipeline:
   - fetches mails from the last N hours across all folders
   - asks the LLM (one batched call) which are newsletters
   - aggregates the kept bodies
   - generates digest → story → LinkedIn post
5. Results show in tabs. Past runs appear at the bottom and persist in Supabase.

## Daily auto-posting

After the Make.com LinkedIn webhook is working manually:

1. In Settings, set the lookback window, then enable **Auto-post daily**.
2. Pick the daily post time and timezone.
3. Click **Save Settings**.
4. Configure the Render cron job from `render.yaml` with:
   - `AUTOMATION_TICK_URL` = `https://<your-service>.onrender.com/automation/tick`
   - `CRON_SECRET` = the same value as the backend service, if you set one

The cron job pings the backend every 5 minutes. The tick endpoint checks all enabled
users, runs only users whose local scheduled time has passed, uses each user's saved
lookback window, and records `last_run_at` so each user runs at most once per local day.

Optional: set `CRON_SECRET` on the backend and send it as the `X-Cron-Secret` header
from your scheduler. If `CRON_SECRET` is not set, the tick endpoint is open but still
only runs saved schedules that are due.

## Local development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys
# load env then run:
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE=http://localhost:8000" > .env.local
npm run dev
```

## Notes

- The OpenRouter free model has rate limits. Each run makes **4 LLM calls** (filter,
  digest, story, LinkedIn). The backend retries on 429/5xx with exponential backoff.
- All errors surface verbatim to the UI (red banner). No silent fallbacks.
- Mail bodies > 8000 chars are truncated per-mail; the aggregate is capped at 80k chars
  to fit context.
- "Newsletter" detection is purely the filter prompt — edit it in the UI to taste.
