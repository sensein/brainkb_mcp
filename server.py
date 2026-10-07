#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BrainKB MCP server.

Exposes the BrainKB query_service API as MCP tools so an assistant can, on the
user's behalf: authenticate with their credentials, ingest knowledge-graph data,
read/search the graphs, query W3C PROV-O provenance, and check the status of
their ingest jobs.

Transport: stdio (default). Run with:  python server.py

Configuration (env, all optional):
  BRAINKB_URL       Base URL of the query_service (default http://localhost:8010)
  BRAINKB_TOKEN     A Personal Access Token (brainkb_pat_...) — the recommended
                    way to authenticate for LOCAL (stdio) use. Mint one with
                    brainkb_create_token() after logging in once, then paste it
                    here; no login/browser is needed afterward until it expires.
                    Ignored on the hosted remote unless MCP_ALLOW_SHARED_IDENTITY
                    is set, since there it would be a shared fallback identity.

Hardening knobs for the hosted (streamable-http) remote:
  MCP_ALLOWED_BASE_URLS    Comma-separated backends a caller may target, each
                           optionally paired with its usermanagement URL:
                           'https://queryservice.example.org=https://usermanagement.example.org'.
                           BRAINKB_URL is always allowed. Anything else is
                           refused — the base URL is the destination of requests
                           that carry credentials, so an arbitrary value is both
                           SSRF and credential exfiltration.
  MCP_TRUSTED_PROXIES      Proxy IPs or CIDR blocks whose X-Forwarded-For may be
                           believed. Empty by default; an unvalidated forwarding
                           header lets a caller mint a new identity per request and
                           evade every rate limit. Prefer CIDRs behind an ALB — its
                           ENI addresses change when it scales.
  MCP_INGEST_ROOT          Directory brainkb_ingest_files may read from. File
                           ingest is disabled on the remote unless this is set,
                           because paths resolve on the SERVER's filesystem.
  MCP_UPLOAD_ENABLED       POST /upload — an AUTHENTICATED HTTP endpoint that stages
                           an RDF file for ingest. This is how a large local file
                           reaches the hosted remote: the client streams it from disk,
                           so the bytes never pass through a model's context. On by
                           default; 5 GB per file, expiring after MCP_UPLOAD_TTL_MIN.
  MCP_UPLOAD_DIR           Where staged uploads live (default: a temp dir).

  MCP_ALLOW_SHARED_IDENTITY  Opt in to BRAINKB_TOKEN as an ambient identity
                           (single-user HTTP deployments only).

There is NO email/password auto-login: a baked-in credential could silently act
as a fallback identity and mis-attribute another user's actions, so it was
removed. Authenticate with BRAINKB_TOKEN, brainkb_login / brainkb_globus_login,
brainkb_use_token, or a per-caller 'Authorization: Bearer' header.

Credentials/token live only in this process's memory; the token is never logged
or returned to the model.
"""

from __future__ import annotations

import hashlib
import html
import importlib
import ipaddress
import json as jsonlib
import os
import re
import sys
import tempfile
import threading
import time
import weakref
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
import qa_registry
from mcp.server.fastmcp import FastMCP

# Registry identity (https://registry.modelcontextprotocol.io — "org.brainkb/brainkb").
SERVER_NAME = "org.brainkb/brainkb"
SERVER_VERSION = "0.1.0"

# Host/port only matter for the streamable-http transport (the hosted remote at
# https://mcp.brainkb.org/mcp); stdio ignores them.
mcp = FastMCP(
    "brainkb",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8080")),
)

_DEFAULT_URL = os.getenv("BRAINKB_URL", "http://localhost:8010").rstrip("/")

# Which transport we were started with. Several protections below are only correct
# for the multi-user hosted remote (streamable-http), where callers are untrusted
# and anonymous; over stdio the caller IS the local user, so the same restrictions
# would only get in their way.
_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").lower().replace("_", "-")
_IS_REMOTE = _TRANSPORT in ("http", "streamable-http")

_TIMEOUT = httpx.Timeout(120.0, connect=15.0)
# File ingest streams potentially very large uploads (TTL/JSON-LD up to ~5GB per
# user). A fixed read/write timeout would abort a legitimate large upload, so we
# disable read/write timeouts here and keep only a connect bound. Files stream
# from disk (httpx multipart), so this does not buffer the whole file in memory.
_UPLOAD_TIMEOUT = httpx.Timeout(None, connect=30.0)

# Per-session login store (fallback for local/stdio use only), keyed by the MCP
# session OBJECT itself. The hosted multi-user remote does NOT rely on this — each
# call carries the caller's own token via the Authorization header (stateless).
#
# Keyed weakly by the session object, never by id(): an address is recycled once
# the object is collected, so an id()-keyed store can hand a brand-new session the
# previous occupant's cached refresh token (wrong-user auth), and it also loses
# writes whenever the address shifts between two calls of the same login. The weak
# keys additionally let dead sessions' credentials be reclaimed automatically.
_SESSIONS: "weakref.WeakKeyDictionary[Any, Dict[str, Any]]" = weakref.WeakKeyDictionary()


# --------------------------------------------------------------------------- #
# multi-user auth resolution
# --------------------------------------------------------------------------- #

def _decode_sub(token: str) -> Optional[str]:
    """Read the 'sub' (email) claim from a JWT without verifying it — used only to
    fill the user_id parameter. The server verifies the token for real."""
    import base64
    import json as _json
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return _json.loads(base64.urlsafe_b64decode(payload)).get("sub")
    except Exception:
        return None


_warned_unkeyable = False


def _session_key() -> Optional[Any]:
    """The current MCP session object, used as the _SESSIONS key.

    Returns the object, not id(it): addresses are reused after garbage collection,
    which both drops writes (login cached under one address, read under another)
    and can hand a new session the previous occupant's cached token.

    A session that cannot be weak-referenced or hashed cannot be cached under.
    That returns None — but LOUDLY: it is reported on stderr, and every caller of
    this function surfaces the condition to the user instead of silently behaving
    as though no one had logged in.
    """
    global _warned_unkeyable
    try:
        sess = mcp.get_context().session
    except Exception:
        # No MCP request context (direct/unit-test call) — expected, not an error.
        return None
    try:
        weakref.ref(sess)
        hash(sess)
    except TypeError as e:
        if not _warned_unkeyable:
            _warned_unkeyable = True
            print(
                f"[brainkb-mcp] WARNING: MCP session objects of type "
                f"{type(sess).__name__!r} cannot be used as a session-cache key "
                f"({e}). Per-session login is DISABLED — authenticate with "
                f"BRAINKB_TOKEN or an 'Authorization: Bearer' header instead.",
                file=sys.stderr, flush=True,
            )
        return None
    return sess


def _request_headers() -> Dict[str, str]:
    """Headers of the current inbound MCP request (empty for stdio / no request)."""
    try:
        req = getattr(mcp.get_context().request_context, "request", None)
        if req is not None and getattr(req, "headers", None) is not None:
            return {k.lower(): v for k, v in req.headers.items()}
    except Exception:
        pass
    return {}


# --------------------------------------------------------------------------- #
# Rate limiting / abuse protection
# --------------------------------------------------------------------------- #
# In-process, per-caller fixed-window limiter. This is a first line of defence
# against abuse / brute-force / accidental floods on the hosted remote — it is
# NOT a substitute for an edge proxy / WAF / API gateway for real DDoS, and it is
# per-process (each worker keeps its own counters). Callers are keyed by client
# IP (X-Forwarded-For / X-Real-IP / socket peer) so header-authenticated users
# behind the same proxy are still separated by source IP. stdio (local) is exempt.

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


_RL_ENABLED = os.getenv("MCP_RATELIMIT_ENABLED", "true").strip().lower() not in ("0", "false", "no")
_RL_WINDOW = max(1, _int_env("MCP_RATELIMIT_WINDOW_SEC", 60))
_RL_READ = _int_env("MCP_RATELIMIT_READ_PER_MIN", 120)     # GET-style reads
_RL_WRITE = _int_env("MCP_RATELIMIT_WRITE_PER_MIN", 40)    # mutations / ingest
_RL_ADMIN = _int_env("MCP_RATELIMIT_ADMIN_PER_MIN", 30)    # usermanagement admin
_RL_AUTH = _int_env("MCP_RATELIMIT_AUTH_PER_MIN", 8)       # register / login (brute-force)

# Payload guards (0 disables). Bound resource use per ingest call.
_MAX_INGEST_BYTES = _int_env("MCP_MAX_INGEST_BYTES", 10_000_000)  # per raw-text ingest
_MAX_INGEST_FILES = _int_env("MCP_MAX_INGEST_FILES", 50)          # per file-ingest call

_RL_MAX_KEYS = _int_env("MCP_RATELIMIT_MAX_KEYS", 20000)

# Reverse proxies whose X-Forwarded-For / X-Real-IP we believe. EMPTY BY DEFAULT:
# an unvalidated forwarding header is caller-controlled, so honouring it from any
# peer lets one client rotate a fresh identity per request (bypassing every limit,
# including the brute-force bucket) and lets it pin a VICTIM's IP to exhaust their
# quota. Set MCP_TRUSTED_PROXIES to your ALB/nginx source IPs when deployed behind
# one; until then the socket peer — which cannot be forged — is used.
#
# Entries are exact IPs or CIDR blocks. CIDR matters for an ALB: its source
# addresses are ENI IPs in the load-balancer subnets and they CHANGE as the ALB
# scales, so pinning exact IPs silently degrades to "all callers share one bucket"
# the next time AWS adds an ENI. Give the ALB subnet CIDRs instead.
def _parse_trusted_proxies() -> Tuple[set, List[Any], List[str]]:
    exact: set = set()
    nets: List[Any] = []
    bad: List[str] = []
    for p in os.getenv("MCP_TRUSTED_PROXIES", "").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            if "/" in p:
                nets.append(ipaddress.ip_network(p, strict=False))
            else:
                exact.add(str(ipaddress.ip_address(p)))
        except ValueError:
            bad.append(p)  # reported at startup; never silently trusted
    return exact, nets, bad


_TRUSTED_PROXIES, _TRUSTED_NETS, _TRUSTED_BAD = _parse_trusted_proxies()


def _peer_trusted(peer: Optional[str]) -> bool:
    """True if this socket peer is a configured reverse proxy."""
    if not peer:
        return False
    if peer in _TRUSTED_PROXIES:
        return True
    if not _TRUSTED_NETS:
        return False
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_NETS)

_RL_LOCK = threading.Lock()
_RL_BUCKETS: Dict[Tuple[str, str], list] = {}  # (client_id, bucket) -> [window, count]
_RL_LAST_SWEEP = [-1]  # window index of the most recent eviction sweep


def _peer_ip() -> Optional[str]:
    """Socket peer address of the inbound request (None for stdio)."""
    try:
        req = getattr(mcp.get_context().request_context, "request", None)
        client = getattr(req, "client", None)
        if client and getattr(client, "host", None):
            return client.host
    except Exception:
        pass
    return None


def _client_id() -> str:
    """Identify the caller for rate-limiting. Prefer source IP so many users
    behind one reverse proxy are still throttled per origin; fall back to the MCP
    session; 'local' for stdio (exempt).

    Forwarding headers are only trusted from a peer in MCP_TRUSTED_PROXIES, and we
    take the RIGHTMOST entry — the hop that trusted proxy actually observed. The
    leftmost entry is whatever the client chose to send and is trivially spoofed.
    """
    peer = _peer_ip()
    if _peer_trusted(peer):
        hdrs = _request_headers()
        xff = hdrs.get("x-forwarded-for", "")
        if xff:
            return "ip:" + xff.split(",")[-1].strip()
        xri = hdrs.get("x-real-ip", "")
        if xri:
            return "ip:" + xri.strip()
    if peer:
        return "ip:" + peer
    sk = _session_key()
    return f"sess:{sk}" if sk is not None else "local"


def _rate_ok(bucket: str, limit: int, cid: Optional[str] = None) -> bool:
    """True if this caller may proceed in `bucket`; False if the limit is hit.
    `cid` identifies the caller outside an MCP request (plain HTTP routes)."""
    if not _RL_ENABLED or limit <= 0:
        return True
    cid = cid or _client_id()
    if cid == "local":
        return True  # stdio single-user — nothing to throttle
    now = time.time()
    win = int(now // _RL_WINDOW)
    key = (cid, bucket)
    with _RL_LOCK:
        slot = _RL_BUCKETS.get(key)
        if slot is None or slot[0] != win:
            if len(_RL_BUCKETS) >= _RL_MAX_KEYS:
                # Evict entries from previous windows — but at most once per
                # window: the sweep is O(len(_RL_BUCKETS)) and runs under the
                # global lock, so doing it on every insert while the map is full
                # would itself be the denial of service.
                if _RL_LAST_SWEEP[0] != win:
                    _RL_LAST_SWEEP[0] = win
                    for k, v in list(_RL_BUCKETS.items()):
                        if v[0] != win:
                            _RL_BUCKETS.pop(k, None)
                if len(_RL_BUCKETS) >= _RL_MAX_KEYS:
                    # Still full: every entry belongs to the CURRENT window, i.e. a
                    # flood of distinct callers. Refuse the new key instead of
                    # growing without bound (fail closed — a genuinely new caller
                    # may be turned away for the rest of this window).
                    return False
            _RL_BUCKETS[key] = [win, 1]
            return True
        if slot[1] >= limit:
            return False
        slot[1] += 1
        return True


def _rl_error(bucket: str, limit: int) -> Dict[str, Any]:
    return {
        "error": True,
        "status_code": 429,
        "detail": (f"Rate limit exceeded for '{bucket}' ({limit} requests per "
                   f"{_RL_WINDOW}s). Slow down and retry shortly."),
    }


class _NotAuthed(RuntimeError):
    pass


class _NoCredentials(_NotAuthed):
    """No credential of any kind was supplied (no header, session or env PAT).

    Distinct from a credential that was supplied but failed (expired/revoked/bad):
    only this case may fall back to an anonymous read of public content. A caller
    whose token is broken must see the auth error, not silently get the anonymous
    view (which looks like "your private space doesn't exist")."""


# --------------------------------------------------------------------------- #
# Backend URL allowlist (SSRF / credential-exfiltration guard)
# --------------------------------------------------------------------------- #
# The backend base URL is caller-influenced: via the `X-BrainKB-Base-URL` header on
# the hosted remote, and via the `base_url` argument of the login tools. Both are
# used as the target of requests that CARRY CREDENTIALS — the caller's bearer
# token, the env Personal Access Token, a session's stored password. Accepting an
# arbitrary value therefore gives any caller two things at once:
#
#   * SSRF — the server fetches an attacker-chosen host (link-local metadata at
#     169.254.169.254, internal load balancers, anything in the VPC) and returns
#     the response body to them, and
#   * credential theft — BRAINKB_TOKEN / a session PAT / stored credentials get
#     POSTed to that host by the token-exchange helpers.
#
# So a base URL is only honoured if it is explicitly configured. Loopback is also
# allowed under stdio, where the caller is the local user and pointing at a dev
# backend on another port is routine.
#
# MCP_ALLOWED_BASE_URLS is a comma-separated list of query_service base URLs, each
# optionally paired with its usermanagement URL:
#     MCP_ALLOWED_BASE_URLS=https://queryservice.example.org=https://usermanagement.example.org,http://localhost:8011

def _norm_base(u: Optional[str]) -> str:
    return (u or "").strip().rstrip("/")


def _parse_allowed() -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {_norm_base(_DEFAULT_URL): None}  # paired with _UM_URL
    for entry in os.getenv("MCP_ALLOWED_BASE_URLS", "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        q, _, u = entry.partition("=")
        q = _norm_base(q)
        if q:
            out[q] = _norm_base(u) or None
    return out


_ALLOWED_BASES: Dict[str, Optional[str]] = _parse_allowed()


def _host_local(url: str) -> bool:
    """True if `url` never leaves the machine — loopback, or Docker's host bridge.

    Deliberately separate from `_is_loopback`, which decides whether a
    caller-supplied base URL is permitted under stdio. Widening a security predicate
    to quiet a startup warning is how a check stops meaning what its name says.
    """
    try:
        host = httpx.URL(url).host
    except Exception:
        return False
    return _is_loopback(url) or host in ("host.docker.internal", "host-gateway")


def _is_loopback(url: str) -> bool:
    try:
        return httpx.URL(url).host in ("localhost", "127.0.0.1", "::1")
    except Exception:
        return False


def _allowed_base(candidate: Optional[str] = "") -> str:
    """Validate a caller-supplied backend base URL against the allowlist.

    Returns the default backend when nothing was supplied; raises _NotAuthed for an
    unknown host rather than silently falling back, so a misconfiguration is
    visible instead of quietly routing to the wrong backend.
    """
    base = _norm_base(candidate)
    if not base:
        return _norm_base(_DEFAULT_URL)
    if base in _ALLOWED_BASES:
        return base
    if not _IS_REMOTE and _is_loopback(base):
        return base
    raise _NotAuthed(
        f"Backend URL {base!r} is not allowed. Credentials are only ever sent to "
        "configured backends; add it to the MCP_ALLOWED_BASE_URLS environment "
        "variable (as '<query-url>' or '<query-url>=<usermanagement-url>') if it "
        "is genuinely yours."
    )


# --------------------------------------------------------------------------- #
# Phase 2 SSO: single login -> per-service token exchange
# --------------------------------------------------------------------------- #
# usermanagement is the single issuer. A login mints a REFRESH token; we exchange
# it for a short-lived per-service ACCESS token (aud=<service>) whenever a tool
# calls that service. A token minted for one service can't be replayed against
# another (containment). Legacy per-service /api/token still works as a fallback,
# so this keeps working against a backend that doesn't yet expose SSO.

_AUD_QUERY = "query_service"
_AUD_UM = "usermanagement"

# Escape hatch for a deliberately single-user HTTP deployment: allow the env
# BRAINKB_TOKEN to serve as the identity for callers who send no credential of
# their own. Off by default — on a shared remote it makes every anonymous caller
# act as the token's owner.
_ALLOW_SHARED_IDENTITY = os.getenv("MCP_ALLOW_SHARED_IDENTITY", "").strip().lower() in ("1", "true", "yes")

# A cached login is not forever: the session expires with its refresh token (or,
# as a hard cap even if credentials are cached, after MCP_SESSION_TTL_MIN). When it
# lapses the session is forgotten and the user must log in again.
_SESSION_TTL_MIN = _int_env("MCP_SESSION_TTL_MIN", 720)  # fallback when no exp claim


def _claims(token: str) -> Dict[str, Any]:
    """Unverified claims of a JWT (for routing only; the server verifies for real)."""
    import base64
    import json as _json
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return _json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def _session_expiry(token: str) -> float:
    """Absolute expiry (epoch seconds) for a cached session: the token's `exp`
    claim, capped by MCP_SESSION_TTL_MIN, falling back to now + TTL if no exp."""
    cap = time.time() + _SESSION_TTL_MIN * 60
    exp = _claims(token).get("exp")
    if isinstance(exp, (int, float)) and exp > 0:
        return min(float(exp), cap)
    return cap


def _session_expired(sd: dict) -> bool:
    exp = sd.get("expires_at")
    return bool(exp) and time.time() >= float(exp)


def _um_base(base: str) -> str:
    """usermanagement base URL for SSO login/exchange, given the query_service base.

    `base` must already have passed _allowed_base(). The port rewrite below is only
    a convenience for the dev layout (:8010 -> :8004); it cannot be relied on for a
    real deployment (e.g. https://queryservice.example.org has no :8010, and the old code
    silently sent usermanagement traffic — PAT exchanges, admin calls — to the
    query_service host instead). Configure the pairing explicitly there.
    """
    base = _norm_base(base)
    if base == _norm_base(_DEFAULT_URL):
        return _UM_URL
    paired = _ALLOWED_BASES.get(base)
    if paired:
        return paired
    if ":8010" in base:
        return base.replace(":8010", ":8004")
    raise _NotAuthed(
        f"No usermanagement URL is configured for backend {base!r}. Pair them in "
        "MCP_ALLOWED_BASE_URLS as '<query-url>=<usermanagement-url>'."
    )


def _sso_login(um: str, email: str, password: str) -> Optional[str]:
    """POST /api/auth/login -> refresh token, or None (incl. a backend without SSO)."""
    try:
        r = httpx.post(f"{um}/api/auth/login", json={"email": email, "password": password}, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("refresh_token")
    except Exception:
        return None


def _exchange(um: str, refresh: str, audience: str) -> Optional[tuple]:
    """POST /api/auth/exchange -> (access_token, ttl_seconds), or None."""
    try:
        r = httpx.post(f"{um}/api/auth/exchange",
                       headers={"Authorization": f"Bearer {refresh}"},
                       json={"audience": audience}, timeout=30)
        r.raise_for_status()
        d = r.json()
        return d["access_token"], int(d.get("expires_in", 900))
    except Exception:
        return None


_PAT_PREFIX = "brainkb_pat_"


def _is_pat(token: str) -> bool:
    """True if the string is a BrainKB Personal Access Token (opaque, not a JWT)."""
    return bool(token) and token.strip().startswith(_PAT_PREFIX)


def _pat_exchange(um: str, pat: str, audience: str) -> Optional[tuple]:
    """POST /api/auth/pat/exchange -> (access_token, ttl_seconds), or None. A PAT is
    an opaque, revocable, long-lived credential; it is exchanged for a short-lived
    per-service access token exactly like a refresh token, but needs no browser."""
    try:
        r = httpx.post(f"{um}/api/auth/pat/exchange",
                       json={"token": pat, "audience": audience}, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        d = r.json()
        return d["access_token"], int(d.get("expires_in", 900))
    except Exception:
        return None


def _legacy_login(base: str, email: str, password: str) -> Optional[str]:
    """Legacy per-service password login -> access token, or None. Prefers
    /api/login; falls back to the deprecated /api/token for older backends."""
    for path in ("/api/login", "/api/token"):
        try:
            r = httpx.post(f"{base}{path}", json={"email": email, "password": password}, timeout=30)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            return r.json().get("access_token")
        except Exception:
            continue
    return None


def _cached_access(sd: dict, aud: str) -> Optional[str]:
    acc = (sd.get("access") or {}).get(aud)
    if acc and acc[1] - time.time() > 30:
        return acc[0]
    return None


def _store_access(sd: dict, aud: str, token: str, ttl: int) -> None:
    sd.setdefault("access", {})[aud] = (token, time.time() + ttl)


def _identify_raw(raw: str, base: str, audience: str = "") -> Dict[str, str]:
    """Auth context for one bearer token, independent of the MCP request context.

    This is the token-handling half of `_token_for`'s header pass-through, factored
    out because the /upload route is a plain HTTP handler: it has a request but no
    MCP session, so it cannot read the context headers. Sharing this code is the
    point — an upload endpoint with its own, slightly different notion of who the
    caller is would be a second authorization surface to keep in step, and the one
    that drifts is the one that leaks.
    """
    audience = audience or _AUD_QUERY
    um = _um_base(base)
    # A Personal Access Token is opaque (not a JWT); exchange it for the requested
    # service audience — one PAT unlocks every service.
    if _is_pat(raw):
        ex = _pat_exchange(um, raw, audience)
        if ex:
            return {"url": base, "token": ex[0], "email": _decode_sub(ex[0]) or ""}
        raise _NotAuthed(f"Could not exchange the provided access token for '{audience}' "
                         "(it may be expired or revoked).")
    cl = _claims(raw)
    if cl.get("typ") == "refresh" or cl.get("aud") == "brainkb-auth":
        ex = _exchange(um, raw, audience)
        if ex:
            return {"url": base, "token": ex[0], "email": cl.get("sub") or ""}
        raise _NotAuthed(f"Could not exchange the provided refresh token for '{audience}'.")
    return {"url": base, "token": raw, "email": cl.get("sub") or _decode_sub(raw) or ""}


def _token_for(audience: str) -> Dict[str, str]:
    """Resolve an auth context {url, token, email} whose token is valid for
    `audience` (query_service | usermanagement). Multi-user safe.

    Precedence:
      1. Caller's `Authorization: Bearer <token>` header (a PAT or REFRESH token is
         exchanged for the requested audience — unlocks every service from one
         header; any other token is used as-is). `X-BrainKB-Base-URL` optionally
         overrides the backend URL.
      2. Env `BRAINKB_TOKEN` Personal Access Token.
      3. Per-session login set by `brainkb_login` / `brainkb_finish_login` /
         `brainkb_use_token` (with re-login from that session's own stored
         credentials if its refresh token has expired).

    There is deliberately NO env email/password auto-login: a baked-in credential
    could silently shadow a real login and mis-attribute actions to the wrong user.
    """
    hdrs = _request_headers()
    base = _allowed_base(hdrs.get("x-brainkb-base-url"))
    um = _um_base(base)
    key = _session_key()

    # 1) header pass-through
    authz = hdrs.get("authorization", "")
    if authz[:7].lower() == "bearer " and authz[7:].strip():
        return _identify_raw(authz[7:].strip(), base, audience)

    # 2) per-session (cached access -> refresh -> legacy)
    if key is not None and key in _SESSIONS and _session_expired(_SESSIONS[key]):
        # Session lapsed — forget it so cached creds aren't silently reused; the
        # caller must log in again.
        _SESSIONS.pop(key, None)
    if key is not None and key in _SESSIONS:
        sd = _SESSIONS[key]
        cached = _cached_access(sd, audience)
        if cached:
            return {"url": sd.get("url", base), "token": cached, "email": sd.get("email", "")}
        if sd.get("pat"):
            ex = _pat_exchange(um, sd["pat"], audience)
            if ex:
                _store_access(sd, audience, ex[0], ex[1])
                return {"url": sd.get("url", base), "token": ex[0],
                        "email": sd.get("email") or _decode_sub(ex[0]) or ""}
        if sd.get("refresh"):
            ex = _exchange(um, sd["refresh"], audience)
            if ex:
                _store_access(sd, audience, ex[0], ex[1])
                return {"url": sd.get("url", base), "token": ex[0], "email": sd.get("email", "")}
        if sd.get("legacy_token") and audience == _AUD_QUERY:
            return {"url": sd.get("url", base), "token": sd["legacy_token"], "email": sd.get("email", "")}

    # 3) env Personal Access Token (browser-free: mint once, paste into config).
    #    IGNORED on the hosted remote unless explicitly opted into: there it is an
    #    ambient fallback identity with exactly the identity-shadowing problem that
    #    got env email/password removed — an anonymous caller who sends no
    #    Authorization header would silently execute as the PAT's owner, and the
    #    result would even be cached into their session below.
    pat_env = os.getenv("BRAINKB_TOKEN", "").strip()
    if _is_pat(pat_env) and (not _IS_REMOTE or _ALLOW_SHARED_IDENTITY):
        ex = _pat_exchange(um, pat_env, audience)
        if ex:
            email = _decode_sub(ex[0]) or ""
            if key is not None:
                sd = _SESSIONS.setdefault(key, {})
                sd.update({"pat": pat_env, "email": email or sd.get("email", ""),
                           "url": base, "access": sd.get("access", {})})
                _store_access(sd, audience, ex[0], ex[1])
            return {"url": base, "token": ex[0], "email": email}

    # 4) session credential re-login — ONLY continues an explicit password
    #    brainkb_login within THIS session (e.g. after its refresh token expired).
    #    There is NO env auto-login: a baked-in BRAINKB_EMAIL/BRAINKB_PASSWORD must
    #    never silently act as a fallback identity (that caused calls to run as the
    #    wrong user). Use BRAINKB_TOKEN, brainkb_globus_login, brainkb_use_token,
    #    or an Authorization header instead.
    em = pw = None
    if key is not None and key in _SESSIONS:
        sd = _SESSIONS[key]
        em, pw = sd.get("email"), sd.get("password")
    if em and pw:
        refresh = _sso_login(um, em, pw)
        if refresh:
            ex = _exchange(um, refresh, audience)
            if ex:
                if key is not None:
                    sd = _SESSIONS.setdefault(key, {})
                    sd.update({"refresh": refresh, "email": em, "password": pw, "url": base,
                               "expires_at": _session_expiry(refresh)})
                    _store_access(sd, audience, ex[0], ex[1])
                return {"url": base, "token": ex[0], "email": em}
        # legacy fallback (per-service /api/token)
        legacy = _legacy_login(base if audience == _AUD_QUERY else um, em, pw)
        if legacy:
            if key is not None and audience == _AUD_QUERY:
                _SESSIONS.setdefault(key, {}).update(
                    {"legacy_token": legacy, "email": em, "password": pw, "url": base})
            return {"url": base, "token": legacy, "email": em}

    raise _NoCredentials(
        "Not authenticated (or your session has expired — sessions don't last "
        "forever). Easiest: set BRAINKB_TOKEN to a Personal Access Token "
        "(brainkb_pat_...) in your config, or pass one with brainkb_use_token(pat) "
        "— no browser needed. To sign in as your account, use "
        "brainkb_globus_login() (Globus/ORCID/GitHub) — it returns a URL to open, "
        "then brainkb_finish_login(code). Do NOT expect a password prompt: password "
        "login (brainkb_login) is legacy and only for explicit password accounts. "
        "On the hosted remote, send 'Authorization: Bearer <token>' (a PAT or "
        "refresh token unlocks all services)."
    )


def _resolve() -> Dict[str, str]:
    """Auth context for query_service calls."""
    return _token_for(_AUD_QUERY)


def _me() -> str:
    return _resolve()["email"]


def _seg(value: str) -> str:
    """Escape a caller-supplied value for use as exactly ONE URL path segment.

    urllib's quote() defaults to safe="/", which leaves slashes intact — so a slug
    of '../../api/admin/users' passed through quote() rewrites the request path
    (httpx collapses the dot segments before sending). safe="" percent-encodes the
    slashes so the value stays a single segment. Bare '.'/'..' still survive, since
    dots are unreserved and never encoded; _path_ok below rejects those.
    """
    return quote((value or "").strip(), safe="")


def _path_ok(path: str, *, allow_trailing_slash: bool = False) -> bool:
    """False if a request path contains an empty or dot segment — i.e. something
    that would traverse to a different endpoint than the tool intended.

    allow_trailing_slash is for the handful of backend routes that are DECLARED
    with a trailing slash (e.g. query_service's '/api/query/sparql/'), where the
    final empty segment is part of the literal template rather than an
    interpolated value. It is opt-in per call, not the default, because a trailing
    empty segment from an *interpolated* value is exactly the dangerous case:
    f"/api/auth/tokens/{_seg(token_id)}" with an empty id would silently address
    the collection instead of one item.
    """
    segs = path.split("/")[1:]
    if allow_trailing_slash and segs and segs[-1] == "":
        segs = segs[:-1]
    return not any(p in ("", ".", "..") for p in segs)


_BAD_PATH = {"error": True, "status_code": 400,
             "detail": "Invalid or empty identifier in the request path."}


def _result(resp: httpx.Response) -> Any:
    """Return parsed JSON on success, else a compact error dict (never raises)."""
    ok = resp.status_code < 400
    body: Any
    try:
        body = resp.json()
    except Exception:
        body = (resp.text or "")[:2000]
    if ok:
        return body
    return {"error": True, "status_code": resp.status_code, "detail": body}


# The ONLY backend paths an unauthenticated caller may reach, all read-only GETs
# the backend itself serves anonymously (public spaces). Anonymous access is
# GET-only by construction (_post/_put/_delete/_um never fall back), and this
# allowlist stops a future `anonymous=True` on any other path from widening it.
_ANON_READ_PATHS = (
    re.compile(r"^/api/spaces$"),
    re.compile(r"^/api/spaces/[^/]+/data$"),
    re.compile(r"^/api/search$"),
)


def _anon_read_ok(path: str) -> bool:
    return any(rx.match(path) for rx in _ANON_READ_PATHS)


def _get(path: str, params: Optional[Dict[str, Any]] = None, *,
         allow_trailing_slash: bool = False, anonymous: bool = False) -> Any:
    """GET a query_service path as the caller.

    anonymous=True is for endpoints the backend serves to unauthenticated callers
    (public spaces: list, read, search). If the caller supplied NO credential, the
    request goes out without an Authorization header and the backend returns only
    public content. A credential that was supplied but failed still errors."""
    if not _path_ok(path, allow_trailing_slash=allow_trailing_slash):
        return dict(_BAD_PATH)
    if not _rate_ok("read", _RL_READ):
        return _rl_error("read", _RL_READ)
    try:
        try:
            ctx = _resolve()
            headers = {"Authorization": f"Bearer {ctx['token']}"}
            base, is_anon = ctx["url"], False
        except _NoCredentials:
            if not (anonymous and _anon_read_ok(path)):
                raise
            base = _allowed_base(_request_headers().get("x-brainkb-base-url"))
            headers, is_anon = {}, True
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.get(f"{base}{path}", params=params or {}, headers=headers)
        out = _result(resp)
        if is_anon and isinstance(out, dict) and out.get("error") \
                and resp.status_code in (401, 403):
            out["anonymous"] = True
            out["hint"] = ("Read anonymously (not logged in), so only public spaces "
                           "are visible. Log in (brainkb_globus_login or a PAT) to "
                           "read private spaces you are a member of.")
        return out
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def _post(path: str, params: Optional[Dict[str, Any]] = None,
          json: Any = None, content: Optional[bytes] = None,
          ctype: Optional[str] = None, files: Any = None,
          timeout: Optional[httpx.Timeout] = None) -> Any:
    if not _path_ok(path):
        return dict(_BAD_PATH)
    if not _rate_ok("write", _RL_WRITE):
        return _rl_error("write", _RL_WRITE)
    try:
        ctx = _resolve()
        headers = {"Authorization": f"Bearer {ctx['token']}"}
        if ctype:
            headers["Content-Type"] = ctype
        with httpx.Client(timeout=timeout or _TIMEOUT) as c:
            resp = c.post(f"{ctx['url']}{path}", params=params or {}, headers=headers,
                          json=json, content=content, files=files)
        return _result(resp)
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def _patch(path: str, json: Any = None) -> Any:
    if not _path_ok(path):
        return dict(_BAD_PATH)
    if not _rate_ok("write", _RL_WRITE):
        return _rl_error("write", _RL_WRITE)
    try:
        ctx = _resolve()
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.patch(f"{ctx['url']}{path}",
                           headers={"Authorization": f"Bearer {ctx['token']}"}, json=json)
        return _result(resp)
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def _delete(path: str) -> Any:
    if not _path_ok(path):
        return dict(_BAD_PATH)
    if not _rate_ok("write", _RL_WRITE):
        return _rl_error("write", _RL_WRITE)
    try:
        ctx = _resolve()
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.delete(f"{ctx['url']}{path}", headers={"Authorization": f"Bearer {ctx['token']}"})
        return _result(resp)
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    except Exception as e:
        return {"error": True, "detail": str(e)}


# --------------------------------------------------------------------------- #
# usermanagement service client (admin: users, roles, activation)
# --------------------------------------------------------------------------- #
# The usermanagement service (:8004) is a SEPARATE service. Under SSO we reach it
# with an access token minted for aud=usermanagement (via the single login +
# exchange in _token_for); no separate login is needed. Legacy /api/token remains
# the fallback inside _token_for. Admin endpoints require an Admin/SuperAdmin role.
_UM_URL = (os.getenv("USERMANAGEMENT_URL") or _DEFAULT_URL.replace(":8010", ":8004")).rstrip("/")


def _um(method: str, path: str, json: Any = None, params: Any = None, _retry: bool = True) -> Any:
    if not _path_ok(path):
        return dict(_BAD_PATH)
    if _retry and not _rate_ok("admin", _RL_ADMIN):
        return _rl_error("admin", _RL_ADMIN)
    try:
        um = _um_base(_allowed_base(_request_headers().get("x-brainkb-base-url")))
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    try:
        ctx = _token_for(_AUD_UM)
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.request(method, f"{um}{path}",
                             headers={"Authorization": f"Bearer {ctx['token']}"}, json=json, params=params)
        if resp.status_code == 401 and _retry:
            # The access token may have expired — drop the cached usermanagement
            # token so _token_for re-exchanges, and retry once.
            key = _session_key()
            if key is not None and key in _SESSIONS:
                (_SESSIONS[key].get("access") or {}).pop(_AUD_UM, None)
            return _um(method, path, json=json, params=params, _retry=False)
        return _result(resp)
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def _um_profile_id(email: str) -> Optional[int]:
    users = _um("GET", "/api/admin/users", params={"q": email, "limit": 200})
    if isinstance(users, list):
        for u in users:
            if (u.get("email") or "").lower() == email.lower():
                return u.get("profile_id")
    return None


# --------------------------------------------------------------------------- #
# auth / session
# --------------------------------------------------------------------------- #

# NOTE: there is no self-registration tool. Users are onboarded by signing in with
# Globus/ORCID/GitHub (brainkb_globus_login), which auto-creates and links their
# profile on first login. There is no separate "register" step.


@mcp.tool()
def brainkb_login(email: str, password: str, base_url: str = "") -> str:
    """Authenticate to BrainKB with the user's credentials and cache the JWT for
    THIS session only (isolated per caller). The password/token are never echoed.

    Uses single sign-on: one login mints a refresh token, cached for THIS session,
    which is exchanged on demand for per-service access tokens (query_service,
    usermanagement, …). Falls back to a legacy per-service token if the backend
    has no SSO. On the hosted multi-user remote you can skip this and instead have
    your client send an 'Authorization: Bearer <token>' header (a refresh token
    unlocks all services)."""
    if not _rate_ok("auth", _RL_AUTH):
        return _rl_error("auth", _RL_AUTH)["detail"]
    key = _session_key()
    try:
        # base_url decides where this password is sent — allowlist it.
        base = _allowed_base(base_url)
        um = _um_base(base)
    except _NotAuthed as e:
        return str(e)
    try:
        # SSO first: single login -> refresh token (exchanged per service later).
        refresh = _sso_login(um, email, password)
        if refresh:
            if key is None:
                return ("Logged in, but this session could not be identified to cache the "
                        "token; send an Authorization header instead.")
            # Credentials kept in memory for THIS session only, to re-login if the
            # refresh token expires. Never echoed.
            _SESSIONS[key] = {"refresh": refresh, "url": base, "email": email,
                              "password": password, "access": {},
                              "expires_at": _session_expiry(refresh)}
            return f"Logged in as {email} at {base} (SSO; this session, expires when the token does)."
        # Legacy fallback: per-service query_service token.
        legacy = _legacy_login(base, email, password)
        if not legacy:
            return "Login failed. Check email/password and base_url."
        if key is None:
            return ("Logged in (legacy), but this session could not be identified to cache "
                    "the token; send an Authorization header instead.")
        _SESSIONS[key] = {"legacy_token": legacy, "url": base, "email": email,
                          "password": password, "access": {},
                          "expires_at": _session_expiry(legacy)}
        return f"Logged in as {email} at {base} (this session)."
    except Exception as e:
        return f"Login error: {e}"


@mcp.tool()
def brainkb_globus_login(provider: str = "globus", base_url: str = "") -> str:
    """Start an OAuth login (Globus / ORCID / GitHub) for THIS session — use this
    instead of brainkb_login when the user signs in with Globus rather than a
    password. Returns a URL to open in a browser; after signing in, the page shows
    a short one-time code — pass it to brainkb_finish_login(code) to complete.
    (The browser step is unavoidable: only the user can consent at the provider.)"""
    if not _rate_ok("auth", _RL_AUTH):
        return _rl_error("auth", _RL_AUTH)["detail"]
    try:
        um = _um_base(_allowed_base(base_url))
    except _NotAuthed as e:
        return str(e)
    try:
        r = httpx.post(f"{um}/api/auth/cli/start", json={"provider": provider}, timeout=30)
        r.raise_for_status()
        url = r.json()["authorize_url"]
        return (f"Open this URL in a browser and sign in with {provider}:\n{url}\n\n"
                "After signing in, the page shows a short code — call "
                "brainkb_finish_login(\"<code>\") with it to finish.")
    except httpx.HTTPStatusError as e:
        return (f"Could not start {provider} login (HTTP {e.response.status_code}). "
                f"Is {provider} OAuth configured on the server?")
    except Exception as e:
        return f"Login start error: {e}"


@mcp.tool()
def brainkb_finish_login(code: str, base_url: str = "") -> str:
    """Complete an OAuth login started with brainkb_globus_login by exchanging the
    one-time code shown in the browser for a session token. The code is single-use
    and never echoed back."""
    if not _rate_ok("auth", _RL_AUTH):
        return _rl_error("auth", _RL_AUTH)["detail"]
    key = _session_key()
    try:
        # base_url decides where this single-use OAuth code is redeemed.
        base = _allowed_base(base_url)
        um = _um_base(base)
    except _NotAuthed as e:
        return str(e)
    try:
        r = httpx.post(f"{um}/api/auth/cli/exchange", json={"code": code}, timeout=30)
        if r.status_code >= 400:
            return ("That code is invalid, expired, or already used. Start again with "
                    "brainkb_globus_login.")
        refresh = r.json()["refresh_token"]
        if key is None:
            return ("Login completed, but this session could not be identified to cache "
                    "the token; send an Authorization header (refresh token) instead.")
        email = _decode_sub(refresh) or ""
        # OAuth session: no password stored (can't password re-login); the refresh
        # token drives per-service SSO exchange like a normal login.
        _SESSIONS[key] = {"refresh": refresh, "url": base, "email": email, "access": {},
                          "expires_at": _session_expiry(refresh)}
        return f"Logged in as {email or 'your account'} at {base} (via OAuth SSO; this session, expires when the token does)."
    except Exception as e:
        return f"Finish login error: {e}"


@mcp.tool()
def brainkb_logout() -> str:
    """Forget the cached token for this session."""
    key = _session_key()
    if key is not None:
        _SESSIONS.pop(key, None)
    return "Logged out (this session)."


@mcp.tool()
def brainkb_whoami() -> Dict[str, Any]:
    """Report the current caller's auth state (email, authenticated, and when the
    cached session expires). When signed in it also returns base_url — the backend
    THIS SERVER talks to, which on a hosted deployment is an internal address and
    says nothing about the caller's own machine."""
    # Rate-limited like a read: resolving auth can trigger a token exchange against
    # the backend, so an unmetered whoami is a request amplifier.
    if not _rate_ok("read", _RL_READ):
        return _rl_error("read", _RL_READ)
    try:
        ctx = _resolve()
        out = {"base_url": ctx["url"], "email": ctx["email"], "authenticated": True}
        key = _session_key()
        if key is not None and key in _SESSIONS:
            exp = _SESSIONS[key].get("expires_at")
            if exp:
                out["session_expires_in_min"] = max(0, round((float(exp) - time.time()) / 60))
        return out
    except _NotAuthed:
        # No base_url here. It is the SERVER's backend, and on the hosted remote it
        # is an internal address (host.docker.internal:8010) — handing that to an
        # anonymous caller discloses deployment topology, and assistants misread it
        # as "the user is pointed at their own local stack" and warn about the wrong
        # thing. The hint is a single instruction rather than a menu: from a cold
        # start OAuth is the only path that works, so offering a choice of methods
        # just costs the user a round trip.
        return {"email": None, "authenticated": False,
                "hint": ("Not signed in. Call brainkb_globus_login() and give the "
                         "user the URL it returns; they sign in and paste back the "
                         "short code for brainkb_finish_login(code). Then mint a "
                         "PAT with brainkb_create_token(name, days) so the next "
                         "session needs no browser. Do not ask the user to choose "
                         "an auth method, and do not ask for a password.")}


