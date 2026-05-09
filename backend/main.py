"""FastAPI backend for the Newsletter Digest pipeline.

Endpoints:
  POST /verify        — check IMAP credentials
  POST /run/stream    — Server-Sent Events stream of pipeline progress + final result
  GET  /runs          — list past runs for an email
  GET  /runs/{id}     — fetch a specific run

AIMLAPI key is read from env (AIMLAPI_API_KEY). All other inputs come
from the request body.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Iterator, List, Optional
import pytz
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field

from db import (
    DBError, get_run, insert_run, list_runs,
    get_settings, upsert_settings, list_users_for_tick, update_last_run
)
from imap_fetch import MailRecord, fetch_recent_mails_stream, verify_imap, fetch_recent_mails
from llm import LLMError, chat, filter_newsletters

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("nl")

app = FastAPI(title="Newsletter Digest", version="1.1")

_origins_env = os.environ.get("ALLOWED_ORIGINS", "*").strip()
_origins = [o.strip() for o in _origins_env.split(",")] if _origins_env != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Default prompts (frontend has its own copies, but kept here too) ----------

DEFAULT_FILTER_PROMPT = (
    "You are deciding which emails are 'newsletters' worth aggregating into a daily digest. "
    "KEEP an email if it is: a regular newsletter, a curated digest, an industry roundup, "
    "a Substack/Beehiiv/Medium-style publication, an editorial brief, a 'this week in X' "
    "update, or a long-form informational broadcast email. "
    "DROP: personal mail, transactional notifications (receipts, password resets, shipping, "
    "calendar invites, OTPs, social-network alerts), marketing promos that are mostly a "
    "coupon or discount code, automated system mail, and anything that looks like a 1:1 "
    "conversation. When unsure, prefer to KEEP."
)
DEFAULT_DIGEST_PROMPT = (
    "You are summarising a batch of newsletters into one cohesive digest for a busy reader "
    "who does not want to read the originals. Write a clean, scannable digest grouped by "
    "theme (not by source). Keep every concrete fact: company names, product names, numbers, "
    "dates, names of people, and links to the original story when present. Drop fluff, "
    "intros, signoffs, and self-promotion. Use short bullet points under bold theme "
    "headings. Aim for ~400-700 words depending on volume. Output plain text/markdown — "
    "no preamble."
)
DEFAULT_STORY_PROMPT = (
    "You are turning the digest below into a single flowing narrative — a 'what happened "
    "today in this world' story. Write in connected paragraphs, not bullets. Keep it "
    "factual and grounded; do not invent details. Weave related items together so the "
    "reader gets the arc of the day across topics. ~300-500 words. Output plain text "
    "with no preamble."
)
DEFAULT_LINKEDIN_PROMPT = (
    "Turn the digest below into an engaging LinkedIn post.\n\n"
    "Goal:\n"
    "Create a LinkedIn-ready post that captures the full value of the digest without losing important details.\n\n"
    "Hard rules:\n"
    "- Do NOT reduce this to only 2–3 themes.\n"
    "- Preserve all important concrete facts: company names, product names, people, numbers, prices, funding amounts, dates, percentages, APYs, viewership numbers, policy changes, and major claims.\n"
    "- You may combine related points, but do not drop major items.\n"
    "- Write it as a cohesive post, not a source-by-source summary.\n"
    "- Use theme-based grouping if needed.\n"
    "- Add light interpretation, but do not invent facts.\n"
    "- Avoid generic lines like “innovation is accelerating” or “the future is here.”\n"
    "- Start with a specific hook based on the strongest pattern in the digest.\n"
    "- Use short paragraphs and clean bullets.\n"
    "- End with a useful takeaway or discussion question.\n"
    "- Length: 350–600 words depending on digest size.\n"
    "- No emojis unless genuinely useful.\n"
    "- Add 3–5 relevant hashtags on the final line.\n"
    "- Output only the LinkedIn post. No preamble.\n\n"
    "Style:\n"
    "Conversational, sharp, professional, founder/investor/operator voice.\n"
    "Make it detailed enough to feel valuable, but clean enough that someone would actually read it on LinkedIn."
)


class Prompts(BaseModel):
    filter: str = DEFAULT_FILTER_PROMPT
    digest: str = DEFAULT_DIGEST_PROMPT
    story: str = DEFAULT_STORY_PROMPT
    linkedin: str = DEFAULT_LINKEDIN_PROMPT


class VerifyReq(BaseModel):
    email: EmailStr
    app_password: str = Field(min_length=1)
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993


class RunReq(BaseModel):
    email: EmailStr
    app_password: str = Field(min_length=1)
    hours_back: int = Field(ge=1, le=720)
    prompts: Prompts = Prompts()
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993


class SettingsReq(BaseModel):
    email: EmailStr
    app_password: str
    timezone: str = "UTC"
    post_time: str = "07:00"
    make_webhook_url: Optional[str] = None
    automation_enabled: bool = False
    prompts: Prompts = Prompts()
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993


def _aggregate_bodies(records: List[MailRecord]) -> str:
    chunks = []
    for r in records:
        body = r.body.strip()
        if len(body) > 8000:
            body = body[:8000] + "\n\n[…truncated…]"
        chunks.append(f"=====\n{r.header_block()}\n{body}\n")
    return "\n".join(chunks)


def _fire_make_webhook(url: str, data: dict):
    """Fire the generated data to a Make.com webhook."""
    try:
        r = requests.post(url, json=data, timeout=10)
        log.info(f"Make.com webhook fired: {r.status_code}")
    except Exception as e:
        log.error(f"Failed to fire Make.com webhook: {e}")


def _sse(event: dict) -> str:
    """Encode one Server-Sent Events frame."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/")
