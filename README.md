# BrainKB MCP server

An [MCP](https://modelcontextprotocol.io) server that exposes the BrainKB
`query_service` API as tools, so an assistant can — using the **user's own
credentials** — ingest knowledge-graph data, read/search the graphs, query
W3C PROV-O provenance, and check the status of ingest jobs.

Credentials and the JWT live only in the server process's memory; the token is
never logged or returned to the model.

## Registry identity

Registered on the [MCP registry](https://registry.modelcontextprotocol.io/?q=brainkb):

| | |
|---|---|
| **name** | `org.brainkb/brainkb` |
| **version** | `0.1.0` |
| **repository** | https://github.com/sensein/BrainKB |
| **remote (later)** | `streamable-http` @ `https://mcp.brainkb.org/mcp` |

See [`server.json`](server.json) for the registry manifest. **For now, run and test
it locally over stdio**; the hosted streamable-http remote URL will be wired up
later (set `MCP_TRANSPORT=streamable-http` to serve it).

## Tools

| Tool | What it does |
|------|--------------|
| `brainkb_login(email, password, base_url?)` | Authenticate; cache JWT in memory |
| `brainkb_whoami()` / `brainkb_set_base_url(url)` | Session info / switch deployment |
| `brainkb_list_spaces()` | List spaces you can see (yours + public) |
| `brainkb_create_space(slug, name, visibility, description?)` | Create a workspace |
| `brainkb_set_space_visibility(slug, visibility)` | Flip private/public (owner) |
| `brainkb_add_space_member(slug, email, role)` | Manage members (owner) |
| `brainkb_add_space_graph(slug, graph_iri, description?)` | Register + bind a graph to a space |
| `brainkb_ingest_text(graph_iri, data)` | Ingest raw RDF text → returns `job_id` |
| `brainkb_ingest_files(graph_iri, [paths], max_concurrency?)` | Ingest RDF files → `job_id` |
| `brainkb_list_jobs(limit?)` / `brainkb_job_status(job_id)` | Ingest status |
| `brainkb_recover_job(job_id)` | Recover a stuck/errored job |
| `brainkb_search(q, space?, limit?, offset?)` | Access-filtered full-text search |
| `brainkb_read_space(slug)` | Read a space's RDF (JSON-LD) |
| `brainkb_list_registered_graphs()` | List visible registered graphs |
| `brainkb_sparql(query)` | Arbitrary SPARQL (admin scope) |
| `brainkb_provenance_job(job_id)` | PROV-O bundle for a job |
| `brainkb_provenance_graph(graph_iri)` | PROV-O history for a graph |
| `brainkb_delta(job_id)` | Exact triples a job added |
| `brainkb_delta_history(graph_iri)` | A graph's change history |
| `brainkb_delta_compare(job_a, job_b)` | Diff two jobs' deltas |

## Install

```bash
cd brainkb_mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optionally set BRAINKB_URL / auto-login creds
```

## Run (standalone, stdio — local testing)

```bash
python server.py            # stdio (default)
```

Later, to serve the hosted remote:

```bash
MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8080 python server.py
# front with TLS at https://mcp.brainkb.org/mcp
```

## Authentication (multi-user safe)

Auth is resolved **per call**, so the hosted remote can serve many users without
one caller's credentials leaking to another. Resolution order:

1. **`Authorization: Bearer <BrainKB JWT>` header** on the inbound request — the
   preferred, **stateless** way for the multi-user remote. Each user's client
   attaches their own token; the server just forwards it to the backend. An
   optional `X-BrainKB-Base-URL` header overrides the backend URL.
2. **Per-session login** — `brainkb_login(email, password)` caches a token scoped
   to *that MCP session only* (convenient for local/stdio use).
3. **Env auto-login** — `BRAINKB_EMAIL` / `BRAINKB_PASSWORD` (single-user/dev).

There is **no shared/global token**. The `user_id` sent to the backend is derived
from the caller's own token (`sub` claim), and the backend independently verifies
the token and enforces space access — so a wrong/forged token is rejected, never
served from another user's context.

## Register with Claude Code

Either add it via the CLI:

```bash
claude mcp add brainkb -- python /Users/tekrajchhetri/Documents/brainypedia_codes_design/brainkb_mcp/server.py
# set the deployment URL (optional; default http://localhost:8010)
claude mcp add brainkb --env BRAINKB_URL=http://localhost:8010 -- python .../brainkb_mcp/server.py
```

…or copy `mcp.config.example.json` into your MCP client config (e.g. project
`.mcp.json`). If you installed into a virtualenv, point `command` at that venv's
`python` (e.g. `.venv/bin/python`).

## Typical flow

1. `brainkb_login(email, password)` (or set `BRAINKB_EMAIL`/`BRAINKB_PASSWORD`).
2. `brainkb_create_space("my-lab", "My Lab", "private")`.
3. `brainkb_add_space_graph("my-lab", "https://brainkb.org/graph/my-lab/")`.
4. `brainkb_ingest_text("https://brainkb.org/graph/my-lab/", "<ttl…>")` → `job_id`.
5. `brainkb_job_status(job_id)` until `done`.
6. `brainkb_search("term", space="my-lab")` / `brainkb_provenance_graph(iri)`.
7. Publish: `brainkb_set_space_visibility("my-lab", "public")`.

## Notes

- Ingestion is submit-and-forget: tools return a `job_id`; poll `brainkb_job_status`.
- Access control is enforced server-side: write requires space owner/editor; public
  spaces are readable anonymously; private-space data is never returned to non-members.
- Scopes: reads need `read`, ingest/space-mutations need `write`, `brainkb_sparql`
  needs `admin`.
