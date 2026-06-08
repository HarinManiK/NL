"""AIMLAPI client. One model, one entry point. Loud failures, polite retries."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import List, Optional

import requests

AIMLAPI_URL = "https://api.aimlapi.com/v1/chat/completions"
AIMLAPI_IMAGE_URL = "https://api.aimlapi.com/images/generations"
# Override at runtime by setting AIMLAPI_MODEL on the server.
MODEL = os.environ.get("AIMLAPI_MODEL", "gemini-3.1-pro-preview").strip()
IMAGE_MODEL = os.environ.get("AIMLAPI_IMAGE_MODEL", "Gemini 3 Pro Image (Nano Banana Pro)").strip()

log = logging.getLogger("nl.llm")


class LLMError(RuntimeError):
    """Raised when the LLM call cannot be completed. Message is user-facing."""


def _api_key() -> str:
    key = os.environ.get("AIMLAPI_API_KEY", "").strip()
    if not key:
        raise LLMError(
            "AIMLAPI_API_KEY is not set on the server. "
            "Set it in Render → Environment and redeploy."
        )
    return key


def chat(system: str, user: str, *, max_tokens: int = 2048,
         temperature: float = 0.4, max_retries: int = 5) -> str:
    """Single chat call. Retries on 429/5xx with exponential backoff up to ~60s waits.
    Raises LLMError with a clear, user-facing message on permanent failure.
    """
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    # Inject the current day of the week to prevent the model from hallucinating the date
    today = datetime.now().strftime("%A")
    system_prompt = f"Note: Today is {today}. The news provided is from recent days.\n\n{system}"

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    last_err = ""
    for attempt in range(max_retries):
        try:
            r = requests.post(AIMLAPI_URL, headers=headers,
                              data=json.dumps(payload), timeout=120)
        except requests.RequestException as e:
            last_err = f"network error: {e}"
            time.sleep(min(2 ** attempt, 30))
            continue

        if r.status_code in (200, 201):
            try:
                data = r.json()
                choice = data["choices"][0]
                content = choice["message"]["content"]
                finish_reason = choice.get("finish_reason")
            except (ValueError, KeyError, IndexError) as e:
                raise LLMError(
                    f"AIMLAPI returned {r.status_code} but response shape was unexpected: {e}. "
                    f"Raw body (first 500 chars): {r.text[:500]}"
                ) from e

            if finish_reason == "length":
                log.warning(f"AIMLAPI model {MODEL} hit token limit ({max_tokens}). Output truncated.")

            if not content or not content.strip():
                raise LLMError("AIMLAPI returned an empty completion.")
            return content.strip()

        # Retryable transient errors
        if r.status_code in (408, 425, 429, 500, 502, 503, 504):
            retry_after = r.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else min(2 ** (attempt + 2), 60)
            except ValueError:
                wait = min(2 ** (attempt + 2), 60)
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            time.sleep(wait)
            continue

        # Non-retryable
        raise LLMError(
            f"AIMLAPI returned HTTP {r.status_code}. "
            f"Body (first 500 chars): {r.text[:500]}"
        )

    raise LLMError(
        f"AIMLAPI error after {max_retries} attempts. "
        f"Last error — {last_err}"
    )


def generate_image(prompt: str, *, max_retries: int = 3) -> str:
    """Generates an image based on the prompt using AIMLAPI. Returns the URL."""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
    }

    last_err = ""
    for attempt in range(max_retries):
        try:
            r = requests.post(AIMLAPI_IMAGE_URL, headers=headers,
                              data=json.dumps(payload), timeout=120)
        except requests.RequestException as e:
            last_err = f"network error: {e}"
            time.sleep(min(2 ** attempt, 30))
            continue

        if r.status_code in (200, 201):
            try:
                data = r.json()
                return data["data"][0]["url"]
            except (ValueError, KeyError, IndexError) as e:
                raise LLMError(
                    f"AIMLAPI returned {r.status_code} but response shape was unexpected: {e}. "
                    f"Raw body (first 500 chars): {r.text[:500]}"
                ) from e

        if r.status_code in (408, 425, 429, 500, 502, 503, 504):
            time.sleep(min(2 ** (attempt + 2), 60))
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            continue

        raise LLMError(
            f"AIMLAPI Image API returned HTTP {r.status_code}. "
            f"Body (first 500 chars): {r.text[:500]}"
        )

    raise LLMError(
        f"AIMLAPI image error after {max_retries} attempts. "
        f"Last error — {last_err}"
    )


def filter_newsletters(filter_prompt: str, mails: List[dict]) -> List[int]:
    """Given mail metadata + body previews, ask the LLM which indices are newsletters.
    Returns the list of indices to keep. Raises LLMError on parse failure.

    `mails` is a list of dicts with keys: subject, sender, preview.
    """
    if not mails:
        return []

    lines = []
    for i, m in enumerate(mails):
        preview = (m.get("preview") or "").replace("\n", " ").strip()
        if len(preview) > 400:
            preview = preview[:400] + "…"
        lines.append(
            f"[{i}] FROM: {m.get('sender','')}\n"
            f"    SUBJECT: {m.get('subject','')}\n"
            f"    PREVIEW: {preview}"
        )
    listing = "\n\n".join(lines)

    system = (
        f"{filter_prompt}\n\n"
        "You will receive a numbered list of emails. Decide which are newsletters per the "
        "criteria above. Respond with ONLY a JSON object of the form "
        '{\"keep\": [<indices>]} — no prose, no markdown fences, no commentary.'
    )
    user = f"Emails:\n\n{listing}\n\nRespond with the JSON object now."

    raw = chat(system, user, max_tokens=4096, temperature=0.1)

    # Strip code fences if the model added them despite instructions
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # remove possible "json" language tag
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    # Find the first '{' and last '}' to be defensive
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(
            f"Filter LLM did not return JSON. Raw output: {raw[:500]}"
        )
    blob = cleaned[start:end + 1]
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as e:
        raise LLMError(
            f"Could not parse filter LLM JSON ({e}). Raw output: {raw[:500]}"
        ) from e

    keep = parsed.get("keep", [])
    if not isinstance(keep, list):
        raise LLMError(
            f"Filter LLM JSON had no 'keep' array. Got: {parsed}"
        )

    valid: List[int] = []
    for x in keep:
        try:
            idx = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(mails):
            valid.append(idx)
    return sorted(set(valid))
