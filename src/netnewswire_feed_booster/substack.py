from __future__ import annotations

import json
import re
from html import unescape
from typing import Any

from .feed_store import Source, source_id_from_title


def parse_substack_profile_html(html: str, profile: str, group: str) -> list[Source]:
    preloads = extract_substack_preloads(html)
    sources: list[Source] = []
    for subscription in preloads.get("profile", {}).get("subscriptions", []):
        publication = subscription.get("publication") or {}
        title = publication.get("name") or publication.get("subdomain")
        if not title:
            continue
        site_url = substack_publication_url(publication)
        sources.append(
            Source(
                id=source_id_from_title(title, site_url),
                title=title,
                feed_url=f"{site_url}/feed",
                site_url=site_url,
                kind="substack",
                profiles=[profile],
                groups=[group],
                source="substack-profile",
            )
        )
    return sources


def extract_substack_preloads(html: str) -> dict[str, Any]:
    match = re.search(r"window\._preloads\s*=\s*JSON\.parse\(\"(?P<payload>.*?)\"\)</script>", html, re.DOTALL)
    if not match:
        raise ValueError("Could not find Substack preload data")
    quoted_payload = f'"{match.group("payload")}"'
    return json.loads(json.loads(quoted_payload))


def substack_publication_url(publication: dict[str, Any]) -> str:
    custom_domain = str(publication.get("custom_domain") or "").strip("/")
    if custom_domain:
        return f"https://{custom_domain}"
    subdomain = publication.get("subdomain")
    if not subdomain:
        raise ValueError(f"Substack publication is missing subdomain: {publication}")
    return f"https://{subdomain}.substack.com"


def parse_substack_library_html(html: str, profile: str, group: str) -> list[Source]:
    sources: list[Source] = []
    seen_feed_urls: set[str] = set()
    pattern = re.compile(
        r'<a href="(?P<href>https?://[^"]+)"[^>]*class="[^"]*libraryItem[^"]*"[^>]*>'
        r"(?P<body>.*?)</a>",
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        site_url = normalize_site_url(unescape(match.group("href")))
        title = clean_html_text(match.group("body"))
        if not title or not looks_like_publication_url(site_url):
            continue
        feed_url = f"{site_url}/feed"
        if feed_url in seen_feed_urls:
            continue
        seen_feed_urls.add(feed_url)
        sources.append(
            Source(
                id=source_id_from_title(title, feed_url),
                title=title,
                feed_url=feed_url,
                site_url=site_url,
                kind="substack",
                profiles=[profile],
                groups=[group],
                source="substack-library-html",
            )
        )
    return sources


def clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def looks_like_publication_url(url: str) -> bool:
    return url not in {"https://substack.com", "https://www.substack.com"} and (
        "substack.com" in url or "." in url.removeprefix("https://").removeprefix("http://")
    )


def normalize_site_url(url: str) -> str:
    return url.strip().rstrip("/")
