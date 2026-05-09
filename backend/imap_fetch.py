"""IMAP fetch + body extraction. Generator version yields progress events."""
from __future__ import annotations

import email
import imaplib
import socket
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Iterator, List, Optional, Tuple

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


@dataclass
class MailRecord:
    msg_id: str
    subject: str
    sender_name: str
    sender_email: str
    date: datetime
    folder: str
    body: str

    def header_block(self) -> str:
        return (
            f"From   : {self.sender_name} <{self.sender_email}>\n"
            f"Subject: {self.subject}\n"
            f"Date   : {self.date.astimezone().isoformat()}\n"
        )


def _decode_h(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value)


def _decode_payload(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset()
        for cs in [charset, "utf-8", "latin-1", "cp1252"]:
            if not cs:
                continue
            try:
                return payload.decode(cs, errors="replace")
            except Exception:
                continue
        return payload.decode("utf-8", errors="replace")
    except Exception:
        return ""


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1
        elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n")

    def handle_data(self, data):
        if self._skip == 0:
            self.out.append(data)


def _html_to_text(h: str) -> str:
    if HAS_BS4:
        soup = BeautifulSoup(h, "html.parser")
        for s in soup(["script", "style"]):
            s.decompose()
        t = soup.get_text("\n")
    else:
        p = _Stripper()
        try:
            p.feed(h)
        except Exception:
            pass
        t = "".join(p.out)
    t = unescape(t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return t.strip()


def _extract_body(msg: Message) -> str:
    text_parts: List[str] = []
    html_parts: List[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain":
                text_parts.append(_decode_payload(part))
            elif ctype == "text/html":
                html_parts.append(_decode_payload(part))
    else:
        payload = _decode_payload(msg)
        if msg.get_content_type() == "text/html":
            html_parts.append(payload)
        else:
            text_parts.append(payload)

    text = "\n".join(t for t in text_parts if t).strip()
    if not text and html_parts:
        text = _html_to_text("\n".join(html_parts))
    return text.strip()


SKIP_FOLDER_PATTERNS = (
    "[gmail]/spam", "[gmail]/trash", "[gmail]/drafts", "[gmail]/sent mail",
    "spam", "trash", "junk", "deleted", "drafts", "sent",
)


def _skip_folder(name: str) -> bool:
    n = name.lower().strip('"').strip()
    return any(n == p or n.endswith("/" + p) for p in SKIP_FOLDER_PATTERNS)


def _list_folders(imap: imaplib.IMAP4_SSL) -> List[str]:
    try:
        status, raw_list = imap.list()
        if status != "OK" or not raw_list:
            return ["INBOX"]
    except Exception:
        return ["INBOX"]

    folders: List[str] = []
    seen: set = set()
    for item in raw_list:
        if not item:
            continue
        raw = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else item
        m = re.match(r'\(([^)]*)\)\s+"[^"]*"\s+"?([^"]+)"?', raw.strip())
        if not m:
            continue
        flags_str = m.group(1).lower()
        folder_name = m.group(2).strip()
        if r"\noselect" in flags_str:
            continue
        if folder_name in seen:
            continue
        seen.add(folder_name)
        if _skip_folder(folder_name):
            continue
        folders.append(folder_name)

    if "INBOX" in folders:
        folders.remove("INBOX")
    folders.insert(0, "INBOX")
    return folders


def _parse_msgid(meta: bytes) -> Optional[str]:
    if not meta:
        return None
    m = re.search(rb"X-GM-MSGID\s+(\d+)", meta, re.IGNORECASE)
    return m.group(1).decode() if m else None


def _parse_internaldate(meta: bytes) -> Optional[datetime]:
    if not meta:
        return None
    m = re.search(rb'INTERNALDATE\s+"([^"]+)"', meta)
    if not m:
        return None
    try:
        return parsedate_to_datetime(m.group(1).decode())
    except Exception:
        return None


def verify_imap(email_id: str, app_password: str, imap_server: str = "imap.gmail.com",
                imap_port: int = 993) -> None:
    """Raise on failure with a clear message; return None on success."""
    try:
        imap = imaplib.IMAP4_SSL(imap_server, imap_port, timeout=20)
    except Exception as e:
        raise RuntimeError(f"Could not reach IMAP server {imap_server}:{imap_port} — {e}") from e
    try:
        imap.login(email_id, app_password)
    except imaplib.IMAP4.error as e:
        raise RuntimeError(
            f"IMAP login rejected by server. Check the email address and the 16-character "
            f"Gmail app password (not your normal password). Server said: {e}"
        ) from e
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def fetch_recent_mails_stream(
    email_id: str, app_password: str, hours_back: int,
    imap_server: str = "imap.gmail.com", imap_port: int = 993,
) -> Iterator[Tuple[str, object]]:
    """Generator. Yields (event_type, payload) tuples:
        ("status", str)            — human-readable progress message
        ("folder", {name, count})  — switched to a folder, found N messages in window
        ("mail",   MailRecord)     — successfully extracted one mail
        ("done",   list[MailRecord]) — final list, end of stream
    Raises RuntimeError on connection/auth failure with a clear message.
    """
    yield ("status", f"Connecting to {imap_server}…")
    try:
        imap = imaplib.IMAP4_SSL(imap_server, imap_port, timeout=60)
    except Exception as e:
        raise RuntimeError(f"Could not reach IMAP server {imap_server}:{imap_port} — {e}") from e

    records: List[MailRecord] = []
    try:
        try:
            imap.login(email_id, app_password)
        except imaplib.IMAP4.error as e:
            raise RuntimeError(
                f"IMAP login rejected. Check the email address and 16-char Gmail app password. "
                f"Server said: {e}"
            ) from e
        yield ("status", "Logged in. Listing folders…")

        folders = _list_folders(imap)
        yield ("status", f"Found {len(folders)} folder(s) to scan.")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        since_str = (datetime.now() - timedelta(hours=hours_back)).strftime("%d-%b-%Y")
        is_gmail = "gmail" in imap_server.lower()
        fetch_spec = "(X-GM-MSGID INTERNALDATE RFC822)" if is_gmail else "(INTERNALDATE RFC822)"

        seen_ids: set = set()

        for folder in folders:
            try:
                status, _ = imap.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    continue
            except Exception:
                continue

            try:
                status, data = imap.uid("SEARCH", None, f'(SINCE "{since_str}")')
                if status != "OK" or not data or not data[0]:
                    yield ("folder", {"name": folder, "count": 0})
                    continue
                uids = data[0].split()
            except Exception:
                continue

            yield ("folder", {"name": folder, "count": len(uids)})

            # Batch UIDs into chunks of 10 for faster fetching
            uid_chunks = [uids[i:i + 10] for i in range(0, len(uids), 10)]

            for chunk in uid_chunks:
                uids_str = ",".join(u.decode() if isinstance(u, bytes) else str(u) for u in chunk)
                try:
                    status, msg_data_list = imap.uid("FETCH", uids_str, fetch_spec)
                    if status != "OK" or not msg_data_list:
                        continue

                    # msg_data_list is a list of tuples/bytes. Each message usually has one tuple.
                    # We need to group them correctly.
                    current_raw_bytes = None
                    current_meta_bytes = None

                    for piece in msg_data_list:
                        if isinstance(piece, tuple) and len(piece) >= 2:
                            current_meta_bytes = piece[0] or b""
                            current_raw_bytes = piece[1]
                            
                            # Process this message
                            if not current_raw_bytes:
                                continue

                            msg = email.message_from_bytes(current_raw_bytes)
                            if is_gmail:
                                msg_unique = _parse_msgid(current_meta_bytes) or (msg.get("Message-ID") or "").strip()
                            else:
                                msg_unique = (msg.get("Message-ID") or "").strip()
                            
                            if msg_unique:
                                if msg_unique in seen_ids:
                                    continue
                                seen_ids.add(msg_unique)

                            msg_dt: Optional[datetime] = None
                            if msg.get("Date"):
                                try:
                                    p = parsedate_to_datetime(msg["Date"])
                                    if p:
                                        msg_dt = p.replace(tzinfo=timezone.utc) if p.tzinfo is None else p
                                except Exception:
                                    pass
                            if msg_dt is None:
                                msg_dt = _parse_internaldate(current_meta_bytes)
                            if msg_dt is None or msg_dt < cutoff:
                                continue

                            subject = _decode_h(msg.get("Subject")) or "(no subject)"
                            from_hdr = _decode_h(msg.get("From"))
                            sender_name, sender_email_addr = parseaddr(from_hdr) if from_hdr else ("", "")
                            sender_name = sender_name or sender_email_addr or "(unknown)"
                            body_full = _extract_body(msg)
                            if not body_full:
                                continue

                            rec = MailRecord(
                                msg_id=msg_unique or f"{folder}:batch",
                                subject=subject,
                                sender_name=sender_name,
                                sender_email=sender_email_addr,
                                date=msg_dt,
                                folder=folder,
                                body=body_full,
                            )
                            records.append(rec)
                            yield ("mail", rec)
                except (imaplib.IMAP4.abort, socket.timeout, OSError) as e:
                    raise RuntimeError(f"IMAP connection dropped mid-fetch: {e}") from e
                except Exception:
                    continue
    finally:
        for fn in (imap.close, imap.logout):
            try:
                fn()
            except Exception:
                pass

    yield ("done", records)


def fetch_recent_mails(
    email_id: str, app_password: str, hours_back: int,
    imap_server: str = "imap.gmail.com", imap_port: int = 993,
) -> List[MailRecord]:
    """Synchronous version of the stream-fetch."""
    for evt_type, payload in fetch_recent_mails_stream(
        email_id, app_password, hours_back, imap_server, imap_port
    ):
        if evt_type == "done":
            return payload
    return []
