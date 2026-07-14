from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime
from html import unescape
from pathlib import Path
from typing import Callable, List

from .rss_safety import html_attr, html_text, image_html, safe_https_url
from .http_client import fetch_text


FetchText = Callable[[str], str]


@dataclass
class NTSEpisode:
    title: str
    url: str
    published_at: str
    description: str = ""
    image_url: str = ""
    audio_url: str = ""


def extract_nts_react_state(html: str) -> dict:
    marker = "window._REACT_STATE_ = "
    start = html.find(marker)
    if start == -1:
        raise ValueError("Could not find NTS React state")
    start += len(marker)
    end = html.find(";</script>", start)
    if end == -1:
        raise ValueError("Could not find end of NTS React state")
    return json.loads(html[start:end])


def parse_nts_show_html(html: str, site_url: str) -> tuple[str, str, List[NTSEpisode]]:
    state = extract_nts_react_state(html)
    show = state.get("show") or {}
    title = str(show.get("name") or "").strip()
    description = clean_html(str(show.get("description_html") or show.get("description") or ""))
    episodes = []

    for item in show.get("episodes", []):
        episode_alias = str(item.get("episode_alias") or "").strip()
        if not episode_alias:
            continue
        media = item.get("media") or {}
        audio_sources = item.get("audio_sources") or []
        audio_url = ""
        if audio_sources:
            audio_url = str(audio_sources[0].get("url") or "").strip()
        episodes.append(
            NTSEpisode(
                title=str(item.get("name") or title or episode_alias).strip(),
                url=f"https://www.nts.live/shows/{show.get('show_alias')}/episodes/{episode_alias}",
                published_at=str(item.get("broadcast") or item.get("updated") or "").strip(),
                description=clean_html(str(item.get("description_html") or item.get("description") or "")),
                image_url=str(media.get("picture_medium") or media.get("background_medium") or media.get("picture_large") or "").strip(),
                audio_url=audio_url,
            )
        )

    if not title:
        raise ValueError(f"Could not find NTS show title for {site_url}")
    if not episodes:
        raise ValueError(f"Could not find NTS episodes for {site_url}")
    return title, description, episodes


def render_nts_show_rss(site_url: str, fetcher: FetchText = fetch_text) -> str:
    html = fetcher(site_url)
    title, description, episodes = parse_nts_show_html(html, site_url)
    return render_nts_rss(site_url=site_url, title=f"NTS: {title}", description=description, episodes=episodes)


def write_nts_show_rss(out_path: Path, site_url: str, fetcher: FetchText = fetch_text) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_nts_show_rss(site_url, fetcher=fetcher), encoding="utf-8")


def render_nts_rss(site_url: str, title: str, description: str, episodes: List[NTSEpisode]) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = site_url
    ET.SubElement(channel, "description").text = description or title

    for episode in episodes:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = episode.title
        ET.SubElement(item, "link").text = episode.url
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = episode.url
        if episode.published_at:
            ET.SubElement(item, "pubDate").text = rss_pubdate(episode.published_at)
        body = html_text(episode.description)
        image = image_html(
            episode.image_url,
            allowed_suffixes={"ntslive.co.uk"},
        )
        if image:
            body = f"{image}<p>{body}</p>"
        audio_url = safe_https_url(
            episode.audio_url,
            allowed_hosts={"soundcloud.com", "www.mixcloud.com"},
            allowed_suffixes={"soundcloud.com", "mixcloud.com"},
        )
        if audio_url:
            body = f'{body}<p>Audio: <a href="{html_attr(audio_url)}">{html_text(audio_url)}</a></p>'
        ET.SubElement(item, "description").text = body

    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


def rss_pubdate(value: str) -> str:
    return format_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))


def clean_html(value: str) -> str:
    text = unescape(value)
    while "<" in text and ">" in text:
        start = text.find("<")
        end = text.find(">", start)
        if end == -1:
            break
        text = text[:start] + " " + text[end + 1 :]
    return " ".join(text.split())
