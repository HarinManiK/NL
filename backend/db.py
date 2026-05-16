"""Supabase REST client. Tiny wrapper — only what we need."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests


class DBError(RuntimeError):
    """User-facing DB error."""


def _conf() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise DBError(
            "Supabase is not configured on the server. "
            "Set SUPABASE_URL and SUPABASE_SERVICE_KEY in Render → Environment."
        )
    return url, key


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    _, key = _conf()
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def insert_run(row: Dict[str, Any]) -> Dict[str, Any]:
    url, _ = _conf()
    r = requests.post(
        f"{url}/rest/v1/runs",
        headers=_headers({"Prefer": "return=representation"}),
        data=json.dumps(row),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise DBError(f"Supabase insert failed: HTTP {r.status_code} — {r.text[:400]}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else {}


def list_runs(email: str, limit: int = 50) -> List[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/runs",
        headers=_headers(),
        params={
            "email": f"eq.{email}",
            "select": "id,created_at,hours_back,num_total,num_kept",
            "order": "created_at.desc",
            "limit": str(limit),
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase list failed: HTTP {r.status_code} — {r.text[:400]}")
    return r.json()


def get_run(run_id: str, email: str) -> Optional[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/runs",
        headers=_headers(),
        params={
            "id": f"eq.{run_id}",
            "email": f"eq.{email}",
            "select": "*",
            "limit": "1",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase get failed: HTTP {r.status_code} — {r.text[:400]}")
    rows = r.json()
    return rows[0] if rows else None


def get_settings(email: str) -> Optional[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/user_settings",
        headers=_headers(),
        params={
            "email": f"eq.{email}",
            "select": "*",
            "limit": "1",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase settings get failed: HTTP {r.status_code} — {r.text[:400]}")
    rows = r.json()
    return rows[0] if rows else None


def upsert_settings(row: Dict[str, Any]) -> Dict[str, Any]:
    url, _ = _conf()
    r = requests.post(
        f"{url}/rest/v1/user_settings",
        headers=_headers({
            "Prefer": "return=representation,resolution=merge-duplicates",
        }),
        params={"on_conflict": "email"},
        data=json.dumps(row),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise DBError(f"Supabase settings upsert failed: HTTP {r.status_code} — {r.text[:400]}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else {}


def list_users_for_tick() -> List[Dict[str, Any]]:
    """Get all users with daily automation enabled."""
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/user_settings",
        headers=_headers(),
        params={
            "automation_enabled": "eq.true",
            "select": "*",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase list users failed: HTTP {r.status_code} â€” {r.text[:400]}")
    return r.json()


def get_settings_by_owner_token(owner_token: str) -> Optional[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/user_settings",
        headers=_headers(),
        params={"owner_token": f"eq.{owner_token}", "select": "*", "limit": "1"},
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase owner token lookup failed: HTTP {r.status_code} - {r.text[:400]}")
    rows = r.json()
    return rows[0] if rows else None


def list_allowed_domains(owner_email: str) -> List[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/owner_allowed_domains",
        headers=_headers(),
        params={"owner_email": f"eq.{owner_email}", "select": "*", "order": "created_at.asc"},
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase allowed domains list failed: HTTP {r.status_code} - {r.text[:400]}")
    return r.json()


def add_allowed_domain(owner_email: str, domain: str) -> Dict[str, Any]:
    url, _ = _conf()
    r = requests.post(
        f"{url}/rest/v1/owner_allowed_domains",
        headers=_headers({"Prefer": "return=representation,resolution=merge-duplicates"}),
        params={"on_conflict": "owner_email,domain"},
        data=json.dumps({"owner_email": owner_email, "domain": domain}),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise DBError(f"Supabase allowed domain upsert failed: HTTP {r.status_code} - {r.text[:400]}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else {}


def delete_allowed_domain(owner_email: str, domain: str) -> None:
    url, _ = _conf()
    r = requests.delete(
        f"{url}/rest/v1/owner_allowed_domains",
        headers=_headers(),
        params={"owner_email": f"eq.{owner_email}", "domain": f"eq.{domain}"},
        timeout=30,
    )
    if r.status_code not in (200, 204):
        raise DBError(f"Supabase allowed domain delete failed: HTTP {r.status_code} - {r.text[:400]}")


def list_subscribers(owner_email: str) -> List[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/newsletter_subscribers",
        headers=_headers(),
        params={
            "owner_email": f"eq.{owner_email}",
            "select": "id,subscriber_email,status,source,source_domain,created_at,confirmed_at,unsubscribed_at",
            "order": "created_at.desc",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase subscribers list failed: HTTP {r.status_code} - {r.text[:400]}")
    return r.json()


def list_active_subscribers(owner_email: str) -> List[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/newsletter_subscribers",
        headers=_headers(),
        params={
            "owner_email": f"eq.{owner_email}",
            "status": "eq.subscribed",
            "select": "*",
            "order": "created_at.asc",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase active subscribers list failed: HTTP {r.status_code} - {r.text[:400]}")
    return r.json()


def upsert_subscriber(row: Dict[str, Any]) -> Dict[str, Any]:
    url, _ = _conf()
    r = requests.post(
        f"{url}/rest/v1/newsletter_subscribers",
        headers=_headers({"Prefer": "return=representation,resolution=merge-duplicates"}),
        params={"on_conflict": "owner_email,subscriber_email"},
        data=json.dumps(row),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise DBError(f"Supabase subscriber upsert failed: HTTP {r.status_code} - {r.text[:400]}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else {}


def get_subscriber_by_token(token_field: str, token: str) -> Optional[Dict[str, Any]]:
    if token_field not in {"confirmation_token", "unsubscribe_token"}:
        raise DBError("Invalid subscriber token field.")
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/newsletter_subscribers",
        headers=_headers(),
        params={token_field: f"eq.{token}", "select": "*", "limit": "1"},
        timeout=30,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase subscriber token lookup failed: HTTP {r.status_code} - {r.text[:400]}")
    rows = r.json()
    return rows[0] if rows else None


def update_subscriber(subscriber_id: str, fields: Dict[str, Any]) -> None:
    url, _ = _conf()
    r = requests.patch(
        f"{url}/rest/v1/newsletter_subscribers",
        headers=_headers(),
        params={"id": f"eq.{subscriber_id}"},
        data=json.dumps(fields),
        timeout=30,
    )
    if r.status_code not in (200, 204):
        raise DBError(f"Supabase subscriber update failed: HTTP {r.status_code} - {r.text[:400]}")


def enqueue_send(row: Dict[str, Any]) -> Dict[str, Any]:
    url, _ = _conf()
    r = requests.post(
        f"{url}/rest/v1/newsletter_send_queue",
        headers=_headers({"Prefer": "return=representation"}),
        data=json.dumps(row),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise DBError(f"Supabase send queue insert failed: HTTP {r.status_code} - {r.text[:400]}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else {}


def update_send_queue(send_id: str, fields: Dict[str, Any]) -> None:
    url, _ = _conf()
    r = requests.patch(
        f"{url}/rest/v1/newsletter_send_queue",
        headers=_headers(),
        params={"id": f"eq.{send_id}"},
        data=json.dumps(fields),
        timeout=30,
    )
    if r.status_code not in (200, 204):
        raise DBError(f"Supabase send queue update failed: HTTP {r.status_code} - {r.text[:400]}")


def update_automation_status(email: str, fields: Dict[str, Any]) -> None:
    url, _ = _conf()
    r = requests.patch(
        f"{url}/rest/v1/user_settings",
        headers=_headers(),
        params={"email": f"eq.{email}"},
        data=json.dumps(fields),
        timeout=30,
    )
    if r.status_code not in (200, 204):
        raise DBError(f"Supabase automation status update failed: HTTP {r.status_code} â€” {r.text[:400]}")