# --------------------------------------------------------------------------- #
# personal access tokens (browser-free, long-lived, revocable)
# --------------------------------------------------------------------------- #
# A PAT lets the user authenticate WITHOUT a browser after a one-time login: mint
# it once (while logged in), paste it into the config as BRAINKB_TOKEN, and every
# call thereafter authenticates with it until it expires or is revoked. The PAT is
# opaque (not a key/JWT); the user handles a single string, never a key.


@mcp.tool()
def brainkb_create_token(name: str = "", days: int = 90) -> Any:
    """Generate a Personal Access Token (PAT) for browser-free auth. Requires you
    to be logged in already (brainkb_login or brainkb_globus_login). The token is
    shown ONCE and never again — copy it and set it as BRAINKB_TOKEN in your
    MCP/skill config; then no login or browser is needed until it expires.
    `name`: a label so you can tell tokens apart (e.g. 'laptop'). `days`: lifetime
    (default 90, server-capped). Treat the returned token like a password."""
    if not _rate_ok("auth", _RL_AUTH):
        return _rl_error("auth", _RL_AUTH)
    return _um("POST", "/api/auth/tokens", json={"name": name, "days": days})


@mcp.tool()
def brainkb_list_tokens() -> Any:
    """List your Personal Access Tokens (metadata only — the secret is never
    shown): id, name, prefix, created/last-used/expiry, and whether each is
    active/revoked/expired. Use the id with brainkb_revoke_token."""
    return _um("GET", "/api/auth/tokens")


