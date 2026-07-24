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
  BRAINKB_EMAIL     Auto-login email (else call brainkb_login)
  BRAINKB_PASSWORD  Auto-login password

Credentials/token live only in this process's memory; the token is never logged
or returned to the model.
"""

from __future__ import annotations

import os
import threading
import time
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
_TIMEOUT = httpx.Timeout(120.0, connect=15.0)
# File ingest streams potentially very large uploads (TTL/JSON-LD up to ~5GB per
# user). A fixed read/write timeout would abort a legitimate large upload, so we
# disable read/write timeouts here and keep only a connect bound. Files stream
# from disk (httpx multipart), so this does not buffer the whole file in memory.
_UPLOAD_TIMEOUT = httpx.Timeout(None, connect=30.0)

# Per-session login store (fallback for local/stdio use only), keyed by the MCP
# session's identity. The hosted multi-user remote does NOT rely on this — each
# call carries the caller's own token via the Authorization header (stateless).
_SESSIONS: Dict[int, Dict[str, str]] = {}


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


def _session_key() -> Optional[int]:
    try:
        return id(mcp.get_context().session)
    except Exception:
        return None


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

_RL_LOCK = threading.Lock()
_RL_BUCKETS: Dict[Tuple[str, str], list] = {}  # (client_id, bucket) -> [window, count]


def _client_id() -> str:
    """Identify the caller for rate-limiting. Prefer source IP so many users
    behind one reverse proxy are still throttled per origin; fall back to the MCP
    session; 'local' for stdio (exempt)."""
    hdrs = _request_headers()
    xff = hdrs.get("x-forwarded-for", "")
    if xff:
        return "ip:" + xff.split(",")[0].strip()
    xri = hdrs.get("x-real-ip", "")
    if xri:
        return "ip:" + xri.strip()
    try:
        req = getattr(mcp.get_context().request_context, "request", None)
        client = getattr(req, "client", None)
        if client and getattr(client, "host", None):
            return "ip:" + client.host
    except Exception:
        pass
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
            _RL_BUCKETS[key] = [win, 1]
            # Bound memory: drop stale windows if the map grows large.
            if len(_RL_BUCKETS) > 20000:
                for k, v in list(_RL_BUCKETS.items()):
                    if v[0] != win:
                        _RL_BUCKETS.pop(k, None)
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
# Phase 2 SSO: single login -> per-service token exchange
# --------------------------------------------------------------------------- #
# usermanagement is the single issuer. A login mints a REFRESH token; we exchange
# it for a short-lived per-service ACCESS token (aud=<service>) whenever a tool
# calls that service. A token minted for one service can't be replayed against
# another (containment). Legacy per-service /api/token still works as a fallback,
# so this keeps working against a backend that doesn't yet expose SSO.

_AUD_QUERY = "query_service"
_AUD_UM = "usermanagement"


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


def _um_base(base: str) -> str:
    """usermanagement base URL for SSO login/exchange, given the query_service base."""
    base = base.rstrip("/")
    if base == _DEFAULT_URL:
        return _UM_URL
    return base.replace(":8010", ":8004")


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


def _legacy_login(base: str, email: str, password: str) -> Optional[str]:
    """Legacy per-service login (POST /api/token) -> access token, or None."""
    try:
        r = httpx.post(f"{base}/api/token", json={"email": email, "password": password}, timeout=30)
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception:
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
      1. Caller's `Authorization: Bearer <token>` header. A REFRESH token is
         exchanged for the requested audience (unlocks every service from one
         header); any other token is used as-is (works at its own service).
         `X-BrainKB-Base-URL` optionally overrides the backend URL.
      2. Per-session credentials/refresh set by `brainkb_login`.
      3. Env `BRAINKB_EMAIL`/`BRAINKB_PASSWORD` auto-login (single-user/dev).
    """
    hdrs = _request_headers()
    base = hdrs.get("x-brainkb-base-url", _DEFAULT_URL).rstrip("/")
    um = _um_base(base)
    key = _session_key()

    # 1) header pass-through
    authz = hdrs.get("authorization", "")
    if authz[:7].lower() == "bearer " and authz[7:].strip():
        raw = authz[7:].strip()
        cl = _claims(raw)
        if cl.get("typ") == "refresh" or cl.get("aud") == "brainkb-auth":
            ex = _exchange(um, raw, audience)
            if ex:
                return {"url": base, "token": ex[0], "email": cl.get("sub") or ""}
            raise _NotAuthed(f"Could not exchange the provided refresh token for '{audience}'.")
        return {"url": base, "token": raw, "email": cl.get("sub") or _decode_sub(raw) or ""}

    # 2) per-session (cached access -> refresh -> legacy)
    if key is not None and key in _SESSIONS:
        sd = _SESSIONS[key]
        cached = _cached_access(sd, audience)
        if cached:
            return {"url": sd.get("url", base), "token": cached, "email": sd.get("email", "")}
        if sd.get("refresh"):
            ex = _exchange(um, sd["refresh"], audience)
            if ex:
                _store_access(sd, audience, ex[0], ex[1])
                return {"url": sd.get("url", base), "token": ex[0], "email": sd.get("email", "")}
        if sd.get("legacy_token") and audience == _AUD_QUERY:
            return {"url": sd.get("url", base), "token": sd["legacy_token"], "email": sd.get("email", "")}

    # 3) env auto-login (or session-stored creds, e.g. after refresh expiry)
    em, pw = os.getenv("BRAINKB_EMAIL"), os.getenv("BRAINKB_PASSWORD")
    if not (em and pw) and key is not None and key in _SESSIONS:
        sd = _SESSIONS[key]
        em, pw = (sd.get("email") or em), (sd.get("password") or pw)
    if em and pw:
        refresh = _sso_login(um, em, pw)
        if refresh:
            ex = _exchange(um, refresh, audience)
            if ex:
                if key is not None:
                    sd = _SESSIONS.setdefault(key, {})
                    sd.update({"refresh": refresh, "email": em, "password": pw, "url": base})
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
        "Not authenticated. On the hosted remote, send 'Authorization: Bearer "
        "<token>' — a refresh token unlocks all services; a service access token "
        "works for that service. Locally, call brainkb_login(email, password) or "
        "set BRAINKB_EMAIL / BRAINKB_PASSWORD."
    )


