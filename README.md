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
| `brainkb_login(email, password, base_url?)` | Authenticate; cache JWT for this session |
| `brainkb_whoami()` / `brainkb_logout()` | Session/auth info / forget this session's token |
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

## Docker / AWS hosting

The image runs the server as the **streamable-http remote** (uvicorn on
`0.0.0.0:8080`, MCP endpoint at `/mcp`).

### Build & run locally

```bash
docker build -t brainkb-mcp:local .
# point at a query_service on the host (Docker Desktop):
BRAINKB_URL=http://host.docker.internal:8010 docker compose up
# MCP now at http://localhost:8080/mcp
```

Required/runtime env:

| Var | Default | Notes |
|-----|---------|-------|
| `MCP_TRANSPORT` | `streamable-http` | keep for hosting |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8080` | bind |
| `BRAINKB_URL` | `http://localhost:8010` | **must** be set to your reachable query_service |

**Local auto-login (optional):** put `BRAINKB_EMAIL` / `BRAINKB_PASSWORD` in a
git-ignored `.env` next to the compose file (see `.env.example`) so you can skip
the `brainkb_login` tool. Verify with:

```bash
docker compose up -d --force-recreate
docker exec brainkb-mcp python -c "import server as s; print(s._resolve()['email'])"
```

> **Do this only for local/dev.** Baked-in credentials make *every* caller act as
> that one user — never do it on the shared remote (see below).

### Deploy on AWS (ECR + ECS/Fargate behind ALB)

```bash
# 1. push to ECR
aws ecr create-repository --repository-name brainkb-mcp
docker tag brainkb-mcp:local <acct>.dkr.ecr.<region>.amazonaws.com/brainkb-mcp:latest
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
docker push <acct>.dkr.ecr.<region>.amazonaws.com/brainkb-mcp:latest
```

2. Run it as an ECS/Fargate service (task container port **8080**), with env
   `BRAINKB_URL` set to the query_service (e.g. an internal ALB / service URL).
3. Front it with an **Application Load Balancer terminating TLS**; target group →
   container port 8080. Route53 `mcp.brainkb.org` → ALB. This is the registry
   remote URL `https://mcp.brainkb.org/mcp`.
4. Target-group **health check**: TCP on 8080, or HTTP `GET /mcp` with a success
   matcher of `400-499` (a bare GET returns 406 — that still proves liveness).

Notes for the ALB:
- **Auth pass-through**: the ALB forwards the `Authorization` header by default —
  that is how each user authenticates (see [Authentication](#authentication-multi-user-safe)).
- **Streaming**: streamable-http keeps long-lived responses; raise the ALB **idle
  timeout** (e.g. 300s) so streams aren't cut. Enable sticky sessions if you rely
  on per-session `brainkb_login` rather than header auth.
- Run it behind TLS only — tokens must not travel over plain HTTP.

### Credentials: local vs remote (important)

- **Local/dev** — auto-login via `.env` (`BRAINKB_EMAIL`/`BRAINKB_PASSWORD`) is
  fine and convenient; the `.env` is git-ignored.
- **Remote/shared** — do **NOT** set `BRAINKB_EMAIL`/`BRAINKB_PASSWORD` on the
  hosted container. That would make every caller act as one shared user and defeat
  multi-user isolation. Instead each user authenticates **per request** with their
  own `Authorization: Bearer <BrainKB token>` header (forwarded by the ALB). Never
  commit credentials or put them in the image; if a genuine service identity is
  ever required, inject it from **AWS Secrets Manager** (ECS `secrets:`), not from
  a baked-in env var, and scope it minimally.

## Register with Claude Code

There are two ways to register: **stdio** (Claude Code launches the Python
process) or **HTTP** (Claude Code connects to the running Docker container). Use
whichever you prefer — both expose the same `brainkb_*` tools.

> Tools load at **session start** — after registering, open a **fresh** Claude
> Code session for the `brainkb_*` tools to appear. Use `--scope user` so the
> server is available from any directory (not just this project).

### A. Docker (HTTP / streamable-http) — recommended for testing the hosted setup

1. Run the container (publishes port 8080 and points at the query_service on the
   host — `host.docker.internal` resolves to your machine on Docker Desktop):

   ```bash
   cd brainkb_mcp
   BRAINKB_URL=http://host.docker.internal:8010 docker compose up -d
   # verify it's serving: 406 on a bare GET is expected (means "up")
   curl -o /dev/null -w "%{http_code}\n" http://localhost:8080/mcp
   ```

   Two gotchas this avoids: the port **must be published** (`docker ps` should show
   `0.0.0.0:8080->8080`, not just `8080/tcp`), and **`BRAINKB_URL` must be
   `host.docker.internal:8010`, not `localhost`** — inside the container
   `localhost` is the container, not your stack.

2. Register the HTTP endpoint (note the `/mcp` path):

   ```bash
   claude mcp add --scope user --transport http brainkb http://localhost:8080/mcp
   claude mcp list | grep brainkb        # -> brainkb: http://localhost:8080/mcp (HTTP) - ✔ Connected
   ```

3. Auth: either call `brainkb_login(email, password)` in-session, or bake
   auto-login into the container:

   ```bash
   BRAINKB_URL=http://host.docker.internal:8010 \
   BRAINKB_EMAIL=you@example.com BRAINKB_PASSWORD=*** \
     docker compose up -d --force-recreate
   ```

### B. stdio (Claude Code launches the process directly)

```bash
claude mcp add --scope user brainkb --env BRAINKB_URL=http://localhost:8010 -- \
  /abs/path/brainkb_mcp/.venv/bin/python /abs/path/brainkb_mcp/server.py
```

Point `command` at the venv's `python` (so `mcp`/`httpx` resolve). No container
needed; the process talks to the query_service at `localhost:8010` directly.

### Notes

- Either way, remove/replace an existing registration first if the name clashes:
  `claude mcp remove brainkb`.
- …or copy `mcp.config.example.json` into your MCP client config (e.g. project
  `.mcp.json`) for the stdio variant.
- **Local only**: `localhost` registrations work in Claude Code **on this
  machine**. A cloud/claude.ai session can't reach them — that needs the hosted
  remote (AWS) with a public URL.

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