@mcp.tool()
def brainkb_revoke_token(token_id: int) -> Any:
    """Revoke one of your Personal Access Tokens by id (see brainkb_list_tokens).
    Takes effect immediately — the next call using that token fails."""
    return _um("DELETE", f"/api/auth/tokens/{int(token_id)}")


@mcp.tool()
def brainkb_use_token(token: str, base_url: str = "") -> str:
    """Use a Personal Access Token (brainkb_pat_...) for THIS session — an
    alternative to setting BRAINKB_TOKEN in the config. Validates the token, then
    caches it so subsequent calls authenticate with it. The token is never echoed."""
    if not _rate_ok("auth", _RL_AUTH):
        return _rl_error("auth", _RL_AUTH)["detail"]
    key = _session_key()
    try:
        # base_url decides where this token is presented — allowlist it.
        base = _allowed_base(base_url)
        um = _um_base(base)
    except _NotAuthed as e:
        return str(e)
    if not _is_pat(token):
        return "That doesn't look like a BrainKB access token (expected brainkb_pat_...)."
    ex = _pat_exchange(um, token.strip(), _AUD_QUERY)
    if not ex:
        return "That token is invalid, expired, or revoked. Mint a new one with brainkb_create_token()."
    email = _decode_sub(ex[0]) or ""
    if key is None:
        return ("Token accepted, but this session could not be identified to cache it; "
                "set BRAINKB_TOKEN in your config or send an Authorization header instead.")
    _SESSIONS[key] = {"pat": token.strip(), "url": base, "email": email, "access": {}}
    _store_access(_SESSIONS[key], _AUD_QUERY, ex[0], ex[1])
    return f"Access token accepted for {email or 'your account'} at {base} (this session)."


# --------------------------------------------------------------------------- #
# spaces (private/public workspaces)
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_list_spaces() -> Any:
    """List spaces the user can see (their own/member spaces + public ones), each
    annotated with THIS caller's permission so you know what they may do:
      - your_role: 'owner' | 'editor' | 'viewer' | null (their space membership)
      - is_owner:  they own the space
      - access:    'owner' | 'member' | 'public' (how it's available to them)
      - can_write: their space role permits ingest (owner/editor) — a real ingest
                   also needs the 'ingest' capability + any per-space access rules.
    Use this to tell the user which spaces they can read vs. write vs. only see as
    public.

    No login needed: an unauthenticated caller gets the public spaces only
    (read-only). Log in to also see your own/member spaces.

    `owner` is shown only for spaces the caller is a member of; a public space
    read by a non-member never exposes its owner's email."""
    out = _get("/api/spaces", anonymous=True)
    if isinstance(out, dict) and isinstance(out.get("spaces"), list):
        for sp in out["spaces"]:
            if isinstance(sp, dict) and not sp.get("your_role"):
                sp["owner"] = None
    return out


@mcp.tool()
def brainkb_create_space(slug: str, name: str, visibility: str = "private",
                         description: str = "", space_type: str = "individual") -> Any:
    """Create a workspace/space. The caller becomes owner.
    slug: lowercase/hyphen id, **globally unique** — if it's already taken the call
    returns 409 (pick another slug; slugs are never reused/deleted).
    visibility: 'private' or 'public';
    description: short human description (recommended — surfaces in the registry);
    space_type: 'individual' (a personal space — any write-capable role) or 'team'
    (a shared space — only Admin/SuperAdmin, or a user granted create_team_space)."""
    return _post("/api/spaces", json={"slug": slug, "name": name, "visibility": visibility,
                                       "description": description, "space_type": space_type})


@mcp.tool()
def brainkb_set_space_visibility(slug: str, visibility: str) -> Any:
    """Set a space 'public' (anyone, even anonymous, can read) or 'private'
    (members only). Owner only."""
    return _patch(f"/api/spaces/{_seg(slug)}/visibility", json={"visibility": visibility})


@mcp.tool()
def brainkb_add_space_member(slug: str, member_email: str, role: str = "viewer") -> Any:
    """Add/update a space member. role: 'owner' | 'editor' | 'viewer'. Owner only."""
    return _post(f"/api/spaces/{_seg(slug)}/members",
                 json={"member": member_email, "role": role})


@mcp.tool()
def brainkb_add_space_graph(slug: str, named_graph_iri: str, description: str = "") -> Any:
    """Register a named graph and bind it to a space, so ingest/read on that graph
    are governed by the space's membership and visibility. Owner/editor only.
    The named_graph_iri is **globally unique** — one graph belongs to exactly one
    space. If it's already registered (to any space) the call returns 409; graph
    bindings are permanent (no unregister/delete)."""
    return _post(f"/api/spaces/{_seg(slug)}/graphs",
                 json={"named_graph_url": named_graph_iri, "description": description})


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #
# File paths in brainkb_ingest_files are resolved on the SERVER, not on the
# caller's machine. Over stdio those are the same machine, so any path the user can
# read is fair game. Over streamable-http they are not: an unconstrained path lets
# a remote caller upload /proc/self/environ (which leaks BRAINKB_TOKEN),
# /app/server.py, or mounted secrets into a graph they own and read it straight
# back with brainkb_read_space. So file ingest is refused on the hosted remote
# unless MCP_INGEST_ROOT confines it to a directory.
_INGEST_ROOT = os.getenv("MCP_INGEST_ROOT", "").strip()

# --- upload staging ------------------------------------------------------------ #
# What this exists for: MCP tool arguments are authored by the model, so any file
# routed through one has to be re-emitted token by token — which is why a 2.8 MB
# Turtle export is unreachable on the hosted remote even though the server itself
# could ingest it in seconds. The bytes need a path to the server that does not pass
# through a context window, and MCP has no upload primitive. So the server offers a
# plain authenticated HTTP endpoint: the CLIENT streams the file from disk (a few
# lines of `requests`), the server stages it, and a tool then ingests it by id.
#
# The file never enters anyone's context: the client code NAMES the file and the HTTP
# library reads it off disk. That is the whole difference between
# `data=open("review.ttl","rb")` and pasting 2.8 MB into a tool call.
_UPLOAD_ENABLED = os.getenv(
    "MCP_UPLOAD_ENABLED", "true").strip().lower() not in ("0", "false", "no")
_UPLOAD_DIR_FALLBACK = os.path.join(tempfile.gettempdir(), "brainkb-uploads")


