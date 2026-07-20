from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError
import urllib.request
from typing import Any, Mapping, Optional
from urllib.parse import urlparse


USER_AGENT = "netnewswire-feed-booster/0.1"
DEFAULT_MAX_FETCH_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class FetchTextResponse:
    text: Optional[str]
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


class _RestrictedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep provider allowlists in force after HTTP redirects."""

    def __init__(self, allowed_hosts: set[str] | frozenset[str], allowed_suffixes: set[str] | frozenset[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts
        self.allowed_suffixes = allowed_suffixes

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_fetch_url(
            newurl,
            allowed_hosts=self.allowed_hosts,
            allowed_suffixes=self.allowed_suffixes,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_text(
    url: str,
    max_bytes: int = DEFAULT_MAX_FETCH_BYTES,
    *,
    allowed_hosts: Optional[set[str] | frozenset[str]] = None,
    allowed_suffixes: Optional[set[str] | frozenset[str]] = None,
) -> str:
    response = fetch_text_response(
        url,
        max_bytes=max_bytes,
        allowed_hosts=allowed_hosts,
        allowed_suffixes=allowed_suffixes,
    )
    if response.text is None:
        raise ValueError(f"Unexpected 304 Not Modified response without cached content: {url}")
    return response.text


def fetch_text_response(
    url: str,
    request_headers: Optional[Mapping[str, str]] = None,
    max_bytes: int = DEFAULT_MAX_FETCH_BYTES,
    allowed_hosts: Optional[set[str] | frozenset[str]] = None,
    allowed_suffixes: Optional[set[str] | frozenset[str]] = None,
) -> FetchTextResponse:
    _validate_fetch_url(url, allowed_hosts=allowed_hosts, allowed_suffixes=allowed_suffixes)
    headers = {"User-Agent": USER_AGENT}
    if request_headers:
        headers.update(request_headers)
    request = urllib.request.Request(url, headers=headers)
    open_request = _restricted_open_request(allowed_hosts, allowed_suffixes)
    try:
        with open_request(request, timeout=30) as response:
            body = response.read(max_bytes + 1)
            response_headers = getattr(response, "headers", {})
    except HTTPError as error:
        if error.code != 304:
            raise
        response_headers = error.headers or {}
        return FetchTextResponse(
            text=None,
            etag=response_headers.get("ETag", ""),
            last_modified=response_headers.get("Last-Modified", ""),
            not_modified=True,
        )
    if len(body) > max_bytes:
        raise ValueError(f"Response exceeded {max_bytes} byte limit: {url}")
    return FetchTextResponse(
        text=body.decode("utf-8", errors="replace"),
        etag=response_headers.get("ETag", ""),
        last_modified=response_headers.get("Last-Modified", ""),
    )


def _validate_fetch_url(
    url: str,
    *,
    allowed_hosts: Optional[set[str] | frozenset[str]],
    allowed_suffixes: Optional[set[str] | frozenset[str]],
) -> None:
    """Reject credentials and non-provider URLs before a hosted adapter fetches."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    allowed_host_names = {host.lower().rstrip(".") for host in allowed_hosts or set()}
    allowed_domain_suffixes = {suffix.lower().rstrip(".") for suffix in allowed_suffixes or set()}
    matches_allowed_host = hostname in allowed_host_names
    matches_allowed_suffix = any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in allowed_domain_suffixes)
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or (parsed.port not in {None, 443})
        or (
            (allowed_host_names or allowed_domain_suffixes)
            and not (matches_allowed_host or matches_allowed_suffix)
        )
    ):
        raise ValueError(f"Unsafe fetch URL: {url}")


def _restricted_open_request(
    allowed_hosts: Optional[set[str] | frozenset[str]],
    allowed_suffixes: Optional[set[str] | frozenset[str]],
):
    if not allowed_hosts and not allowed_suffixes:
        return urllib.request.urlopen
    return urllib.request.build_opener(
        _RestrictedRedirectHandler(allowed_hosts or set(), allowed_suffixes or set())
    ).open


def fetch_json(url: str) -> dict[str, Any]:
    return json.loads(fetch_text(url))