def root():
    return {"ok": True, "service": "newsletter-digest", "version": "1.1"}


@app.get("/defaults")
def defaults():
    return Prompts().model_dump()


@app.post("/verify")
def verify(req: VerifyReq):
    try:
        verify_imap(req.email, req.app_password, req.imap_server, req.imap_port)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("verify failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")
    return {"ok": True}


@app.post("/run/stream")
def run_stream(req: RunReq):
    """Stream pipeline progress over Server-Sent Events.

    Event types yielded as JSON in `data:` frames:
      {type: 'status',  message: str}
      {type: 'folder',  name: str, count: int}
      {type: 'mail',    subject, sender, date, folder}
      {type: 'fetch_done', total: int}
      {type: 'filter_start'}
      {type: 'decision', subject, sender, kept: bool}
      {type: 'filter_done', kept: int, total: int}
      {type: 'step',    name: 'digest'|'story'|'linkedin', status: 'start'|'done', text?: str}
      {type: 'complete', run_id, num_total, num_kept, digest, story, linkedin}
      {type: 'error',   message: str}
    """
    def gen() -> Iterator[str]:
        run_id = str(uuid.uuid4())
        started = time.time()
        log.info(f"stream-run {run_id} email={req.email} hours={req.hours_back}")

        try:
            # 1) Fetch
            mails: List[MailRecord] = []
            for evt_type, payload in fetch_recent_mails_stream(
                req.email, req.app_password, req.hours_back,
                req.imap_server, req.imap_port,
            ):
                if evt_type == "status":
                    yield _sse({"type": "status", "message": payload})
                elif evt_type == "folder":
                    yield _sse({"type": "folder", "name": payload["name"],
                                "count": payload["count"]})
                elif evt_type == "mail":
                    rec: MailRecord = payload
                    mails.append(rec)
                    yield _sse({"type": "mail",
                                "subject": rec.subject,
                                "sender": rec.sender_name,
                                "date": rec.date.astimezone().isoformat(),
                                "folder": rec.folder})
                elif evt_type == "done":
                    pass

            yield _sse({"type": "fetch_done", "total": len(mails)})

            if not mails:
                yield _sse({"type": "error",
                            "message": f"No mails found in the last {req.hours_back} hours. "
                                       f"Try increasing the lookback window."})
                return

            # 2) Filter (one batched LLM call, then emit per-mail decisions)
            yield _sse({"type": "filter_start"})
            try:
                keep_idx = filter_newsletters(
                    req.prompts.filter,
                    [{"subject": m.subject,
                      "sender": f"{m.sender_name} <{m.sender_email}>",
                      "preview": m.body[:1500]} for m in mails],
                )
            except LLMError as e:
                yield _sse({"type": "error", "message": f"Filter step failed: {e}"})
                return

            keep_set = set(keep_idx)
            for i, m in enumerate(mails):
                yield _sse({"type": "decision",
                            "subject": m.subject,
                            "sender": m.sender_name,
                            "kept": i in keep_set})

            kept = [mails[i] for i in keep_idx]
            yield _sse({"type": "filter_done", "kept": len(kept), "total": len(mails), "run_id": run_id})

            if not kept:
                yield _sse({"type": "error",
                            "message": f"Filter kept 0 of {len(mails)} mails. "
                                       f"Try loosening the filter prompt or extending the "
                                       f"lookback window."})
                return

            # 3) Aggregate
            aggregate = _aggregate_bodies(kept)
            if len(aggregate) > 80000:
                aggregate = aggregate[:80000] + "\n\n[…aggregate truncated to fit context…]"

            # 4) Digest
            yield _sse({"type": "step", "name": "digest", "status": "start"})
            try:
                digest = chat(req.prompts.digest, aggregate, max_tokens=8192, temperature=0.4)
            except LLMError as e:
                yield _sse({"type": "error", "message": f"Digest step failed: {e}"})
                return
            yield _sse({"type": "step", "name": "digest", "status": "done", "text": digest})

            # 5 & 6) Story and LinkedIn in parallel
            yield _sse({"type": "step", "name": "story", "status": "start"})
            yield _sse({"type": "step", "name": "linkedin", "status": "start"})

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    f_story = executor.submit(
                        chat, req.prompts.story, digest, max_tokens=8192, temperature=0.6
                    )
                    f_linkedin = executor.submit(
                        chat, req.prompts.linkedin, digest, max_tokens=8192, temperature=0.7
                    )

                    # Wait for results (result() will re-raise exceptions from the thread)
                    story = f_story.result()
                    linkedin = f_linkedin.result()

            except LLMError as e:
                yield _sse({"type": "error", "message": f"Parallel generation failed: {e}"})
                return
            except Exception as e:
                yield _sse({"type": "error", "message": f"Unexpected error in parallel step: {e}"})
                return

            yield _sse({"type": "step", "name": "story", "status": "done", "text": story})
            yield _sse({"type": "step", "name": "linkedin", "status": "done", "text": linkedin})

            elapsed = round(time.time() - started, 1)

            # 7) Persist
            try:
                insert_run({
                    "id": run_id,
                    "email": req.email,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "hours_back": req.hours_back,
                    "num_total": len(mails),
                    "num_kept": len(kept),
                    "digest": digest,
                    "story": story,
                    "linkedin": linkedin,
                    "filter_prompt": req.prompts.filter,
                    "digest_prompt": req.prompts.digest,
                    "story_prompt": req.prompts.story,
                    "linkedin_prompt": req.prompts.linkedin,
                    "elapsed_seconds": elapsed,
                })
            except DBError as e:
                # Save failed but generation succeeded — surface it but still send results.
                yield _sse({"type": "status",
                            "message": f"⚠ Saving to Supabase failed: {e}. Results below "
                                       f"are still valid for this session."})

            yield _sse({"type": "complete",
                        "run_id": run_id,
                        "num_total": len(mails),
                        "num_kept": len(kept),
                        "digest": digest,
                        "story": story,
                        "linkedin": linkedin,
                        "elapsed_seconds": elapsed})
        except RuntimeError as e:
            yield _sse({"type": "error", "message": str(e)})
        except Exception as e:
            log.exception("stream run crashed")
            yield _sse({"type": "error",
                        "message": f"Unexpected error: {e}\n{traceback.format_exc()[:1500]}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables proxy buffering on common reverse proxies
            "Connection": "keep-alive",
        },
    )


