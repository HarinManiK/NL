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
import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, HttpUrl
import requests

from db import (
    DBError, get_run, insert_run, list_runs,
    get_settings, list_users_for_tick, update_automation_status, upsert_settings,
)
from imap_fetch import MailRecord, fetch_recent_mails, fetch_recent_mails_stream, verify_imap
from llm import LLMError, chat, filter_newsletters
from newsletter import (
    append_links_html,
    append_links_text,
    extract_useful_links,
    filter_links_for_body,
    sanitize_newsletter_html,
    strip_links_text,
    useful_links_block,
)

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
    "no preamble. Do not include URLs, markdown links, HTML links, or a final link section in the body; "
    "the system appends the only link section at the bottom."
)
DEFAULT_STORY_PROMPT = (
    "You are turning the digest below into a single flowing narrative — a 'what happened "
    "today in this world' story. Write in connected paragraphs, not bullets. Keep it "
    "factual and grounded; do not invent details. Weave related items together so the "
    "reader gets the arc of the day across topics. ~300-500 words. Output plain text "
    "with no preamble. Do not include URLs, markdown links, HTML links, or a final link section in the body; "
    "the system appends the only link section at the bottom."
)
DEFAULT_LINKEDIN_PROMPT = (
    "Turn the digest below into a highly engaging, synthesis-driven LinkedIn post.\n\n"
    "Goal:\n"
    "Identify an overarching narrative connecting the most surprising or impactful stories in the digest and craft a thought-provoking post around it.\n\n"
    "Hard rules:\n"
    "- Do NOT just summarize or list the news. Synthesize 2-3 converging storylines into a single big-picture thesis (e.g., 'The AI build-out has gone physical' or 'The math is getting uncomfortable').\n"
    "- Hook the reader immediately with a punchy, surprising stat or fact from the digest in the very first sentence.\n"
    "- Use short, punchy paragraphs (1-3 sentences max). Maximize whitespace.\n"
    "- Highlight specific numbers, company names, and hard facts to ground your narrative.\n"
    "- Use clean formatting: occasional arrows (→) or short numbered lists to break down shifts/trends.\n"
    "- End with a strong closing thought followed by a single thought-provoking, open-ended question for the audience.\n"
    "- Include exactly 5 relevant hashtags on the final line.\n"
    "- Length: 200-400 words.\n"
    "- No emojis unless genuinely useful (rarely).\n"
    "- Do not include URLs, markdown links, HTML links, or a final link section in the body; the system appends the only link section at the bottom.\n"
    "- Output only the LinkedIn post. No preamble.\n\n"
    "Style:\n"
    "Conversational, sharp, professional, highly analytical founder/investor/operator voice. "
    "Make it read like a top-tier industry analyst connecting the dots between seemingly unrelated events."
)
DEFAULT_NEWSLETTER_SUBJECT_PROMPT = (
    "Write a concise, specific email subject line for the newsletter below. "
    "Make it useful and professional. Keep it under 80 characters. "
    "Output only the subject line, no quotes and no preamble."
)
DEFAULT_NEWSLETTER_HTML_PROMPT = (
    "Turn the digest below into a polished HTML newsletter email. Use simple email-safe HTML only: "
    "h1, h2, h3, p, ul, ol, li, strong, em, a, br, hr, div, and span. "
    "Preserve important concrete facts and do not invent details. Do not include URLs, anchor tags, or a final link section in the body; "
    "the system appends the only link section at the bottom. "
    "Do not include scripts, forms, external stylesheets, images, or an unsubscribe footer. "
    "Output only the HTML body."
)


DEFAULT_ARTICLE_PROMPT = (
    "You are turning the digest below into a polished, professional newsletter article.\n\n"
    "Goal:\n"
    "Create a cohesive article that starts with a strong executive summary and then presents the detailed news.\n\n"
    "Hard rules:\n"
    "- Start with a oneline greeting referencing yesterday's day of the current week (e.g., 'Welcome to your Friday Briefing.' or 'Here is an overview of our Thursday Briefing:'). Vary the phrasing naturally.\n"
    "- Write a compelling opening section that highlights 1-3 overarching themes, major takeaways, or shocking statistics from the digest.\n"
    "- After the intro, present the rest of the digest content grouped by the original themes.\n"
    "- Use clean formatting: clear headings and readable paragraphs or clean lists. Do NOT use excessive markdown like bolding every entity or using asterisks for every line.\n"
    "- Preserve all concrete facts, numbers, and details from the digest.\n"
    "- Do not include URLs, markdown links, HTML links, or a final link section in the body; the system appends the only link section at the bottom.\n"
    "- Output plain text/markdown — no preamble."
)


