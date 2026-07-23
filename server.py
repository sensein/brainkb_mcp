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
from typing import Any, Dict, List, Optional
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


class _NotAuthed(RuntimeError):
    pass


def _resolve() -> Dict[str, str]:
    """
    Resolve the auth context for THIS call — multi-user safe.

    Order of precedence:
      1. Caller's `Authorization: Bearer <BrainKB JWT>` header (stateless
         pass-through; how the hosted multi-user remote authenticates each user).
         `X-BrainKB-Base-URL` header optionally overrides the backend URL.
      2. Per-session token set by `brainkb_login` (local/stdio convenience).
      3. Env `BRAINKB_EMAIL`/`BRAINKB_PASSWORD` auto-login (single-user/dev).

    Returns {url, token, email}. Never stores one caller's token where another
    caller could read it.
    """
    hdrs = _request_headers()
    base = hdrs.get("x-brainkb-base-url", _DEFAULT_URL).rstrip("/")

    # 1) header pass-through
    authz = hdrs.get("authorization", "")
    if authz[:7].lower() == "bearer " and authz[7:].strip():
        token = authz[7:].strip()
        return {"url": base, "token": token, "email": _decode_sub(token) or ""}

    # 2) per-session login
    key = _session_key()
    if key is not None and key in _SESSIONS:
        sd = _SESSIONS[key]
        return {"url": sd.get("url", base), "token": sd["token"],
                "email": sd.get("email") or _decode_sub(sd["token"]) or ""}

    # 3) env auto-login
    em, pw = os.getenv("BRAINKB_EMAIL"), os.getenv("BRAINKB_PASSWORD")
    if em and pw:
        try:
            r = httpx.post(f"{base}/api/token", json={"email": em, "password": pw}, timeout=30)
            r.raise_for_status()
            token = r.json()["access_token"]
            if key is not None:
                _SESSIONS[key] = {"token": token, "url": base, "email": em}
            return {"url": base, "token": token, "email": em}
        except Exception:
            pass

    raise _NotAuthed(
        "Not authenticated. On the hosted remote, send an 'Authorization: Bearer "
        "<BrainKB token>' header. Locally, call brainkb_login(email, password) or "
        "set BRAINKB_EMAIL / BRAINKB_PASSWORD."
    )


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
          ctype: Optional[str] = None, files: Any = None) -> Any:
    try:
        ctx = _resolve()
        headers = {"Authorization": f"Bearer {ctx['token']}"}
        if ctype:
            headers["Content-Type"] = ctype
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.post(f"{ctx['url']}{path}", params=params or {}, headers=headers,
                          json=json, content=content, files=files)
        return _result(resp)
    except _NotAuthed as e:
        return {"error": True, "detail": str(e)}
    except Exception as e:
        return {"error": True, "detail": str(e)}


def _patch(path: str, json: Any = None) -> Any:
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
# auth / session
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_login(email: str, password: str, base_url: str = "") -> str:
    """Authenticate to BrainKB with the user's credentials and cache the JWT for
    THIS session only (isolated per caller). The password/token are never echoed.

    On the hosted multi-user remote you can skip this and instead have your client
    send an 'Authorization: Bearer <BrainKB token>' header — that is the preferred,
    stateless way to authenticate per user."""
    key = _session_key()
    base = (base_url or _DEFAULT_URL).rstrip("/")
    try:
        r = httpx.post(f"{base}/api/token", json={"email": email, "password": password}, timeout=30)
        r.raise_for_status()
        token = r.json()["access_token"]
        if key is None:
            return ("Logged in, but this session could not be identified to cache the "
                    "token; send an Authorization header instead.")
        _SESSIONS[key] = {"token": token, "url": base, "email": email}
        return f"Logged in as {email} at {base} (this session)."
    except httpx.HTTPStatusError as e:
        return f"Login failed (HTTP {e.response.status_code}). Check email/password and base_url."
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
    return _post("/api/insert/raw/knowledge-graph-triples",
                 params={"user_id": _me(), "named_graph_iri": named_graph_iri},
                 content=data.encode("utf-8"), ctype="text/plain")


@mcp.tool()
def brainkb_ingest_files(named_graph_iri: str, file_paths: List[str],
                         max_concurrency: int = 8) -> Any:
    """Ingest local RDF files (ttl/nt/nq/rdf/owl/jsonld/json) into a named graph.
    Returns a job_id; runs in the background — poll with brainkb_job_status."""
    handles = []
    try:
        files = []
        for p in file_paths:
            fh = open(p, "rb")
            handles.append(fh)
            files.append(("files", (os.path.basename(p), fh, "application/octet-stream")))
        return _post("/api/insert/files/knowledge-graph-triples",
                     params={"user_id": _me(), "named_graph_iri": named_graph_iri,
                             "max_concurrency": max_concurrency},
                     files=files)
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


if __name__ == "__main__":
    # Default to stdio (local use). Set MCP_TRANSPORT=streamable-http to run as the
    # hosted remote (behind TLS at https://mcp.brainkb.org/mcp) — used later.
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower().replace("_", "-")
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
