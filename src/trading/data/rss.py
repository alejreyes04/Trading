"""Titulares de noticias por RSS (sin dependencias externas)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from trading.net import HttpClient


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def headlines(feeds: list[str], hours: int = 36) -> list[dict]:
    http = HttpClient(rate=2.0, timeout=20)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items: list[dict] = []
    for url in feeds:
        try:
            root = ET.fromstring(http.get(url).content)
        except Exception:
            continue
        source = urlparse(url).netloc.replace("www.", "")
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            published = _parse_date(item.findtext("pubDate"))
            if not title or (published and published < cutoff):
                continue
            items.append({"source": source, "title": title, "link": (item.findtext("link") or "").strip(),
                          "published": published.isoformat() if published else None})
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    return items


def mentions(items: list[dict], terms: list[str]) -> list[dict]:
    """Titulares que mencionan alguno de los términos (símbolo o nombre), como palabra completa."""
    import re

    patterns = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in terms if len(t) >= 3]
    return [it for it in items if any(p.search(it["title"]) for p in patterns)]