class Prompts(BaseModel):
    filter: str = DEFAULT_FILTER_PROMPT
    digest: str = DEFAULT_DIGEST_PROMPT
    story: str = DEFAULT_STORY_PROMPT
    linkedin: str = DEFAULT_LINKEDIN_PROMPT
    newsletter_subject: str = DEFAULT_NEWSLETTER_SUBJECT_PROMPT
    newsletter_html: str = DEFAULT_NEWSLETTER_HTML_PROMPT
    article: str = DEFAULT_ARTICLE_PROMPT


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
    story_enabled: bool = True
    linkedin_enabled: bool = True
    newsletter_enabled: bool = False
    article_enabled: bool = True
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993


class SettingsReq(BaseModel):
    email: EmailStr
    app_password: str
    hours_back: int = Field(default=24, ge=1, le=720)
    make_webhook_url: Optional[str] = None
    automation_enabled: bool = False
    timezone: str = "UTC"
    post_time: str = "07:00"
    prompts: Prompts = Prompts()
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993
    story_enabled: bool = True
    linkedin_enabled: bool = True
    newsletter_enabled: bool = False
    article_enabled: bool = True
    linkedin_auto_post_enabled: bool = False
    linkedin_post_time: str = "07:00"
    linkedin_timezone: str = "UTC"


class ManualPostReq(BaseModel):
    webhook_url: HttpUrl
    content: str = Field(min_length=1)


def _aggregate_bodies(records: List[MailRecord]) -> str:
    chunks = []
    for r in records:
        body = strip_links_text(r.body).strip()
        if len(body) > 8000:
            body = body[:8000] + "\n\n[…truncated…]"
        chunks.append(f"=====\n{r.header_block()}\n{body}\n")
    return "\n".join(chunks)


def _append_links_to_input(base: str, useful_links: List[dict]) -> str:
    block = useful_links_block(useful_links)
    return base + block if block else base


def _link_context_block(useful_links: List[dict]) -> str:
    if not useful_links:
        return ""
    lines = [
        "\n\nUseful link topics extracted from the filtered emails. Use this only to understand the source topics; do not include links in the body:"
    ]
    for i, link in enumerate(useful_links, 1):
        label = link.get("text") or link.get("nearby_text") or link.get("source_subject") or "Related update"
        lines.append(f"{i}. {strip_links_text(label)[:240]}")
    return "\n".join(lines)


def _clean_generated_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^[:\s*]+(?=[A-Za-z0-9])", "", cleaned)
    return cleaned


def _schedule_due_status(settings: dict, now_utc: datetime, prefix: str) -> dict:
    if prefix == "linkedin":
        time_key = "linkedin_post_time"
        timezone_key = "linkedin_timezone"
        last_key = "last_linkedin_run_at"
    else:
        time_key = f"{prefix}_time"
        timezone_key = f"{prefix}_timezone"
        last_key = f"last_{prefix}_run_at"

    timezone_name = settings.get(timezone_key) or settings.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "UTC"
        tz = ZoneInfo("UTC")
    post_time = settings.get(time_key) or settings.get("post_time") or "07:00"
    user_now = now_utc.astimezone(tz)
    if user_now.strftime("%H:%M") < post_time:
        return {"due": False, "reason": "before_scheduled_time", "user_time": user_now.isoformat()}
    last_run = settings.get(last_key)
    if not last_run:
        return {"due": True, "reason": "scheduled_time_reached", "user_time": user_now.isoformat()}
    try:
        last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00")).astimezone(tz)
    except (TypeError, ValueError):
        return {"due": True, "reason": "last_run_time_invalid", "user_time": user_now.isoformat()}
    if last_dt.date() < user_now.date():
        return {"due": True, "reason": "scheduled_time_reached", "user_time": user_now.isoformat()}
    return {"due": False, "reason": "already_ran_today", "user_time": user_now.isoformat()}


