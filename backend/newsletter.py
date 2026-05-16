from __future__ import annotations

import html
import re
import smtplib
import ssl
from dataclasses import asdict
from email.message import EmailMessage
from typing import Iterable, List, Optional
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from imap_fetch import MailRecord


JUNK_LINK_WORDS = (
    "unsubscribe", "manage preferences", "email preferences", "privacy policy",
    "terms of use", "terms and conditions", "view in browser", "view online",
    "update preferences", "subscription preferences", "preferences center",
    "forward to a friend", "facebook", "twitter", "x.com", "linkedin.com/company",
    "instagram", "youtube", "tiktok", "discord", "slack", "copyright",
)

TRACKING_QUERY_KEYS = (
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "mc_cid", "mc_eid", "fbclid", "gclid", "igshid", "ref",
)

REDIRECT_PARAM_KEYS = (
    "url", "u", "target", "dest", "destination", "redirect", "redirect_url",
    "r", "to", "link", "link_url", "cta_url",
)


def _unwrap_redirect_url(raw_url: str) -> str:
    url = html.unescape((raw_url or "").strip())
    if not url:
        return ""
    url = unquote(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""

    params = parse_qs(parsed.query)
    for key in REDIRECT_PARAM_KEYS:
        for value in params.get(key, []):
            candidate = unquote(html.unescape(value or "")).strip()
            if candidate.startswith(("http://", "https://")):
                return _normalize_url(candidate)
    return _normalize_url(url)


def _normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""

    query_pairs = parse_qs(parsed.query, keep_blank_values=True)
    clean_pairs = {
        key: vals for key, vals in query_pairs.items()
        if key.lower() not in TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
    }
    clean_query = urlencode(clean_pairs, doseq=True)
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "",
        "",
        clean_query,
        "",
    ))


def _is_useful_link(url: str, text: str, context: str) -> bool:
    haystack = f"{url} {text} {context}".lower()
    if any(word in haystack for word in JUNK_LINK_WORDS):
        return False
    if re.search(r"\.(png|jpg|jpeg|gif|webp|svg|ico)(\?|$)", url, re.IGNORECASE):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    if not text.strip() and len((context or "").strip()) < 12:
        return False
    return True


def extract_useful_links(records: Iterable[MailRecord]) -> List[dict]:
    useful: List[dict] = []
    seen: set[str] = set()
    for rec in records:
        for link in rec.links:
            url = _unwrap_redirect_url(link.url)
            text = re.sub(r"\s+", " ", link.text or "").strip()
            context = re.sub(r"\s+", " ", link.context or "").strip()
            if not url or not _is_useful_link(url, text, context):
                continue
            key = url.lower()
            if key in seen:
                continue
            seen.add(key)
            useful.append({
                "url": url,
                "text": text[:300],
                "source_sender": rec.sender_name,
                "source_subject": rec.subject,
                "nearby_text": context[:500],
            })
    return useful


def useful_links_block(links: List[dict]) -> str:
    if not links:
        return ""
    lines = ["\n\nUseful links extracted from the filtered newsletter emails:"]
    for i, link in enumerate(links, 1):
        label = link.get("text") or link.get("nearby_text") or link.get("source_subject") or "Related link"
        lines.append(
            f"{i}. {label}\n"
            f"   URL: {link.get('url', '')}\n"
            f"   Source: {link.get('source_sender', '')} - {link.get('source_subject', '')}"
        )
    return "\n".join(lines)


def sanitize_newsletter_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    allowed_tags = {
        "html", "body", "main", "section", "article", "header", "footer",
        "h1", "h2", "h3", "p", "br", "hr", "ul", "ol", "li",
        "strong", "b", "em", "i", "a", "blockquote", "div", "span",
    }
    allowed_attrs = {
        "a": {"href", "title"},
        "div": {"style"},
        "span": {"style"},
        "p": {"style"},
        "h1": {"style"},
        "h2": {"style"},
        "h3": {"style"},
        "ul": {"style"},
        "ol": {"style"},
        "li": {"style"},
    }
    for tag in list(soup.find_all(True)):
        if tag.name in ("script", "style", "iframe", "object", "embed", "form", "input", "button"):
            tag.decompose()
            continue
        if tag.name not in allowed_tags:
            tag.unwrap()
            continue
        allowed = allowed_attrs.get(tag.name, set())
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag.attrs[attr]
        if tag.name == "a":
            href = (tag.get("href") or "").strip()
            if not href.startswith(("http://", "https://", "mailto:")):
                tag.unwrap()
            else:
                tag["target"] = "_blank"
                tag["rel"] = "noopener noreferrer"
    body = soup.body.decode_contents() if soup.body else str(soup)
    return body.strip()


def html_to_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    return soup.get_text("\n", strip=True)


def append_unsubscribe_footer(raw_html: str, unsubscribe_url: str) -> str:
    footer = (
        '<hr style="border:0;border-top:1px solid #e5e7eb;margin:24px 0;" />'
        '<p style="font-size:12px;color:#71717a;line-height:1.5;">'
        f'You are receiving this because you subscribed to this newsletter. '
        f'<a href="{html.escape(unsubscribe_url)}">Unsubscribe</a>.'
        '</p>'
    )
    return f"{raw_html}\n{footer}"


def send_html_email_smtp(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    sender_email: str,
    recipient_email: str,
    subject: str,
    html_body: str,
    sender_name: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> None:
    msg = EmailMessage()
    from_header = f"{sender_name} <{sender_email}>" if sender_name else sender_email
    msg["From"] = from_header
    msg["To"] = recipient_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(html_to_text(html_body) or subject)
    msg.add_alternative(html_body, subtype="html")

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(host, port, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(username, password)
        smtp.send_message(msg)
