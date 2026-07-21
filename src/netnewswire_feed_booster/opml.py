from __future__ import annotations

import html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .feed_store import Source, source_id_from_title


def parse_opml(path: Path, profile: str) -> List[Source]:
    tree = ET.parse(path)
    root = tree.getroot()
    body = root.find("body")
    if body is None:
        return []

    sources: List[Source] = []
    for source, _groups in _walk_outlines(body, []):
        source.profiles = [profile]
        sources.append(source)
    return sources


def _walk_outlines(element: ET.Element, groups: List[str]) -> Iterable[Tuple[Source, List[str]]]:
    for outline in element.findall("outline"):
        feed_url = _outline_attr(outline, "xmlUrl", "xmlurl", "url").strip()
        title = (
            _outline_attr(outline, "title")
            or _outline_attr(outline, "text")
            or _outline_attr(outline, "htmlUrl", "htmlurl")
            or feed_url
        ).strip()

        if feed_url:
            site_url = _outline_attr(outline, "htmlUrl", "htmlurl").strip()
            kind = infer_kind(feed_url, site_url)
            yield (
                Source(
                    id=source_id_from_title(title, feed_url),
                    title=title,
                    feed_url=feed_url,
                    site_url=site_url,
                    kind=kind,
                    groups=groups,
                    source="netnewswire-import",
                ),
                groups,
            )
        else:
            group_name = (_outline_attr(outline, "title") or _outline_attr(outline, "text") or "").strip()
            next_groups = groups + ([group_name] if group_name else [])
            yield from _walk_outlines(outline, next_groups)


def infer_kind(feed_url: str, site_url: str = "") -> str:
    haystack = f"{feed_url} {site_url}".lower()
    if "youtube.com/feeds/videos.xml" in haystack:
        return "youtube"
    if "substack.com" in haystack or haystack.rstrip("/").endswith("/feed"):
        return "substack" if "substack" in haystack else "website"
    return "website"


def render_opml(sources: Iterable[Source], title: str = "netnewswire-feed-booster") -> str:
    folders: Dict[str, Dict] = {}
    ungrouped: List[Source] = []
    for source in sorted(sources, key=lambda item: item.title.lower()):
        if source.groups:
            node = folders
            for folder_name in source.groups:
                current = node.setdefault(folder_name, {"folders": {}, "sources": []})
                node = current["folders"]
            current["sources"].append(source)
        else:
            ungrouped.append(source)

    generated_at = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        f"    <title>{_x(title)}</title>",
        f"    <dateCreated>{generated_at}</dateCreated>",
        "  </head>",
        "  <body>",
    ]

    lines.extend(_render_folder_tree(folders, indent="    "))

    for source in ungrouped:
        lines.append(_source_outline(source, indent="    "))

    lines.extend(["  </body>", "</opml>", ""])
    return "\n".join(lines)


def write_opml(path: Path, sources: Iterable[Source], title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_opml(sources, title=title), encoding="utf-8")


def _source_outline(source: Source, indent: str) -> str:
    attrs = {
        "text": source.title,
        "title": source.title,
        "type": "rss",
        "xmlUrl": source.feed_url,
    }
    if source.site_url:
        attrs["htmlUrl"] = source.site_url
    rendered = " ".join(f'{key}="{_x(value)}"' for key, value in attrs.items())
    return f"{indent}<outline {rendered}/>"


def _render_folder_tree(folders: Dict[str, Dict], indent: str) -> List[str]:
    lines: List[str] = []
    for folder_name in sorted(folders, key=str.lower):
        node = folders[folder_name]
        lines.append(f'{indent}<outline text="{_x(folder_name)}" title="{_x(folder_name)}">')
        lines.extend(_render_folder_tree(node["folders"], indent + "  "))
        for source in sorted(node["sources"], key=lambda item: item.title.lower()):
            lines.append(_source_outline(source, indent=indent + "  "))
        lines.append(f"{indent}</outline>")
    return lines


def _x(value: str) -> str:
    return html.escape(value, quote=True)


def _outline_attr(outline: ET.Element, *names: str) -> str:
    lowered = {key.lower(): value for key, value in outline.attrib.items()}
    for name in names:
        value = outline.attrib.get(name)
        if value is not None:
            return value
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return ""