def _post_to_make(webhook_url: str, content: str) -> int:
    try:
        r = requests.post(
            webhook_url,
            json={
                "linkedin_post": content,
                "content": content,
                "source": "newsletter-digest",
                "sent_at": datetime.now(timezone.utc).isoformat(),
            },
            timeout=(5, 30),
        )
    except requests.Timeout as e:
        raise RuntimeError(
            "The LinkedIn webhook timed out. Check that the Make.com scenario is on and the webhook URL is correct."
        ) from e
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach the LinkedIn webhook: {e}") from e

    if not 200 <= r.status_code < 300:
        if r.status_code == 410:
            raise RuntimeError(
                "Make.com says there is no scenario listening for this webhook. "
                "Create a fresh Custom Webhook in Make, attach it to an active scenario, "
                "turn the scenario on, then paste the new webhook URL in Settings."
            )
        raise RuntimeError(
            f"LinkedIn webhook returned HTTP {r.status_code}. "
            f"Response body: {r.text[:500] or '(empty)'}"
        )

    return r.status_code


def _generate_run(
    *,
    email: str,
    app_password: str,
    hours_back: int,
    prompts: Prompts,
    story_enabled: bool = True,
    linkedin_enabled: bool = True,
    newsletter_enabled: bool = False,
    imap_server: str = "imap.gmail.com",
    imap_port: int = 993,
) -> dict:
    run_id = str(uuid.uuid4())
    started = time.time()

    mails = fetch_recent_mails(email, app_password, hours_back, imap_server, imap_port)
    if not mails:
        insert_run({
            "id": run_id,
            "email": email,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hours_back": hours_back,
            "num_total": 0,
            "num_kept": 0,
            "digest": f"Auto-run skipped: no mails found in the last {hours_back} hours.",
            "story": "",
            "linkedin": "",
            "article": "",
            "story_enabled": story_enabled,
            "linkedin_enabled": linkedin_enabled,
            "newsletter_enabled": newsletter_enabled,
            "article_enabled": article_enabled,
            "newsletter_subject": "",
            "newsletter_html": "",
            "useful_links": [],
            "filter_prompt": prompts.filter,
            "digest_prompt": prompts.digest,
            "story_prompt": prompts.story,
            "linkedin_prompt": prompts.linkedin,
            "newsletter_subject_prompt": prompts.newsletter_subject,
            "newsletter_html_prompt": prompts.newsletter_html,
            "article_prompt": prompts.article,
            "elapsed_seconds": round(time.time() - started, 1),
        })
        return {
            "run_id": run_id,
            "num_total": 0,
            "num_kept": 0,
            "skipped": True,
            "skip_reason": f"No mails found in the last {hours_back} hours.",
        }

    keep_idx = filter_newsletters(
        prompts.filter,
        [{"subject": m.subject,
          "sender": f"{m.sender_name} <{m.sender_email}>",
          "preview": m.body[:1500]} for m in mails],
    )
    kept = [mails[i] for i in keep_idx]
    if not kept:
        insert_run({
            "id": run_id,
            "email": email,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hours_back": hours_back,
            "num_total": len(mails),
            "num_kept": 0,
            "digest": f"Auto-run skipped: filter kept 0 of {len(mails)} mails.",
            "story": "",
            "linkedin": "",
            "article": "",
            "story_enabled": story_enabled,
            "linkedin_enabled": linkedin_enabled,
            "newsletter_enabled": newsletter_enabled,
            "article_enabled": article_enabled,
            "newsletter_subject": "",
            "newsletter_html": "",
            "useful_links": [],
            "filter_prompt": prompts.filter,
            "digest_prompt": prompts.digest,
            "story_prompt": prompts.story,
            "linkedin_prompt": prompts.linkedin,
            "newsletter_subject_prompt": prompts.newsletter_subject,
            "newsletter_html_prompt": prompts.newsletter_html,
            "article_prompt": prompts.article,
            "elapsed_seconds": round(time.time() - started, 1),
        })
        return {
            "run_id": run_id,
            "num_total": len(mails),
            "num_kept": 0,
            "skipped": True,
            "skip_reason": f"Filter kept 0 of {len(mails)} mails.",
        }

    useful_links = extract_useful_links(kept)
    aggregate = _aggregate_bodies(kept) + _link_context_block(useful_links)
    if len(aggregate) > 80000:
        aggregate = aggregate[:80000] + "\n\n[...aggregate truncated to fit context...]"

    digest_body = _clean_generated_text(chat(prompts.digest, aggregate, max_tokens=8192, temperature=0.4))
    digest_links = filter_links_for_body(useful_links, digest_body)
    digest = append_links_text(digest_body, digest_links)
    optional_input = digest_body + _link_context_block(digest_links)
    
    unique_sources = []
    for m in kept:
        s = (m.sender_name or m.sender_email or "").strip()
        if s and s not in unique_sources:
            unique_sources.append(s)
    sources_text = ""
    if unique_sources:
        sources_text = "\n\n### Sources used to create this digest:\n" + "\n".join(f"- {s}" for s in unique_sources)

    story = ""
    linkedin = ""
    newsletter_subject = ""
    newsletter_html = ""
    article = ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        if story_enabled:
            futures["story"] = executor.submit(chat, prompts.story, optional_input, max_tokens=8192, temperature=0.6)
        if linkedin_enabled:
            futures["linkedin"] = executor.submit(chat, prompts.linkedin, optional_input, max_tokens=8192, temperature=0.7)
        if newsletter_enabled:
            futures["newsletter_subject"] = executor.submit(
                chat, prompts.newsletter_subject, optional_input, max_tokens=256, temperature=0.5
            )
        if article_enabled:
            futures["article"] = executor.submit(chat, prompts.article, optional_input, max_tokens=8192, temperature=0.6)
        for name, future in futures.items():
            if name == "story":
                story_body = _clean_generated_text(future.result())
                story = append_links_text(story_body, filter_links_for_body(digest_links, story_body))
            elif name == "linkedin":
                linkedin_body = _clean_generated_text(future.result())
                linkedin = append_links_text(linkedin_body, filter_links_for_body(digest_links, linkedin_body))
            elif name == "newsletter_subject":
                newsletter_subject = future.result().strip().strip('"')
            elif name == "article":
                article_body = _clean_generated_text(future.result())
                article = article_body + sources_text
    if newsletter_enabled:
        newsletter_body = sanitize_newsletter_html(
            chat(prompts.newsletter_html, optional_input, max_tokens=8192, temperature=0.5)
        )
        newsletter_html = append_links_html(newsletter_body, filter_links_for_body(digest_links, newsletter_body))

    elapsed = round(time.time() - started, 1)
    row = {
        "id": run_id,
        "email": email,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hours_back": hours_back,
        "num_total": len(mails),
        "num_kept": len(kept),
        "digest": digest,
        "story": story,
        "linkedin": linkedin,
        "article": article,
        "story_enabled": story_enabled,
        "linkedin_enabled": linkedin_enabled,
        "newsletter_enabled": newsletter_enabled,
        "article_enabled": article_enabled,
        "newsletter_subject": newsletter_subject,
        "newsletter_html": newsletter_html,
        "useful_links": digest_links,
        "filter_prompt": prompts.filter,
        "digest_prompt": prompts.digest,
        "story_prompt": prompts.story,
        "linkedin_prompt": prompts.linkedin,
        "newsletter_subject_prompt": prompts.newsletter_subject,
        "newsletter_html_prompt": prompts.newsletter_html,
        "article_prompt": prompts.article,
        "elapsed_seconds": elapsed,
    }
    insert_run(row)
    return {**row, "run_id": run_id, "skipped": False}