def _resolve() -> Dict[str, str]:
    """Auth context for query_service calls."""
    return _token_for(_AUD_QUERY)


def _me() -> str:
    return _resolve()["email"]


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
    if _retry and not _rate_ok("admin", _RL_ADMIN):
        return _rl_error("admin", _RL_ADMIN)
    base = _request_headers().get("x-brainkb-base-url", _DEFAULT_URL).rstrip("/")
    um = _um_base(base)
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

@mcp.tool()
def brainkb_register(full_name: str, email: str, password: str, base_url: str = "") -> str:
    """Self-register a new BrainKB account (no login required).

    Creates the credential plus a canonical user profile with a default role, so
    the new user is a first-class identity (not a role-less orphan). The account
    starts INACTIVE — an Admin/SuperAdmin must activate it (brainkb_activate_user)
    before it can log in. The password is never echoed."""
    if not _rate_ok("auth", _RL_AUTH):
        return _rl_error("auth", _RL_AUTH)["detail"]
    base = (base_url or _DEFAULT_URL).rstrip("/")
    try:
        r = httpx.post(
            f"{base}/api/register",
            json={"full_name": full_name, "email": email, "password": password},
            timeout=30,
        )
        r.raise_for_status()
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            pass
        return detail or (
            f"Registered {email}. An admin must activate the account before login.")
    except httpx.HTTPStatusError as e:
        msg = ""
        try:
            msg = e.response.json().get("detail", "")
        except Exception:
            pass
        return f"Registration failed (HTTP {e.response.status_code})" + (f": {msg}" if msg else ".")
    except Exception as e:
        return f"Registration error: {e}"


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
    base = (base_url or _DEFAULT_URL).rstrip("/")
    um = _um_base(base)
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
                              "password": password, "access": {}}
            return f"Logged in as {email} at {base} (SSO; this session)."
        # Legacy fallback: per-service query_service token.
        legacy = _legacy_login(base, email, password)
        if not legacy:
            return "Login failed. Check email/password and base_url."
        if key is None:
            return ("Logged in (legacy), but this session could not be identified to cache "
                    "the token; send an Authorization header instead.")
        _SESSIONS[key] = {"legacy_token": legacy, "url": base, "email": email,
                          "password": password, "access": {}}
        return f"Logged in as {email} at {base} (this session)."
    except Exception as e:
        return f"Login error: {e}"


@mcp.tool()
def brainkb_logout() -> str:
    """Forget the cached token for this session."""
    key = _session_key()
    if key is not None:
        _SESSIONS.pop(key, None)
    return "Logged out (this session)."


@mcp.tool()
def brainkb_whoami() -> Dict[str, Any]:
    """Report the current caller's auth state (base URL, email, authenticated)."""
    try:
        ctx = _resolve()
        return {"base_url": ctx["url"], "email": ctx["email"], "authenticated": True}
    except _NotAuthed:
        return {"base_url": _DEFAULT_URL, "email": None, "authenticated": False}