def _resolve_upload_dir() -> Tuple[str, Optional[str]]:
    """(usable staging directory, note about a fallback).

    The configured directory may not be creatable: the image runs as uid 10001, so a
    path like /var/lib/brainkb-uploads only exists if the Dockerfile made it (it does)
    or a bind mount provides it with the right ownership. If neither holds, failing
    every upload with "upload failed: Permission denied" is a poor trade against
    staging in a temp dir — the files are ephemeral either way, deleted as soon as the
    ingest API accepts them. So fall back, and say so loudly at startup rather than
    letting the configured path silently mean something else.
    """
    want = os.getenv("MCP_UPLOAD_DIR", "").strip() or _UPLOAD_DIR_FALLBACK
    try:
        os.makedirs(want, exist_ok=True)
        probe = os.path.join(want, ".write-probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.unlink(probe)
        return want, None
    except OSError as exc:
        if want == _UPLOAD_DIR_FALLBACK:
            return want, f"{want!r} is not writable ({exc})"
        try:
            os.makedirs(_UPLOAD_DIR_FALLBACK, exist_ok=True)
        except OSError as exc2:
            return want, (f"neither {want!r} nor {_UPLOAD_DIR_FALLBACK!r} is writable "
                          f"({exc}; {exc2})")
        return _UPLOAD_DIR_FALLBACK, (
            f"MCP_UPLOAD_DIR={want!r} is not writable by this process ({exc}); "
            f"staging in {_UPLOAD_DIR_FALLBACK!r} instead. The container runs as uid "
            "10001 — either let the image's own /var/lib/brainkb-uploads stand, or "
            "chown the bind-mounted host directory to 10001.")


_UPLOAD_DIR, _UPLOAD_DIR_NOTE = _resolve_upload_dir()
# One file, 5 GB — the ingest API's own per-user ceiling, so a file this endpoint
# accepts is one the backend will also take.
_UPLOAD_MAX_BYTES = _int_env("MCP_UPLOAD_MAX_BYTES", 5_000_000_000)
# Total across all staged uploads. The per-file cap alone does not stop a caller
# from filling the disk with many files.
_UPLOAD_TOTAL_BYTES = _int_env("MCP_UPLOAD_TOTAL_BYTES", 20_000_000_000)
_UPLOAD_TTL_MIN = _int_env("MCP_UPLOAD_TTL_MIN", 360)
_UPLOAD_ID_RE = re.compile(r"^up_[0-9a-f]{32}$")


def _upload_meta_path(upload_id: str) -> str:
    return os.path.join(_UPLOAD_DIR, upload_id + ".json")


def _upload_data_path(upload_id: str) -> str:
    return os.path.join(_UPLOAD_DIR, upload_id + ".data")


def _upload_sweep() -> int:
    """Delete staged uploads past their TTL. Returns the bytes reclaimed."""
    if not os.path.isdir(_UPLOAD_DIR):
        return 0
    cutoff = time.time() - max(1, _UPLOAD_TTL_MIN) * 60
    freed = 0
    for name in os.listdir(_UPLOAD_DIR):
        if not name.startswith("up_"):
            continue
        path = os.path.join(_UPLOAD_DIR, name)
        try:
            st = os.stat(path)
            if st.st_mtime >= cutoff:
                continue
            # Never reap a file whose background submission is still running — the
            # upload would vanish from under the thread streaming it.
            if _upload_state(name.split(".")[0]) == "submitting":
                continue
            freed += st.st_size
            os.unlink(path)
        except OSError:
            continue
    return freed


def _upload_state(upload_id: str) -> str:
    try:
        with open(_upload_meta_path(upload_id), "r", encoding="utf-8") as fh:
            return str(jsonlib.load(fh).get("state") or "staged")
    except (OSError, ValueError):
        return "gone"


def _upload_meta_write(upload_id: str, meta: dict) -> None:
    """Write meta atomically — a half-written file would read as a corrupt upload."""
    tmp = _upload_meta_path(upload_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        jsonlib.dump(meta, fh)
    os.replace(tmp, _upload_meta_path(upload_id))


def _upload_submit(upload_id: str, meta: dict, url: str, token: str,
                   email: str, graph: str) -> None:
    """POST a staged upload to the ingest API. Runs on a background thread.

    The caller's token is passed in and used here, never written to disk: the meta
    file sits on the server's filesystem, and a credential in it would outlive the
    request that authorised it.
    """
    meta = dict(meta)
    try:
        with open(_upload_data_path(upload_id), "rb") as fh:
            with httpx.Client(timeout=_UPLOAD_TIMEOUT) as c:
                resp = c.post(
                    f"{url}/api/insert/files/knowledge-graph-triples",
                    params={"user_id": email, "named_graph_iri": graph},
                    headers={"Authorization": f"Bearer {token}"},
                    files=[("files", (str(meta.get("filename") or "upload.ttl"),
                                      fh, "application/octet-stream"))],
                )
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text[:400]}
        if resp.status_code < 400:
            meta.update({"state": "submitted", "http_status": resp.status_code,
                         "job": body,
                         "job_id": (body or {}).get("job_id")
                         if isinstance(body, dict) else None})
            _upload_meta_write(upload_id, meta)
            # The ingest API holds the bytes now; a second copy is just disk.
            try:
                os.unlink(_upload_data_path(upload_id))
            except OSError:
                pass
        else:
            meta.update({"state": "failed", "http_status": resp.status_code,
                         "error": body,
                         "note": ("the staged bytes were kept — fix the cause and "
                                  "retry with brainkb_ingest_upload, no re-upload "
                                  "needed")})
            _upload_meta_write(upload_id, meta)
    except Exception as exc:                      # noqa: BLE001 - thread boundary
        meta.update({"state": "failed", "error": str(exc),
                     "note": "staged bytes kept; retry with brainkb_ingest_upload"})
        try:
            _upload_meta_write(upload_id, meta)
        except OSError:
            pass


def _upload_total_bytes() -> int:
    if not os.path.isdir(_UPLOAD_DIR):
        return 0
    total = 0
    for name in os.listdir(_UPLOAD_DIR):
        if name.startswith("up_") and name.endswith(".data"):
            try:
                total += os.path.getsize(os.path.join(_UPLOAD_DIR, name))
            except OSError:
                pass
    return total


def _upload_load(upload_id: str, email: str) -> Tuple[Optional[dict], Optional[dict]]:
    """(meta, error) for `upload_id`, enforcing ownership and expiry.

    Ownership is the part that matters. An upload id is a bearer capability sitting
    on a multi-user host, so it is bound to the email that uploaded it and only that
    identity may ingest it — otherwise one caller could name another's staged file
    and have the server write it into a graph under their own attribution, which the
    provenance record would then report as theirs forever.
    """
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        return None, {"error": True, "status_code": 400,
                      "detail": "malformed upload_id (expected up_<32 hex>)"}
    if not email:
        return None, {"error": True, "status_code": 401,
                      "detail": "cannot establish the caller's identity"}
    try:
        with open(_upload_meta_path(upload_id), "r", encoding="utf-8") as fh:
            meta = jsonlib.load(fh)
    except (OSError, ValueError):
        return None, {"error": True, "status_code": 404,
                      "detail": (f"no staged upload {upload_id!r} (it may have "
                                 f"expired — uploads are kept "
                                 f"{_UPLOAD_TTL_MIN} minutes — or already been "
                                 "ingested)")}
    if (meta.get("email") or "").lower() != email.lower():
        # Deliberately the same 404 shape as a missing upload: confirming that some
        # other user's id exists is itself information.
        return None, {"error": True, "status_code": 404,
                      "detail": f"no staged upload {upload_id!r} for this account"}
    if not os.path.isfile(_upload_data_path(upload_id)):
        return None, {"error": True, "status_code": 404,
                      "detail": (f"staged bytes for {upload_id!r} are gone (already "
                                 "ingested? brainkb_upload_status reports its "
                                 "outcome)")}
    return meta, None


def _upload_load_any(upload_id: str, email: str) -> Tuple[Optional[dict],
                                                          Optional[dict]]:
    """Like `_upload_load` but does not require the staged bytes to still exist.

    A successful ingest deletes the payload and keeps the metadata, so status has to
    read the metadata alone — otherwise the one case a caller most wants to check
    (did it land?) is the one that 404s. Ownership is enforced identically.
    """
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        return None, {"error": True, "status_code": 400,
                      "detail": "malformed upload_id (expected up_<32 hex>)"}
    if not email:
        return None, {"error": True, "status_code": 401,
                      "detail": "cannot establish the caller's identity"}
    try:
        with open(_upload_meta_path(upload_id), "r", encoding="utf-8") as fh:
            meta = jsonlib.load(fh)
    except (OSError, ValueError):
        return None, {"error": True, "status_code": 404,
                      "detail": f"no record of upload {upload_id!r}"}
    if (meta.get("email") or "").lower() != email.lower():
        return None, {"error": True, "status_code": 404,
                      "detail": f"no record of upload {upload_id!r} for this account"}
    return meta, None


def _upload_discard(upload_id: str) -> None:
    """Delete a staged upload's payload and metadata, ignoring what is already gone.

    Used by the cleanup paths — a rejected digest, a body over the cap, an explicit
    discard, and a successful ingest — so it has to be indifferent to which of the two
    files still exists.
    """
    for path in (_upload_data_path(upload_id), _upload_meta_path(upload_id)):
        try:
            os.unlink(path)
        except OSError:
            pass


def _ingest_path(p: str) -> str:
    """Resolve a file-ingest path, or raise PermissionError if it is not allowed.

    realpath() first, so symlinks cannot point out of the configured root.
    """
    rp = os.path.realpath(os.path.expanduser(p))
    if _INGEST_ROOT:
        root = os.path.realpath(os.path.expanduser(_INGEST_ROOT))
        if rp != root and not rp.startswith(root + os.sep):
            raise PermissionError(
                f"{p!r} is outside the permitted ingest directory (MCP_INGEST_ROOT)."
            )
    elif _IS_REMOTE:
        # Do NOT suggest brainkb_ingest_text here. This message used to, and the
        # advice is actively harmful for a file: a caller followed it, tried to
        # reproduce a 2.7 MB Turtle export through the tool argument, split it to
        # fit, and came within one call of writing silently-detached triples into an
        # append-only graph (the first splitter alone lost 2,070 triples, and
        # splitting at all breaks blank-node identity across calls).
        raise PermissionError(
            "File ingest is disabled on the hosted remote: paths are read from the "
            "SERVER's filesystem, not yours, so this server cannot see your file. "
            "Upload it instead — no operator change needed:\n"
            "  import requests\n"
            "  requests.post('https://<this-host>/upload',\n"
            "                params={'filename': 'file.ttl', 'graph': '<graph_iri>'},\n"
            "                headers={'Authorization': f'Bearer {TOKEN}'},\n"
            "                data=open('file.ttl','rb'))\n"
            "That streams the bytes off your disk and the server ingests them; poll "
            "brainkb_upload_status(upload_id). (Omit &graph to stage it and call "
            "brainkb_ingest_upload yourself.) Alternatively the operator can set "
            "MCP_INGEST_ROOT plus a bind mount and place the file there. Do NOT "
            "re-type the "
            "file's RDF into brainkb_ingest_text: ingest is append-only, dense "
            "Turtle does not survive transcription intact, and splitting it across "
            "calls silently breaks blank nodes. brainkb_ingest_text is for RDF you "
            "authored in-session, and takes sha256= so even that is checked."
        )
    if not os.path.isfile(rp):
        raise PermissionError(f"{p!r} is not a regular file.")
    return rp

@mcp.tool()
def brainkb_ingest_text(named_graph_iri: str, data: str,
                        sha256: str = "", expected_bytes: int = 0) -> Any:
    """Ingest raw RDF text (Turtle / N-Triples / JSON-LD, auto-detected) into a
    named graph. Returns a job_id; ingestion runs in the background — poll with
    brainkb_job_status. The graph must be registered (see brainkb_add_space_graph)
    and the caller must have write access to its space.

    `sha256` / `expected_bytes` are an integrity contract, and you should use them
    whenever the RDF came from a file. Ingest is append-only — no delete for
    triples, no unregister for a graph — so RDF that arrives here mangled is
    permanent. Because `data` is a string, it passes through the caller's context,
    where dense Turtle is exactly what gets silently altered: ligatures, Greek
    letters, embedded newlines, escaped quotes. Declare the digest of the bytes you
    MEANT to send (`shasum -a 256 file.ttl`) and this refuses the write on any
    mismatch, turning an unrecoverable corruption into a clean rejection.
    """
    payload = data.encode("utf-8")
    if expected_bytes and len(payload) != expected_bytes:
        return {"error": True, "status_code": 400,
                "detail": (f"Byte-count mismatch: declared {expected_bytes}, "
                           f"received {len(payload)}. Nothing was written. The "
                           "payload was truncated or altered in transit — send the "
                           "file's bytes without passing them through a model, or "
                           "use file ingest.")}
    if sha256:
        got = hashlib.sha256(payload).hexdigest()
        want = sha256.strip().lower()
        if got != want:
            return {"error": True, "status_code": 400,
                    "detail": (f"Digest mismatch: declared {want}, received {got} "
                               f"({len(payload)} bytes). Nothing was written — this "
                               "is the guard working. Do not retry by re-typing the "
                               "RDF; put the file where the server can read it "
                               "(MCP_INGEST_ROOT) and use brainkb_ingest_files.")}
    if _MAX_INGEST_BYTES > 0 and len(payload) > _MAX_INGEST_BYTES:
        return {"error": True, "status_code": 413,
                "detail": (f"Payload too large ({len(payload)} bytes > "
                           f"{_MAX_INGEST_BYTES}). Upload the file instead — "
                           "POST /upload with 'Authorization: Bearer <token>' and "
                           "--data-binary @file, then brainkb_ingest_upload (or add "
                           "&graph=<iri> to have the server submit it). Do NOT split "
                           "the RDF across calls: blank-node labels are scoped to "
                           "one document, so a bnode shared by two calls becomes "
                           "two distinct nodes and the triples silently detach.")}
    return _post("/api/insert/raw/knowledge-graph-triples",
                 params={"user_id": _me(), "named_graph_iri": named_graph_iri},
                 content=payload, ctype="text/plain")


def _scopes_of(token: str) -> List[str]:
    """Scopes claimed by an access token. Used to refuse an upload the caller could
    never ingest — the REST insert routes gate on `write`, so staging bytes for a
    read-only account is just disk we would have to reclaim."""
    cl = _claims(token) or {}
    sc = cl.get("scopes")
    return [str(s) for s in sc] if isinstance(sc, list) else []


@mcp.tool()
def brainkb_list_uploads() -> Any:
    """List RDF files YOU have staged with POST /upload but not yet ingested.

    Shows each upload_id, its size, sha256 and when it expires. Only your own
    uploads are visible."""
    try:
        email = _me()
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    _upload_sweep()
    out = []
    if os.path.isdir(_UPLOAD_DIR):
        for name in sorted(os.listdir(_UPLOAD_DIR)):
            if not name.endswith(".json"):
                continue
            uid = name[:-5]
            meta, _ = _upload_load_any(uid, email)
            if meta:
                out.append({k: meta.get(k) for k in
                            ("upload_id", "filename", "bytes", "sha256", "state",
                             "job_id", "named_graph_iri", "uploaded_at",
                             "expires_at") if meta.get(k) is not None})
    return {"uploads": out, "count": len(out),
            "ttl_minutes": _UPLOAD_TTL_MIN,
            "next": ("brainkb_ingest_upload(named_graph_iri, upload_id) — the server "
                     "ingests the staged bytes; nothing passes through your context")}


@mcp.tool()
def brainkb_upload_status(upload_id: str) -> Any:
    """State of one of your staged/submitted uploads.

    `state` is `staged` (waiting for brainkb_ingest_upload), `submitting` (the server
    is streaming it to the ingest API), `submitted` (accepted — `job_id` is set, poll
    brainkb_job_status) or `failed` (the staged bytes were KEPT, so retry with
    brainkb_ingest_upload rather than re-uploading)."""
    try:
        email = _me()
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    meta, err = _upload_load_any(upload_id, email)
    if err:
        return err
    assert meta is not None
    out = {k: meta.get(k) for k in
           ("upload_id", "filename", "bytes", "sha256", "state", "job_id",
            "named_graph_iri", "uploaded_at", "expires_at", "error", "note")
           if meta.get(k) is not None}
    if meta.get("state") == "submitted" and meta.get("job_id"):
        out["next"] = (f"brainkb_job_status('{meta['job_id']}') then "
                       f"brainkb_delta('{meta['job_id']}') to count the triples "
                       "actually added")
    return out


@mcp.tool()
def brainkb_discard_upload(upload_id: str) -> Any:
    """Delete one of your staged uploads without ingesting it."""
    try:
        email = _me()
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    meta, err = _upload_load(upload_id, email)
    if err:
        return err
    _upload_discard(upload_id)
    return {"discarded": upload_id, "bytes": (meta or {}).get("bytes")}


@mcp.tool()
def brainkb_ingest_upload(named_graph_iri: str, upload_id: str) -> Any:
    """Ingest a file you staged with `POST /upload` into a named graph.

    This is the route for a large local file: your HTTP client streams the bytes
    straight to this server over HTTPS, then you name the resulting upload_id here. The
    server reads its own staged copy and posts it to the ingest API internally, so
    the RDF never passes through a model's context — nothing to transcribe, no
    context-window ceiling, and no reason to split the document (splitting breaks
    blank-node identity and silently detaches triples, permanently).

    Stage a file with any HTTP client — the point is that the LIBRARY reads the file,
    so the bytes never pass through a model:

        import requests, hashlib, pathlib
        f = pathlib.Path("review.ttl")
        r = requests.post(
            "https://mcp.brainkb.org/upload",
            params={"filename": f.name,
                    "sha256": hashlib.sha256(f.read_bytes()).hexdigest()},
            headers={"Authorization": f"Bearer {TOKEN}"},
            data=f.open("rb"),          # streamed — never loaded into memory
        )
        print(r.json())                 # -> {"upload_id": "up_...", "state": "staged"}

    It returns an upload_id and the sha256 the server computed — compare it with your
    own before ingesting.

    Returns a job_id; poll brainkb_job_status, then reconcile brainkb_delta(job_id)
    against the triple count you expected. The staged copy is deleted once the
    ingest API has accepted the bytes.
    """
    try:
        ctx = _resolve()
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    meta, err = _upload_load(upload_id, ctx.get("email") or "")
    if err:
        return err
    assert meta is not None
    name = str(meta.get("filename") or "upload.ttl")
    try:
        with open(_upload_data_path(upload_id), "rb") as fh:
            out = _post("/api/insert/files/knowledge-graph-triples",
                        params={"user_id": ctx["email"],
                                "named_graph_iri": named_graph_iri},
                        files=[("files", (name, fh, "application/octet-stream"))],
                        timeout=_UPLOAD_TIMEOUT)
    except OSError as e:
        return {"error": True, "detail": f"could not read the staged upload: {e}"}
    accepted = isinstance(out, dict) and not out.get("error")
    if accepted:
        # The ingest API has the bytes now; keeping a second copy here only fills the
        # disk. A failed submission keeps the staging file so the call can be retried
        # without re-uploading.
        _upload_discard(upload_id)
    if isinstance(out, dict):
        out["ingested_upload"] = {
            "upload_id": upload_id, "filename": name,
            "bytes": meta.get("bytes"), "sha256": meta.get("sha256"),
            "staged_copy": "deleted" if accepted else "kept for retry",
            "verify": ("compare brainkb_delta(job_id)'s triple count against the "
                       "source file's own count"),
        }
    return out


@mcp.tool()
def brainkb_ingest_files(named_graph_iri: str, file_paths: List[str],
                         max_concurrency: int = 8) -> Any:
    """Ingest local RDF files (ttl/nt/nq/rdf/owl/jsonld/json) into a named graph.
    Returns a job_id; runs in the background — poll with brainkb_job_status."""
    if _MAX_INGEST_FILES > 0 and len(file_paths) > _MAX_INGEST_FILES:
        return {"error": True, "status_code": 413,
                "detail": f"Too many files ({len(file_paths)} > {_MAX_INGEST_FILES} per call)."}
    handles = []
    try:
        files = []
        for p in file_paths:
            rp = _ingest_path(p)
            fh = open(rp, "rb")
            handles.append(fh)
            files.append(("files", (os.path.basename(rp), fh, "application/octet-stream")))
        # No byte cap here (only a file-count cap): TTL/JSON-LD uploads may be
        # large (up to ~5GB per user). Use the long upload timeout so big
        # streams aren't aborted mid-transfer.
        return _post("/api/insert/files/knowledge-graph-triples",
                     params={"user_id": _me(), "named_graph_iri": named_graph_iri,
                             "max_concurrency": max_concurrency},
                     files=files, timeout=_UPLOAD_TIMEOUT)
    except PermissionError as e:
        return {"error": True, "status_code": 403, "detail": str(e)}
    except FileNotFoundError as e:
        return {"error": True, "detail": str(e)}
    finally:
        for fh in handles:
            try:
                fh.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# ingest status
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_list_jobs(limit: int = 50) -> Any:
    """List the user's ingest jobs (newest first) with status and progress."""
    return _get("/api/insert/jobs", params={"user_id": _me(), "limit": limit})


@mcp.tool()
def brainkb_job_status(job_id: str) -> Any:
    """Detailed status of one ingest job: status, progress %, current file/stage,
    per-file failures, and (when complete) a summary."""
    return _get("/api/insert/user/jobs/detail", params={"user_id": _me(), "job_id": job_id})


@mcp.tool()
def brainkb_recover_job(job_id: str) -> Any:
    """Attempt to recover a stuck/errored ingest job (marks it recoverable/errored)."""
    return _post("/api/insert/jobs/recover", params={"user_id": _me(), "job_id": job_id})


# --------------------------------------------------------------------------- #
# read / search
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_search(q: str, space: str = "", limit: int = 25, offset: int = 0) -> Any:
    """Full-text search over the knowledge graphs, access-filtered by space
    visibility. Pass `space` to scope to one workspace, omit for a full search.
    No login needed: an unauthenticated caller searches public spaces only.
    Anonymous/other users never see private-space data."""
    params: Dict[str, Any] = {"q": q, "limit": limit, "offset": offset}
    if space:
        params["space"] = space
    return _get("/api/search", params=params, anonymous=True)


@mcp.tool()
def brainkb_read_space(slug: str) -> Any:
    """Read all RDF (JSON-LD) in a space's graphs. Public spaces are readable by
    anyone without logging in (read-only); private spaces require login and
    membership. Don't ask the user to log in just to read a public space."""
    return _get(f"/api/spaces/{_seg(slug)}/data", anonymous=True)


@mcp.tool()
def brainkb_list_registered_graphs() -> Any:
    """List registered named graphs visible to the caller (private-space graphs the
    caller can't access are hidden)."""
    return _get("/api/query/registered-named-graphs")


@mcp.tool()
def brainkb_sparql(sparql_query: str) -> Any:
    """Run an arbitrary SPARQL query. Requires an Admin/SuperAdmin role (the
    sparql_admin capability) — for ordinary questions prefer brainkb_search,
    brainkb_read_space, or the provenance/delta tools, which need no admin role."""
    # query_service declares this route WITH a trailing slash, so the guard has to
    # tolerate the final empty segment here (it is literal, not interpolated).
    return _get("/api/query/sparql/", params={"sparql_query": sparql_query},
                allow_trailing_slash=True)


# --------------------------------------------------------------------------- #
# canned QA queries (see qa_registry.py and guide.md)
# --------------------------------------------------------------------------- #

# Each module registers its QAQuery objects on import. A new category is a new
# module plus one entry here.
QA_MODULES = ("named_entities_qa", "resources_qa")

for _mod in QA_MODULES:
    importlib.import_module(_mod)


@mcp.tool()
def brainkb_qa_list(category: str = "", search: str = "") -> Any:
    """Find a canned question BrainKB can answer, then run it with brainkb_qa_run.

    Prefer these over writing SPARQL: they are vetted against BrainKB's actual
    vocabulary. Discovery is two steps, so you never read every query at once:

    1. brainkb_qa_list() -> the menu: each category's name, a description of
       the questions it covers, and how many queries it has. Pick the category
       whose description matches the user's question.
    2. brainkb_qa_list(category="<name>") -> that category's queries. Each has
       `question` (what it answers), `notes` (when to use it, where parameter
       values come from, what the results mean), `params` (required ones have
       no default — ask the user rather than guess) and a working `example`.

    `search="words"` filters queries by words in their id/question/notes; use it
    alone to search every category when none of the descriptions fits.
    """
    if category and not qa_registry.has_category(category):
        return {"error": True, "status_code": 404,
                "detail": f"Unknown category {category!r}.",
                "categories": [c["name"] for c in qa_registry.categories()]}
    if not category and not search:
        return {"categories": qa_registry.categories(),
                "next": "Call brainkb_qa_list(category=...) for the queries in one category."}
    return {"category": category or None, "search": search or None,
            "queries": qa_registry.list_queries(category, search),
            "next": "Call brainkb_qa_run(query_id, params) with a query's id."}


@mcp.tool()
def brainkb_qa_run(query_id: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Run a canned question from brainkb_qa_list by id. `params` maps parameter
    names to values; they are validated and escaped, never spliced in raw. Runs
    through the same SPARQL endpoint as brainkb_sparql, so it needs the same
    role."""
    q = qa_registry.get(query_id)
    if q is None:
        return {"error": True, "status_code": 404,
                "detail": f"Unknown query id {query_id!r}; see brainkb_qa_list."}
    try:
        sparql = q.render(params)
    except ValueError as e:
        return {"error": True, "status_code": 400, "detail": str(e)}
    return _get("/api/query/sparql/", params={"sparql_query": sparql},
                allow_trailing_slash=True)


# --------------------------------------------------------------------------- #
# provenance (PROV-O)
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_provenance_job(job_id: str) -> Any:
    """PROV-O provenance bundle (JSON-LD) for one ingest job."""
    return _get("/api/provenance/job", params={"user_id": _me(), "job_id": job_id})


@mcp.tool()
def brainkb_provenance_graph(named_graph_iri: str) -> Any:
    """PROV-O ingestion/activity history (JSON-LD) for a named graph."""
    return _get("/api/provenance/named-graph", params={"iri": named_graph_iri})


@mcp.tool()
def brainkb_delta(job_id: str) -> Any:
    """The exact triples a job added (its delta), as JSON-LD."""
    return _get("/api/provenance/delta", params={"user_id": _me(), "job_id": job_id})


@mcp.tool()
def brainkb_delta_history(named_graph_iri: str) -> Any:
    """A named graph's change history: one entry per ingest delta (job, triple
    count, timestamp), newest first."""
    return _get("/api/provenance/delta/history", params={"iri": named_graph_iri})


@mcp.tool()
def brainkb_delta_compare(job_id_a: str, job_id_b: str) -> Any:
    """Compare two jobs' deltas: A-only / B-only / shared triple counts + triples."""
    return _get("/api/provenance/delta/compare",
                params={"user_id": _me(), "job_id_a": job_id_a, "job_id_b": job_id_b})


# --------------------------------------------------------------------------- #
# authorization (RBAC): capability grants + per-space access rules
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_capabilities(member: str) -> Any:
    """(Admin only) Show a user's roles, effective capabilities, and delegated
    grants. Useful to check why someone can/can't create team spaces, ingest, etc."""
    return _get("/api/admin/capabilities", params={"member": member})


@mcp.tool()
def brainkb_grant_capability(member: str, capability: str) -> Any:
    """(Admin only) Delegate a capability to a user — e.g. 'create_team_space' or
    'manage_team_space' so a Curator/Lab Member can create/manage team spaces.
    Grantable: create_private_space, create_team_space, manage_team_space, ingest,
    recover, read_private (NOT the admin-only 'grant'/'sparql_admin')."""
    return _post("/api/admin/capabilities/grant", json={"member": member, "capability": capability})


@mcp.tool()
def brainkb_revoke_capability(member: str, capability: str) -> Any:
    """(Admin only) Revoke a previously granted capability from a user."""
    return _post("/api/admin/capabilities/revoke", json={"member": member, "capability": capability})


@mcp.tool()
def brainkb_list_capabilities() -> Any:
    """(Admin only) Catalog of all KG capabilities, which are delegatable
    ('grantable'), which are admin-only, and a description of each. Use this to see
    the available permission options before granting to a user or group/role."""
    return _get("/api/admin/capabilities/available")


@mcp.tool()
def brainkb_role_capabilities(role: str) -> Any:
    """(Admin only) List the capabilities granted to a role/group (e.g.
    'uk_collaborator', 'Lab Member')."""
    return _get("/api/admin/capabilities/role", params={"role": role})


@mcp.tool()
def brainkb_grant_role_capability(role: str, capability: str) -> Any:
    """(Admin only) Grant a capability to a whole role/group so EVERY member gets
    it — e.g. give a custom group 'uk_collaborator' the 'ingest' or
    'create_private_space' capability. Grantable: create_private_space,
    create_team_space, manage_team_space, ingest, recover, read_private (NOT the
    admin-only 'grant'/'sparql_admin'). Create the group first with
    brainkb_create_role, then assign it to users with brainkb_assign_role."""
    return _post("/api/admin/capabilities/grant-role", json={"role": role, "capability": capability})


@mcp.tool()
def brainkb_revoke_role_capability(role: str, capability: str) -> Any:
    """(Admin only) Revoke a capability from a role/group."""
    return _post("/api/admin/capabilities/revoke-role", json={"role": role, "capability": capability})


@mcp.tool()
def brainkb_list_access_rules(slug: str) -> Any:
    """List a space's fine-grained access rules (member/manager of the space)."""
    return _get(f"/api/spaces/{_seg(slug)}/access-rules")


@mcp.tool()
def brainkb_add_access_rule(slug: str, action: str, subject_type: str, subject_value: str) -> Any:
    """(Space manager) Restrict a space action to a subject.
    action: 'read' | 'write' | 'manage'.
    subject_type: 'global_role' (e.g. 'Admin','Lab Member') | 'member' (an email) |
    'space_role' ('viewer'|'editor'|'owner', matched as >=).
    When rules exist for an action, only matching callers may perform it; the space
    owner and Admin/SuperAdmin always bypass (no lockout). Example: restrict writing
    to Admins -> action='write', subject_type='global_role', subject_value='Admin'."""
    return _post(f"/api/spaces/{_seg(slug)}/access-rules",
                 json={"action": action, "subject_type": subject_type, "subject_value": subject_value})


@mcp.tool()
def brainkb_remove_access_rule(slug: str, rule_id: int) -> Any:
    """(Space manager) Delete a fine-grained access rule by its id
    (see brainkb_list_access_rules)."""
    return _delete(f"/api/spaces/{_seg(slug)}/access-rules/{rule_id}")


# --------------------------------------------------------------------------- #
# admin user management (usermanagement service; requires Admin/SuperAdmin)
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_list_users(q: str = "", role: str = "", limit: int = 50) -> Any:
    """(Admin) List users (profiles) — filter by `q` (name/email/orcid) or `role`.
    Shows profile_id, email, roles, providers, ban status."""
    params: Dict[str, Any] = {"limit": limit}
    if q:
        params["q"] = q
    if role:
        params["role"] = role
    return _um("GET", "/api/admin/users", params=params)


@mcp.tool()
def brainkb_available_roles() -> Any:
    """(Admin) List the available roles/groups (Admin, Lab Member, Curator, …)."""
    return _um("GET", "/api/admin/roles")


@mcp.tool()
def brainkb_create_role(name: str, category: str = "Content", description: str = "") -> Any:
    """(Admin) Create a new role/group — e.g. an 'External' collaborator group —
    which can then be assigned with brainkb_assign_role."""
    return _um("POST", "/api/admin/roles",
               json={"name": name, "category": category, "description": description})


@mcp.tool()
def brainkb_assign_role(email: str, role: str) -> Any:
    """(Admin) Assign a role/group to a user by email (e.g. 'Lab Member', 'External',
    or a custom group). The user must already have a profile (created on first
    login/registration). NOTE: assigning the 'Admin'/'SuperAdmin' role is
    SuperAdmin-only (hierarchy: SuperAdmin > Admin)."""
    pid = _um_profile_id(email)
    if not pid:
        return {"error": True, "detail": f"no profile found for {email} — the user must sign in "
                                         "(e.g. via Globus) or be created before roles can be assigned."}
    return _um("POST", f"/api/admin/users/{pid}/roles", json={"role": role, "is_active": True})


@mcp.tool()
def brainkb_remove_role(email: str, role: str) -> Any:
    """(Admin) Remove a role/group from a user by email."""
    pid = _um_profile_id(email)
    if not pid:
        return {"error": True, "detail": f"no profile found for {email}"}
    return _um("DELETE", f"/api/admin/users/{pid}/roles/{_seg(role)}")


@mcp.tool()
def brainkb_activate_user(email: str) -> Any:
    """(Admin) Activate a user's account (sets the JWT user active) by email."""
    return _um("POST", "/api/admin/users/activate", json={"email": email})


@mcp.tool()
def brainkb_deactivate_user(email: str) -> Any:
    """(Admin) Deactivate a user's account by email."""
    return _um("POST", "/api/admin/users/deactivate", json={"email": email})


@mcp.tool()
def brainkb_ban_user(email: str, reason: str) -> Any:
    """(Admin) Ban a user by email (reversible; preserves history). This is how
    accounts are removed — there is NO hard delete. Banning an Admin is
    SuperAdmin-only; SuperAdmin accounts cannot be banned."""
    pid = _um_profile_id(email)
    if not pid:
        return {"error": True, "detail": f"no profile found for {email}"}
    return _um("POST", f"/api/admin/users/{pid}/ban", json={"reason": reason})


@mcp.tool()
def brainkb_unban_user(email: str) -> Any:
    """(Admin) Lift a ban on a user by email."""
    pid = _um_profile_id(email)
    if not pid:
        return {"error": True, "detail": f"no profile found for {email}"}
    return _um("DELETE", f"/api/admin/users/{pid}/ban")


@mcp.tool()
def brainkb_list_permissions() -> Any:
    """(Admin) List all usermanagement permissions (resource/action pairs used for
    page-access and role-permission mapping). These are the addable 'permission'
    options; KG action-capabilities are listed by brainkb_list_capabilities."""
    return _um("GET", "/api/admin/permissions")


@mcp.tool()
def brainkb_create_permission(name: str, resource: str, action: str, description: str = "") -> Any:
    """(Admin) Create a new usermanagement permission, e.g.
    name='dataset.export', resource='dataset', action='export'. Attach it to roles
    via the usermanagement role-permissions API."""
    return _um("POST", "/api/admin/permissions",
               json={"name": name, "resource": resource, "action": action, "description": description})


# --------------------------------------------------------------------------- #
# Public HTTP routes (landing page + health check)
# --------------------------------------------------------------------------- #
# /mcp is the protocol endpoint and answers a browser GET with
# "Not Acceptable: Client must accept text/event-stream" — correct, but it looks
# like a failure to anyone who opens the host in a browser, and / answers 404.
# These two routes exist so the host explains itself. custom_route handlers are
# deliberately UNAUTHENTICATED (see FastMCP.custom_route), so they must disclose
# nothing about the deployment: no backend URLs, no versions, no config, no
# request echo beyond an escaped hostname.

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:[0-9]{1,5})?$")
# Public MCP hosts the landing page may name in its sample commands. Any other
# Host (localhost, a bare IP, a proxy's internal name) falls back to production,
# so the page never tells people to register an address they can't reach.
_PUBLIC_HOSTS = ("mcp.brainkb.org", "mcp.sandbox.brainkb.org")
_DEFAULT_PUBLIC_HOST = "mcp.brainkb.org"