def _prompts_from_settings(settings: dict) -> Prompts:
    return Prompts(
        filter=settings.get("filter_prompt") or DEFAULT_FILTER_PROMPT,
        digest=settings.get("digest_prompt") or DEFAULT_DIGEST_PROMPT,
        story=settings.get("story_prompt") or DEFAULT_STORY_PROMPT,
        linkedin=settings.get("linkedin_prompt") or DEFAULT_LINKEDIN_PROMPT,
        newsletter_subject=settings.get("newsletter_subject_prompt") or DEFAULT_NEWSLETTER_SUBJECT_PROMPT,
        newsletter_html=settings.get("newsletter_html_prompt") or DEFAULT_NEWSLETTER_HTML_PROMPT,
        article=settings.get("article_prompt") or DEFAULT_ARTICLE_PROMPT,
    )


def _validate_post_time(post_time: str) -> None:
    try:
        datetime.strptime(post_time, "%H:%M")
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Post time must be a valid HH:MM time.") from e


def _automation_due_status(settings: dict, now_utc: datetime) -> dict:
    try:
        timezone_name = settings.get("timezone") or "UTC"
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "UTC"
        tz = ZoneInfo("UTC")

    user_now = now_utc.astimezone(tz)
    target_hhmm = settings.get("post_time") or "07:00"
    base = {
        "timezone": timezone_name,
        "post_time": target_hhmm,
        "user_time": user_now.isoformat(),
        "user_date": user_now.date().isoformat(),
    }

    if user_now.strftime("%H:%M") < target_hhmm:
        return {**base, "due": False, "reason": "before_scheduled_time"}

    last_run = settings.get("last_run_at")
    if not last_run:
        return {**base, "due": True, "reason": "scheduled_time_reached"}

    try:
        last_run_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00")).astimezone(tz)
    except (TypeError, ValueError):
        return {**base, "due": True, "reason": "last_run_time_invalid"}

    if last_run_dt.date() < user_now.date():
        return {
            **base,
            "due": True,
            "reason": "scheduled_time_reached",
            "last_run_local": last_run_dt.isoformat(),
        }

    return {
        **base,
        "due": False,
        "reason": "already_ran_for_current_settings",
        "last_run_local": last_run_dt.isoformat(),
    }