# --------------------------------------------------------------------------- #
# spaces (private/public workspaces)
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_list_spaces() -> Any:
    """List spaces the user can see (their own/member spaces + public ones)."""
    return _get("/api/spaces")


@mcp.tool()
def brainkb_create_space(slug: str, name: str, visibility: str = "private",
                         description: str = "", space_type: str = "individual") -> Any:
    """Create a workspace/space. The caller becomes owner.
    slug: lowercase/hyphen id; visibility: 'private' or 'public';
    description: short human description (recommended — surfaces in the registry);
    space_type: 'individual' (a personal space — any write-capable role) or 'team'
    (a shared space — only Admin/SuperAdmin, or a user granted create_team_space)."""
    return _post("/api/spaces", json={"slug": slug, "name": name, "visibility": visibility,
                                       "description": description, "space_type": space_type})


@mcp.tool()
def brainkb_set_space_visibility(slug: str, visibility: str) -> Any:
    """Set a space 'public' (anyone, even anonymous, can read) or 'private'
    (members only). Owner only."""
    return _patch(f"/api/spaces/{quote(slug)}/visibility", json={"visibility": visibility})


@mcp.tool()
def brainkb_add_space_member(slug: str, member_email: str, role: str = "viewer") -> Any:
    """Add/update a space member. role: 'owner' | 'editor' | 'viewer'. Owner only."""
    return _post(f"/api/spaces/{quote(slug)}/members",
                 json={"member": member_email, "role": role})


@mcp.tool()
def brainkb_add_space_graph(slug: str, named_graph_iri: str, description: str = "") -> Any:
    """Register a named graph and bind it to a space, so ingest/read on that graph
    are governed by the space's membership and visibility. Owner/editor only."""
    return _post(f"/api/spaces/{quote(slug)}/graphs",
                 json={"named_graph_url": named_graph_iri, "description": description})


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #

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
            fh = open(p, "rb")
            handles.append(fh)
            files.append(("files", (os.path.basename(p), fh, "application/octet-stream")))
        # No byte cap here (only a file-count cap): TTL/JSON-LD uploads may be
        # large (up to ~5GB per user). Use the long upload timeout so big
        # streams aren't aborted mid-transfer.
        return _post("/api/insert/files/knowledge-graph-triples",
                     params={"user_id": _me(), "named_graph_iri": named_graph_iri,
                             "max_concurrency": max_concurrency},
                     files=files, timeout=_UPLOAD_TIMEOUT)
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
    return _get(f"/api/spaces/{quote(slug)}/data")


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
def brainkb_list_access_rules(slug: str) -> Any:
    """List a space's fine-grained access rules (member/manager of the space)."""
    return _get(f"/api/spaces/{quote(slug)}/access-rules")


@mcp.tool()
def brainkb_add_access_rule(slug: str, action: str, subject_type: str, subject_value: str) -> Any:
    """(Space manager) Restrict a space action to a subject.
    action: 'read' | 'write' | 'manage'.
    subject_type: 'global_role' (e.g. 'Admin','Lab Member') | 'member' (an email) |
    'space_role' ('viewer'|'editor'|'owner', matched as >=).
    When rules exist for an action, only matching callers may perform it; the space
    owner and Admin/SuperAdmin always bypass (no lockout). Example: restrict writing
    to Admins -> action='write', subject_type='global_role', subject_value='Admin'."""
    return _post(f"/api/spaces/{quote(slug)}/access-rules",
                 json={"action": action, "subject_type": subject_type, "subject_value": subject_value})


@mcp.tool()
def brainkb_remove_access_rule(slug: str, rule_id: int) -> Any:
    """(Space manager) Delete a fine-grained access rule by its id
    (see brainkb_list_access_rules)."""
    return _delete(f"/api/spaces/{quote(slug)}/access-rules/{rule_id}")


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
    """(Admin) Assign a role/group to a user by email (e.g. 'Admin', 'Lab Member',
    'External'). The user must already have a profile (created on first login)."""
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
    return _um("DELETE", f"/api/admin/users/{pid}/roles/{quote(role)}")


@mcp.tool()
def brainkb_activate_user(email: str) -> Any:
    """(Admin) Activate a user's account (sets the JWT user active) by email."""
    return _um("POST", "/api/admin/users/activate", json={"email": email})


@mcp.tool()
def brainkb_deactivate_user(email: str) -> Any:
    """(Admin) Deactivate a user's account by email."""
    return _um("POST", "/api/admin/users/deactivate", json={"email": email})


if __name__ == "__main__":
    # Default to stdio (local use). Set MCP_TRANSPORT=streamable-http to run as the
    # hosted remote (behind TLS at https://mcp.brainkb.org/mcp) — used later.
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower().replace("_", "-")
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
