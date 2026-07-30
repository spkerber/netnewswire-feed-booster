from __future__ import annotations

# The filename is retained for existing deployment commands; the app serves every
# registered generated adapter, not only Bandcamp sources.

import hmac
import json
import os
import time
from pathlib import Path
from typing import Optional

import modal


APP_NAME = os.environ.get("MODAL_APP_NAME", "rss-feed-bridge")
RSS_PROFILE = os.environ.get("RSS_PROFILE", "me")
MODAL_SECRET_NAME = os.environ.get("MODAL_SECRET_NAME", "rss-feed-bridge-token")
MODAL_VOLUME_NAME = os.environ.get("MODAL_VOLUME_NAME", f"{APP_NAME}-cache")
LOCAL_DATA_PATH = Path(os.environ.get("RSS_SOURCES_FILE", f"data/sources.{RSS_PROFILE}.json" if Path(f"data/sources.{RSS_PROFILE}.json").exists() else "data/sources.json"))
DATA_PATH = Path("/root/data/sources.json")
SEED_DIR = Path("/root/seed/bandcamp")
GENERATED_SEED_DIR = Path("/root/seed/generated")
CACHE_ROOT = Path("/cache")
CACHE_DIR = CACHE_ROOT / "bandcamp"
GENERATED_CACHE_DIR = CACHE_ROOT / "generated"
VALIDATOR_DIR = CACHE_ROOT / "validators"
REFRESH_STATE_DIR = CACHE_ROOT / "refresh-state"
FAN_MAX_ITEMS = 40
FULL_FAN_SOURCE_IDS = {
    source_id.strip()
    for source_id in os.environ.get("FULL_FAN_SOURCE_IDS", "").split(",")
    if source_id.strip()
}
REFRESH_PAUSE_SECONDS = 1.0
REFRESH_INTERVAL_SECONDS = int(os.environ.get("RSS_REFRESH_INTERVAL_SECONDS", str(12 * 60 * 60)))
REFRESH_SCHEDULE_HOURS = int(os.environ.get("RSS_REFRESH_SCHEDULE_HOURS", "1"))
MAX_REFRESH_SOURCES_PER_RUN = int(os.environ.get("RSS_MAX_REFRESH_SOURCES_PER_RUN", "20"))
MAX_RSS_ITEMS_PER_SOURCE = int(os.environ.get("RSS_MAX_ITEMS_PER_SOURCE", "50"))
OPEN_FILES_CONFLICT = "open files preventing the operation"
READER_CACHE_CONTROL = "private, max-age=3600, stale-if-error=21600"

if not 1 <= REFRESH_SCHEDULE_HOURS:
    raise ValueError("RSS_REFRESH_SCHEDULE_HOURS must be positive")
if REFRESH_INTERVAL_SECONDS < REFRESH_SCHEDULE_HOURS * 60 * 60:
    raise ValueError("RSS_REFRESH_INTERVAL_SECONDS must be at least the scheduler interval")
if not 1 <= MAX_REFRESH_SOURCES_PER_RUN <= 25:
    raise ValueError("RSS_MAX_REFRESH_SOURCES_PER_RUN must be between 1 and 25")
if MAX_RSS_ITEMS_PER_SOURCE < 1:
    raise ValueError("RSS_MAX_ITEMS_PER_SOURCE must be positive")


image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("fastapi[standard]")
    .env({"PYTHONPATH": "/root/src", "RSS_PROFILE": RSS_PROFILE})
    .add_local_dir("src/netnewswire_feed_booster", "/root/src/netnewswire_feed_booster")
    .add_local_file(LOCAL_DATA_PATH, "/root/data/sources.json")
    .add_local_dir("exports/bandcamp", "/root/seed/bandcamp")
    .add_local_dir("exports/generated", "/root/seed/generated")
)
cache_volume = modal.Volume.from_name(MODAL_VOLUME_NAME, create_if_missing=True)
app = modal.App(APP_NAME)


def _cache_path(source_id: str) -> Path:
    from netnewswire_feed_booster.rss_safety import validate_source_id

    source_id = validate_source_id(source_id)
    return CACHE_DIR / f"{source_id}.rss"


def _seed_path(source_id: str) -> Path:
    from netnewswire_feed_booster.rss_safety import validate_source_id

    source_id = validate_source_id(source_id)
    return SEED_DIR / f"{source_id}.rss"