def _is_due(settings: dict, now_utc: datetime) -> bool:
    return bool(_automation_due_status(settings, now_utc)["due"])


def _sse(event: dict, *, pad: bool = False) -> str:
    """Encode one Server-Sent Events frame."""
    prefix = ":" + (" " * 2048) + "\n" if pad else ""
    return f"{prefix}data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/")
def root():
    return {"ok": True, "service": "newsletter-digest", "version": "1.1"}


@app.head("/")
def root_head():
    return {}


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
            yield _sse({"type": "status", "message": "Starting run..."}, pad=True)
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

            # 3) Aggregate + useful links
            useful_links = extract_useful_links(kept)
            yield _sse({"type": "links_done", "count": len(useful_links), "links": useful_links})
            aggregate = _aggregate_bodies(kept) + _link_context_block(useful_links)
            if len(aggregate) > 80000:
                aggregate = aggregate[:80000] + "\n\n[…aggregate truncated to fit context…]"

            # 4) Digest
            yield _sse({"type": "step", "name": "digest", "status": "start"})
            try:
                digest_body = _clean_generated_text(chat(req.prompts.digest, aggregate, max_tokens=8192, temperature=0.4))
                digest_links = filter_links_for_body(useful_links, digest_body)
                digest = append_links_text(digest_body, digest_links)
            except LLMError as e:
                yield _sse({"type": "error", "message": f"Digest step failed: {e}"})
                return
            yield _sse({"type": "step", "name": "digest", "status": "done", "text": digest})

            # 5+) Optional outputs
            optional_input = digest_body + _link_context_block(digest_links)
            story = ""
            linkedin = ""
            newsletter_subject = ""
            newsletter_html = ""
            article = ""

            if req.story_enabled:
                yield _sse({"type": "step", "name": "story", "status": "start"})
            if req.linkedin_enabled:
                yield _sse({"type": "step", "name": "linkedin", "status": "start"})
            if req.newsletter_enabled:
                yield _sse({"type": "step", "name": "newsletter", "status": "start"})
            if req.article_enabled:
                yield _sse({"type": "step", "name": "article", "status": "start"})

            try:
                unique_sources = []
                for m in kept:
                    s = (m.sender_name or m.sender_email or "").strip()
                    if s and s not in unique_sources:
                        unique_sources.append(s)
                sources_text = ""
                if unique_sources:
                    sources_text = "\n\n### Sources used to create this digest:\n" + "\n".join(f"- {s}" for s in unique_sources)

                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    futures: Dict[str, Any] = {}
                    if req.story_enabled:
                        futures["story"] = executor.submit(
                            chat, req.prompts.story, optional_input, max_tokens=8192, temperature=0.6
                        )
                    if req.linkedin_enabled:
                        futures["linkedin"] = executor.submit(
                            chat, req.prompts.linkedin, optional_input, max_tokens=8192, temperature=0.7
                        )
                    if req.newsletter_enabled:
                        futures["newsletter_subject"] = executor.submit(
                            chat, req.prompts.newsletter_subject, optional_input, max_tokens=256, temperature=0.5
                        )
                    if req.article_enabled:
                        futures["article"] = executor.submit(
                            chat, req.prompts.article, optional_input, max_tokens=8192, temperature=0.6
                        )

                    for name, future in futures.items():
                        if name == "story":
                            story_body = _clean_generated_text(future.result())
                            story = append_links_text(story_body, filter_links_for_body(digest_links, story_body))
                        elif name == "linkedin":
                            linkedin_body = _clean_generated_text(future.result())
                            linkedin = append_links_text(linkedin_body, filter_links_for_body(digest_links, linkedin_body))
                        elif name == "newsletter_subject":
                            newsletter_subject = future.result().strip().strip('"')
                        elif name == "article":
                            article_body = _clean_generated_text(future.result())
                            article = article_body + sources_text

                if req.newsletter_enabled:
                    newsletter_body = sanitize_newsletter_html(
                        chat(req.prompts.newsletter_html, optional_input, max_tokens=8192, temperature=0.5)
                    )
                    newsletter_html = append_links_html(
                        newsletter_body, filter_links_for_body(digest_links, newsletter_body)
                    )

            except LLMError as e:
                yield _sse({"type": "error", "message": f"Parallel generation failed: {e}"})
                return
            except Exception as e:
                yield _sse({"type": "error", "message": f"Unexpected error in parallel step: {e}"})
                return

            if req.story_enabled:
                yield _sse({"type": "step", "name": "story", "status": "done", "text": story})
            if req.linkedin_enabled:
                yield _sse({"type": "step", "name": "linkedin", "status": "done", "text": linkedin})
            if req.article_enabled:
                yield _sse({"type": "step", "name": "article", "status": "done", "text": article})
            if req.newsletter_enabled:
                yield _sse({"type": "newsletter_done", "subject": newsletter_subject, "html": newsletter_html})
                yield _sse({"type": "step", "name": "newsletter", "status": "done"})

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
                    "article": article,
                    "story_enabled": req.story_enabled,
                    "linkedin_enabled": req.linkedin_enabled,
                    "newsletter_enabled": req.newsletter_enabled,
                    "article_enabled": req.article_enabled,
                    "newsletter_subject": newsletter_subject,
                    "newsletter_html": newsletter_html,
                    "useful_links": digest_links,
                    "filter_prompt": req.prompts.filter,
                    "digest_prompt": req.prompts.digest,
                    "story_prompt": req.prompts.story,
                    "linkedin_prompt": req.prompts.linkedin,
                    "newsletter_subject_prompt": req.prompts.newsletter_subject,
                    "newsletter_html_prompt": req.prompts.newsletter_html,
                    "article_prompt": req.prompts.article,
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
                        "article": article,
                        "newsletter_subject": newsletter_subject,
                        "newsletter_html": newsletter_html,
                        "useful_links": digest_links,
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
            "Cache-Control": "no-cache, no-transform",
            "Content-Encoding": "identity",
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


