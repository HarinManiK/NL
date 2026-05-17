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
    "facebook.com", "instagram.com", "youtube.com", "tiktok.com",
    "twitter.com", "x.com", "linkedin.com/in", "linkedin.com/company",
)

GENERIC_LINK_TEXT = {
    "", "read", "read more", "read full story", "learn more", "click here",
    "here", "view", "view more", "view all", "open", "open link", "link",
    "apply", "apply now", "register", "register now", "join now", "watch",
    "watch now", "see more", "explore", "explore now", "check it out",
    "continue reading", "full article", "more details", "details",
}

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


def _is_unresolved_tracking_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query
    if "moengage" in host or "emailclick" in path:
        return True
    if host.endswith("quora.com") and path.startswith("/qemail/tc"):
        return True
    if "list-manage.com" in host and "track" in path:
        return True
    if any(marker in path for marker in ("/click", "/track", "/redirect", "/r/")) and len(query) > 120:
        return True
    if len(query) > 350 and not any(f"{key}=" in query.lower() for key in REDIRECT_PARAM_KEYS):
        return True
    return False


def _is_generic_link_text(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in GENERIC_LINK_TEXT or len(normalized) <= 2


def _clean_label_text(value: str) -> str:
    value = html.unescape(value or "")
    value = strip_links_text(value)
    value = re.sub(r"\b(click here|read more|learn more|view all|apply now|register now)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" -:|\t\r\n")
    return value


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
            raw_normalized = _normalize_url(html.unescape((link.url or "").strip()))
            text = re.sub(r"\s+", " ", link.text or "").strip()
            context = re.sub(r"\s+", " ", link.context or "").strip()
            unresolved_tracking = bool(url and raw_normalized and url == raw_normalized and _is_unresolved_tracking_url(url))
            if unresolved_tracking and _is_generic_link_text(text) and len(_clean_label_text(context)) < 20:
                continue
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


def _link_label(link: dict) -> str:
    text = _clean_label_text(link.get("text") or "")
    context = _clean_label_text(link.get("nearby_text") or "")
    subject = _clean_label_text(link.get("source_subject") or "")
    if text and not _is_generic_link_text(text):
        label = text
    elif context:
        label = context
    elif subject:
        label = subject
    else:
        label = "this update"
    label = re.sub(r"\s+", " ", label).strip()
    if len(label) > 120:
        label = label[:117].rstrip() + "..."
    return label


def format_links_text(links: List[dict]) -> str:
    if not links:
        return ""
    lines = ["For more info:"]
    for link in links:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        label = _link_label(link)
        lines.append(f"- For more info about {label}, [click here]({url})")
    return "\n".join(lines)


def append_links_text(text: str, links: List[dict]) -> str:
    text = strip_links_text(remove_existing_link_sections_text(text or ""))
    block = format_links_text(links)
    if not block:
        return (text or "").strip()
    return f"{(text or '').strip()}\n\n{block}".strip()


def append_links_html(raw_html: str, links: List[dict]) -> str:
    raw_html = strip_links_html(remove_existing_link_sections_html(raw_html or ""))
    if not links:
        return (raw_html or "").strip()
    items = []
    for link in links:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        label = html.escape(_link_label(link))
        safe_url = html.escape(url, quote=True)
        items.append(
            "<li>"
            f"For more info about {label}, click here: "
            f'<a href="{safe_url}">click here</a>'
            "</li>"
        )
    if not items:
        return (raw_html or "").strip()
    section = (
        "<hr />"
        "<h2>For more info</h2>"
        "<ul>"
        + "".join(items)
        + "</ul>"
    )
    return f"{(raw_html or '').strip()}\n{section}".strip()


def strip_links_text(text: str) -> str:
    cleaned = html.unescape(text or "")

    def markdown_link_replacement(match: re.Match) -> str:
        label = re.sub(r"\s+", " ", match.group(1) or "").strip()
        return "" if _is_generic_link_text(label) else label

    def html_link_replacement(match: re.Match) -> str:
        label = re.sub(r"<[^>]+>", "", match.group(1) or "")
        label = re.sub(r"\s+", " ", html.unescape(label)).strip()
        return "" if _is_generic_link_text(label) else label

    cleaned = re.sub(r"\[([^\]]+)\]\((?:https?://|mailto:)[^)]+\)", markdown_link_replacement, cleaned)
    cleaned = re.sub(r"<a\b[^>]*>(.*?)</a>", html_link_replacement, cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"https?://[^\s<>()\"']+", "", cleaned)
    cleaned = re.sub(r"\b(?:www\.)[^\s<>()\"']+", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def remove_existing_link_sections_text(text: str) -> str:
    lines = (text or "").splitlines()
    cut_at = None
    pattern = re.compile(r"^\s*(for more info|useful links|source links|links)\s*:?\s*$", re.IGNORECASE)
    for idx, line in enumerate(lines):
        if pattern.match(line):
            cut_at = idx
    if cut_at is None:
        return text or ""
    return "\n".join(lines[:cut_at]).strip()


def strip_links_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for a in list(soup.find_all("a")):
        label = a.get_text(" ", strip=True)
        if _is_generic_link_text(label):
            a.decompose()
        else:
            a.replace_with(label)
    for text_node in list(soup.find_all(string=True)):
        cleaned = re.sub(r"https?://[^\s<>()\"']+", "", str(text_node))
        cleaned = re.sub(r"\b(?:www\.)[^\s<>()\"']+", "", cleaned)
        if cleaned != str(text_node):
            text_node.replace_with(cleaned)
    body = soup.body.decode_contents() if soup.body else str(soup)
    return body.strip()


def remove_existing_link_sections_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    headings = soup.find_all(["h1", "h2", "h3", "h4", "p"])
    for heading in headings:
        text = heading.get_text(" ", strip=True).lower()
        if text in {"for more info", "useful links", "source links", "links"}:
            for sibling in list(heading.find_next_siblings()):
                if sibling.name in {"h1", "h2", "h3"}:
                    break
                sibling.decompose()
            heading.decompose()
    body = soup.body.decode_contents() if soup.body else str(soup)
    return body.strip()


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