def _self_host(request: Any) -> str:
    """Public hostname to show in the sample commands.

    MCP_PUBLIC_HOST, when set, always wins. Otherwise the Host header is used only
    if it names a known public host; it is caller-controlled, so it is matched
    against a fixed list (and HTML-escaped at the call site) rather than reflected.
    """
    configured = os.getenv("MCP_PUBLIC_HOST", "").strip().lower()
    if configured and _HOST_RE.match(configured):
        return configured
    host = (request.headers.get("host") or "").split(",")[0].strip().lower()
    host = host.split(":")[0]
    return host if host in _PUBLIC_HOSTS else _DEFAULT_PUBLIC_HOST


@mcp.custom_route("/upload", methods=["POST"])
async def _upload(request: Any) -> Any:
    """Stage an RDF file for ingest, streamed straight from the client's disk.

    Unlike the other two custom routes this one is AUTHENTICATED — it writes to the
    server's filesystem and acts on the caller's behalf, so it validates the bearer
    token through exactly the same path the MCP tools use (`_identify_raw`), and
    checks the `write` scope the ingest API itself requires.

        import requests
        requests.post("https://mcp.brainkb.org/upload",
                      params={"filename": "review.ttl"},
                      headers={"Authorization": f"Bearer {TOKEN}"},
                      data=open("review.ttl", "rb"))

    Pass `graph=<iri>` as well to have the server submit the ingest itself and answer
    202 immediately — upload and forget. Poll brainkb_upload_status(upload_id) or,
    once it reports a job_id, brainkb_job_status(job_id).
    """
    from starlette.responses import JSONResponse

    if not _UPLOAD_ENABLED:
        return JSONResponse({"error": True, "detail": (
            "uploads are disabled on this server (MCP_UPLOAD_ENABLED=false)")}, 403)

    authz = request.headers.get("authorization", "")
    if authz[:7].lower() != "bearer " or not authz[7:].strip():
        return JSONResponse(
            {"error": True, "detail": (
                "send 'Authorization: Bearer <token>' — a Personal Access Token "
                "(brainkb_pat_...) or a refresh token. Uploads are attributed to "
                "that identity and only it can ingest them.")},
            401, headers={"WWW-Authenticate": "Bearer"})
    try:
        ctx = _identify_raw(authz[7:].strip(), _DEFAULT_URL)
    except _NotAuthed as exc:
        return JSONResponse({"error": True, "detail": str(exc)}, 401)
    email = (ctx.get("email") or "").strip()
    if not email:
        return JSONResponse({"error": True, "detail": (
            "the token is valid but carries no identity; cannot attribute an "
            "upload to it")}, 401)
    # Refuse bytes the caller could never ingest. The REST insert routes are gated
    # on the `write` scope, so without it this would be staging a file solely to
    # fail later — while consuming the disk in the meantime.
    scopes = _scopes_of(ctx.get("token") or "")
    if scopes and "write" not in scopes:
        return JSONResponse({"error": True, "detail": (
            f"this token has scopes {scopes} and ingest requires 'write'. Nothing "
            "was stored. If you hold a role that should permit writing, your token "
            "predates the role — log out and back in, or mint a fresh PAT.")}, 403)

    graph = (request.query_params.get("graph") or "").strip()
    raw_name = (request.query_params.get("filename")
                or request.headers.get("x-upload-filename") or "upload.ttl")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(raw_name))[:120] or "upload.ttl"
    declared = (request.query_params.get("sha256")
                or request.headers.get("x-upload-sha256") or "").strip().lower()

    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    _upload_sweep()
    staged = _upload_total_bytes()
    if _UPLOAD_TOTAL_BYTES and staged >= _UPLOAD_TOTAL_BYTES:
        return JSONResponse({"error": True, "detail": (
            f"the staging area is full ({staged} bytes >= "
            f"{_UPLOAD_TOTAL_BYTES}). Ingest or discard what is already staged "
            "(brainkb_list_uploads / brainkb_discard_upload).")}, 507)

    upload_id = "up_" + os.urandom(16).hex()
    digest = hashlib.sha256()
    total = 0
    try:
        with open(_upload_data_path(upload_id), "wb") as fh:
            # Streamed: a 5 GB body must never be buffered in memory, and the cap
            # has to bite mid-transfer rather than after the disk is full.
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if _UPLOAD_MAX_BYTES and total > _UPLOAD_MAX_BYTES:
                    fh.close()
                    _upload_discard(upload_id)
                    return JSONResponse({"error": True, "detail": (
                        f"file exceeds {_UPLOAD_MAX_BYTES} bytes "
                        "(MCP_UPLOAD_MAX_BYTES)")}, 413)
                digest.update(chunk)
                fh.write(chunk)
    except Exception as exc:                       # noqa: BLE001 - request boundary
        _upload_discard(upload_id)
        return JSONResponse({"error": True, "detail": f"upload failed: {exc}"}, 400)

    if total == 0:
        _upload_discard(upload_id)
        return JSONResponse({"error": True, "detail": (
            "received 0 bytes — use --data-binary @file (not -d, which mangles "
            "newlines)")}, 400)
    got = digest.hexdigest()
    if declared and declared != got:
        _upload_discard(upload_id)
        return JSONResponse({"error": True, "detail": (
            f"digest mismatch: declared {declared}, received {got} ({total} bytes). "
            "Nothing was stored.")}, 400)

    now = time.time()
    meta = {
        "upload_id": upload_id, "filename": name, "bytes": total, "sha256": got,
        "email": email, "state": "staged",
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime(now + _UPLOAD_TTL_MIN * 60)),
    }
    if graph:
        meta.update({"state": "submitting", "named_graph_iri": graph})
    _upload_meta_write(upload_id, meta)

    if graph:
        # Upload and forget: hand the submission to a daemon thread so a multi-GB
        # POST to the ingest API does not hold the client's connection open, and
        # answer 202 with something to poll.
        threading.Thread(
            target=_upload_submit,
            args=(upload_id, meta, ctx["url"], ctx["token"], email, graph),
            daemon=True, name=f"ingest-{upload_id}").start()
        return JSONResponse({
            "upload_id": upload_id, "bytes": total, "sha256": got,
            "state": "submitting", "named_graph_iri": graph,
            "next": (f"brainkb_upload_status('{upload_id}') — it reports a job_id "
                     "once the ingest API accepts the bytes, then poll "
                     "brainkb_job_status(job_id)"),
        }, 202)
    return JSONResponse({
        "upload_id": upload_id, "bytes": total, "sha256": got, "state": "staged",
        "expires_at": meta["expires_at"],
        "next": (f"brainkb_ingest_upload(named_graph_iri, '{upload_id}') — or "
                 "re-upload with &graph=<iri> to have the server submit it for you"),
    }, 201)


# Landing page for a browser that opens the server root. The CSS, JS and page
# data are kept out of the f-string in _landing so their braces need no escaping.
_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brainkb_logo.png")