# ---------- Settings & manual LinkedIn posting ----------

@app.post("/post")
def manual_post(req: ManualPostReq):
    """Send the generated LinkedIn post to the configured Make.com webhook."""
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="LinkedIn post content is empty.")

    try:
        status_code = _post_to_make(str(req.webhook_url), content)
    except RuntimeError as e:
        log.error("Manual post failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e

    return {"ok": True, "status_code": status_code}

@app.get("/settings")
def get_user_settings(email: EmailStr = Query(...)):
    try:
        s = get_settings(email)
        if not s:
            return {"email": email, "found": False}
        return {
            "email": s.get("email", email),
            "app_password": s.get("app_password", ""),
            "make_webhook_url": s.get("make_webhook_url") or "",
            "hours_back": s.get("hours_back") or 24,
            "automation_enabled": bool(s.get("automation_enabled")),
            "timezone": s.get("timezone") or "UTC",
            "post_time": s.get("post_time") or "07:00",
            "story_enabled": bool(s.get("story_enabled", True)),
            "linkedin_enabled": bool(s.get("linkedin_enabled", True)),
            "newsletter_enabled": bool(s.get("newsletter_enabled", False)),
            "article_enabled": bool(s.get("article_enabled", True)),
            "linkedin_auto_post_enabled": bool(s.get("linkedin_auto_post_enabled", s.get("automation_enabled", False))),
            "linkedin_post_time": s.get("linkedin_post_time") or s.get("post_time") or "07:00",
            "linkedin_timezone": s.get("linkedin_timezone") or s.get("timezone") or "UTC",
            "last_linkedin_run_at": s.get("last_linkedin_run_at"),
            "last_run_at": s.get("last_run_at"),
            "last_automation_error": s.get("last_automation_error"),
            "prompts": {
                "filter": s.get("filter_prompt") or DEFAULT_FILTER_PROMPT,
                "digest": s.get("digest_prompt") or DEFAULT_DIGEST_PROMPT,
                "story": s.get("story_prompt") or DEFAULT_STORY_PROMPT,
                "linkedin": s.get("linkedin_prompt") or DEFAULT_LINKEDIN_PROMPT,
                "newsletter_subject": s.get("newsletter_subject_prompt") or DEFAULT_NEWSLETTER_SUBJECT_PROMPT,
                "newsletter_html": s.get("newsletter_html_prompt") or DEFAULT_NEWSLETTER_HTML_PROMPT,
                "article": s.get("article_prompt") or DEFAULT_ARTICLE_PROMPT,
            },
            "imap_server": s.get("imap_server", "imap.gmail.com"),
            "imap_port": s.get("imap_port", 993),
            "found": True,
        }
    except DBError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/settings")
def save_user_settings(req: SettingsReq):
    try:
        if req.linkedin_auto_post_enabled and not (req.make_webhook_url or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Add a Make.com webhook URL before enabling daily auto-post.",
            )
        try:
            ZoneInfo(req.timezone)
        except ZoneInfoNotFoundError as e:
            raise HTTPException(status_code=400, detail=f"Unknown timezone: {req.timezone}") from e
        _validate_post_time(req.post_time)
        try:
            ZoneInfo(req.linkedin_timezone)
        except ZoneInfoNotFoundError as e:
            raise HTTPException(status_code=400, detail=f"Unknown timezone: {req.linkedin_timezone}") from e
        _validate_post_time(req.linkedin_post_time)

        # Verify password before saving
        verify_imap(req.email, req.app_password, req.imap_server, req.imap_port)

        existing = get_settings(req.email)
        automation_enabled = bool(
            req.automation_enabled
            or req.linkedin_auto_post_enabled
        )
        reset_last_run = (
            not existing
            or (existing.get("app_password") or "") != req.app_password
            or bool(existing.get("automation_enabled")) != automation_enabled
            or (existing.get("timezone") or "UTC") != req.timezone
            or (existing.get("post_time") or "07:00") != req.post_time
            or int(existing.get("hours_back") or 24) != req.hours_back
            or (existing.get("make_webhook_url") or "") != (req.make_webhook_url or "")
            or (existing.get("filter_prompt") or DEFAULT_FILTER_PROMPT) != req.prompts.filter
            or (existing.get("digest_prompt") or DEFAULT_DIGEST_PROMPT) != req.prompts.digest
            or (existing.get("story_prompt") or DEFAULT_STORY_PROMPT) != req.prompts.story
            or (existing.get("linkedin_prompt") or DEFAULT_LINKEDIN_PROMPT) != req.prompts.linkedin
            or (existing.get("newsletter_subject_prompt") or DEFAULT_NEWSLETTER_SUBJECT_PROMPT) != req.prompts.newsletter_subject
            or (existing.get("newsletter_html_prompt") or DEFAULT_NEWSLETTER_HTML_PROMPT) != req.prompts.newsletter_html
            or (existing.get("article_prompt") or DEFAULT_ARTICLE_PROMPT) != req.prompts.article
            or (existing.get("imap_server") or "imap.gmail.com") != req.imap_server
            or int(existing.get("imap_port") or 993) != req.imap_port
            or bool(existing.get("last_automation_error"))
        )
        
        row = {
            "email": req.email,
            "app_password": req.app_password,
            "hours_back": req.hours_back,
            "make_webhook_url": req.make_webhook_url,
            "automation_enabled": automation_enabled,
            "timezone": req.timezone,
            "post_time": req.post_time,
            "story_enabled": req.story_enabled,
            "linkedin_enabled": req.linkedin_enabled,
            "newsletter_enabled": req.newsletter_enabled,
            "article_enabled": req.article_enabled,
            "linkedin_auto_post_enabled": req.linkedin_auto_post_enabled,
            "linkedin_post_time": req.linkedin_post_time,
            "linkedin_timezone": req.linkedin_timezone,
            "filter_prompt": req.prompts.filter,
            "digest_prompt": req.prompts.digest,
            "story_prompt": req.prompts.story,
            "linkedin_prompt": req.prompts.linkedin,
            "newsletter_subject_prompt": req.prompts.newsletter_subject,
            "newsletter_html_prompt": req.prompts.newsletter_html,
            "article_prompt": req.prompts.article,
            "imap_server": req.imap_server,
            "imap_port": req.imap_port,
        }
        if reset_last_run:
            row["last_run_at"] = None
            row["last_linkedin_run_at"] = None
            row["last_automation_error"] = None

        upsert_settings(row)
        return {"ok": True, "automation_run_reset": reset_last_run}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=f"IMAP Verification failed: {e}")
    except DBError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/automation/status")
