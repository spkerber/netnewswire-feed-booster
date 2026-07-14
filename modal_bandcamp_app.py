from __future__ import annotations

import hmac
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
FAN_MAX_ITEMS = 40
FULL_FAN_SOURCE_IDS = {
    source_id.strip()
    for source_id in os.environ.get("FULL_FAN_SOURCE_IDS", "").split(",")
    if source_id.strip()
}
REFRESH_PAUSE_SECONDS = 1.0
OPEN_FILES_CONFLICT = "open files preventing the operation"


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


def _configured_feed_token() -> str:
    return os.environ.get("RSS_FEED_TOKEN", os.environ.get("BANDCAMP_FEED_TOKEN", "")).strip()


def _token_is_valid(value: str) -> bool:
    expected = _configured_feed_token()
    return bool(expected) and hmac.compare_digest(value, expected)


def _ensure_rss(rss: str) -> str:
    from netnewswire_feed_booster.rss_safety import ensure_rss_channel

    return ensure_rss_channel(rss)


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


def _bandcamp_source(source_id: str):
    from netnewswire_feed_booster.feed_store import FeedStore
    from netnewswire_feed_booster.rss_safety import is_safe_source_id

    if not is_safe_source_id(source_id):
        return None

    source = FeedStore(DATA_PATH).source_by_id(source_id)
    if source is None or source.kind != "bandcamp" or source.status != "active":
        return None
    return source


def _active_bandcamp_sources():
    from netnewswire_feed_booster.feed_store import FeedStore

    return [
        source
        for source in FeedStore(DATA_PATH).sources()
        if source.kind == "bandcamp" and source.status == "active" and RSS_PROFILE in source.profiles
    ]


def _generated_source(source_id: str):
    from netnewswire_feed_booster.feed_store import FeedStore
    from netnewswire_feed_booster.rss_safety import is_safe_source_id

    if not is_safe_source_id(source_id):
        return None

    source = FeedStore(DATA_PATH).source_by_id(source_id)
    if source is None or source.status != "active" or source.source not in {"nts-local-generated", "radio-local-generated"}:
        return None
    return source


def _active_refreshable_generated_sources():
    from netnewswire_feed_booster.feed_store import FeedStore

    return [
        source
        for source in FeedStore(DATA_PATH).sources()
        if source.source in {"nts-local-generated", "radio-local-generated"} and source.status == "active" and RSS_PROFILE in source.profiles
    ]


def _refresh_source(source) -> str:
    from netnewswire_feed_booster.hosted_bandcamp import render_bandcamp_source_rss

    rss = render_bandcamp_source_rss(
        source,
        fan_max_items=FAN_MAX_ITEMS,
        full_fan_source_ids=FULL_FAN_SOURCE_IDS,
    )
    rss = _ensure_rss(rss)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(source.id).write_text(rss, encoding="utf-8")
    cache_volume.commit()
    return rss


