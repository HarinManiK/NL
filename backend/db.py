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


def get_run(run_id: str, email: str) -> Optional[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/runs?id=eq.{run_id}&email=eq.{email}&select=*",
        headers=_headers(),
        timeout=10,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase fetch failed: HTTP {r.status_code}")
    data = r.json()
    return data[0] if data else None


def get_public_run(run_id: str) -> Optional[Dict[str, Any]]:
    url, _ = _conf()
    r = requests.get(
        f"{url}/rest/v1/runs?id=eq.{run_id}&select=*",
        headers=_headers(),
        timeout=10,
    )
    if r.status_code != 200:
        raise DBError(f"Supabase fetch failed: HTTP {r.status_code}")
    data = r.json()
    return data[0] if data else None


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
