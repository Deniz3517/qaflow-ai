"""Fake-mail / test mailbox bridge for AI-generated E2E tests.

Production sites have flows that send real emails: signup verification,
password reset, magic links. To test those flows in CI we need a way for
the test to:
  1. trigger the flow (UI submit → backend sends email)
  2. read the email from a known inbox
  3. extract the verification link / OTP
  4. continue the flow

This module provides a SINGLE abstract Bridge interface plus 3 concrete
implementations:

    memory   in-process fake — the backend can shovel an email into a
             queue keyed by recipient address; useful for local demos +
             integration tests of QAFLOW itself (no real SMTP).

    mailosaur a paid 3rd-party service (api.mailosaur.com). Reads
             MAILOSAUR_API_KEY / MAILOSAUR_SERVER from env.

    imap     generic IMAP over TLS. Reads IMAP_HOST / IMAP_USER /
             IMAP_PASS from env. Polls INBOX for messages matching a
             To: header.

The e2e prompt's mailbox helper file (`support/mailbox.{ext}`) calls
back to QAFLOW endpoints under `/api/ai/fakemail/*` — so test code never
needs Mailosaur credentials baked into specs.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TestMail:
    to: str
    from_addr: str
    subject: str
    text_body: str
    html_body: str
    received_at: float = field(default_factory=time.time)
    headers: dict = field(default_factory=dict)

    def extract_links(self) -> list[str]:
        """Pull http(s) URLs from both text and html bodies."""
        seen: set[str] = set()
        out: list[str] = []
        for body in (self.text_body, self.html_body):
            for u in re.findall(r"https?://[^\s\"'<>)\]]+", body or ""):
                clean = u.rstrip(".,;:!?")
                if clean not in seen:
                    seen.add(clean)
                    out.append(clean)
        return out

    def extract_otp(self) -> str | None:
        """Best-effort OTP extraction — 4-8 digit consecutive number."""
        body = (self.text_body or "") + " " + (self.html_body or "")
        m = re.search(r"\b(\d{4,8})\b", body)
        return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Bridge interface
# ---------------------------------------------------------------------------

class Bridge:
    name: str

    def deliver(self, mail: TestMail) -> None:
        """In-memory bridges only. Real bridges ignore."""
        raise NotImplementedError

    def peek(self, to: str, timeout_s: float = 10.0,
             subject_contains: str | None = None) -> TestMail | None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Memory bridge
# ---------------------------------------------------------------------------

class MemoryBridge(Bridge):
    """Per-address FIFO queue. Bounded to 100 messages per address."""
    name = "memory"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mail: dict[str, deque[TestMail]] = defaultdict(lambda: deque(maxlen=100))

    def deliver(self, mail: TestMail) -> None:
        with self._lock:
            self._mail[mail.to.lower()].append(mail)

    def peek(self, to: str, timeout_s: float = 10.0,
             subject_contains: str | None = None) -> TestMail | None:
        deadline = time.time() + max(0.0, timeout_s)
        key = to.lower()
        while True:
            with self._lock:
                bucket = self._mail.get(key)
                if bucket:
                    for m in list(bucket):
                        if subject_contains and subject_contains.lower() not in m.subject.lower():
                            continue
                        bucket.remove(m)
                        return m
            if time.time() >= deadline:
                return None
            time.sleep(0.25)

    def all_for(self, to: str) -> list[TestMail]:
        """Snapshot of every message currently queued for `to` (no consume)."""
        with self._lock:
            return list(self._mail.get(to.lower()) or ())


# ---------------------------------------------------------------------------
# Mailosaur bridge — reads MAILOSAUR_API_KEY / MAILOSAUR_SERVER from env
# ---------------------------------------------------------------------------

class MailosaurBridge(Bridge):
    name = "mailosaur"

    def __init__(self,
                 api_key: str | None = None,
                 server_id: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("MAILOSAUR_API_KEY", "")
        self.server_id = server_id or os.environ.get("MAILOSAUR_SERVER", "")
        if not self.api_key or not self.server_id:
            raise RuntimeError(
                "Mailosaur bridge requires MAILOSAUR_API_KEY + MAILOSAUR_SERVER",
            )

    def deliver(self, mail: TestMail) -> None:  # noqa: ARG002
        raise RuntimeError("MailosaurBridge.deliver — real provider, not supported")

    def peek(self, to: str, timeout_s: float = 10.0,
             subject_contains: str | None = None) -> TestMail | None:
        deadline = time.time() + max(0.0, timeout_s)
        while True:
            try:
                msg = self._poll_once(to, subject_contains)
                if msg:
                    return msg
            except Exception:
                pass
            if time.time() >= deadline:
                return None
            time.sleep(1.0)

    def _poll_once(self, to: str, subject_contains: str | None) -> TestMail | None:
        params = urllib.parse.urlencode({
            "server": self.server_id,
            "sentTo": to,
        })
        url = f"https://mailosaur.com/api/messages?{params}"
        req = urllib.request.Request(url, headers={
            "Authorization": "Basic " + _basic_auth(self.api_key, ""),
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            import json
            data = json.loads(resp.read())
        items = data.get("items") or []
        for it in items:
            subject = it.get("subject") or ""
            if subject_contains and subject_contains.lower() not in subject.lower():
                continue
            msg_id = it.get("id")
            if not msg_id:
                continue
            full_req = urllib.request.Request(
                f"https://mailosaur.com/api/messages/{msg_id}",
                headers={"Authorization": "Basic " + _basic_auth(self.api_key, "")},
            )
            with urllib.request.urlopen(full_req, timeout=8) as r2:
                import json as _json
                full = _json.loads(r2.read())
            return TestMail(
                to=to,
                from_addr=((full.get("from") or [{}])[0].get("email") or ""),
                subject=full.get("subject") or "",
                text_body=(full.get("text") or {}).get("body") or "",
                html_body=(full.get("html") or {}).get("body") or "",
                headers={h.get("field"): h.get("value") for h in (full.get("metadata", {}).get("headers") or [])},
            )
        return None


def _basic_auth(user: str, pw: str) -> str:
    import base64
    raw = f"{user}:{pw}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------------------
# IMAP bridge — generic test-mail server (smtp4dev, mailpit, fastmail, ...)
# ---------------------------------------------------------------------------

class IMAPBridge(Bridge):
    name = "imap"

    def __init__(self,
                 host: str | None = None,
                 user: str | None = None,
                 password: str | None = None,
                 mailbox: str = "INBOX",
                 use_ssl: bool = True,
                 port: int | None = None) -> None:
        self.host = host or os.environ.get("IMAP_HOST", "")
        self.user = user or os.environ.get("IMAP_USER", "")
        self.password = password or os.environ.get("IMAP_PASS", "")
        self.mailbox = mailbox
        self.use_ssl = use_ssl
        self.port = port or (993 if use_ssl else 143)
        if not (self.host and self.user and self.password):
            raise RuntimeError(
                "IMAP bridge requires IMAP_HOST / IMAP_USER / IMAP_PASS",
            )

    def deliver(self, mail: TestMail) -> None:  # noqa: ARG002
        raise RuntimeError("IMAPBridge.deliver — real provider, not supported")

    def peek(self, to: str, timeout_s: float = 10.0,
             subject_contains: str | None = None) -> TestMail | None:
        deadline = time.time() + max(0.0, timeout_s)
        while True:
            try:
                msg = self._poll_once(to, subject_contains)
                if msg:
                    return msg
            except Exception:
                pass
            if time.time() >= deadline:
                return None
            time.sleep(2.0)

    def _poll_once(self, to: str, subject_contains: str | None) -> TestMail | None:
        cls = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4
        conn = cls(self.host, self.port)
        try:
            conn.login(self.user, self.password)
            conn.select(self.mailbox)
            criteria = f'(TO "{to}")'
            if subject_contains:
                criteria = f'({criteria} SUBJECT "{subject_contains}")'
            typ, data = conn.search(None, criteria)
            if typ != "OK":
                return None
            ids = (data[0] or b"").split()
            if not ids:
                return None
            latest_id = ids[-1]
            typ, msg_data = conn.fetch(latest_id, "(RFC822)")
            if typ != "OK" or not msg_data:
                return None
            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
            if not raw:
                return None
            msg = email.message_from_bytes(raw)
            text_body, html_body = _imap_extract_bodies(msg)
            return TestMail(
                to=to,
                from_addr=msg.get("From") or "",
                subject=msg.get("Subject") or "",
                text_body=text_body, html_body=html_body,
                headers={k: v for k, v in msg.items()},
            )
        finally:
            try: conn.close()
            except Exception: pass
            try: conn.logout()
            except Exception: pass


def _imap_extract_bodies(msg) -> tuple[str, str]:
    text_body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and not text_body:
                try: text_body = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace")
                except Exception: pass
            elif ctype == "text/html" and not html_body:
                try: html_body = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace")
                except Exception: pass
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            body = ""
        if msg.get_content_type() == "text/html":
            html_body = body
        else:
            text_body = body
    return text_body, html_body


# ---------------------------------------------------------------------------
# Module-level singleton + factory
# ---------------------------------------------------------------------------

_BRIDGES: dict[str, Bridge] = {}


def get_bridge(name: str = "memory") -> Bridge:
    """Return a singleton bridge by name. Lazy-instantiates real ones."""
    if name in _BRIDGES:
        return _BRIDGES[name]
    if name == "memory":
        b = MemoryBridge()
    elif name == "mailosaur":
        b = MailosaurBridge()
    elif name == "imap":
        b = IMAPBridge()
    else:
        raise ValueError(f"unknown fakemail bridge: {name}")
    _BRIDGES[name] = b
    return b


def discover_default_provider() -> str:
    """Inspect env to pick the highest-quality provider available."""
    if os.environ.get("MAILOSAUR_API_KEY") and os.environ.get("MAILOSAUR_SERVER"):
        return "mailosaur"
    if os.environ.get("IMAP_HOST") and os.environ.get("IMAP_USER"):
        return "imap"
    return "memory"


def provision_test_users(
    role_specs: Iterable[dict],
    domain: str | None = None,
) -> list[dict]:
    """Build a `test_users` list ready to inject into the discovery prompt.

    Each role_spec is {role, password?}. Email address is auto-generated
    using a stable UUID seeded from the role name (so re-runs use the
    same accounts and don't pollute mailboxes).
    """
    import uuid

    if not domain:
        provider = discover_default_provider()
        if provider == "mailosaur":
            domain = f"{os.environ.get('MAILOSAUR_SERVER')}.mailosaur.net"
        elif provider == "imap":
            domain = os.environ.get("IMAP_DOMAIN") or "fakemail.local"
        else:
            domain = "fakemail.local"

    out: list[dict] = []
    for spec in role_specs:
        role = (spec.get("role") or "viewer").lower()
        # Stable per-role addresses — uuid5 of role under a project namespace.
        uid = uuid.uuid5(uuid.NAMESPACE_OID, f"qaflow-{role}").hex[:12]
        out.append({
            "role": role,
            "email": f"qaflow+{role}-{uid}@{domain}",
            "password": spec.get("password") or f"T3st!{role.title()}-{uid[:8]}",
            "inbox_url": f"/api/ai/fakemail/peek?to=qaflow+{role}-{uid}@{domain}",
        })
    return out
