from __future__ import annotations

import html
import json
import os
import tempfile
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .feed_store import Source, validate_profile_id
from .feed_validation import validate_feed_text
from .generated_adapters import BANDCAMP_ADAPTER
from .hosted_bandcamp import render_bandcamp_source_rss
from .http_client import fetch_text
from .opml import render_opml


@dataclass(frozen=True)
class StarterImportResult:
    source_count: int
    direct_count: int
    generated_count: int
    profile: str
    opml_name: str
    report_name: str


def load_starter_import_manifest(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported starter import manifest schema.")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Starter import manifest must contain at least one source.")

    required = {"id", "title", "delivery", "kind", "site_url", "group"}
    seen_ids: set[str] = set()
    for item in sources:
        if not isinstance(item, dict) or required - set(item):
            raise ValueError("Every example source needs an id, title, delivery, kind, site URL, and group.")
        if item["id"] in seen_ids:
            raise ValueError(f"Duplicate example source ID: {item['id']}")
        seen_ids.add(item["id"])
        if item["delivery"] not in {"direct", "generated"}:
            raise ValueError(f"Unsupported delivery mode for {item['id']}: {item['delivery']}")
        if item["delivery"] == "direct" and not item.get("feed_url"):
            raise ValueError(f"Direct example source is missing a feed URL: {item['id']}")
        for field in ("site_url", "feed_url"):
            value = item.get(field, "")
            if value and not value.startswith("https://"):
                raise ValueError(f"Example source URLs must use HTTPS: {item['id']}")
    return payload


def build_starter_import(
    *,
    repo_root: Path,
    manifest_path: Path,
    profile: str = "starter",
    force: bool = False,
    validate_network: bool = True,
) -> StarterImportResult:
    profile = validate_profile_id(profile)
    manifest = load_starter_import_manifest(manifest_path)
    specs: List[Dict[str, Any]] = manifest["sources"]

    data_path = repo_root / "data" / f"sources.{profile}.json"
    history_path = repo_root / "data" / f"subscription-history.{profile}.json"
    profiles_path = repo_root / "data" / f"profiles.{profile}.json"
    opml_path = repo_root / "exports" / f"{profile}-netnewswire.opml"
    report_path = repo_root / "exports" / f"{profile}-import-report.html"
    bandcamp_dir = repo_root / "exports" / "bandcamp"
    bandcamp_targets = [
        bandcamp_dir / f"{item['id']}.rss"
        for item in specs
        if item["delivery"] == "generated"
    ]
    targets = [data_path, history_path, profiles_path, opml_path, report_path, *bandcamp_targets]
    existing = [path for path in targets if path.exists()]
    if existing and not force:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(
            f"Refusing to replace an existing example profile ({names}). "
            "Choose a new profile name or pass --force intentionally."
        )

    sources: List[Source] = []
    generated_rss: Dict[str, str] = {}
    for spec in specs:
        if spec["delivery"] == "direct":
            source = _source_from_spec(spec, profile, spec["feed_url"])
            if validate_network:
                validate_feed_text(fetch_text(source.feed_url))
        else:
            final_feed_path = bandcamp_dir / f"{spec['id']}.rss"
            source = _source_from_spec(spec, profile, final_feed_path.resolve().as_uri())
            BANDCAMP_ADAPTER.validate(source)
            rss = render_bandcamp_source_rss(
                source,
                fetcher=lambda url: fetch_text(
                    url,
                    allowed_hosts=BANDCAMP_ADAPTER.allowed_hosts,
                    allowed_suffixes=BANDCAMP_ADAPTER.allowed_suffixes,
                ),
            )
            validate_feed_text(rss)
            generated_rss[spec["id"]] = rss
        sources.append(source)

    profile_payload = {
        "schema_version": 1,
        "sources": [source.to_dict() for source in sorted(sources, key=lambda item: item.title.lower())],
    }
    history_payload = {"schema_version": 1, "entries": []}
    profiles_payload = {
        "schema_version": 1,
        "profiles": [
            {
                "id": profile,
                "display_name": "Starter import",
                "default_reader": "NetNewsWire",
                "devices": [{"id": "mac", "label": "Mac", "reader": "NetNewsWire"}],
            }
        ],
    }
    opml = render_opml(sources, title=manifest["title"])
    report = render_import_report(manifest, sources)

    repo_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="netnewswire-example-") as temp_dir:
        staging = Path(temp_dir)
        staged_files: List[tuple[Path, Path]] = []
        staged_files.append((_stage_json(staging / data_path.name, profile_payload), data_path))
        staged_files.append((_stage_json(staging / history_path.name, history_payload), history_path))
        staged_files.append((_stage_json(staging / profiles_path.name, profiles_payload), profiles_path))
        staged_files.append((_stage_text(staging / opml_path.name, opml), opml_path))
        staged_files.append((_stage_text(staging / report_path.name, report), report_path))
        for source_id, rss in generated_rss.items():
            staged_files.append((_stage_text(staging / f"{source_id}.rss", rss), bandcamp_dir / f"{source_id}.rss"))

        for staged, target in staged_files:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
            target.chmod(0o600)

    return StarterImportResult(
        source_count=len(sources),
        direct_count=sum(source.kind != "bandcamp" for source in sources),
        generated_count=sum(source.kind == "bandcamp" for source in sources),
        profile=profile,
        opml_name=opml_path.name,
        report_name=report_path.name,
    )