def automation_status(email: EmailStr = Query(...)):
    try:
        s = get_settings(email)
    except DBError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not s:
        return {"found": False, "email": email}

    now = datetime.now(timezone.utc)
    linkedin_status = _schedule_due_status(s, now, "linkedin")
    return {
        "found": True,
        "email": email,
        "automation_enabled": bool(s.get("automation_enabled")),
        "timezone": s.get("timezone") or "UTC",
        "post_time": s.get("post_time") or "07:00",
        "hours_back": s.get("hours_back") or 24,
        "has_webhook": bool((s.get("make_webhook_url") or "").strip()),
        "last_run_at": s.get("last_run_at"),
        "last_linkedin_run_at": s.get("last_linkedin_run_at"),
        "last_automation_error": s.get("last_automation_error"),
        "linkedin_due_now": bool(linkedin_status["due"]) if bool(s.get("linkedin_auto_post_enabled")) else False,
        "linkedin_due_reason": linkedin_status["reason"] if bool(s.get("linkedin_auto_post_enabled")) else "linkedin_disabled",
        "server_time_utc": now.isoformat(),
    }


def _run_automation_for_user(settings: dict, mode: str, claimed_at: Optional[datetime] = None) -> dict:
    email = settings["email"]
    claim_time = claimed_at or datetime.now(timezone.utc)
    last_key = "last_linkedin_run_at"
    try:
        # Claim before slow work so repeated cron ticks cannot start duplicates.
        update_automation_status(email, {
            last_key: claim_time.isoformat(),
            "last_run_at": claim_time.isoformat(),
            "last_automation_error": None,
        })

        result = _generate_run(
            email=email,
            app_password=settings["app_password"],
            hours_back=settings.get("hours_back") or 24,
            prompts=_prompts_from_settings(settings),
            story_enabled=bool(settings.get("story_enabled", True)),
            linkedin_enabled=mode == "linkedin" and bool(settings.get("linkedin_enabled", True)),
            newsletter_enabled=False,
            article_enabled=bool(settings.get("article_enabled", True)),
            imap_server=settings.get("imap_server") or "imap.gmail.com",
            imap_port=settings.get("imap_port") or 993,
        )

        posted = False
        webhook_url = (settings.get("make_webhook_url") or "").strip()
        if mode == "linkedin" and webhook_url and not result.get("skipped") and result.get("linkedin"):
            _post_to_make(webhook_url, result["linkedin"])
            posted = True

        try:
            update_automation_status(email, {
                last_key: claim_time.isoformat(),
                "last_run_at": claim_time.isoformat(),
                "last_automation_error": None,
            })
        except DBError:
            log.exception("automation final status update failed email=%s", email)
        log.info("automation complete email=%s skipped=%s", email, result.get("skipped"))
        return {
            "email": email,
            "ok": True,
            "skipped": bool(result.get("skipped")),
            "skip_reason": result.get("skip_reason"),
            "run_id": result.get("run_id"),
            "num_total": result.get("num_total"),
            "num_kept": result.get("num_kept"),
            "mode": mode,
            "posted": posted,
            "claimed_at": claim_time.isoformat(),
        }
    except Exception as e:
        log.exception("automation failed email=%s", email)
        update_automation_status(email, {
            last_key: None,
            "last_automation_error": str(e)[:1000],
        })
        return {
            "email": email,
            "ok": False,
            "error": str(e)[:1000],
            "claimed_at": claim_time.isoformat(),
        }


