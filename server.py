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

_STATE: Dict[str, Optional[str]] = {
    "url": os.getenv("BRAINKB_URL", "http://localhost:8010").rstrip("/"),
    "email": None,
    "token": None,
}
_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #

def _base() -> str:
    return _STATE["url"] or "http://localhost:8010"


def _do_login(email: str, password: str, base_url: Optional[str] = None) -> None:
    if base_url:
        _STATE["url"] = base_url.rstrip("/")
    r = httpx.post(f"{_base()}/api/token",
                   json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    _STATE["token"] = r.json()["access_token"]
    _STATE["email"] = email


def _ensure_auth() -> None:
    """Log in if we have no token but env credentials are set."""
    if _STATE["token"]:
        return
    email, pw = os.getenv("BRAINKB_EMAIL"), os.getenv("BRAINKB_PASSWORD")
    if email and pw:
        _do_login(email, pw)
    if not _STATE["token"]:
        raise RuntimeError("Not logged in. Call brainkb_login(email, password) first "
                           "(or set BRAINKB_EMAIL / BRAINKB_PASSWORD).")


def _headers() -> Dict[str, str]:
    _ensure_auth()
    return {"Authorization": f"Bearer {_STATE['token']}"}


def _me() -> str:
    _ensure_auth()
    return _STATE["email"] or ""


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


def _get(path: str, params: Optional[Dict[str, Any]] = None, auth: bool = True) -> Any:
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.get(f"{_base()}{path}", params=params or {},
                         headers=_headers() if auth else {})
        return _result(resp)
    except Exception as e:
        return {"error": True, "detail": str(e)}


def _post(path: str, params: Optional[Dict[str, Any]] = None,
          json: Any = None, content: Optional[bytes] = None,
          ctype: Optional[str] = None, files: Any = None) -> Any:
    try:
        headers = _headers()
        if ctype:
            headers["Content-Type"] = ctype
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.post(f"{_base()}{path}", params=params or {}, headers=headers,
                          json=json, content=content, files=files)
        return _result(resp)
    except Exception as e:
        return {"error": True, "detail": str(e)}


def _patch(path: str, json: Any = None) -> Any:
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            resp = c.patch(f"{_base()}{path}", headers=_headers(), json=json)
        return _result(resp)
    except Exception as e:
        return {"error": True, "detail": str(e)}


# --------------------------------------------------------------------------- #
# auth / session
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_login(email: str, password: str, base_url: str = "") -> str:
    """Authenticate to BrainKB with the user's credentials and cache the JWT in
    memory for subsequent calls. The password/token are never echoed back."""
    try:
        _do_login(email, password, base_url or None)
        return f"Logged in as {email} at {_base()}."
    except httpx.HTTPStatusError as e:
        return f"Login failed (HTTP {e.response.status_code}). Check email/password and base_url."
    except Exception as e:
        return f"Login error: {e}"


@mcp.tool()
def brainkb_whoami() -> Dict[str, Any]:
    """Report the current session: base URL, logged-in email, and auth state."""
    return {"base_url": _base(), "email": _STATE["email"], "authenticated": bool(_STATE["token"])}


@mcp.tool()
def brainkb_set_base_url(base_url: str) -> str:
    """Point the client at a different BrainKB deployment (clears the session)."""
    _STATE["url"] = base_url.rstrip("/")
    _STATE["token"] = None
    _STATE["email"] = None
    return f"Base URL set to {_base()}. Please log in again."


# --------------------------------------------------------------------------- #
# spaces (private/public workspaces)
# --------------------------------------------------------------------------- #

@mcp.tool()
def brainkb_list_spaces() -> Any:
    """List spaces the user can see (their own/member spaces + public ones)."""
    return _get("/api/spaces")


@mcp.tool()
def brainkb_create_space(slug: str, name: str, visibility: str = "private",
                         description: str = "") -> Any:
    """Create a workspace/space (private by default). The caller becomes owner.
    slug: lowercase/hyphen id; visibility: 'private' or 'public'."""
    return _post("/api/spaces", json={"slug": slug, "name": name,
                                       "visibility": visibility, "description": description})


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


if __name__ == "__main__":
    # Default to stdio (local use). Set MCP_TRANSPORT=streamable-http to run as the
    # hosted remote (behind TLS at https://mcp.brainkb.org/mcp) — used later.
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower().replace("_", "-")
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