def _refresh_generated_source(source) -> str:
    if source.source == "nts-local-generated":
        from netnewswire_feed_booster.nts import render_nts_show_rss

        rss = render_nts_show_rss(source.site_url)
    elif source.source == "radio-local-generated" and "hydefm.com" in source.site_url:
        from netnewswire_feed_booster.hydefm import render_hydefm_archive_rss

        rss = render_hydefm_archive_rss(source.site_url)
    else:
        raise ValueError(f"Generated source is not refreshable: {source.id}")

    rss = _ensure_rss(rss)
    GENERATED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _generated_cache_path(source.id).write_text(rss, encoding="utf-8")
    cache_volume.commit()
    return rss


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

    web_app = FastAPI(title=f"{APP_NAME} RSS bridge")

    @web_app.get("/healthz")
    def healthz():
        return {"ok": True, "app": APP_NAME, "profile": RSS_PROFILE}

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
    def bandcamp_feed(feed_token: str, source_id: str, refresh: bool = False):
        if not _token_is_valid(feed_token):
            raise HTTPException(status_code=404, detail="Unknown feed")

        source = _bandcamp_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Unknown active Bandcamp source")

        if refresh:
            try:
                rss = _refresh_source(source)
            except Exception as error:
                rss = _read_cached_or_seeded_rss(source_id)
                if rss is None:
                    raise HTTPException(status_code=502, detail="Bandcamp refresh failed and no cached feed is available") from error
        else:
            rss = _read_cached_or_seeded_rss(source_id)
            if rss is None:
                try:
                    rss = _refresh_source(source)
                except Exception as error:
                    raise HTTPException(status_code=502, detail="Bandcamp refresh failed and no cached feed is available") from error

        return Response(content=rss, media_type="application/rss+xml; charset=utf-8")

    @web_app.get("/feeds/{feed_token}/generated/{source_id}.rss")
    def generated_feed(feed_token: str, source_id: str, refresh: bool = False):
        if not _token_is_valid(feed_token):
            raise HTTPException(status_code=404, detail="Unknown feed")

        source = _generated_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Unknown active generated source")

        if refresh and source.source in {"nts-local-generated", "radio-local-generated"}:
            try:
                rss = _refresh_generated_source(source)
            except Exception as error:
                rss = _read_cached_or_seeded_generated_rss(source_id)
                if rss is None:
                    raise HTTPException(status_code=502, detail="Generated refresh failed and no cached feed is available") from error
        else:
            rss = _read_cached_or_seeded_generated_rss(source_id)
            if rss is None and source.source in {"nts-local-generated", "radio-local-generated"}:
                try:
                    rss = _refresh_generated_source(source)
                except Exception as error:
                    raise HTTPException(status_code=502, detail="Generated refresh failed and no cached feed is available") from error
            if rss is None:
                raise HTTPException(status_code=502, detail="Generated feed seed is unavailable")

        return Response(content=rss, media_type="application/rss+xml; charset=utf-8")

    @web_app.get("/")
    def index():
        count = len(_active_bandcamp_sources())
        generated_count = len(_active_refreshable_generated_sources())
        return PlainTextResponse(
            f"{APP_NAME}\n"
            f"Active Bandcamp feeds: {count}\n"
            f"Refreshable generated feeds: {generated_count}\n"
            "Feed URL shape: /feeds/{token}/bandcamp/{source_id}.rss\n"
        )

    return web_app


@app.function(
    image=image,
    volumes={str(CACHE_ROOT): cache_volume},
    schedule=modal.Period(hours=6),
    timeout=900,
)
def refresh_bandcamp_cache() -> dict:
    refreshed = 0
    failed = 0
    failures = []

    for source in _active_bandcamp_sources():
        try:
            _refresh_source(source)
            refreshed += 1
        except Exception as error:
            failed += 1
            failures.append({"source_id": source.id, "error": f"{type(error).__name__}: {error}"})
        time.sleep(REFRESH_PAUSE_SECONDS)

    return {"refreshed": refreshed, "failed": failed, "failures": failures[:20]}


@app.function(
    image=image,
    volumes={str(CACHE_ROOT): cache_volume},
    schedule=modal.Period(hours=6),
    timeout=900,
)
def refresh_generated_cache() -> dict:
    refreshed = 0
    failed = 0
    failures = []

    for source in _active_refreshable_generated_sources():
        try:
            _refresh_generated_source(source)
            refreshed += 1
        except Exception as error:
            failed += 1
            failures.append({"source_id": source.id, "error": f"{type(error).__name__}: {error}"})
        time.sleep(REFRESH_PAUSE_SECONDS)

    return {"refreshed": refreshed, "failed": failed, "failures": failures[:20]}


@app.local_entrypoint()
def main():
    print("Deploy with: modal deploy modal_bandcamp_app.py")
    print("Then export hosted OPML with:")
    print("  ./scripts/netnewswire_workflow.sh export")