@app.get("/automation/tick")
@app.post("/automation/tick")
def automation_tick(
    x_cron_secret: Optional[str] = Header(default=None),
):
    expected_secret = os.environ.get("CRON_SECRET", "").strip()
    if not expected_secret:
        raise HTTPException(status_code=500, detail="CRON_SECRET is not configured.")
    if expected_secret and x_cron_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid cron secret.")

    try:
        users = list_users_for_tick()
    except DBError as e:
        raise HTTPException(status_code=500, detail=str(e))

    now = datetime.now(timezone.utc)
    checked = []
    results = []
    for user in users:
        linkedin_due = (
            bool(user.get("linkedin_auto_post_enabled"))
            and bool(user.get("linkedin_enabled", True))
            and bool((user.get("make_webhook_url") or "").strip())
        )
        linkedin_status = _schedule_due_status(user, now, "linkedin") if linkedin_due else {
            "due": False, "reason": "linkedin_disabled"
        }
        checked.append({
            "email": user.get("email"),
            "linkedin_due": bool(linkedin_status["due"]),
            "linkedin_reason": linkedin_status["reason"],
            "last_linkedin_run_at": user.get("last_linkedin_run_at"),
        })
        if linkedin_status["due"]:
            results.append(_run_automation_for_user(user, "linkedin", now))

    log.info("automation tick users_checked=%s triggered=%s", len(users), len(results))
    return {
        "ok": True,
        "users_checked": len(users),
        "triggered": len(results),
        "checked": checked,
        "results": results,
        "server_time_utc": now.isoformat(),
    }