_LANDING_CSS = """
:root{--ink:#0b1628;--muted:#738094;--blue:#1d4ed8;--line:#e4e8ef;--pale:#f5f7fc;--green:#30ad86}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:95px}
body{margin:0;background:#fff;color:var(--ink);font-family:'DM Sans',ui-sans-serif,system-ui,sans-serif;font-size:16px;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
button{font:inherit;cursor:pointer}
button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #93c5fd;outline-offset:5px}
code,pre,.mono{font-family:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace}
.wrap{max-width:1200px;margin:auto;padding:0 36px}

header{height:90px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}
.logo{display:flex;align-items:center;gap:12px;font-size:24px;font-weight:700;letter-spacing:-.8px}
.logo img{width:40px;height:40px;border-radius:50%}
.logo small{font:500 12px 'IBM Plex Mono',monospace;color:var(--blue);background:#eff6ff;border:1px solid #dbeafe;border-radius:4px;padding:3px 6px;letter-spacing:0}
nav{display:flex;align-items:center;gap:32px;font-size:14px;color:#596476}
nav a:not(.btn):hover{color:var(--blue)}

.btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:7px;padding:13px 21px;font-size:14px;font-weight:600;transition:transform .2s,background .2s}
.btn:hover{background:#f1f4ff;transform:translateY(-2px)}
.primary{background:var(--blue);border-color:var(--blue);color:#fff;box-shadow:0 4px 9px #1d4ed820}
.primary:hover{background:#1e40af}
.navcta{padding:10px 17px}
.eyebrow{font:12px 'IBM Plex Mono',monospace;letter-spacing:1.5px;color:var(--blue);text-transform:uppercase}

.hero{padding:78px 0 62px;display:grid;grid-template-columns:1fr 1fr;gap:30px;align-items:center}
.hero-copy{min-width:0}
.badge{display:inline-flex;align-items:center;gap:10px;background:#eff6ff;border:1px solid #dbeafe;padding:7px 10px;border-radius:5px;font:12px 'IBM Plex Mono',monospace;color:#1e3a8a}
.badge b{background:#dbeafe;padding:3px 5px;font-weight:500;color:var(--blue)}
h1{font-size:64px;line-height:1.04;letter-spacing:-3.2px;font-weight:550;margin:25px 0 23px}
h1 span{background:linear-gradient(92deg,#0b1628 0%,#1d4ed8 70%,#3b82f6 100%);-webkit-background-clip:text;background-clip:text;color:transparent}
.lead{color:#6b7789;line-height:1.75;max-width:460px;font-size:17px}
.actions{display:flex;gap:11px;margin:28px 0 19px;flex-wrap:wrap}
.note{font-size:12px;color:#8690a1;font-family:'IBM Plex Mono',monospace}

.hero-visual{height:430px;position:relative;background-image:radial-gradient(#dce2ee 1px,transparent 1px);background-size:18px 18px;border-radius:50%;display:flex;align-items:center;justify-content:center}
.orbit{position:absolute;border:1px solid #e5e9f5;border-radius:50%;width:350px;height:350px}
.orbit.second{width:240px;height:240px}
.network{position:absolute;width:100%;height:100%;overflow:visible}
.network path{fill:none;stroke:#93c5fd;stroke-width:1.5;stroke-dasharray:5 5;animation:flow 20s linear infinite}
@keyframes flow{to{stroke-dashoffset:-200}}
.core{z-index:2;width:142px;height:142px;border-radius:27px;background:#fff;border:1px solid #bfdbfe;box-shadow:0 14px 50px #1d4ed820,0 0 0 10px #ffffffa8;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px}
.core img{width:56px;height:56px;border-radius:50%}
.core strong{font-size:14px}
.node{position:absolute;background:#fff;border:1px solid var(--line);border-radius:9px;box-shadow:0 5px 15px #26355607;padding:13px 16px;display:flex;align-items:center;gap:11px;font-size:14px;z-index:2}
.node small{display:block;color:#8a93a4;font:11px 'IBM Plex Mono',monospace;margin-top:4px}
.ico{height:31px;width:31px;flex:none;background:#eff6ff;border:1px solid #dbeafe;border-radius:7px;display:grid;place-items:center;color:#1e3a8a;font:17px 'IBM Plex Mono',monospace}
.n1{top:37px;left:4%}.n2{right:0;top:80px}.n3{left:0;bottom:93px}.n4{right:4%;bottom:51px}
.live{position:absolute;bottom:1px;font:11px 'IBM Plex Mono',monospace;color:#77869c;display:flex;align-items:center;gap:8px}
.live i{width:6px;height:6px;background:var(--green);border-radius:100%;box-shadow:0 0 0 3px #30ad8626}
.live.down{color:#b4485b}
.live.down i{background:#d9546b;box-shadow:0 0 0 3px #d9546b26}

.strip{padding:27px 0 36px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);text-align:center}
.strip p{font:11px 'IBM Plex Mono',monospace;color:#929baa;letter-spacing:1.4px;margin:0 0 24px}
.brands{display:flex;align-items:center;justify-content:space-around;color:#5f6878;font-weight:600;font-size:20px}
.brands span{display:flex;gap:9px;align-items:center}
.brands em{font-style:normal;font-size:23px;color:#808b9f}

.section{padding:80px 0}
.section-title{display:flex;justify-content:space-between;align-items:end;gap:30px;margin-bottom:33px}
h2{font-size:39px;font-weight:550;letter-spacing:-1.5px;margin:12px 0 0;line-height:1.18}
.section-title>p{max-width:360px;line-height:1.7;font-size:15px;color:var(--muted);margin:0}

.features{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}
.card{border:1px solid var(--line);border-radius:12px;padding:27px;background:linear-gradient(150deg,#fff,#f9faff);display:flex;flex-direction:column}
.card.highlight{border-color:#bfdbfe;box-shadow:0 10px 30px #1d4ed812}
.card h3{font-weight:550;font-size:20px;letter-spacing:-.4px;margin:22px 0 10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.card h3 .count{font:500 11px 'IBM Plex Mono',monospace;color:var(--blue);background:#eff6ff;border:1px solid #bfdbfe;border-radius:4px;padding:3px 6px;letter-spacing:0}
.card p{font-size:14px;line-height:1.7;color:var(--muted);margin:0}
.card p code{font-size:12px;color:#1e40af}
.card .ico{width:39px;height:39px;font-size:20px}
.mini{margin-top:auto;padding-top:24px}
.mini-box{background:#fff;border:1px solid var(--line);border-radius:6px;padding:12px 14px;font:11px 'IBM Plex Mono',monospace;color:#7a879b}
.mini-line{display:flex;align-items:baseline;gap:8px;margin:5px 0;color:var(--blue)}
.mini-line b{font-weight:400;color:#3d4c69}

.navpublic{white-space:nowrap}
.public{border-top:1px solid var(--line)}
.public .eyebrow{color:#0f8a63}
.access{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.access-card{border:1px solid var(--line);border-radius:12px;padding:27px;background:linear-gradient(150deg,#fff,#f9faff)}
.access-card.open{border-color:#a7e3cc;background:linear-gradient(150deg,#fff,#f1fbf6);box-shadow:0 10px 30px #30ad8614}
.access-card h3{font-weight:550;font-size:20px;letter-spacing:-.4px;margin:0 0 6px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.access-card .sub{font-size:14px;color:var(--muted);margin:0 0 18px;line-height:1.6}
.tag{font:500 11px 'IBM Plex Mono',monospace;border-radius:4px;padding:3px 6px;letter-spacing:.5px}
.tag.green{color:#0f7a58;background:#e7f8f0;border:1px solid #a7e3cc}
.tag.blue{color:var(--blue);background:#eff6ff;border:1px solid #bfdbfe}
.access-card ul{list-style:none;margin:0;padding:0;display:grid;gap:10px;font-size:14px;line-height:1.5}
.access-card li{display:flex;gap:10px;align-items:baseline}
.access-card li i{font-style:normal;font-weight:700;width:14px;flex:none;text-align:center}
.access-card li i.y{color:#16a34a}
.access-card li i.n{color:#b4bcc9}
.access-card li.off{color:#8a94a5}
.access-card code{font-size:12px;color:#1e40af}
.access-foot{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-top:18px}
.access-foot div{border:1px dashed #d6dce8;border-radius:10px;padding:18px 20px;font-size:14px;line-height:1.65;color:var(--muted)}
.access-foot b{display:block;color:var(--ink);font-weight:600;margin-bottom:4px}
.access-foot code{font-size:12px;color:#1e40af;word-break:break-all}
.note a{color:#0f8a63;text-decoration:underline;text-underline-offset:2px}
@media(max-width:900px){.access-foot{grid-template-columns:1fr}}
@media(max-width:680px){.access{grid-template-columns:1fr}}

.connect{display:grid;grid-template-columns:.85fr 1.15fr;gap:40px;align-items:start;padding:0 0 80px}
.connect p{color:var(--muted);line-height:1.7;font-size:15px}
.codebox{position:relative;border:1px solid #dde3ef;border-radius:9px;background:#fff;box-shadow:0 10px 25px #28375906;overflow:hidden}
.codebox + .codebox{margin-top:14px}
.tabs.clients{margin:0 0 12px}
.codebox[hidden]{display:none}
.codebox[data-pane]{margin-top:0!important}
.pane[hidden]{display:none}
.ok{color:#16a34a;font-weight:600;margin-left:3px}
.legend{font-size:13px!important}
.legend a,.steps a{color:var(--blue);text-decoration:underline;text-underline-offset:2px}
.connect p code{font-size:12.5px;color:#1e40af}
.connect a.inline{color:var(--blue);text-decoration:underline;text-underline-offset:2px}
.faq-code{margin-top:14px}
.tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;line-height:16px}
.tag.skill{color:#6d28d9;background:#f5f3ff;border:1px solid #ddd6fe}
.tag.mcp{color:#1d4ed8;background:#eff6ff;border:1px solid #bfdbfe}
.connect .links{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}
.connect p code{font-size:12.5px;color:#1e40af}
.setup{text-align:center;padding:10px 0 80px}
.setup h2{margin-top:12px}
.setup-lead{max-width:680px;margin:16px auto 0;color:var(--muted);font-size:16px;line-height:1.7}
.segmented{display:inline-flex;gap:4px;margin:32px auto 26px;padding:5px;border:1px solid var(--line);border-radius:12px;background:var(--pale)}
.segmented button{border:0;background:transparent;border-radius:8px;padding:9px 18px;font-size:14px;color:#788398;cursor:pointer}
.segmented button[aria-pressed=true]{background:#fff;color:var(--ink);box-shadow:0 1px 4px #0b16281a}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;text-align:left}
.card{border:1px solid var(--line);border-radius:14px;background:#fff;padding:20px;min-width:0;box-shadow:0 10px 25px #28375906}
.card.accent{border-color:#c4b5fd;background:linear-gradient(150deg,#fff,#faf5ff)}
.card-head{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.card-head h4{margin:0;font-size:16px;font-weight:600}
.card-head .tag{margin-left:4px;vertical-align:2px}
.num{flex:none;width:34px;height:34px;border-radius:50%;background:#0f172a;color:#fff;font-size:14px;font-weight:600;line-height:34px;text-align:center}
.card p{margin:0 0 12px;font-size:14px;line-height:1.65;color:var(--muted)}
.card p.hint{margin:12px 0 0;font-size:12.5px}
.card code{font-size:12px;color:#1e40af;overflow-wrap:anywhere}
.card a:not(.btn){color:var(--blue);text-decoration:underline;text-underline-offset:2px}
.card-actions{margin-top:14px}
.card-actions .btn{padding:9px 15px;font-size:13px}
.setup-foot{margin:26px auto 0;max-width:760px;color:var(--muted);font-size:13px;line-height:1.7}
.setup-foot code{font-size:12px;color:#1e40af}
.setup-foot a{color:var(--blue)}
@media(max-width:900px){.cards{grid-template-columns:1fr}}
.steps{margin:0;padding:12px 18px 14px 36px;border-top:1px solid var(--line);font-size:13px;line-height:1.7;color:var(--muted)}
.steps code{font-size:12px;color:#1e40af}
.steps a{color:var(--blue);text-decoration:underline;text-underline-offset:2px}
.codebox-head{display:flex;justify-content:space-between;align-items:center;padding:11px 16px;border-bottom:1px solid var(--line);font:11px 'IBM Plex Mono',monospace;color:#929aad}
.codebox pre{margin:0;padding:16px 18px;font-size:12.5px;line-height:1.8;color:#3d4c69;white-space:pre;overflow-x:auto}
.copy{border:1px solid var(--line);background:#fff;border-radius:5px;padding:4px 9px;font:11px 'IBM Plex Mono',monospace;color:#788398}
.copy:hover{color:var(--blue);border-color:#bfdbfe;background:#eff6ff}
details p a{color:var(--blue);text-decoration:underline;text-underline-offset:2px}

.workflow{background:#f7f9fd;border:1px solid var(--line);border-radius:14px;display:grid;grid-template-columns:.85fr 1.15fr;gap:40px;padding:42px;margin-bottom:75px}
.workflow>*{min-width:0}
.workflow h2{font-size:35px}
.workflow p{color:var(--muted);line-height:1.7;font-size:15px}
.tabs{display:flex;gap:7px;margin-top:26px;flex-wrap:wrap}
.tabs button{background:transparent;border:1px solid #dfe5ee;border-radius:5px;padding:8px 12px;font-size:12px;color:#788398}
.tabs button[aria-pressed=true]{color:var(--blue);background:#eff6ff;border-color:#bfdbfe}
.tabs button{position:relative;overflow:hidden}
.tabs button.playing::after{content:'';position:absolute;left:0;bottom:0;height:2px;width:100%;background:var(--blue);transform-origin:left;animation:tabprogress var(--dwell,6.5s) linear both}
@keyframes tabprogress{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.terminal{background:#fff;border:1px solid #dde3ef;border-radius:9px;overflow:hidden;box-shadow:0 10px 25px #28375906}
.terminal-head{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding:14px 18px;font:11px 'IBM Plex Mono',monospace;color:#929aad}
.dots{letter-spacing:3px;color:#cbd3df}
.terminal-body{padding:20px;font:12px/1.9 'IBM Plex Mono',monospace;min-height:250px;overflow-wrap:anywhere}
.terminal-body .result{overflow-x:auto}
.prompt{color:#3d4c69;margin-bottom:13px}
.log{color:#8b96a8}
.log strong{font-weight:400;color:#1d4ed8}
.log em{font-style:normal;color:#319575}
.result{margin-top:14px;padding:9px 12px;border:1px solid #d7ede5;background:#f6fcf9;color:#34826c;border-radius:4px}
.result b{font-weight:500;color:#1f6b55}
.result .src{display:block;margin-top:6px;color:#6f8f84;font-size:11px}
.result ul.notes{margin:8px 0 0;padding-left:16px;color:#3d4c69;font-size:11px;line-height:1.6}
.result ul.notes li{margin:2px 0}
.result ul.notes b{color:#1d4ed8}
.rtable{width:100%;border-collapse:collapse;margin-top:8px;font-size:11px;line-height:1.5;color:#3d4c69;background:#fff;border:1px solid #d7ede5}
.rtable th,.rtable td{text-align:left;padding:4px 8px;border-bottom:1px solid #e6f1ec;vertical-align:top}
.rtable th{font-weight:500;color:#34826c;background:#f6fcf9;white-space:nowrap}
.rtable{overflow-wrap:normal;word-break:normal}
.rtable .n{text-align:right;white-space:nowrap}
.rtable td:first-child{color:#1d4ed8;white-space:nowrap}
.rtable tr:last-child td{border-bottom:0}
.step{animation:appear .45s ease both}
@keyframes appear{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}

.lower{display:grid;grid-template-columns:1fr 1fr;gap:90px;padding-bottom:80px}
.lower>*{min-width:0}
.lower h2{font-size:35px}
.lower p{color:var(--muted);font-size:15px;line-height:1.8}
details{border-bottom:1px solid var(--line);padding:19px 0}
summary{cursor:pointer;font-size:15px;list-style:none;display:flex;justify-content:space-between;gap:20px}
summary::-webkit-details-marker{display:none}
summary:after{content:'+';color:#8895ae}
details[open] summary:after{content:'−'}
details p{margin-bottom:0;font-size:14px!important}
details p code{font-size:12.5px;color:#1e40af;background:var(--pale);border:1px solid var(--line);border-radius:4px;padding:1px 5px}

.cta{border-top:1px solid var(--line);padding:45px 0;display:flex;align-items:center;justify-content:space-between;gap:24px}
.cta h2{font-size:29px;margin:0}
.cta p{color:var(--muted);font-size:14px}
footer{border-top:1px solid var(--line);padding:24px 0 30px;display:flex;justify-content:space-between;color:#939baa;font-size:12px}
.footerlinks{display:flex;gap:23px}
.footerlinks a:hover{color:var(--blue)}

@media(max-width:900px){h1{font-size:50px}.hero{gap:0}.node{padding:10px;font-size:12px}.hero-visual{transform:scale(.92)}.wrap{padding:0 24px}.workflow,.connect{gap:22px}.workflow{padding:28px}.lower{gap:40px}.features{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:680px){header{height:76px}nav{gap:15px}nav>a:not(.navcta):not(.navpublic){display:none}.logo small{display:none}.hero{grid-template-columns:1fr;padding-top:45px;gap:20px}h1{font-size:44px;letter-spacing:-2px}.hero-visual{height:380px;transform:none}.brands{flex-wrap:wrap;gap:20px;font-size:16px;justify-content:center}.section{padding:55px 0}.section-title{display:block}.section-title>p{margin-top:20px}.features,.workflow,.lower,.connect{grid-template-columns:1fr}.features{gap:12px}.workflow{padding:25px;margin-bottom:55px}.lower{gap:18px;padding-bottom:45px}.cta{display:block}.cta .btn{margin-top:15px}footer{gap:20px;flex-wrap:wrap}h2{font-size:31px}.note{font-size:11px}.terminal-body{font-size:11px;padding:15px}.n1{left:0}.n4{right:0}}
@media(max-width:420px){.node small{display:none}.hero-visual{height:340px}.core{width:124px;height:124px}.core img{width:48px;height:48px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;scroll-behavior:auto!important;transition:none!important}}
"""

_LANDING_JS = """
(function () {
  var status = document.getElementById('status');
  if (status && window.fetch) {
    var check = function () {
      fetch('/healthz', {cache: 'no-store'}).then(function (r) { return r.ok; })
        .catch(function () { return false; })
        .then(function (up) {
          status.classList.toggle('down', !up);
          status.querySelector('span').textContent = up ? 'ONLINE' : 'OFFLINE';
        });
    };
    check();
    setInterval(check, 30000);
  }
})();
(function () {
  var data = document.getElementById('session-data');
  var session = document.getElementById('session');
  if (data && session) {
    var examples = JSON.parse(data.textContent);
    var keys = Object.keys(examples);
    var buttons = document.querySelectorAll('[data-task]');
    var current = 0, timer = null, paused = false;
    var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var kindEl = document.getElementById('session-kind');
    var render = function (idx) {
      current = idx;
      var e = examples[keys[idx]];
      var step = 0.45;
      session.innerHTML = '<div class="prompt step">› ' + e.prompt + '</div>' +
        e.lines.map(function (s, i) {
          return '<div class="log step" style="animation-delay:' + (0.35 + i * step) + 's">0' + (i + 1) + ' &nbsp; ' + s + '</div>';
        }).join('') +
        '<div class="result step" style="animation-delay:' + (0.45 + e.lines.length * step) + 's">' + e.result + '</div>';
      if (kindEl) kindEl.textContent = e.kind.toUpperCase();
      buttons.forEach(function (b) {
        var on = b.dataset.task === keys[idx];
        b.setAttribute('aria-pressed', String(on));
        b.classList.remove('playing');
        b.style.setProperty('--dwell', e.dwell + 'ms');
        if (on && !reduced && !paused) { void b.offsetWidth; b.classList.add('playing'); }
      });
    };
    var schedule = function () {
      clearTimeout(timer);
      if (reduced || paused || document.hidden) return;
      timer = setTimeout(function () { render((current + 1) % keys.length); schedule(); }, examples[keys[current]].dwell);
    };
    buttons.forEach(function (b) {
      b.addEventListener('click', function () { render(keys.indexOf(b.dataset.task)); schedule(); });
    });
    var box = document.getElementById('workflow');
    var pause = function () { paused = true; clearTimeout(timer);
      buttons.forEach(function (b) { b.classList.remove('playing'); }); };
    var resume = function () { if (!paused) return; paused = false; render(current); schedule(); };
    box.addEventListener('mouseenter', pause);
    box.addEventListener('mouseleave', resume);
    box.addEventListener('focusin', pause);
    box.addEventListener('focusout', function (ev) { if (!box.contains(ev.relatedTarget)) resume(); });
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { clearTimeout(timer); } else { schedule(); }
    });
    render(current);
    schedule();
  }
  var clientButtons = document.querySelectorAll('[data-client]');
  clientButtons.forEach(function (b) {
    b.addEventListener('click', function () {
      var group = b.closest('section');
      group.querySelectorAll('[data-client]').forEach(function (o) { o.setAttribute('aria-pressed', String(o === b)); });
      group.querySelectorAll('[data-pane]').forEach(function (p) {
        p.hidden = p.dataset.pane !== b.dataset.client;
      });
    });
  });
  document.querySelectorAll('.codebox').forEach(function (box) {
    var btn = box.querySelector('.copy');
    if (!btn) return;
    if (!navigator.clipboard) { btn.remove(); return; }
    var label = btn.textContent;
    btn.addEventListener('click', function () {
      navigator.clipboard.writeText(box.dataset.copy || box.querySelector('pre').innerText).then(function () {
        btn.textContent = 'Copied';
        setTimeout(function () { btn.textContent = label; }, 1500);
      });
    });
  });
})();
"""

# Capability cards: (icon, title, description, example requests). The page is for
# people, so the examples are things a user asks their agent, not tool names. The
# ready-made-questions card is built at request time so its count comes from the
# QA registry.
_LANDING_FEATURES = (
    ("↓", "Ingest",
     "Load RDF from text, files or large streamed uploads. Every ingest runs as a "
     "background job, attributed to you.",
     ("Ingest review.ttl into my lab space", "Did last night's ingest finish?")),
    ("⌕", "Search and read",
     "Search everything you may read, open a whole space, or list the graphs in the "
     "knowledge base.",
     ("Find anything about Purkinje cells", "What's in the hmba space?")),
    None,  # ready-made questions
    ("↻", "Provenance",
     "See the history of any graph: who added what, when, and the exact triples each "
     "ingest contributed.",
     ("What changed in my space this week?", "Who added these triples?")),
    ("▤", "Workspaces",
     "Create private or public spaces, for yourself or a team, and choose who can read "
     "or edit them.",
     ("Create a private space for my lab", "Give alice@lab.org edit access")),
    ("⚿", "Identity and access",
     "Sign in with Globus, ORCID or GitHub, keep a personal access token, and let "
     "admins manage roles and permissions.",
     ("Log me in with Globus", "Make me a token for this laptop")),
)

