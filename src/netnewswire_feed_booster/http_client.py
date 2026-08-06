from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from urllib.error import HTTPError
import urllib.request
from typing import Any, Mapping, Optional
from urllib.parse import urlparse


USER_AGENT = "netnewswire-feed-booster/0.1"
DEFAULT_MAX_FETCH_BYTES = 10 * 1024 * 1024

# Content types that were never going to be the HTML/XML/JSON page or feed this
# tool expects. Reject before decoding rather than silently regex-matching
# against garbage. Deliberately a blocklist, not an allowlist: publishers set
# Content-Type inconsistently on otherwise-legitimate text responses, so being
# strict about what to accept would break real fetches; being strict about what
# to outright refuse (binaries, media, archives) doesn't.
UNEXPECTED_BINARY_CONTENT_TYPE_PREFIXES = (
    "application/octet-stream",
    "application/zip",
    "application/gzip",
    "application/x-7z-compressed",
    "application/x-rar",
    "application/x-msdownload",
    "application/x-executable",
    "application/pdf",
    "image/",
    "audio/",
    "video/",
    "font/",
)


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
    _validate_response_content_type(response_headers.get("Content-Type", ""), url)
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
    _validate_resolved_address(hostname, url)


def _validate_resolved_address(hostname: str, url: str) -> None:
    """Reject a hostname that currently resolves to a private, loopback, link-local,
    or otherwise non-public address — this is what blocks an allowlisted domain from
    reaching a cloud metadata endpoint or internal service. It's a point-in-time
    check, not a guarantee: DNS could still change between this check and the actual
    connection (a narrow TOCTOU/rebinding window), but it closes the common case of
    a domain currently pointed somewhere it shouldn't be.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return  # Let the real request surface its own clear DNS error.
    for _family, _type, _proto, _canonname, sockaddr in infos:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError(f"Unsafe fetch URL: {url} resolves to a non-public address ({sockaddr[0]})")


def _validate_response_content_type(content_type: str, url: str) -> None:
    normalized = (content_type or "").split(";")[0].strip().lower()
    for prefix in UNEXPECTED_BINARY_CONTENT_TYPE_PREFIXES:
        if normalized == prefix or (prefix.endswith("/") and normalized.startswith(prefix)):
            raise ValueError(f"Unexpected content type {normalized!r} for {url}")


def _restricted_open_request(
    allowed_hosts: Optional[set[str] | frozenset[str]],
    allowed_suffixes: Optional[set[str] | frozenset[str]],
):
    # Always install the redirect handler, even with no host allowlist configured
    # (the "any publisher's feed URL is valid" case, e.g. podcasts). _validate_fetch_url
    # still enforces https-only, no credentials, and the DNS-rebinding check on every
    # redirect hop that way — falling back to bare urlopen here would silently skip
    # all of that revalidation on a redirect, which is exactly the SSRF-via-redirect
    # class these checks exist to close.
    return urllib.request.build_opener(
        _RestrictedRedirectHandler(allowed_hosts or set(), allowed_suffixes or set())
    ).open


def fetch_json(
    url: str,
    *,
    allowed_hosts: Optional[set[str] | frozenset[str]] = None,
    allowed_suffixes: Optional[set[str] | frozenset[str]] = None,
) -> dict[str, Any]:
    return json.loads(fetch_text(url, allowed_hosts=allowed_hosts, allowed_suffixes=allowed_suffixes))


def fetch_json_post(
    url: str,
    payload: dict[str, Any],
    *,
    allowed_hosts: Optional[set[str] | frozenset[str]] = None,
    allowed_suffixes: Optional[set[str] | frozenset[str]] = None,
    max_bytes: int = DEFAULT_MAX_FETCH_BYTES,
    timeout: int = 30,
) -> dict[str, Any]:
    """POST a JSON body and return the parsed JSON response, with the same URL
    validation, DNS-rebinding check, and size cap as fetch_text — a raw
    urllib.request.urlopen call gets none of those for free.
    """
    _validate_fetch_url(url, allowed_hosts=allowed_hosts, allowed_suffixes=allowed_suffixes)
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    open_request = _restricted_open_request(allowed_hosts, allowed_suffixes)
    with open_request(request, timeout=timeout) as response:
        response_body = response.read(max_bytes + 1)
    if len(response_body) > max_bytes:
        raise ValueError(f"Response exceeded {max_bytes} byte limit: {url}")
    return json.loads(response_body.decode("utf-8"))
