from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse


SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def validate_source_id(value: str) -> str:
    if not SOURCE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid source id: {value!r}")
    return value


def is_safe_source_id(value: str) -> bool:
    return bool(SOURCE_ID_PATTERN.fullmatch(value or ""))


def safe_https_url(
    value: str,
    *,
    allowed_hosts: set[str] | None = None,
    allowed_suffixes: set[str] | None = None,
) -> str:
    value = (value or "").strip()
    if not value or any(ord(character) < 32 for character in value):
        return ""

    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return ""

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if allowed_hosts and hostname in {host.lower().rstrip(".") for host in allowed_hosts}:
        return value
    for suffix in allowed_suffixes or set():
        suffix = suffix.lower().rstrip(".")
        if hostname == suffix or hostname.endswith(f".{suffix}"):
            return value
    return ""


def html_attr(value: str) -> str:
    return html.escape(value, quote=True)


def html_text(value: str) -> str:
    return html.escape(value or "", quote=False)


def image_html(url: str, *, allowed_hosts: set[str] | None = None, allowed_suffixes: set[str] | None = None) -> str:
    safe_url = safe_https_url(url, allowed_hosts=allowed_hosts, allowed_suffixes=allowed_suffixes)
    if not safe_url:
        return ""
    return f'<img src="{html_attr(safe_url)}" alt="" />'


def ensure_rss_channel(rss: str) -> str:
    try:
        root = ET.fromstring(rss)
    except ET.ParseError as error:
        raise ValueError("Feed content is not valid XML") from error
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Feed content is not an RSS channel")
    return rss


def limit_rss_items(rss: str, max_items: int) -> str:
    """Keep generated feed payloads bounded without retaining article history."""
    if max_items < 1:
        raise ValueError("RSS item limit must be positive")
    root = ET.fromstring(ensure_rss_channel(rss))
    channel = root.find("channel")
    if channel is None:
        raise ValueError("Feed content is not an RSS channel")
    items = channel.findall("item")
    for item in items[max_items:]:
        channel.remove(item)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def parse_internet_date(value: str) -> datetime:
    value = (value or "").strip()
    if not value:
        raise ValueError("Date value is empty")
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        pass

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"Unsupported internet date: {value}") from error
