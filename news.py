# -*- coding: utf-8 -*-
"""גירוד כותרות מפידי RSS ישראליים באמצעות feedparser."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import feedparser


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


def _source_name(feed: feedparser.FeedParserDict, url: str) -> str:
    """שם מקור קריא: כותרת הפיד, ואם אין — הדומיין."""
    title = feed.feed.get("title") if getattr(feed, "feed", None) else None
    if title:
        return str(title).strip()
    host = urlparse(url).netloc
    return host.replace("www.", "") or url


def fetch_feed(url: str, limit: int = 15) -> list[Headline]:
    """שולף עד `limit` כותרות מפיד בודד. מחזיר רשימה ריקה בכשל."""
    parsed = feedparser.parse(url)
    source = _source_name(parsed, url)
    items: list[Headline] = []
    for entry in parsed.entries[:limit]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        items.append(
            Headline(
                title=title,
                source=source,
                link=entry.get("link", ""),
                summary=(entry.get("summary") or "").strip(),
                published=entry.get("published", ""),
            )
        )
    return items


def fetch_all(urls: list[str], per_feed: int = 15) -> tuple[list[Headline], list[str]]:
    """
    שולף מכל הפידים, מסיר כפילויות לפי כותרת.
    מחזיר (כותרות, שגיאות) — שגיאות הן רשימת URL-ים שנכשלו.
    """
    all_items: list[Headline] = []
    seen: set[str] = set()
    errors: list[str] = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        try:
            items = fetch_feed(url, per_feed)
            if not items:
                errors.append(url)
                continue
            for item in items:
                norm = item.title.strip()
                if norm and norm not in seen:
                    seen.add(norm)
                    all_items.append(item)
        except Exception:
            errors.append(url)
    return all_items, errors
