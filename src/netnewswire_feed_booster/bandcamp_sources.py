from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .bandcamp import extract_bandcamp_pagedata, extract_meta_content, title_from_html
from .feed_store import Source, normalize_url, slugify
from .http_client import fetch_text


def build_bandcamp_source_from_url(
    url: str,
    title: str,
    profile: str,
    source_type: str,
    out_dir: Path,
) -> Source:
    site_url = normalize_url(url).rstrip("/")
    parsed = urlparse(site_url)
    candidate = Source(id="bandcamp-source", title="Bandcamp", feed_url="generated", site_url=site_url, kind="bandcamp")
    from .generated_adapters import BANDCAMP_ADAPTER

    BANDCAMP_ADAPTER.validate(candidate)
    is_fan = parsed.netloc.lower() == "bandcamp.com"
    if source_type == "fan":
        is_fan = True
    elif source_type == "artist":
        is_fan = False

    html = fetch_text(
        site_url if is_fan else f"{site_url}/music",
        allowed_hosts=BANDCAMP_ADAPTER.allowed_hosts,
        allowed_suffixes=BANDCAMP_ADAPTER.allowed_suffixes,
    )
    resolved_title = title.strip() or bandcamp_title_from_page(html, fallback=parsed.netloc)
    source_title = (
        resolved_title
        if resolved_title.lower().startswith("bandcamp")
        else f"Bandcamp Fan: {resolved_title}"
        if is_fan
        else f"Bandcamp: {resolved_title}"
    )
    source_id = slugify(source_title)
    out_path = out_dir / f"{source_id}.rss"
    return Source(
        id=source_id,
        title=source_title,
        feed_url=out_path.resolve().as_uri(),
        site_url=site_url + ("/" if not is_fan and not url.endswith("/") else ""),
        kind="bandcamp",
        profiles=[profile],
        groups=["Bandcamp"],
        status="active",
        source="bandcamp-local-generated",
        notes="Generated local RSS feed from the saved Bandcamp source page because OpenRSS did not mirror this Bandcamp feed reliably.",
    )


def bandcamp_title_from_page(html: str, fallback: str) -> str:
    try:
        blob = extract_bandcamp_pagedata(html)
        fan_name = str(blob.get("fan_data", {}).get("name") or blob.get("fan_data", {}).get("username") or "").strip()
        if fan_name:
            return fan_name
    except ValueError:
        pass

    title = (
        extract_meta_content(html, "og:site_name")
        or extract_meta_content(html, "og:title")
        or title_from_html(html)
        or fallback
    )
    return title.replace("Music | ", "").strip()
