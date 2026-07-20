# -*- coding: utf-8 -*-
"""גירוד כותרות מפידי RSS ישראליים באמצעות feedparser, עם הקשחת רשת."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import feedparser

# UA דמוי-דפדפן — חלק מהמקורות (מעריב, כאן) חוסמים את ה-UA הדיפולטי של feedparser.
USER_AGENT = "Mozilla/5.0 (compatible; StrategicContentDashboard/1.0)"
FETCH_TIMEOUT = 12          # שניות לכל פיד — מונע תקיעה על פיד לא מגיב
MAX_BYTES = 4_000_000       # תקרת הורדה לפיד
MAX_SUMMARY = 600           # תקרת אורך לתקציר שנשלח ל-Claude
MAX_REDIRECTS = 5           # תקרת הפניות


@dataclass
class Headline:
    """כותרת בודדת שנשלפה מפיד."""
    title: str
    source: str
    link: str
    summary: str = ""
    published: str = ""

    @property
    def key(self) -> str:
        """מזהה יציב לכרטיס — משמש למפתחות ב-session_state."""
        return self.link or self.title


# ---------------------------------------------------------------------------
# הקשחת SSRF — פותרים כתובת פעם אחת, מוודאים שהיא ציבורית ומצמידים את החיבור
# אליה (pinning). כך אין TOCTOU/DNS-rebinding: אותה כתובת שנבדקה היא זו שמתחברים
# אליה בפועל, וכל יעד הפניה נבדק מחדש.
# ---------------------------------------------------------------------------
def _ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    # is_global פוסל גם טווחי shared address space (RFC 6598, למשל 100.64.0.0/10)
    # שאינם ניתובים גלובלית; is_reserved נשמר כחגורת-ביטחון לכתובות מיוחדות/ממופות.
    return ip.is_global and not ip.is_reserved


def _resolve_pinned_ips(host: str) -> list[str]:
    """
    פותר את המארח, מוודא שכל כתובות ה-IP ציבוריות, ומחזיר את כולן להצמדה.
    זורק ValueError אם אחת הכתובות פרטית/שמורה — מונע SSRF (כולל metadata).
    מחזיר את כל הכתובות (IPv4/IPv6) כדי לאפשר fallback בין כתובות תקינות.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"שגיאת DNS: {host}") from e
    if not infos:
        raise ValueError(f"אין כתובת ל-{host}")
    pinned: list[str] = []
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as e:
            raise ValueError(f"כתובת לא תקינה: {addr}") from e
        if not _ip_is_public(ip):
            raise ValueError("יעד לא מורשה (כתובת פרטית/שמורה)")
        if addr not in pinned:
            pinned.append(addr)
    return pinned


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """מתחבר לכתובת ה-IP שאומתה, אך שומר את שם המארח ל-SNI ולאימות התעודה."""
    def __init__(self, host: str, ip: str, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """מתחבר לכתובת ה-IP שאומתה, שומר את שם המארח ל-Host header."""
    def __init__(self, host: str, ip: str, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def _open_pinned(host: str, ips: list[str], port: int, is_https: bool,
                 path: str, context: ssl.SSLContext):
    """
    מנסה להתחבר לכל אחת מכתובות ה-IP שאומתו עד להצלחה (fallback IPv4/IPv6),
    כל חיבור מוצמד לכתובת ליטרלית כדי לשמר את הגנת ה-SSRF.
    מחזיר (conn, resp) של החיבור המוצלח, או זורק על כשל בכל הכתובות.
    """
    last_err: Exception | None = None
    for ip in ips:
        if is_https:
            conn = _PinnedHTTPSConnection(host, ip, port=port,
                                          timeout=FETCH_TIMEOUT, context=context)
        else:
            conn = _PinnedHTTPConnection(host, ip, port=port, timeout=FETCH_TIMEOUT)
        try:
            conn.request("GET", path,
                         headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
            return conn, conn.getresponse()
        except OSError as e:  # כתובת לא נגישה — ננסה את הבאה
            conn.close()
            last_err = e
    raise ValueError(f"החיבור נכשל בכל הכתובות של {host}") from last_err


def _fetch_bytes(url: str) -> bytes:
    """
    מוריד את גוף הפיד עם timeout, UA דמוי-דפדפן והצמדת-IP נגד SSRF.
    עוקב אחרי הפניות ידנית ובודק כל יעד מחדש.
    """
    context = ssl.create_default_context()
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"סכימה לא נתמכת: {parsed.scheme or 'ריק'}")
        host = parsed.hostname
        if not host:
            raise ValueError("כתובת ללא מארח")
        ips = _resolve_pinned_ips(host)  # אימות + הצמדה בו-זמנית (כל הכתובות)
        is_https = parsed.scheme == "https"
        # parsed.port is None ⟵ ברירת מחדל; port 0 מפורש נפסל (0 נחשב falsy).
        if parsed.port == 0:
            raise ValueError("פורט לא תקין: 0")
        port = parsed.port if parsed.port is not None else (443 if is_https else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        conn, resp = _open_pinned(host, ips, port, is_https, path, context)
        try:
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                resp.close()  # גוף ההפניה מיותר — סוגרים בלי לקרוא כדי לשמור על MAX_BYTES
                if not location:
                    raise ValueError("הפניה ללא כתובת יעד")
                url = urljoin(url, location)  # ⟵ נבדק בסבב הבא
                continue
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            return resp.read(MAX_BYTES)
        finally:
            conn.close()
    raise ValueError("יותר מדי הפניות")


def _source_name(feed: feedparser.FeedParserDict, url: str) -> str:
    """שם מקור קריא: כותרת הפיד, ואם אין — הדומיין."""
    title = feed.feed.get("title") if getattr(feed, "feed", None) else None
    if title:
        return str(title).strip()
    host = urlparse(url).netloc
    return host.replace("www.", "") or url


def fetch_feed(url: str, limit: int = 15) -> list[Headline]:
    """
    שולף עד `limit` כותרות מפיד בודד.
    זורק חריגה על כשל רשת/SSRF או על פיד שאינו ניתן לפענוח (למען הבחנה בין
    'נכשל' ל'ריק אך תקין' ברמת fetch_all).
    """
    raw = _fetch_bytes(url)                 # זורק על כשל רשת/timeout/SSRF
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:  # פיד פגום ללא תוכן ⟵ כשל אמיתי
        raise ValueError(f"פענוח נכשל: {parsed.get('bozo_exception', 'לא ידוע')}")

    source = _source_name(parsed, url)
    items: list[Headline] = []
    for entry in parsed.entries[:limit]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        summary = (entry.get("summary") or "").strip()
        if len(summary) > MAX_SUMMARY:      # תקרת אורך — מגן על גודל הבקשה ל-Claude
            summary = summary[:MAX_SUMMARY].rstrip() + "…"
        items.append(
            Headline(
                title=title,
                source=source,
                link=entry.get("link", ""),
                summary=summary,
                published=entry.get("published", ""),
            )
        )
    return items


def fetch_all(urls: list[str], per_feed: int = 15) -> tuple[list[Headline], list[str]]:
    """
    שולף מכל הפידים, מסיר כפילויות לפי כותרת.
    מחזיר (כותרות, נכשלו) — 'נכשלו' הם URL-ים שזרקו חריגה בלבד; פיד תקין אך
    ריק אינו נחשב כשל.
    """
    all_items: list[Headline] = []
    seen: set[str] = set()
    failed: list[str] = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        try:
            items = fetch_feed(url, per_feed)
        except Exception:
            failed.append(url)
            continue
        for item in items:
            norm = item.title.strip()
            if norm and norm not in seen:
                seen.add(norm)
                all_items.append(item)
    return all_items, failed