def _generated_cache_path(source_id: str) -> Path:
    from netnewswire_feed_booster.rss_safety import validate_source_id

    source_id = validate_source_id(source_id)
    return GENERATED_CACHE_DIR / f"{source_id}.rss"


def _generated_seed_path(source_id: str) -> Path:
    from netnewswire_feed_booster.rss_safety import validate_source_id

    source_id = validate_source_id(source_id)
    return GENERATED_SEED_DIR / f"{source_id}.rss"


def _validator_path(source_id: str) -> Path:
    from netnewswire_feed_booster.rss_safety import validate_source_id

    source_id = validate_source_id(source_id)
    return VALIDATOR_DIR / f"{source_id}.json"


def _validator_headers(source_id: str) -> dict[str, str]:
    try:
        stored = json.loads(_validator_path(source_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(stored, dict):
        return {}
    headers: dict[str, str] = {}
    if isinstance(stored.get("etag"), str) and stored["etag"]:
        headers["If-None-Match"] = stored["etag"]
    if isinstance(stored.get("last_modified"), str) and stored["last_modified"]:
        headers["If-Modified-Since"] = stored["last_modified"]
    return headers


def _fetch_source_text(source_id: str, url: str, adapter) -> Optional[str]:
    from netnewswire_feed_booster.http_client import fetch_text_response

    response = fetch_text_response(
        url,
        request_headers=_validator_headers(source_id),
        allowed_hosts=adapter.allowed_hosts,
        allowed_suffixes=adapter.allowed_suffixes,
    )
    if response.not_modified:
        return None
    VALIDATOR_DIR.mkdir(parents=True, exist_ok=True)
    _validator_path(source_id).write_text(
        json.dumps({"etag": response.etag, "last_modified": response.last_modified}),
        encoding="utf-8",
    )
    return response.text


def _configured_feed_token() -> str:
    return os.environ.get("RSS_FEED_TOKEN", os.environ.get("BANDCAMP_FEED_TOKEN", "")).strip()


def _token_is_valid(value: str) -> bool:
    expected = _configured_feed_token()
    return bool(expected) and hmac.compare_digest(value, expected)


def _ensure_rss(rss: str) -> str:
    from netnewswire_feed_booster.rss_safety import ensure_rss_channel

    return ensure_rss_channel(rss)


def _limit_rss_items(rss: str) -> str:
    from netnewswire_feed_booster.rss_safety import limit_rss_items

    return limit_rss_items(rss, MAX_RSS_ITEMS_PER_SOURCE)


def _reload_cache_volume() -> None:
    try:
        cache_volume.reload()
    except Exception as error:
        if OPEN_FILES_CONFLICT not in str(error):
            raise


def _read_rss_file(path: Path) -> Optional[str]:
    try:
        return _ensure_rss(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        return None


def _source_for_route(source_id: str, hosted_route: str):
    from netnewswire_feed_booster.feed_store import FeedStore
    from netnewswire_feed_booster.generated_adapters import adapter_for_source
    from netnewswire_feed_booster.rss_safety import is_safe_source_id

    if not is_safe_source_id(source_id):
        return None

    source = FeedStore(DATA_PATH).source_by_id(source_id)
    if source is None or source.status != "active" or RSS_PROFILE not in source.profiles:
        return None
    adapter = adapter_for_source(source)
    if adapter is None or adapter.hosted_route != hosted_route:
        return None
    try:
        adapter.validate(source)
    except ValueError:
        return None
    return source


def _active_sources_for_route(hosted_route: str):
    from netnewswire_feed_booster.feed_store import FeedStore
    from netnewswire_feed_booster.generated_adapters import adapter_for_source

    active_sources = []
    for source in FeedStore(DATA_PATH).sources():
        if source.status != "active" or RSS_PROFILE not in source.profiles:
            continue
        adapter = adapter_for_source(source)
        if adapter is None or adapter.hosted_route != hosted_route:
            continue
        try:
            adapter.validate(source)
        except ValueError:
            continue
        active_sources.append(source)
    return active_sources


def _refresh_source(source) -> str:
    from netnewswire_feed_booster.generated_adapters import adapter_for_source
    from netnewswire_feed_booster.hosted_bandcamp import render_bandcamp_source_rss

    adapter = adapter_for_source(source)
    if adapter is None:
        raise ValueError(f"Generated source is not refreshable: {source.id}")
    html = _fetch_source_text(source.id, adapter.upstream_url(source), adapter)
    if html is None:
        rss = _read_cached_or_seeded_rss(source.id)
        if rss is None:
            raise ValueError(f"Bandcamp returned 304 without a cached feed: {source.id}")
        return rss

    rss = render_bandcamp_source_rss(
        source,
        fetcher=lambda _: html,
        fan_max_items=FAN_MAX_ITEMS,
        full_fan_source_ids=FULL_FAN_SOURCE_IDS,
        max_items=MAX_RSS_ITEMS_PER_SOURCE,
    )
    rss = _limit_rss_items(_ensure_rss(rss))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(source.id).write_text(rss, encoding="utf-8")
    return rss


def _refresh_generated_source(source) -> str:
    from netnewswire_feed_booster.generated_adapters import adapter_for_source

    adapter = adapter_for_source(source)
    if adapter is None:
        raise ValueError(f"Generated source is not refreshable: {source.id}")
    content = _fetch_source_text(source.id, adapter.upstream_url(source), adapter)
    if content is None:
        rss = _read_cached_or_seeded_generated_rss(source.id)
        if rss is None:
            raise ValueError(f"{adapter.name} returned 304 without a cached feed: {source.id}")
        return rss
    rss = adapter.render(source, content)

    rss = _limit_rss_items(_ensure_rss(rss))
    GENERATED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _generated_cache_path(source.id).write_text(rss, encoding="utf-8")
    return rss


def _refresh_state_path(source_id: str) -> Path:
    from netnewswire_feed_booster.rss_safety import validate_source_id

    return REFRESH_STATE_DIR / f"{validate_source_id(source_id)}.json"


def _source_is_due(source_id: str, now: float) -> bool:
    try:
        state = json.loads(_refresh_state_path(source_id).read_text(encoding="utf-8"))
        last_attempt = float(state.get("last_attempt", 0))
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return True
    return now - last_attempt >= REFRESH_INTERVAL_SECONDS


def _mark_refresh_attempt(source_id: str, now: float) -> None:
    REFRESH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    _refresh_state_path(source_id).write_text(json.dumps({"last_attempt": now}), encoding="utf-8")


def _active_generated_source_ids() -> set[str]:
    from netnewswire_feed_booster.feed_store import FeedStore
    from netnewswire_feed_booster.generated_adapters import adapter_for_source

    return {
        source.id
        for source in FeedStore(DATA_PATH).sources()
        if source.status == "active" and RSS_PROFILE in source.profiles and adapter_for_source(source) is not None
    }


def _prune_retired_cache_files() -> int:
    active_ids = _active_generated_source_ids()
    removed = 0
    for directory, suffix in (
        (CACHE_DIR, ".rss"),
        (GENERATED_CACHE_DIR, ".rss"),
        (VALIDATOR_DIR, ".json"),
        (REFRESH_STATE_DIR, ".json"),
    ):
        if not directory.exists():
            continue
        for path in directory.glob(f"*{suffix}"):
            if path.stem not in active_ids:
                path.unlink()
                removed += 1
    return removed


def _read_cached_or_seeded_rss(source_id: str) -> Optional[str]:
    _reload_cache_volume()
    path = _cache_path(source_id)
    rss = _read_rss_file(path)
    if rss is not None:
        return rss

    seed_path = _seed_path(source_id)
    rss = _read_rss_file(seed_path)
    if rss is not None:
        return rss

    return None


def _read_cached_or_seeded_generated_rss(source_id: str) -> Optional[str]:
    _reload_cache_volume()
    path = _generated_cache_path(source_id)
    rss = _read_rss_file(path)
    if rss is not None:
        return rss

    seed_path = _generated_seed_path(source_id)
    rss = _read_rss_file(seed_path)
    if rss is not None:
        return rss

    return None


@app.function(
    image=image,
    secrets=[modal.Secret.from_name(MODAL_SECRET_NAME)],
    volumes={str(CACHE_ROOT): cache_volume},
    timeout=120,
)
@modal.concurrent(max_inputs=10)
@modal.asgi_app()
def web():
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import PlainTextResponse, Response
    from netnewswire_feed_booster.bridge_policy import FeedWaitingForRefresh, require_cached_feed

    web_app = FastAPI(title=f"{APP_NAME} RSS bridge")

    @web_app.get("/healthz")
    def healthz():
        return {"ok": True}

    @web_app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @web_app.get("/feeds/bandcamp/{source_id}.rss")
    def public_bandcamp_feed(source_id: str):
        raise HTTPException(status_code=404, detail="Tokenized feed URL required")

    @web_app.get("/feeds/generated/{source_id}.rss")
    def public_generated_feed(source_id: str):
        raise HTTPException(status_code=404, detail="Tokenized feed URL required")

    @web_app.get("/feeds/{feed_token}/bandcamp/{source_id}.rss")
    def bandcamp_feed(feed_token: str, source_id: str):
        if not _token_is_valid(feed_token):
            raise HTTPException(status_code=404, detail="Unknown feed")

        source = _source_for_route(source_id, "bandcamp")
        if source is None:
            raise HTTPException(status_code=404, detail="Unknown active Bandcamp source")

        try:
            rss = require_cached_feed(_read_cached_or_seeded_rss(source_id), "Bandcamp")
        except FeedWaitingForRefresh as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        return Response(content=rss, media_type="application/rss+xml; charset=utf-8", headers={"Cache-Control": READER_CACHE_CONTROL})

    @web_app.get("/feeds/{feed_token}/generated/{source_id}.rss")
    def generated_feed(feed_token: str, source_id: str):
        if not _token_is_valid(feed_token):
            raise HTTPException(status_code=404, detail="Unknown feed")

        source = _source_for_route(source_id, "generated")
        if source is None:
            raise HTTPException(status_code=404, detail="Unknown active generated source")

        try:
            rss = require_cached_feed(_read_cached_or_seeded_generated_rss(source_id), "Generated")
        except FeedWaitingForRefresh as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        return Response(content=rss, media_type="application/rss+xml; charset=utf-8", headers={"Cache-Control": READER_CACHE_CONTROL})

    @web_app.get("/")
    def index():
        return PlainTextResponse("RSS feed bridge\n")

    return web_app


@app.function(
    image=image,
    secrets=[modal.Secret.from_name(MODAL_SECRET_NAME)],
    volumes={str(CACHE_ROOT): cache_volume},
    schedule=modal.Period(hours=REFRESH_SCHEDULE_HOURS),
    timeout=900,
)
def refresh_bandcamp_cache() -> dict:
    return _refresh_due_sources(_active_sources_for_route("bandcamp"), _refresh_source, prune_retired=True)


@app.function(
    image=image,
    volumes={str(CACHE_ROOT): cache_volume},
    schedule=modal.Period(hours=REFRESH_SCHEDULE_HOURS),
    timeout=900,
)
def refresh_generated_cache() -> dict:
    return _refresh_due_sources(_active_sources_for_route("generated"), _refresh_generated_source)


def _refresh_due_sources(sources, refresh_source, prune_retired: bool = False) -> dict:
    from netnewswire_feed_booster.bridge_policy import due_sources

    refreshed = 0
    failed = 0
    failures = []
    attempted = 0
    now = time.time()
    _reload_cache_volume()
    pruned = _prune_retired_cache_files() if prune_retired else 0

    for source in due_sources(sources, lambda item: _source_is_due(item.id, now), MAX_REFRESH_SOURCES_PER_RUN):
        try:
            refresh_source(source)
            refreshed += 1
        except Exception as error:
            failed += 1
            failures.append({"source_id": source.id, "error": f"{type(error).__name__}: {error}"})
        finally:
            _mark_refresh_attempt(source.id, now)
            attempted += 1
        time.sleep(REFRESH_PAUSE_SECONDS)

    if attempted or pruned:
        cache_volume.commit()
    return {
        "attempted": attempted,
        "refreshed": refreshed,
        "failed": failed,
        "pruned": pruned,
        "failures": failures[:20],
    }


@app.local_entrypoint()
def main():
    print("Deploy with: modal deploy modal_bandcamp_app.py")
    print("Then export hosted OPML with:")
    print("  ./scripts/netnewswire_workflow.sh export")