# Example sessions for the "how it works" terminal, rendered server-side for the
# first tab and by JS on tab change. "real" sessions replay actual answers from the
# named-entity graph over three papers; "illustrative" ones show a flow with no
# live data behind it. Keep the two labelled honestly.
_REPLAY_SOURCES = ("10.1038/s41576-022-00509-1", "10.7554/eLife.47889", "10.3233/JAD-190687")
# (entity key, papers, mentions, verbatim wording) — entities shared across sources.
_REPLAY_SHARED = (
    ('neocortex', 3, 10, 'isocortex · neocortex · mouse neocortex'),
    ('transcription_factor', 2, 17, 'transcription factors · Transcription factor · Transcription factors · TF'),
    ('single_cell_rna_seq', 2, 15, 'single cell RNA-seq · single-cell RNA-seq · scRNA-seq'),
    ('rna_seq', 2, 13, 'RNA-seq · RNA sequencing'),
    ('mus_musculus', 2, 15, 'mouse'),
    ('hippocampus', 2, 7, 'hippocampus'),
    ('developing_mouse_cortex', 2, 2, 'developing mouse cortex'),
)


# (term, likely label, 2019, 2020, 2023) — dated sources per year for entities
# mapped exactly or closely to each term; "–" means no source that year.
_REPLAY_TERMS_BY_YEAR = (
    ("UBERON_0001950", "neocortex", "1", "1", "1"),
    ("UBERON_0001954", "hippocampus (Ammon's horn)", "–", "1", "1"),
    ("NCBITaxon_10090", "Mus musculus", "1", "–", "1"),
    ("EFO_0008896", "RNA-seq", "1", "–", "1"),
    ("EFO_0008913", "single-cell RNA-seq", "1", "–", "1"),
    ("brainkb concept 7f506536…", "provisional", "1", "–", "1"),
    ("brainkb concept ac3ec995…", "provisional", "1", "–", "1"),
    ("brainkb concept d9d088dc…", "provisional", "1", "–", "1"),
)


# (relationship, count, what it claims) — ne_entity_external_mappings on the
# sandbox named-entity test graph: 216 mappings for 215 entities.
_REPLAY_MAPPINGS = (
    ("exact", "192 (89%)", "The same concept; the only identity claim"),
    ("close", "19", "Similar enough for some uses, but not the same"),
    ("broad", "2", "The ontology term is more general than the entity"),
    ("narrow", "1", "The ontology term is more specific than the entity"),
    ("related", "2", "Associated, not the same"),
)


def _replay_table(headers: Any, rows: Any, numeric: Any = ()) -> str:
    """A small results table for the replay terminal; `numeric` are column
    indexes to right-align."""
    def cell(tag: str, idx: int, value: Any) -> str:
        cls = ' class="n"' if idx in numeric else ""
        return f"<{tag}{cls}>{html.escape(str(value))}</{tag}>"
    head = "".join(cell("th", i, h) for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(cell("td", i, v) for i, v in enumerate(r)) + "</tr>"
                   for r in rows)
    return f'<table class="rtable"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


_REPLAY_SOURCES_HTML = " · ".join(html.escape(d) for d in _REPLAY_SOURCES)

_LANDING_SESSIONS = {
    "terms": {
        "label": "Naming variants",
        "kind": "Real data",
        "dwell": 9000,
        "prompt": "How is the neocortex written across the papers in BrainKB?",
        "lines": [
            "Confirms it is acting <strong>as you</strong>",
            "Picks the <strong>naming-variants</strong> question from the ready-made set",
            "Finds the entity <strong>neocortex</strong> in the graph",
            "Groups every <strong>verbatim mention</strong> by paper",
        ],
        "result": ("✓ <b>3 papers · 10 mentions</b>, written as "
                   "<b>isocortex</b> · <b>neocortex</b> · <b>mouse neocortex</b>"
                   f'<span class="src">Sources: {_REPLAY_SOURCES_HTML}</span>'),
    },
    "shared": {
        "label": "Shared entities",
        "kind": "Real data",
        "dwell": 12000,
        "prompt": "Which entities do these papers share, and under what names?",
        "lines": [
            "Picks the <strong>shared-across-sources</strong> question",
            "Counts <strong>papers</strong> and <strong>mentions</strong> per entity",
            "Collects the <strong>verbatim wording</strong> each paper uses",
        ],
        "result": ("✓ <b>7 entities</b> appear in more than one paper"
                   + _replay_table(("Entity", "Papers", "Mentions", "Verbatim wording"),
                                   _REPLAY_SHARED, numeric=(1, 2))
                   + f'<span class="src">Sources: {_REPLAY_SOURCES_HTML}</span>'),
    },
    "years": {
        "label": "Trends by year",
        "kind": "Real data",
        "dwell": 12000,
        "prompt": "How many dated sources mention entities mapped exactly or closely to each term, per year?",
        "lines": [
            "Picks the <strong>terms-by-year</strong> question",
            "Follows <strong>exact and close</strong> ontology mappings to each term",
            "Counts <strong>dated sources</strong> per publication year",
        ],
        "result": ("✓ <b>8 terms</b> appear in more than one year"
                   + _replay_table(("Term", "Likely label", "2019", "2020", "2023"),
                                   _REPLAY_TERMS_BY_YEAR, numeric=(2, 3, 4))
                   + f'<span class="src">Sources: {_REPLAY_SOURCES_HTML}</span>'),
    },
    "mappings": {
        "label": "Mapping review",
        "kind": "Real data",
        "dwell": 15000,
        "prompt": "What is each entity mapped to, and at which mapping relationship?",
        "lines": [
            "Picks the <strong>mapping-relationship</strong> question",
            "Reads <strong>exact, close, broad, narrow</strong> and <strong>related</strong> matches",
            "Leaves out <strong>provisional BRAINKB</strong> concepts, which count as unmapped",
            "Flags the <strong>non-exact</strong> matches for review",
        ],
        "result": ("✓ <b>216 mappings</b> for <b>215 entities</b>"
                   + _replay_table(("Relationship", "Count", "What it claims"),
                                   _REPLAY_MAPPINGS, numeric=(1,))
                   + '<ul class="notes">'
                   '<li><b>hippocampus</b> is only a close match: UBERON:0001954 is Ammon\'s '
                   'horn, which is narrower than the hippocampus.</li>'
                   '<li><b>chromatin_immunoprecipitation</b> has two exact targets, '
                   'EFO:0004176 and OBI:0001975. Fine, but worth a quick check.</li>'
                   '<li><b>762 provisional BRAINKB concepts</b> are left out; they count '
                   'as unmapped.</li></ul>'
                   '<span class="src">Graph: sandbox named-entity test graph. Mappings '
                   'belong to the entity, not to any one paper.</span>'),
    },
    "ingest": {
        "label": "Ingest a file",
        "kind": "Illustrative",
        "dwell": 6500,
        "prompt": "Ingest review.ttl into my lab space.",
        "lines": [
            "Confirms it is acting <strong>as you</strong>",
            "Finds <strong>your lab space</strong> and its graph",
            "Streams the file <strong>straight from disk</strong>",
            "Checks the <strong>triple count</strong> against the file",
        ],
        "result": "✓ Ingested, attributed to you, and nothing lost.",
    },
    "history": {
        "label": "Trace a change",
        "kind": "Illustrative",
        "dwell": 6500,
        "prompt": "What changed in my lab space this week, and who did it?",
        "lines": [
            "Lists the <strong>graphs</strong> in the space",
            "Reads each graph's <strong>change log</strong>",
            "Looks up <strong>who</strong> ran each ingest",
            "Pulls the <strong>exact triples</strong> that were added",
        ],
        "result": "✓ A dated change log with the person behind each change.",
    },
}


def _session_html(key: str) -> str:
    e = _LANDING_SESSIONS[key]
    lines = "".join(
        f'<div class="log">0{i + 1} &nbsp; {line}</div>' for i, line in enumerate(e["lines"])
    )
    return f'<div class="prompt">› {e["prompt"]}</div>{lines}<div class="result">{e["result"]}</div>'


def _feature_card(icon: str, title: str, desc: str, examples: Any,
                  highlight: bool = False, count: str = "") -> str:
    count_html = f'<span class="count">{count}</span>' if count else ""
    rows = "".join(f'<div class="mini-line">› <b>{html.escape(e)}</b></div>' for e in examples)
    return (f'<article class="card{" highlight" if highlight else ""}">'
            f'<span class="ico">{icon}</span><h3>{html.escape(title)}{count_html}</h3>'
            f'<p>{desc}</p><div class="mini"><div class="mini-box">{rows}</div></div></article>')


@mcp.custom_route("/", methods=["GET"])
async def _landing(request: Any) -> Any:
    from starlette.responses import HTMLResponse

    host = html.escape(_self_host(request))
    # The brainkb npm installer registers production by default; the sandbox page
    # tells it which server to register instead.
    npx_install = "npx brainkb install" + (" --mcp sandbox" if "sandbox" in host else "")
    cats = qa_registry.categories()
    total = sum(c["query_count"] for c in cats)

    cards = []
    for f in _LANDING_FEATURES:
        if f is None:
            cards.append(_feature_card(
                "◈", "Ready-made questions",
                "Vetted questions about extracted entities, cells, markers, phenotypes, "
                "causal claims and research resources, answered from the graph without writing a query.",
                ("How is the neocortex written across papers?", "Which entities do these papers share?"),
                highlight=True, count=f"{total} questions"))
        else:
            icon, title, desc, examples = f
            cards.append(_feature_card(icon, title, html.escape(desc), examples))
    features = "\n".join(cards)

    first = next(iter(_LANDING_SESSIONS))
    tabs = "".join(
        f'<button type="button" aria-pressed="{"true" if k == first else "false"}" '
        f'data-task="{k}">{html.escape(v["label"])}</button>'
        for k, v in _LANDING_SESSIONS.items()
    )
    session_json = jsonlib.dumps(
        {k: {"prompt": v["prompt"], "lines": v["lines"], "result": v["result"],
             "kind": v["kind"], "dwell": v["dwell"]}
         for k, v in _LANDING_SESSIONS.items()}
    ).replace("</", "<\\/")

    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="BrainKB MCP lets AI agents interact with the BrainKB knowledge base through the Model Context Protocol.">
<title>BrainKB MCP</title>
<link rel="icon" type="image/png" href="/logo.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;550;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{_LANDING_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <a class="logo" href="#top" aria-label="BrainKB MCP home"><img src="/logo.png" alt="" width="40" height="40">BrainKB <small>MCP</small></a>
  <nav aria-label="Main navigation">
    <a href="#capabilities">Capabilities</a>
    <a class="navpublic" href="#public">Open knowledge</a>
    <a href="#skill">Skill</a>
    <a href="#workflow">How it works</a>
    <a href="#questions">Questions</a>
    <a class="btn navcta primary" href="#connect">Connect your agent</a>
  </nav>
</header>

<main id="top">
<section class="hero">
  <div class="hero-copy">
    <div class="badge"><b>MCP</b> ACCESS BRAINKB FROM ANY MCP CLIENT</div>
    <h1>Interact with<br>BrainKB through<br><span>your AI agent.</span></h1>
    <p class="lead">BrainKB MCP connects your AI agent to the BrainKB knowledge base
    through the Model Context Protocol. Ask in plain language, and your agent signs in as
    you, ingests RDF into your workspaces, answers questions over extracted entities, and
    traces where every triple came from.</p>
    <div class="actions">
      <a class="btn primary" href="#connect">Connect your agent</a>
      <a class="btn" href="#workflow">See it in action</a>
    </div>
    <div class="note"><a href="#public">Access open knowledge without login</a> · Per-caller identity · Provenance on every change</div>
  </div>
  <div class="hero-visual" aria-label="Diagram: BrainKB MCP connects your AI agent with BrainKB workspaces, the knowledge graph and provenance">
    <div class="orbit"></div><div class="orbit second"></div>
    <svg class="network" viewBox="0 0 540 430" aria-hidden="true"><path d="M125 77 Q270 75 270 215 M425 118 Q400 215 270 215 M97 300 Q180 215 270 215 M425 338 Q270 355 270 215"/></svg>
    <div class="node n1"><span class="ico">✳</span><div>Your agent<small>REASON &amp; PLAN</small></div></div>
    <div class="node n2"><span class="ico">▤</span><div>Workspaces<small>INGEST &amp; SHARE</small></div></div>
    <div class="core"><img src="/logo.png" alt="BrainKB" width="56" height="56"><strong>One connection.</strong></div>
    <div class="node n3"><span class="ico">≋</span><div>Knowledge graph<small>QUERY &amp; DISCOVER</small></div></div>
    <div class="node n4"><span class="ico">↻</span><div>Provenance<small>TRACE &amp; AUDIT</small></div></div>
    <div class="live" id="status" role="status"><i></i><span>ONLINE</span></div>
  </div>
</section>

<section class="strip" aria-label="What you can reach in BrainKB">
  <p>WHAT YOU CAN REACH IN BRAINKB</p>
  <div class="brands">
    <span><em>▤</em> Workspaces</span>
    <span><em>≋</em> RDF graphs</span>
    <span><em>◈</em> Named entities</span>
    <span><em>↻</em> PROV-O provenance</span>
  </div>
</section>

<section class="section" id="capabilities">
  <div class="section-title">
    <div><span class="eyebrow">Through MCP</span><h2>Less SPARQL.<br>More science.</h2></div>
    <p>Ask in plain language and your agent picks the right BrainKB tools. Every action
    runs as you, within your permissions.</p>
  </div>
  <div class="features">
{features}
  </div>
</section>

<section class="section public" id="public">
  <div class="section-title">
    <div><span class="eyebrow">Open knowledge · no login</span><h2>Access open knowledge.<br>No need to log in.</h2></div>
    <p>Public spaces in BrainKB are open knowledge for everyone. Connect any MCP client
    without logging in and your agent can browse, search and read them. It is <b>read-only</b>:
    nothing can be added or changed without an account.</p>
  </div>
  <div class="access">
    <div class="access-card open">
      <h3>No login <span class="tag green">READ-ONLY</span></h3>
      <p class="sub">Anyone, from any MCP client, with no token or account.</p>
      <ul>
        <li><i class="y">✓</i><span>List public spaces <code>brainkb_list_spaces</code></span></li>
        <li><i class="y">✓</i><span>Search across public spaces <code>brainkb_search</code></span></li>
        <li><i class="y">✓</i><span>Read a public space's RDF <code>brainkb_read_space</code></span></li>
        <li class="off"><i class="n">✕</i><span>Private spaces never appear and can't be read</span></li>
        <li class="off"><i class="n">✕</i><span>No ingest, edits, sharing, provenance, SPARQL or admin</span></li>
      </ul>
    </div>
    <div class="access-card">
      <h3>Logged in <span class="tag blue">YOUR ACCOUNT</span></h3>
      <p class="sub">Everything on the left, plus what your role and memberships allow.</p>
      <ul>
        <li><i class="y">✓</i><span>Your own and shared private spaces</span></li>
        <li><i class="y">✓</i><span>Create spaces, ingest RDF, share and publish</span></li>
        <li><i class="y">✓</i><span>Provenance and triple-level change history</span></li>
        <li><i class="y">✓</i><span>Ready-made questions and SPARQL (by role)</span></li>
        <li><i class="y">✓</i><span>Admin tools, if you are authorized</span></li>
      </ul>
    </div>
  </div>
  <div class="access-foot">
    <div><b>What "public" means</b>A space owner chose to publish it. Owners can switch a
    space between public and private at any time; private data is never returned to
    anyone who isn't a member.</div>
    <div><b>How to connect without login</b>Add <code>https://{host}/mcp</code> to your
    client and skip login. Then just ask: "What public spaces are in BrainKB?"</div>
    <div><b>When you'll be asked to log in</b>Only when a request needs an account, such
    as a private space or any change. Your agent says so, then starts the BrainKB
    login.</div>
  </div>
</section>

<section class="connect" id="connect">
  <div>
    <span class="eyebrow">Connect</span>
    <h2>Connect from<br>any MCP client.</h2>
    <p>Point your client at <code>https://{host}/mcp</code> (streamable HTTP). Every call
    that needs an account runs as you: the client signs in with your own BrainKB account,
    never a shared one.</p>
    <p><b>Just reading open knowledge?</b> No need to log in; see
    <a class="inline" href="#public">Open knowledge</a>.</p>
    <p>Apps that connect by URL (Perplexity, claude.ai, ChatGPT) choose <b>OAuth</b> with no
    client ID or secret, then sign in on the BrainKB page that opens.</p>
    <p class="legend"><span class="ok">✓</span> tested with BrainKB · want the agent skill too? See <a href="#skill">Skill</a>.</p>
  </div>
  <div>
    <div class="tabs clients" role="group" aria-label="MCP client">
      <button type="button" data-client="claude-code" aria-pressed="true">Claude Code <span class="ok" title="Tested">✓</span></button>
      <button type="button" data-client="codex" aria-pressed="false">Codex</button>
      <button type="button" data-client="apps" aria-pressed="false">Perplexity <span class="ok" title="Tested">✓</span> · claude.ai <span class="ok" title="Tested">✓</span> · ChatGPT</button>
      <button type="button" data-client="cursor" aria-pressed="false">Cursor <span class="ok" title="Tested">✓</span></button>
      <button type="button" data-client="vscode" aria-pressed="false">VS Code <span class="ok" title="Tested">✓</span></button>
      <button type="button" data-client="copilot" aria-pressed="false">Copilot in VS Code <span class="ok" title="Tested">✓</span></button>
      <button type="button" data-client="other" aria-pressed="false">Other clients</button>
    </div>
    <div class="codebox" data-pane="claude-code">
      <div class="codebox-head"><span>Claude Code · terminal</span><button class="copy" type="button">Copy</button></div>
      <pre>claude mcp add --scope user --transport http brainkb https://{host}/mcp</pre>
      <ol class="steps">
        <li>Start <code>claude</code> and say <i>“log me in to BrainKB”</i>; it hands you a browser link.</li>
        <li>For the server plus the BrainKB skill in one step, use <code>{npx_install}</code> instead (<a href="#skill">Skill</a>).</li>
      </ol>
    </div>
    <div class="codebox" data-pane="codex" hidden>
      <div class="codebox-head"><span>Codex · ~/.codex/config.toml</span><button class="copy" type="button">Copy</button></div>
      <pre>[mcp_servers.brainkb]
url = "https://{host}/mcp"</pre>
      <ol class="steps">
        <li>Add it to <code>~/.codex/config.toml</code>, start <code>codex</code>, and say <i>“log me in to BrainKB”</i>.</li>
        <li>Skill for Codex: <code>{npx_install} --agent codex</code> (<a href="#skill">Skill</a>).</li>
      </ol>
    </div>
    <div class="codebox" data-pane="apps" data-copy="https://{host}/mcp" hidden>
      <div class="codebox-head"><span>Connector apps · add a custom connector</span><button class="copy" type="button">Copy URL</button></div>
      <pre>1. Open the app's connector settings
     Perplexity   Settings → Connectors → + Custom connector   ✓ tested
     claude.ai    Settings → Connectors → Add custom connector     ✓ tested
     ChatGPT      Settings → Apps &amp; Connectors → Create (developer mode)
2. Server URL      https://{host}/mcp
3. Authentication  OAuth (leave client ID and secret empty)
4. Sign in on the BrainKB page that opens, then allow access