def render_import_report(manifest: Dict[str, Any], sources: Iterable[Source]) -> str:
    source_list = list(sources)
    folders: Dict[str, List[Source]] = {}
    for source in source_list:
        folder = source.groups[0] if source.groups else "Unfiled"
        folders.setdefault(folder, []).append(source)

    folder_html = []
    for folder, folder_sources in folders.items():
        cards = []
        for source in folder_sources:
            generated = source.kind == "bandcamp"
            badge = "Generated locally" if generated else "Direct feed"
            detail = (
                "Public Bandcamp page → local RSS"
                if generated
                else "Publisher or platform RSS → NetNewsWire"
            )
            cards.append(
                f"""
                <article class="source-card">
                  <div class="source-row">
                    <span class="source-mark" aria-hidden="true">{html.escape(source.title[:1].upper())}</span>
                    <div>
                      <h3>{html.escape(source.title)}</h3>
                      <p>{html.escape(detail)}</p>
                    </div>
                  </div>
                  <span class="badge {'generated' if generated else 'direct'}">{badge}</span>
                </article>
                """
            )
        folder_html.append(
            f"""
            <section class="folder">
              <div class="folder-heading">
                <h2>{html.escape(folder)}</h2>
                <span>{len(folder_sources)} feeds</span>
              </div>
              <div class="source-grid">{''.join(cards)}</div>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(manifest['title'])}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17201d;
      --muted: #64706b;
      --paper: #f4f1e8;
      --panel: #fffdf7;
      --line: #d9d5c9;
      --green: #126b55;
      --green-soft: #dceddf;
      --coral: #ab4d32;
      --coral-soft: #f6ddd2;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ width: min(1120px, calc(100% - 48px)); margin: 0 auto; padding: 52px 0 64px; }}
    .eyebrow {{ color: var(--green); font-size: 13px; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }}
    h1 {{ max-width: 780px; margin: 12px 0 14px; font: 700 clamp(38px, 6vw, 68px)/.98 Georgia, serif; letter-spacing: -.04em; }}
    .lede {{ max-width: 720px; margin: 0; color: var(--muted); font-size: 18px; line-height: 1.55; }}
    .summary {{
      display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;
      margin: 32px 0 40px;
    }}
    .stat {{ padding: 18px 20px; background: var(--panel); border: 1px solid var(--line); border-radius: 16px; }}
    .stat strong {{ display: block; font: 700 31px/1 Georgia, serif; }}
    .stat span {{ color: var(--muted); font-size: 13px; }}
    .folder {{ margin-top: 30px; }}
    .folder-heading {{ display: flex; align-items: baseline; justify-content: space-between; border-bottom: 1px solid var(--line); }}
    .folder-heading h2 {{ margin: 0 0 10px; font: 700 24px/1.2 Georgia, serif; }}
    .folder-heading span {{ color: var(--muted); font-size: 13px; }}
    .source-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 12px; }}
    .source-card {{
      min-height: 126px; padding: 18px; background: var(--panel); border: 1px solid var(--line);
      border-radius: 16px; display: flex; flex-direction: column; justify-content: space-between; gap: 18px;
    }}
    .source-row {{ display: flex; gap: 13px; align-items: flex-start; }}
    .source-mark {{
      width: 38px; height: 38px; flex: 0 0 38px; display: grid; place-items: center;
      border-radius: 10px; background: var(--ink); color: white; font: 700 17px/1 Georgia, serif;
    }}
    h3 {{ margin: 1px 0 5px; font-size: 16px; }}
    .source-card p {{ margin: 0; color: var(--muted); font-size: 13px; line-height: 1.4; }}
    .badge {{ align-self: flex-start; padding: 5px 9px; border-radius: 999px; font-size: 11px; font-weight: 750; }}
    .badge.direct {{ color: var(--green); background: var(--green-soft); }}
    .badge.generated {{ color: var(--coral); background: var(--coral-soft); }}
    .next {{
      margin-top: 42px; padding: 24px; border-radius: 18px; background: var(--ink); color: white;
    }}
    .next h2 {{ margin: 0 0 7px; font: 700 25px/1.1 Georgia, serif; }}
    .next p {{ margin: 0; color: #cad2cf; line-height: 1.45; }}
    footer {{ margin-top: 20px; color: var(--muted); font-size: 12px; }}
    @media (max-width: 720px) {{
      main {{ width: min(100% - 28px, 1120px); padding-top: 30px; }}
      .summary, .source-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">NetNewsWire Feed Booster · import report</div>
    <h1>{len(source_list)} feeds are ready to review.</h1>
    <p class="lede">{html.escape(manifest['description'])}</p>
    <div class="summary" aria-label="Bundle summary">
      <div class="stat"><strong>{len(source_list)}</strong><span>feeds checked</span></div>
      <div class="stat"><strong>{sum(source.kind != 'bandcamp' for source in source_list)}</strong><span>direct feeds</span></div>
      <div class="stat"><strong>{sum(source.kind == 'bandcamp' for source in source_list)}</strong><span>generated Bandcamp feeds</span></div>
    </div>
    {''.join(folder_html)}
    <section class="next">
      <div>
        <h2>Import with NetNewsWire when you want these feeds.</h2>
        <p>Choose File → Import Subscriptions…, select On My Mac, then choose the generated starter OPML file. This report did not import or subscribe to anything.</p>
      </div>
    </section>
    <footer>This is a build report, not a feed reader. Read and manage subscriptions in NetNewsWire. Generated Bandcamp RSS stays on this Mac unless you deliberately configure optional hosting.</footer>
  </main>
</body>
</html>
"""


def open_import_report(repo_root: Path, result: StarterImportResult) -> None:
    webbrowser.open((repo_root / "exports" / result.report_name).resolve().as_uri())


def _source_from_spec(spec: Dict[str, Any], profile: str, feed_url: str) -> Source:
    generated = spec["delivery"] == "generated"
    return Source(
        id=spec["id"],
        title=spec["title"],
        feed_url=feed_url,
        site_url=spec["site_url"],
        kind=spec["kind"],
        profiles=[profile],
        groups=[spec["group"]],
        status="active",
        source="example-generated" if generated else "example-direct",
        notes=(
            "Generated locally from this public Bandcamp page."
            if generated
            else "Uses the public source's direct feed."
        ),
    )


def _stage_json(path: Path, payload: Dict[str, Any]) -> Path:
    return _stage_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _stage_text(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path