@app.get("/runs")
def runs(email: EmailStr = Query(...)):
    try:
        return list_runs(email)
    except DBError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/runs/{run_id}")
def run_detail(run_id: str, email: EmailStr = Query(...)):
    try:
        row = get_run(run_id, email)
    except DBError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not row:
        raise HTTPException(status_code=404, detail="Run not found for this email.")
    return row


# ---------- Settings & Automation ----------

@app.get("/settings")
def get_user_settings(email: EmailStr = Query(...)):
    try:
        s = get_settings(email)
        if not s:
            return {"email": email, "found": False}
        return {**s, "found": True}
    except DBError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/settings")
def save_user_settings(req: SettingsReq):
    try:
        # Verify password before saving
        verify_imap(req.email, req.app_password, req.imap_server, req.imap_port)
        
        row = {
            "email": req.email,
            "app_password": req.app_password,
            "timezone": req.timezone,
            "post_time": req.post_time,
            "make_webhook_url": req.make_webhook_url,
            "automation_enabled": req.automation_enabled,
            "filter_prompt": req.prompts.filter,
            "digest_prompt": req.prompts.digest,
            "story_prompt": req.prompts.story,
            "linkedin_prompt": req.prompts.linkedin,
            "imap_server": req.imap_server,
            "imap_port": req.imap_port,
        }
        upsert_settings(row)
        return {"ok": True}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=f"IMAP Verification failed: {e}")
    except DBError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _run_full_pipeline(email: str, settings: dict):
    """The background task that runs the actual work."""
    run_id = str(uuid.uuid4())
    started = time.time()
    log.info(f"bg-run {run_id} email={email}")

    try:
        # 1) Fetch (24h lookback for automation)
        mails = fetch_recent_mails(
            email, settings["app_password"], 24,
            settings.get("imap_server", "imap.gmail.com"),
            settings.get("imap_port", 993)
        )
        if not mails:
            log.info(f"No mails for {email}, skipping.")
            return

        # 2) Filter
        keep_idx = filter_newsletters(
            settings.get("filter_prompt", DEFAULT_FILTER_PROMPT),
            [{"subject": m.subject,
              "sender": f"{m.sender_name} <{m.sender_email}>",
              "preview": m.body[:1500]} for m in mails],
        )
        kept = [mails[i] for i in keep_idx]
        if not kept:
            log.info(f"Filter kept 0 mails for {email}, skipping.")
            return

        # 3) Aggregate & Digest
        aggregate = _aggregate_bodies(kept)
        if len(aggregate) > 80000:
            aggregate = aggregate[:80000] + "\n\n[…truncated…]"
        
        digest = chat(
            settings.get("digest_prompt", DEFAULT_DIGEST_PROMPT),
            aggregate, max_tokens=8192, temperature=0.4
        )

        # 4) Story & LinkedIn
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f_story = executor.submit(
                chat, settings.get("story_prompt", DEFAULT_STORY_PROMPT),
                digest, max_tokens=8192, temperature=0.6
            )
            f_linkedin = executor.submit(
                chat, settings.get("linkedin_prompt", DEFAULT_LINKEDIN_PROMPT),
                digest, max_tokens=8192, temperature=0.7
            )
            story = f_story.result()
            linkedin = f_linkedin.result()

        elapsed = round(time.time() - started, 1)

        # 5) Persist Run
        insert_run({
            "id": run_id,
            "email": email,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hours_back": 24,
            "num_total": len(mails),
            "num_kept": len(kept),
            "digest": digest,
            "story": story,
            "linkedin": linkedin,
            "filter_prompt": settings.get("filter_prompt"),
            "digest_prompt": settings.get("digest_prompt"),
            "story_prompt": settings.get("story_prompt"),
            "linkedin_prompt": settings.get("linkedin_prompt"),
            "elapsed_seconds": elapsed,
        })
        
        # 6) Webhook
        webhook_url = settings.get("make_webhook_url")
        if webhook_url:
            _fire_make_webhook(webhook_url, {
                "email": email,
                "run_id": run_id,
                "digest": digest,
                "story": story,
                "linkedin_post": linkedin,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        update_last_run(email)
        log.info(f"Completed automation for {email}")

    except Exception as e:
        log.error(f"Automation failed for {email}: {e}")


@app.post("/automation/tick")
def automation_tick(background_tasks: BackgroundTasks):
    """Global entry point for the automation. Ping every 5-15 mins."""
    try:
        users = list_users_for_tick()
        now = datetime.now(timezone.utc)
        triggered = 0

        for u in users:
            tz = pytz.timezone(u.get("timezone", "UTC"))
            user_now = now.astimezone(tz)
            
            # Check if it's the right time
            # We match on HH:MM. Since we run every ~5-15 mins, we check if
            # current time is within a small window OR matches exactly.
            # Simple approach: if user_now.strftime("%H:%M") == u["post_time"]
            # But to be robust, we check if we haven't run TODAY yet.
            
            current_hhmm = user_now.strftime("%H:%M")
            target_hhmm = u.get("post_time", "07:00")
            
            # If current time is past or equal to target time
            if current_hhmm >= target_hhmm:
                # Check last_run_at to see if we already ran today in user's TZ
                last_run = u.get("last_run_at")
                should_run = False
                if not last_run:
                    should_run = True
                else:
                    last_run_dt = datetime.fromisoformat(last_run).astimezone(tz)
                    if last_run_dt.date() < user_now.date():
                        should_run = True
                
                if should_run:
                    background_tasks.add_task(_run_full_pipeline, u["email"], u)
                    triggered += 1

        return {"ok": True, "users_checked": len(users), "triggered": triggered}
    except Exception as e:
        log.exception("Tick failed")
        raise HTTPException(status_code=500, detail=str(e))