Only reading open knowledge? No need to log in: public
spaces can be listed, searched and read with no authentication.</pre>
    </div>
    <div class="codebox" data-pane="cursor" hidden>
      <div class="codebox-head"><span>Cursor · ~/.cursor/mcp.json</span><button class="copy" type="button">Copy</button></div>
      <pre>{{
  "mcpServers": {{
    "brainkb": {{ "url": "https://{host}/mcp" }}
  }}
}}</pre>
      <ol class="steps">
        <li>In Cursor, open <b>Customize → MCPs → New MCP Server</b> (“Add a Custom MCP Server”); in the classic editor, <b>Cursor Settings → MCP → Add new MCP server</b>. Either opens <code>~/.cursor/mcp.json</code> (on Windows: <code>%USERPROFILE%&#92;.cursor&#92;mcp.json</code>).</li>
        <li>Paste the snippet and <b>save</b>. <code>brainkb</code> appears under <b>Connected</b> with its tools enabled; if it shows <b>Needs login</b>, click it and sign in on the BrainKB page.</li>
        <li>Start a <b>New Chat</b> and ask; Cursor asks before running each tool.</li>
      </ol>
    </div>
    <div class="codebox" data-pane="vscode" hidden>
      <div class="codebox-head"><span>VS Code · .vscode/mcp.json</span><button class="copy" type="button">Copy</button></div>
      <pre>{{
  "servers": {{
    "brainkb": {{ "type": "http", "url": "https://{host}/mcp" }}
  }}
}}</pre>
      <ol class="steps">
        <li>Create the file, or open the Command Palette (<b>⇧⌘P</b> on macOS, <b>Ctrl+Shift+P</b> on Windows/Linux), run <b>MCP: Add Server</b> → HTTP → paste the URL. For every workspace, use <b>MCP: Open User Configuration</b>.</li>
        <li>Click <b>Start</b> above <code>brainkb</code>, accept the sign-in prompt, and sign in on the BrainKB page.</li>
        <li>Open Copilot Chat in <b>Agent</b> mode, enable the BrainKB tools in the tools picker, and ask your question.</li>
        <li>More options (user vs. workspace config, sandboxing, troubleshooting): <a href="https://code.visualstudio.com/docs/agent-customization/mcp-servers" target="_blank" rel="noopener">VS Code docs: MCP servers ↗</a></li>
      </ol>
    </div>
    <div class="codebox" data-pane="copilot" hidden>
      <div class="codebox-head"><span>GitHub Copilot in VS Code · ~/.copilot/mcp-config.json</span><button class="copy" type="button">Copy</button></div>
      <pre>{{
  "mcpServers": {{
    "brainkb": {{
      "type": "http",
      "url": "https://{host}/mcp",
      "tools": ["*"]
    }}
  }}
}}</pre>
      <ol class="steps">
        <li>In VS Code, open <b>File → Open File…</b> (or the Command Palette: <b>⇧⌘P</b> on macOS, <b>Ctrl+Shift+P</b> on Windows/Linux) and create <code>~/.copilot/mcp-config.json</code> (on Windows: <code>%USERPROFILE%&#92;.copilot&#92;mcp-config.json</code>). It applies to Copilot everywhere, even with no folder open.</li>
        <li>Paste the snippet and <b>save</b>. <code>"tools": ["*"]</code> enables every BrainKB tool; list tool names instead to allow only some.</li>
        <li>Open Copilot Chat, switch to <b>Agent</b> mode, sign in when prompted, and ask, e.g. <i>"What can I do with BrainKB?"</i> Approve each tool call when asked.</li>
        <li>See also: <a href="https://code.visualstudio.com/docs/agent-customization/mcp-servers" target="_blank" rel="noopener">VS Code docs: MCP servers ↗</a></li>
      </ol>
    </div>
    <div class="codebox" data-pane="other" hidden>
      <div class="codebox-head"><span>Clients that only run local (stdio) servers · via mcp-remote</span><button class="copy" type="button">Copy</button></div>
      <pre>{{
  "mcpServers": {{
    "brainkb": {{
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://{host}/mcp"]
    }}
  }}
}}</pre>
      <ol class="steps">
        <li>Paste into the client's MCP config (e.g. Claude Desktop, Windsurf) and restart it. Needs Node.js.</li>
        <li><code>mcp-remote</code> opens the BrainKB sign-in page in your browser on first connect.</li>
      </ol>
    </div>
  </div>
</section>

<section class="setup" id="skill">
  <span class="eyebrow">Skill</span>
  <h2>Connect with the BrainKB skill.</h2>
  <p class="setup-lead">One command installs the BrainKB skill and registers this MCP server. The skill
  teaches your agent to confirm who it is acting as, ingest large files safely, pick the right
  workspace and question, and answer provenance questions.</p>
  <div class="segmented" role="group" aria-label="Agent">
    <button type="button" data-client="skill-claude" aria-pressed="true">Claude Code</button>
    <button type="button" data-client="skill-codex" aria-pressed="false">Codex</button>
    <button type="button" data-client="skill-other" aria-pressed="false">Any SKILL.md agent</button>
  </div>
  <div class="pane" data-pane="skill-claude">
    <div class="cards">
      <div class="card"><div class="card-head"><span class="num">1</span><h4>Install Claude Code</h4></div>
        <div class="codebox"><div class="codebox-head"><span>terminal</span><button class="copy" type="button">Copy</button></div>
        <pre>npm install -g @anthropic-ai/claude-code</pre></div>
        <div class="card-actions"><a class="btn" href="https://docs.claude.com/en/docs/claude-code/overview" target="_blank" rel="noopener">Get Claude Code ↗</a></div></div>
      <div class="card accent"><div class="card-head"><span class="num">2</span><h4>Install the skill <span class="tag skill">Skill + MCP server</span></h4></div>
        <p>Adds the skill to <code>~/.claude/skills/brainkb</code> and registers <code>brainkb</code> with Claude Code.</p>
        <div class="codebox"><div class="codebox-head"><span>terminal</span><button class="copy" type="button">Copy</button></div>
        <pre>{npx_install}</pre></div>
        <p class="hint"><code>--scope project</code> for this project only · <code>--mcp both</code> for production and sandbox · <code>npx brainkb status</code> to check.</p></div>
      <div class="card"><div class="card-head"><span class="num">3</span><h4>Sign in and ask</h4></div>
        <p>Start <code>claude</code>, then:</p>
        <div class="codebox"><div class="codebox-head"><span>prompt</span><button class="copy" type="button">Copy</button></div>
        <pre>log me in to BrainKB</pre></div>
        <p class="hint">Opens a browser sign-in (Globus, ORCID or GitHub). Then ask, e.g. <i>“Which papers mention the subthalamic nucleus?”</i></p></div>
    </div>
  </div>
  <div class="pane" data-pane="skill-codex" hidden>
    <div class="cards">
      <div class="card"><div class="card-head"><span class="num">1</span><h4>Install Codex</h4></div>
        <div class="codebox"><div class="codebox-head"><span>terminal</span><button class="copy" type="button">Copy</button></div>
        <pre>npm install -g @openai/codex</pre></div>
        <div class="card-actions"><a class="btn" href="https://github.com/openai/codex" target="_blank" rel="noopener">Get Codex ↗</a></div></div>
      <div class="card accent"><div class="card-head"><span class="num">2</span><h4>Install the skill <span class="tag skill">Skill</span></h4></div>
        <div class="codebox"><div class="codebox-head"><span>terminal</span><button class="copy" type="button">Copy</button></div>
        <pre>{npx_install} --agent codex</pre></div>
        <p class="hint">Installs to <code>~/.agents/skills/brainkb</code>; <code>--agent both</code> adds Claude Code too.</p></div>
      <div class="card"><div class="card-head"><span class="num">3</span><h4>Add the server <span class="tag mcp">MCP server</span></h4></div>
        <p>Codex is not registered automatically. Add to <code>~/.codex/config.toml</code>, then start <code>codex</code> and say <i>“log me in to BrainKB”</i>.</p>
        <div class="codebox"><div class="codebox-head"><span>~/.codex/config.toml</span><button class="copy" type="button">Copy</button></div>
        <pre>[mcp_servers.brainkb]
url = "https://{host}/mcp"</pre></div></div>
    </div>
  </div>
  <div class="pane" data-pane="skill-other" hidden>
    <div class="cards">
      <div class="card accent"><div class="card-head"><span class="num">1</span><h4>Install the skill <span class="tag skill">Skill</span></h4></div>
        <div class="codebox"><div class="codebox-head"><span>terminal</span><button class="copy" type="button">Copy</button></div>
        <pre>npx brainkb install --dest ~/my-agent/skills --mcp none</pre></div>
        <p class="hint">Use the folder your agent reads <code>SKILL.md</code> skills from.</p></div>
      <div class="card"><div class="card-head"><span class="num">2</span><h4>Add the server <span class="tag mcp">MCP server</span></h4></div>
        <p>Add <code>https://{host}/mcp</code> (streamable HTTP) in the agent's MCP settings. Per-client steps are under <a href="#connect">Connect</a>.</p></div>
      <div class="card"><div class="card-head"><span class="num">3</span><h4>Sign in and ask</h4></div>
        <p>Ask the agent to log you in to BrainKB, then ask your question.</p></div>
    </div>
  </div>
  <p class="setup-foot">Re-running <code>install</code> updates the skill; a copy you edited is never overwritten silently.
  <code>npx brainkb uninstall</code> removes it. Node.js 18.17+.
  <a href="https://www.npmjs.com/package/brainkb" target="_blank" rel="noopener">brainkb on npm ↗</a> ·
  <a href="https://github.com/sensein/agent_skills/tree/main/skills/brainkb" target="_blank" rel="noopener">Skill on GitHub ↗</a></p>
</section>

<section class="workflow" id="workflow">
  <div>
    <span class="eyebrow">From question to answer</span>
    <h2>Watch your agent<br>work with BrainKB.</h2>
    <p>Replays of real answers from BrainKB: naming variants, shared entities and
    trends by year across three papers, and a mapping review of the sandbox test
    graph. Then illustrative ingest and provenance flows. Pick a task to jump to it; hover to pause.</p>
    <div class="tabs" role="group" aria-label="Example sessions">{tabs}</div>
  </div>
  <div class="terminal">
    <div class="terminal-head"><span><span class="dots">●●●</span> &nbsp; agent-session</span><span id="session-kind">{html.escape(_LANDING_SESSIONS[first]["kind"]).upper()}</span></div>
    <div class="terminal-body" id="session" aria-live="polite">{_session_html(first)}</div>
  </div>
</section>

<section class="lower" id="questions">
  <div>
    <span class="eyebrow">Open by design</span>
    <h2>Every triple<br>has a history.</h2>
    <p>Ingests are attributed to the account that made them and recorded as W3C PROV-O,
    with the exact triples each job added. Your agent can only see or change what you
    are allowed to: your role decides what you may do, and a space's members and access
    rules decide who may read or write it.</p>
  </div>
  <div>
    <details open><summary>What is BrainKB MCP?</summary><p>A Model Context Protocol
    server for the BrainKB knowledge base. MCP clients such as Claude Code, Cursor, VS Code,
    Perplexity, claude.ai and ChatGPT connect to
    <code>https://{host}/mcp</code>, and your agent can then work with workspaces,
    ingest, search, ready-made questions and provenance on your behalf.</p></details>
    <details open><summary>Do I need to log in?</summary><p>No, not to read open
    knowledge. Without logging in, your agent can list, search and read every
    <b>public</b> space; it is read-only, and private spaces stay hidden. You only need
    to log in to read your private spaces or to change anything (create a space, ingest,
    share), and for provenance, SPARQL and admin tools. See
    <a href="#public">Open knowledge</a>.</p></details>
    <details><summary>Can I use BrainKB through skills?</summary><p>Yes. The
    <a href="https://github.com/sensein/agent_skills/tree/main/skills/brainkb" target="_blank" rel="noopener">BrainKB skill</a> works alongside
    this server: install it in your agent, and it guides the agent through sign-in,
    ingest, questions and provenance using the server's tools.</p></details>
    <details><summary>How does my agent sign in?</summary><p>With a personal access token
    sent as an <code>Authorization: Bearer</code> header, or through the Globus, ORCID or
    GitHub login tools, which hand you a browser link. Every call that needs an account
    runs as you. Reading public spaces needs no login.</p></details>
    <details><summary>How do I add BrainKB to Perplexity, claude.ai or ChatGPT?</summary><p>Add
    a custom connector with the URL <code>https://{host}/mcp</code> and choose OAuth, leaving
    the client ID and secret empty. The app registers itself and opens a BrainKB sign-in
    page; sign in with Globus, ORCID or GitHub, or paste a personal access token. If an app
    says the server "does not support automatic registration", it is not using OAuth
    discovery: pick OAuth explicitly, or use a client that does. Custom connectors may
    need a paid or developer plan in the app.</p></details>
    <details><summary>Who can see my data?</summary><p>Private spaces are visible to their
    members only. Public spaces are readable by anyone, including unauthenticated clients.
    Ready-made questions currently need an Admin role.</p></details>
    <details><summary>How do I ingest a large file?</summary><p>Stream it to
    <code>/upload</code>. The server answers <code>202</code> with an
    <code>upload_id</code> and ingests in the background, and your agent can follow the
    job from there. Up to 5&nbsp;GB per file.</p>
    <div class="codebox faq-code">
      <div class="codebox-head"><span>Large RDF file · python</span><button class="copy" type="button">Copy</button></div>
      <pre>import requests
requests.post("https://{host}/upload",
              params={{"filename": "review.ttl", "graph": "&lt;graph_iri&gt;"}},
              headers={{"Authorization": f"Bearer {{TOKEN}}"}},
              data=open("review.ttl", "rb"))   # streamed off disk</pre>
    </div>
    </details>
    <details><summary>Why does /mcp say "Not Acceptable" in a browser?</summary><p>That is
    the endpoint working correctly: a browser GET sends no
    <code>Accept: text/event-stream</code>, so the server refuses it. Use an MCP client.</p></details>
  </div>
</section>

<section class="cta">
  <div><h2>Start working with BrainKB from your agent.</h2>
  <p>Register the server, sign in, and ask your first question.</p></div>
  <a class="btn primary" href="#connect">Connect your agent</a>
</section>
</main>

<footer>
  <span>BrainKB · Model Context Protocol server</span>
  <div class="footerlinks"><a href="#capabilities">Capabilities</a><a href="#questions">Questions</a><a href="#top">Back to top</a></div>
</footer>
</div>
<script type="application/json" id="session-data">{session_json}</script>
<script>{_LANDING_JS}</script>
</body>
</html>
"""
    return HTMLResponse(body, headers={"Cache-Control": "public, max-age=300"})


@mcp.custom_route("/logo.png", methods=["GET"])
async def _logo(request: Any) -> Any:
    from starlette.responses import FileResponse, PlainTextResponse

    if not os.path.isfile(_LOGO_PATH):
        return PlainTextResponse("not found\n", status_code=404)
    return FileResponse(_LOGO_PATH, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


def _http_client_id(request: Any) -> str:
    """_client_id() for a plain HTTP route, which has a request but no MCP context."""
    peer = getattr(getattr(request, "client", None), "host", None)
    if _peer_trusted(peer):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return "ip:" + xff.split(",")[-1].strip()
        xri = request.headers.get("x-real-ip", "")
        if xri:
            return "ip:" + xri.strip()
    return "ip:" + (peer or "unknown")


def _public_origin(request: Any) -> str:
    """Origin the OAuth metadata and redirects name: https://<public host>.

    Same host rules as the landing page, plus plain-http loopback so the flow can
    be exercised locally. The issuer and callback URL must never be caller-chosen.
    """
    host = (request.headers.get("host") or "").split(",")[0].strip().lower()
    if not os.getenv("MCP_PUBLIC_HOST", "").strip() and host.split(":")[0] in ("localhost", "127.0.0.1") \
            and _HOST_RE.match(host):
        return f"http://{host}"
    return f"https://{_self_host(request)}"


def _oauth_start_login(provider: str, return_to: str) -> str:
    r = httpx.post(f"{_UM_URL}/api/auth/cli/start",
                   json={"provider": provider, "return_to": return_to}, timeout=30)
    r.raise_for_status()
    return r.json()["authorize_url"]


def _oauth_redeem_code(code: str) -> Optional[str]:
    try:
        r = httpx.post(f"{_UM_URL}/api/auth/cli/exchange", json={"code": code}, timeout=30)
        return r.json().get("refresh_token") if r.status_code == 200 else None
    except Exception:
        return None


# MCP OAuth for connector apps (Perplexity, claude.ai, ChatGPT, ...): discovery,
# dynamic client registration, and a BrainKB sign-in page. See oauth_server.py.
# Sign-in always uses the DEFAULT backend (BRAINKB_URL's usermanagement): an app
# connecting by URL cannot pick another, and its token is resolved against it.
_OAUTH_ENABLED = _IS_REMOTE and os.getenv("MCP_OAUTH_ENABLED", "true").strip().lower() not in ("0", "false", "no")
if _OAUTH_ENABLED:
    import oauth_server

    mcp._custom_starlette_routes.extend(oauth_server.build_routes(oauth_server.Hooks(
        origin=_public_origin,
        start_login=_oauth_start_login,
        redeem_code=_oauth_redeem_code,
        check_pat=lambda pat: _pat_exchange(_UM_URL, pat, _AUD_QUERY) is not None,
        rate_ok=lambda request: _rate_ok("auth", _RL_AUTH, _http_client_id(request)),
    )))


@mcp.custom_route("/healthz", methods=["GET"])
async def _healthz(request: Any) -> Any:
    """Liveness only — deliberately does NOT probe the backend.

    A health check that called the query_service would let anyone use this
    endpoint to hammer it, and would flap this service on a backend blip.
    """
    from starlette.responses import PlainTextResponse

    return PlainTextResponse("ok\n", headers={"Cache-Control": "no-store"})


def _startup_warnings() -> None:
    """Surface deployment settings that weaken the multi-user guarantees."""
    if not _IS_REMOTE:
        return

    def warn(msg: str) -> None:
        print(f"[brainkb-mcp] WARNING: {msg}", file=sys.stderr, flush=True)

    if os.getenv("BRAINKB_TOKEN", "").strip():
        if _ALLOW_SHARED_IDENTITY:
            warn("MCP_ALLOW_SHARED_IDENTITY is on — callers who send no credential "
                 "of their own will act as BRAINKB_TOKEN's owner. Single-user "
                 "deployments only.")
        else:
            warn("BRAINKB_TOKEN is set but IGNORED on this transport (it would be a "
                 "shared fallback identity). Callers must authenticate per request.")
    if _TRUSTED_BAD:
        warn(f"MCP_TRUSTED_PROXIES has unparseable entries {_TRUSTED_BAD} — they are "
             "NOT trusted. Use plain IPs or CIDR blocks (e.g. 10.0.1.0/24).")
    if not _TRUSTED_PROXIES and not _TRUSTED_NETS:
        warn("MCP_TRUSTED_PROXIES is empty — X-Forwarded-For is ignored and rate "
             "limits key on the socket peer. Behind a proxy that means all callers "
             "share one bucket; set it to your proxy's IP(s) or subnet CIDR(s).")
    if _UPLOAD_DIR_NOTE:
        warn(_UPLOAD_DIR_NOTE)
    if _INGEST_ROOT:
        # Worth saying once at startup, because the consequence is not obvious: a
        # file under this root can be ingested into a graph and then read straight
        # back out with brainkb_read_space. So the root has to be a dedicated upload
        # directory, not a data volume that happens to contain RDF.
        warn(f"brainkb_ingest_files may read anything under {_INGEST_ROOT!r} on the "
             "SERVER's filesystem, and callers can read it back out of the graph "
             "afterwards. Keep it a dedicated upload dir. Not needed for a caller's "
             "own file — that is POST /upload.")
    if _DEFAULT_URL.startswith("http://") and not _host_local(_DEFAULT_URL):
        warn(f"BRAINKB_URL is plaintext HTTP ({_DEFAULT_URL}) — bearer tokens will "
             "cross the network unencrypted.")


if __name__ == "__main__":
    # Default to stdio (local use). Set MCP_TRANSPORT=streamable-http to run as the
    # hosted remote (behind TLS at https://mcp.brainkb.org/mcp) — used later.
    _startup_warnings()
    if _IS_REMOTE:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
