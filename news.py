# -*- coding: utf-8 -*-
"""גירוד כותרות מפידי RSS ישראליים באמצעות feedparser, עם הקשחת רשת."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

import feedparser

# UA דמוי-דפדפן — חלק מהמקורות (מעריב, כאן) חוסמים את ה-UA הדיפולטי של feedparser.
USER_AGENT = "Mozilla/5.0 (compatible; StrategicContentDashboard/1.0)"
FETCH_TIMEOUT = 12          # שניות לכל פיד — מונע תקיעה על פיד לא מגיב
MAX_BYTES = 4_000_000       # תקרת הורדה לפיד
MAX_SUMMARY = 600           # תקרת אורך לתקציר שנשלח ל-Claude


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
# הקשחת SSRF — חוסמים יעדים פרטיים/שמורים גם בהפניות (redirects)
# ---------------------------------------------------------------------------
def _host_is_public(host: str) -> bool:
    """True רק אם כל כתובות ה-IP של המארח ציבוריות (מונע SSRF פנימי/metadata)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _validate_url(url: str) -> None:
    """מוודא סכימת http(s) ומארח ציבורי. זורק ValueError אחרת."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"סכימה לא נתמכת: {parsed.scheme or 'ריק'}")
    if not parsed.hostname or not _host_is_public(parsed.hostname):
        raise ValueError("יעד לא מורשה (כתובת פרטית/שמורה)")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """מוודא כל יעד הפניה מול אותם כללי SSRF."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)  # זורק ⟵ עוצר את ההפניה
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirectHandler())


def _fetch_bytes(url: str) -> bytes:
    """מוריד את גוף הפיד עם timeout, UA דמוי-דפדפן ובדיקת SSRF (כולל הפניות)."""
    _validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _opener.open(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(MAX_BYTES)


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
