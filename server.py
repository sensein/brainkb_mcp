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
                           'https://queryservice.brainkb.org=https://usermanagement.brainkb.org'.
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

import html
import ipaddress
import os
import re
import sys
import threading
import time
import weakref
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
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


def _rate_ok(bucket: str, limit: int) -> bool:
    """True if this caller may proceed in `bucket`; False if the limit is hit."""
    if not _RL_ENABLED or limit <= 0:
        return True
    cid = _client_id()
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
#     MCP_ALLOWED_BASE_URLS=https://queryservice.brainkb.org=https://usermanagement.brainkb.org,http://localhost:8011

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
    real deployment (e.g. https://queryservice.brainkb.org has no :8010, and the old code
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
        raw = authz[7:].strip()
        # A Personal Access Token is opaque (not a JWT); exchange it for the
        # requested service audience — one PAT unlocks every service.
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

    raise _NotAuthed(
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


def _path_ok(path: str) -> bool:
    """False if a request path contains an empty or dot segment — i.e. something
    that would traverse to a different endpoint than the tool intended."""
    return not any(p in ("", ".", "..") for p in path.split("/")[1:])


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


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    if not _path_ok(path):
        return dict(_BAD_PATH)
    if not _rate_ok("read", _RL_READ):
        return _rl_error("read", _RL_READ)
    try:
        ctx = _resolve()
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.get(f"{ctx['url']}{path}", params=params or {},
                         headers={"Authorization": f"Bearer {ctx['token']}"})
        return _result(resp)
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
    """Report the current caller's auth state (base URL, email, authenticated, and
    when the cached session expires)."""
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
    except _NotAuthed as e:
        return {"base_url": _DEFAULT_URL, "email": None, "authenticated": False,
                "hint": "Session may have expired — log in again (brainkb_login / brainkb_globus_login)."}


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
    public."""
    return _get("/api/spaces")


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
        raise PermissionError(
            "File ingest is disabled on the hosted remote because file paths are "
            "read from the SERVER's filesystem, not yours. Use brainkb_ingest_text "
            "instead, or ask the operator to set MCP_INGEST_ROOT to a directory "
            "that may be ingested from."
        )
    if not os.path.isfile(rp):
        raise PermissionError(f"{p!r} is not a regular file.")
    return rp

@mcp.tool()
def brainkb_ingest_text(named_graph_iri: str, data: str) -> Any:
    """Ingest raw RDF text (Turtle / N-Triples / JSON-LD, auto-detected) into a
    named graph. Returns a job_id; ingestion runs in the background — poll with
    brainkb_job_status. The graph must be registered (see brainkb_add_space_graph)
    and the caller must have write access to its space."""
    payload = data.encode("utf-8")
    if _MAX_INGEST_BYTES > 0 and len(payload) > _MAX_INGEST_BYTES:
        return {"error": True, "status_code": 413,
                "detail": (f"Payload too large ({len(payload)} bytes > "
                           f"{_MAX_INGEST_BYTES}). Split it or use file ingest.")}
    return _post("/api/insert/raw/knowledge-graph-triples",
                 params={"user_id": _me(), "named_graph_iri": named_graph_iri},
                 content=payload, ctype="text/plain")


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
    Anonymous/other users never see private-space data."""
    params: Dict[str, Any] = {"q": q, "limit": limit, "offset": offset}
    if space:
        params["space"] = space
    return _get("/api/search", params=params)


@mcp.tool()
def brainkb_read_space(slug: str) -> Any:
    """Read all RDF (JSON-LD) in a space's graphs. Public spaces are readable by
    anyone; private spaces require membership."""
    return _get(f"/api/spaces/{_seg(slug)}/data")


@mcp.tool()
def brainkb_list_registered_graphs() -> Any:
    """List registered named graphs visible to the caller (private-space graphs the
    caller can't access are hidden)."""
    return _get("/api/query/registered-named-graphs")


@mcp.tool()
def brainkb_sparql(sparql_query: str) -> Any:
    """Run an arbitrary SPARQL query (requires the 'admin' scope)."""
    return _get("/api/query/sparql/", params={"sparql_query": sparql_query})


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


def _self_host(request: Any) -> str:
    """Hostname to show in the sample command, from the Host header.

    Validated against a strict charset and HTML-escaped at the call site: Host is
    caller-controlled, so an unvalidated value would be reflected markup.
    """
    host = (request.headers.get("host") or "").split(",")[0].strip()
    return host if _HOST_RE.match(host) else "mcp.brainkb.org"


@mcp.custom_route("/", methods=["GET"])
async def _landing(request: Any) -> Any:
    from starlette.responses import HTMLResponse

    host = html.escape(_self_host(request))
    body = f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BrainKB MCP</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.6 ui-sans-serif, system-ui, sans-serif;
         max-width: 46rem; margin: 4rem auto; padding: 0 1.25rem; }}
  code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; }}
  pre {{ padding: .85rem 1rem; border-radius: .5rem; overflow-x: auto;
        background: color-mix(in srgb, currentColor 8%, transparent); }}
  h1 {{ font-size: 1.5rem; margin-bottom: .25rem; }}
  .sub {{ opacity: .7; margin-top: 0; }}
</style>
<h1>BrainKB MCP</h1>
<p class="sub">Model Context Protocol server for the BrainKB knowledge base.</p>
<p>This is an API host, not a web app. The protocol endpoint is
<code>/mcp</code> and speaks MCP over streamable HTTP — it is meant for an MCP
client, not a browser.</p>
<p>Register it with Claude Code:</p>
<pre>claude mcp add --scope user --transport http brainkb https://{host}/mcp</pre>
<p>Each caller authenticates <strong>per request</strong> with their own BrainKB
credential — an <code>Authorization: Bearer</code> header, or a Personal Access
Token via the login tools. There is no shared or ambient identity.</p>
<p>Opening <code>/mcp</code> in a browser returns
<code>Not Acceptable: Client must accept text/event-stream</code>. That is the
endpoint working correctly: a browser GET sends no
<code>Accept: text/event-stream</code>, so the server refuses it.</p>
"""
    return HTMLResponse(body, headers={"Cache-Control": "public, max-age=300"})


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
    if _INGEST_ROOT:
        warn(f"brainkb_ingest_files may read from {_INGEST_ROOT!r} on the SERVER's "
             "filesystem. Ensure it holds nothing callers shouldn't retrieve.")
    if _DEFAULT_URL.startswith("http://") and not _is_loopback(_DEFAULT_URL):
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
