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

Authorization is enforced **server-side** (roles → capabilities → space membership
→ per-space rules); these tools just invoke it and surface `403`s.

### Session
| Tool | What it does |
|------|--------------|
| `brainkb_login(email, password, base_url?)` | Password login; caches the session token |
| `brainkb_globus_login(provider?)` | Start OAuth (Globus/ORCID/GitHub) login → returns a URL to open; browser shows a one-time code |
| `brainkb_finish_login(code)` | Complete OAuth login by pasting the code the browser showed |
| `brainkb_whoami()` / `brainkb_logout()` | Session/auth info / forget this session's token |

### Spaces
| Tool | What it does |
|------|--------------|
| `brainkb_list_spaces()` | List spaces you can see (yours + public) |
| `brainkb_create_space(slug, name, visibility, description?, space_type?)` | Create a workspace — `space_type` `individual` (write-capable role) or `team` (Admin/granted) |
| `brainkb_set_space_visibility(slug, visibility)` | Flip private/public (owner/manager) |
| `brainkb_add_space_member(slug, email, role)` | Manage members (owner/manager) |
| `brainkb_add_space_graph(slug, graph_iri, description?)` | Register + bind a graph to a space |
| `brainkb_read_space(slug)` | Read a space's RDF (JSON-LD) |

### Ingest & jobs
| Tool | What it does |
|------|--------------|
| `brainkb_ingest_text(graph_iri, data, sha256?, expected_bytes?)` | Ingest raw RDF text → `job_id`. For RDF you authored in-session; declare `sha256` and the write is refused if the bytes changed in transit |
| `brainkb_ingest_upload(graph_iri, upload_id)` | Ingest a file staged via `POST /upload` — **the route for a large local file**; the client streams the bytes, the server ingests them |
| `brainkb_upload_status(upload_id)` / `brainkb_list_uploads()` / `brainkb_discard_upload(id)` | Track or drop staged uploads |
| `brainkb_ingest_url(graph_iri, url, sha256?, filename?)` | **Large files on the remote.** The server fetches the URL and streams it to the ingest API — the bytes never pass through a caller's context. Needs `MCP_INGEST_URL_ALLOWLIST` |
| `brainkb_ingest_files(graph_iri, [paths], max_concurrency?)` | Ingest RDF files (TTL/JSON-LD/…) → `job_id`. Streams large uploads (up to ~5 GB/user); no byte cap, only a file-count cap |
| `brainkb_list_jobs(limit?)` / `brainkb_job_status(job_id)` | Ingest status |
| `brainkb_recover_job(job_id)` | Recover a stuck/errored job |

### Read / search / provenance
| Tool | What it does |
|------|--------------|
| `brainkb_search(q, space?, limit?, offset?)` | Access-filtered full-text search |
| `brainkb_list_registered_graphs()` | List visible registered graphs |
| `brainkb_sparql(query)` | Arbitrary SPARQL (**admin** role) |
| `brainkb_provenance_job(job_id)` | PROV-O bundle for a job |
| `brainkb_provenance_graph(graph_iri)` | PROV-O history for a graph |
| `brainkb_delta(job_id)` | Exact triples a job added |
| `brainkb_delta_history(graph_iri)` | A graph's change history |
| `brainkb_delta_compare(job_a, job_b)` | Diff two jobs' deltas |

### Authorization / RBAC (query_service)
| Tool | What it does |
|------|--------------|
| `brainkb_list_capabilities()` | (Admin) Catalog of KG capabilities — all / grantable / admin-only + meanings |
| `brainkb_capabilities(member)` | (Admin) A user's roles / effective capabilities / grants |
| `brainkb_grant_capability(member, capability)` / `brainkb_revoke_capability(...)` | (Admin) Delegate/revoke a capability to an **individual** |
| `brainkb_role_capabilities(role)` | (Admin) Capabilities granted to a role/group |
| `brainkb_grant_role_capability(role, capability)` / `brainkb_revoke_role_capability(...)` | (Admin) Grant/revoke a capability to a whole **group/role** (e.g. `uk_collaborator`) |
| `brainkb_list_access_rules(slug)` | List a space's fine-grained access rules |
| `brainkb_add_access_rule(slug, action, subject_type, subject_value)` | Add a per-space rule (read/write/manage × global_role/member/space_role) — a write rule GRANTS a group ingest into that space |
| `brainkb_remove_access_rule(slug, rule_id)` | Remove a per-space access rule |

### Admin user management (usermanagement service, `:8004`)
Require an **Admin/SuperAdmin** role and an authenticated caller (`BRAINKB_TOKEN`, `brainkb_login`, or an `Authorization` header).
Assigning/removing the `Admin` role and banning an Admin are **SuperAdmin-only**.
| Tool | What it does |
|------|--------------|
| `brainkb_list_users(q?, role?, limit?)` | List users/profiles |
| `brainkb_available_roles()` | List roles/groups |
| `brainkb_create_role(name, category?, description?)` | Create a role/group/category (e.g. `uk_collaborator`) |
| `brainkb_assign_role(email, role)` / `brainkb_remove_role(email, role)` | Assign/remove a role by email (Admin role = SuperAdmin-only) |
| `brainkb_list_permissions()` | List usermanagement permissions (resource/action) |
| `brainkb_create_permission(name, resource, action, description?)` | Add a new permission |
| `brainkb_activate_user(email)` / `brainkb_deactivate_user(email)` | Activate/deactivate login access |
| `brainkb_ban_user(email, reason)` / `brainkb_unban_user(email)` | Ban/unban — the removal mechanism (**no hard delete**; reversible) |

## Rate limiting & payload guards

The server applies an in-process, **per-caller** fixed-window rate limit (keyed by
source IP — `X-Forwarded-For` / `X-Real-IP` / socket peer) plus payload guards, as
a first line of defence against abuse / brute-force / floods on the hosted remote.
It is **per-process** (each worker has its own counters) and is **not** a
substitute for an edge proxy / WAF / API gateway for real DDoS. `stdio` (local) is
exempt. Over-limit calls return HTTP `429`.

| Env | Default | Limits |
|-----|---------|--------|
| `MCP_RATELIMIT_ENABLED` | `true` | Master switch (`false` to disable) |
| `MCP_RATELIMIT_WINDOW_SEC` | `60` | Window length (seconds) |
| `MCP_RATELIMIT_AUTH_PER_MIN` | `8` | `brainkb_login` / `brainkb_globus_login` (brute-force) |
| `MCP_RATELIMIT_WRITE_PER_MIN` | `40` | mutations / ingest |
| `MCP_RATELIMIT_READ_PER_MIN` | `120` | reads |
| `MCP_RATELIMIT_ADMIN_PER_MIN` | `30` | usermanagement admin calls |
| `MCP_MAX_INGEST_BYTES` | `10000000` | max **raw-text** ingest size (0 = unlimited); file ingest is **not** capped |
| `MCP_MAX_INGEST_FILES` | `50` | max files per `brainkb_ingest_files` call |
| `MCP_RATELIMIT_MAX_KEYS` | `20000` | distinct callers tracked per window; once full, new callers are refused for the rest of the window rather than growing the map without bound |

**`X-Forwarded-For` is only believed from `MCP_TRUSTED_PROXIES`** (see below). It
is a caller-supplied header, so honouring it from any peer would let one client
present a fresh identity per request — bypassing every limit including the
brute-force bucket — and let it pin a *victim's* IP to exhaust their quota. With
the list empty, limits key on the socket peer, which means everything arriving
through a reverse proxy shares one bucket. Set it when you deploy behind one.

## Hardening the hosted remote

The `streamable-http` remote is reachable by anonymous callers and performs **no
transport-level authentication** — every access decision happens downstream in the
backend. Three inputs are therefore treated as hostile:

| Env | Default | What it guards |
|-----|---------|----------------|
| `MCP_ALLOWED_BASE_URLS` | *(only `BRAINKB_URL`)* | Backends a caller may select via the `X-BrainKB-Base-URL` header or a tool's `base_url` argument |
| `MCP_TRUSTED_PROXIES` | *(empty)* | Peers whose `X-Forwarded-For` / `X-Real-IP` is believed |
| `MCP_INGEST_ROOT` | *(unset → file ingest disabled on http)* | Directory `brainkb_ingest_files` may read from |
| `MCP_UPLOAD_ENABLED` | `true` | `POST /upload` staging endpoint (authenticated) |
| `MCP_UPLOAD_DIR` | `<tmp>/brainkb-uploads` | where staged uploads live |
| `MCP_UPLOAD_MAX_BYTES` | `5000000000` | 5 GB per file |
| `MCP_UPLOAD_TOTAL_BYTES` | `20000000000` | across all staged uploads |
| `MCP_UPLOAD_TTL_MIN` | `360` | stale uploads are swept |
| `MCP_INGEST_URL_ALLOWLIST` | *(unset → URL ingest disabled)* | Hosts `brainkb_ingest_url` may fetch from; `.s3.amazonaws.com` matches any subdomain |
| `MCP_MAX_INGEST_URL_BYTES` | `1000000000` | cap on a fetched body |
| `MCP_INGEST_URL_ALLOW_HTTP` | `false` | permit a plaintext http fetch (trusted internal hosts only) |
| `MCP_ALLOW_SHARED_IDENTITY` | `false` | Opt in to `BRAINKB_TOKEN` as an ambient identity (single-user HTTP only) |

**Backend URL allowlist.** The base URL is the *destination of requests that carry
credentials* — the caller's bearer token, the env PAT, a session's stored
password. Accepting an arbitrary value would give any caller SSRF (fetch
`169.254.169.254`, internal load balancers, anything in the VPC, with the response
body returned to them) *and* credential exfiltration (the token-exchange helpers
POST `BRAINKB_TOKEN` to that host). Only `BRAINKB_URL` and entries in
`MCP_ALLOWED_BASE_URLS` are honoured; anything else is refused with a clear error.
Loopback is additionally allowed under stdio, where the caller is the local user.

Leaving it **unset is the tightest setting**, and is the right answer when the MCP
runs on the same host as the BrainKB stack (`BRAINKB_URL=http://host.docker.internal:8010`)
— that host is then the only backend any caller can reach. Add entries only for a
second backend you genuinely own:

```bash
# pair each backend with its usermanagement URL — the :8010 -> :8004 convention
# never applies to an https host, so an unpaired https entry has no way to reach
# usermanagement and the admin/login tools will fail against it
MCP_ALLOWED_BASE_URLS=https://queryservice.example.org=https://usermanagement.example.org
```

Never list the MCP's own hostname (`mcp.brainkb.org`): it is this server, not a
backend, so it would point credentialed calls back at itself.

**Ingesting a large file through the remote.** Three routes, all of which keep the
bytes out of the caller's context:

1. **`POST /upload` + `brainkb_ingest_upload`** — the general answer, needing nothing
   but the credential you already have. Your client streams the file straight to the
   server:

   ```bash
   curl -H "Authorization: Bearer $BRAINKB_TOKEN" \
        --data-binary @review.ttl \
        "https://mcp.brainkb.org/upload?filename=review.ttl&sha256=$(shasum -a 256 review.ttl | cut -d" " -f1)"
   # -> 201 {"upload_id": "up_...", "bytes": 2794655, "sha256": "..."}
   ```

   then `brainkb_ingest_upload("https://brainkb.org/graph/x/", "up_...")`. Add
   `&graph=<iri>` to the URL instead and the server submits the ingest itself,
   answering 202 at once — upload and forget, then poll
   `brainkb_upload_status(upload_id)`. 5 GB per file. The endpoint is authenticated
   through the same token path as the tools, requires the `write` scope the ingest
   API requires, and an upload can only be ingested by the identity that made it.
2. `brainkb_ingest_url` — put the file behind an HTTPS URL the server can reach (an
   S3/GCS presigned URL, a release asset, an internal artifact store) and pass that
   URL. The server streams it to the ingest API. Requires
   `MCP_INGEST_URL_ALLOWLIST`; it is SSRF-guarded (allowlisted hosts only, private /
   loopback / link-local / metadata resolutions refused even for an allowlisted
   name, redirects not followed, byte cap, HTML-instead-of-RDF detected, optional
   `sha256` verified before anything is written).
3. `MCP_INGEST_ROOT` + `brainkb_ingest_files` — place the file on the server host in
   a dedicated upload dir with a matching bind mount.

What NOT to do, in either case: reproduce the file's RDF into `brainkb_ingest_text`.
Ingest is append-only — no delete for triples, no unregister for a graph — dense
Turtle does not survive transcription intact, and splitting one document across
calls breaks blank-node identity (bnode labels are document-scoped, so a shared
bnode becomes two nodes and its triples detach) while still parsing cleanly. That
failure is invisible until someone counts triples, and permanent once written.
Reconcile `brainkb_delta(job_id)` against a count you knew in advance.

**File ingest resolves paths on the SERVER.** `brainkb_ingest_files(paths)` opens
those paths in the *MCP process*, not on the caller's machine. Over stdio that's
the same machine and is the point of the tool. Over http it would let a remote
caller ingest `/proc/self/environ` (leaking `BRAINKB_TOKEN`), `/app/server.py`, or
mounted secrets into a graph they own and read it back with `brainkb_read_space` —
so it is **disabled unless `MCP_INGEST_ROOT` confines it**. Symlinks are resolved
before the check, so they cannot point out of the root.

**`BRAINKB_TOKEN` is ignored on http** unless `MCP_ALLOW_SHARED_IDENTITY=true`. It
is an *ambient* credential with the same identity-shadowing problem that got
`BRAINKB_EMAIL`/`BRAINKB_PASSWORD` auto-login removed: a caller who sends no
credential of their own would silently execute as the token's owner.

The server prints a warning to stderr at startup for each of these that is
configured in a weakening way.

Large-file ingest (TTL/JSON-LD up to ~5 GB per user) streams from disk with
read/write timeouts disabled, so big uploads are not aborted. The **backend** must
also allow it — ensure the query_service reverse proxy / load balancer permits
large request bodies and a long enough idle timeout.

## Install

```bash
cd brainkb_mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set BRAINKB_URL (and optionally BRAINKB_TOKEN for local use)
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

## Authentication (multi-user safe, single sign-on)

BrainKB uses **single sign-on**: one login mints a short-lived **refresh token**,
which is exchanged on demand for a narrow, per-service **access token**
(`aud=query_service`, `aud=usermanagement`, …). A token minted for one service
can't be replayed against another. The MCP does this exchange for you — a single
`brainkb_login` now covers both knowledge-graph and admin (usermanagement) tools,
no second login. If the backend has no SSO, it falls back to legacy per-service
`/api/token` automatically.

Auth is resolved **per call**, so the hosted remote can serve many users without
one caller's credentials leaking to another. Resolution order:

1. **`Authorization: Bearer <token>` header** on the inbound request — the
   preferred, **stateless** way for the multi-user remote. A **PAT** or a
   **refresh token** here unlocks every service (the MCP exchanges it per
   service); a single **service access token** works for that service. An
   optional `X-BrainKB-Base-URL` header selects the backend — but only from the
   configured allowlist (see [Hardening](#hardening-the-hosted-remote)).
2. **`BRAINKB_TOKEN` env — a Personal Access Token** (`brainkb_pat_…`). The
   recommended way to stay logged in across tasks/sessions *locally*: it
   authenticates per-call, so it survives when an in-session login would not.
   **Ignored on the streamable-http remote** unless
   `MCP_ALLOW_SHARED_IDENTITY=true`, since there it is an ambient identity that
   anonymous callers would inherit.
3. **Per-session login** — `brainkb_login(email, password)` (or
   `brainkb_globus_login` → `brainkb_finish_login`, or `brainkb_use_token`) caches
   the credential for *that MCP session only*.

There is **no email/password auto-login** — a baked-in `BRAINKB_EMAIL`/
`BRAINKB_PASSWORD` was removed because it could silently shadow a real login and
mis-attribute actions. Use a PAT, an explicit login, or an `Authorization` header.

**Onboarding = first login (no self-registration).** New users are created
automatically the first time they sign in with **Globus/ORCID/GitHub**
(`brainkb_globus_login` → `brainkb_finish_login`); there is no separate register
step (the backend `/api/register` is disabled). Password login (`brainkb_login`)
works for accounts that already exist.

### Expiration — what lasts how long

| Credential | Lifetime | Notes |
|---|---|---|
| **Personal Access Token (PAT)** — `BRAINKB_TOKEN` | **3 days, SLIDING** (default) | Each use pushes expiry to *now + `USERMANAGEMENT_PAT_DEFAULT_DAYS`* (default 3); stays alive while you keep using it, expires after that many **idle** days. Hard ceiling = *created + `USERMANAGEMENT_PAT_MAX_DAYS`* (default 365). Revocable instantly (`brainkb_revoke_token`). Sliding is toggled by `USERMANAGEMENT_PAT_SLIDING`. |
| **Per-service access token** (internal, from exchange) | `USERMANAGEMENT_ACCESS_TOKEN_TTL_MIN` (default 15 min) | Short-lived; the MCP re-exchanges automatically. You never handle it. |
| **Refresh token** (in-session login) | `USERMANAGEMENT_REFRESH_TOKEN_TTL_MIN` (default 12h), capped by MCP `MCP_SESSION_TTL_MIN` (default 720 min) | Cached for that MCP session only; does **not** persist across tasks on the hosted remote — use a PAT for that. |
| **OAuth paste-code** (`brainkb_globus_login`) | ~10 min, **single-use** | Only a handle swapped once for a refresh token; high-entropy (`USERMANAGEMENT_CLI_CODE_LEN`, default 20 chars ≈ 98 bits). |

**To stay logged in across tasks** (no re-prompt): log in once, run
`brainkb_create_token`, and set the returned `brainkb_pat_…` as `BRAINKB_TOKEN`
in your MCP config. Thanks to sliding expiry it keeps working for 3 days of
activity and self-extends; leave it unused 3 days and it expires — mint a new one.

**In-session logins expire — and don't cross sessions.** A cached in-session
login lasts until its refresh token expires (`USERMANAGEMENT_REFRESH_TOKEN_TTL_MIN`,
default 12h), hard-capped by `MCP_SESSION_TTL_MIN` (default 720). On the hosted
`streamable-http` transport a *new task/conversation* is a new session, so an
in-session login won't carry over — that's expected; use a PAT. `brainkb_whoami`
reports `session_expires_in_min`.

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
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8080` | bind *inside* the container |
| `BRAINKB_URL` | `http://localhost:8010` | **must** be set to your reachable query_service |

Plus the guards in [Hardening the hosted remote](#hardening-the-hosted-remote) —
at minimum `MCP_TRUSTED_PROXIES` (your ALB/nginx source IPs, else X-Forwarded-For
is ignored and rate limits key on the proxy), `MCP_ALLOWED_BASE_URLS` if the
deployment serves more than one backend, and `MCP_INGEST_ROOT` **only if** you want
`brainkb_ingest_files` enabled on the remote (it's disabled otherwise). Leave
`MCP_ALLOW_SHARED_IDENTITY` unset unless it's a single-user deployment.

> The compose file publishes the port as `127.0.0.1:8080:8080`. The MCP speaks
> plaintext HTTP and does no transport-level auth, so the reverse proxy that
> terminates TLS should be the only thing that can reach it. Don't widen that
> binding without a security group or firewall in front.

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
4. Target-group **health check**: HTTP `GET /healthz` with the default `200`
   matcher. It is liveness only — it deliberately does not probe the query_service,
   so a backend blip won't cycle healthy MCP tasks, and nobody can use the health
   endpoint to hammer the backend. (TCP on 8080 also works; `GET /mcp` with a
   `400-499` matcher works too, since a bare GET returns 406.)

Two public, unauthenticated routes exist for humans and load balancers:

| Route | Response |
|-------|----------|
| `GET /` | Landing page: what the host is, the `claude mcp add` command, and why `/mcp` returns 406 in a browser |
| `GET /healthz` | `200` `ok` — liveness for the target group |

Neither discloses configuration (no backend URLs, versions, or request echo beyond
a validated, HTML-escaped `Host`), because both are reachable without credentials.

Notes for the ALB:
- **Auth pass-through**: the ALB forwards the `Authorization` header by default —
  that is how each user authenticates (see [Authentication](#authentication-multi-user-safe)).
- **Streaming**: streamable-http keeps long-lived responses; raise the ALB **idle
  timeout** (e.g. 300s) so streams aren't cut. Enable sticky sessions if you rely
  on per-session `brainkb_login` rather than header auth.
- Run it behind TLS only — tokens must not travel over plain HTTP.

### Hardened `.env` for a single-host (EC2) deployment

When the MCP container and the BrainKB stack share one instance and a reverse
proxy terminating TLS for `mcp.brainkb.org` is the only ingress:

```bash
# Backends — co-located stack, reached via the host gateway (not `localhost`,
# which inside the container is the container). This is also the ONLY backend
# any caller can reach, which is what keeps the allowlist below empty.
BRAINKB_URL=http://host.docker.internal:8010
USERMANAGEMENT_URL=http://host.docker.internal:8004

# Backend allowlist: leave UNSET. Only BRAINKB_URL is honoured, so
# X-BrainKB-Base-URL / base_url cannot redirect credentials anywhere.
# MCP_ALLOWED_BASE_URLS=

# The proxy's source address *as the container sees it*. Exact IPs or CIDRs.
#   * ALB straight to this instance — the ALB's subnet CIDRs (see below)
#   * proxy on the host, published 127.0.0.1:8080 — the compose network gateway:
#       docker network inspect brainkb_mcp_default \
#         --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'   # e.g. 172.18.0.1
# Without this, every caller shares one rate-limit bucket.
MCP_TRUSTED_PROXIES=10.0.1.0/24,10.0.2.0/24

# Loopback publishing (the default) is unreachable from an ALB. Set this when the
# ALB forwards to the instance, and allow TCP 8080 only from the ALB's SG.
MCP_BIND_ADDR=0.0.0.0

# File ingest stays DISABLED (paths resolve on the server's filesystem). Set
# MCP_INGEST_ROOT only if you want it, and only to a dedicated upload dir.
# MCP_INGEST_ROOT=

# No ambient identity: BRAINKB_TOKEN is ignored here, and must stay unset in the
# image/compose env. Callers authenticate per request.
# MCP_ALLOW_SHARED_IDENTITY=false
```

The proxy must set the forwarding header from what *it* observed —
`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` (or
`X-Real-IP $remote_addr`). The MCP reads the **rightmost** `X-Forwarded-For`
entry, so anything a client prepends itself is ignored.

**No host-side proxy? Then the ALB is the proxy.** Its source addresses are ENI IPs
in the load-balancer subnets, and AWS adds or replaces those ENIs as the ALB
scales — so trust the **subnet CIDRs**, not a pinned IP list that will silently
stop matching:

```bash
aws ec2 describe-subnets --query 'Subnets[].CidrBlock' --output text \
  --subnet-ids $(aws elbv2 describe-load-balancers --names <lb-name> \
    --query 'LoadBalancers[0].AvailabilityZones[].SubnetId' --output text)
```

The ALB also has to *reach* the container, and the compose default publishes on
`127.0.0.1` — loopback, which only a proxy on the same host can use. Set
`MCP_BIND_ADDR=0.0.0.0` and make the **security group** the wall: TCP 8080 from the
ALB's security group only. This endpoint is plaintext and does no transport auth, so
an open 8080 is an open MCP — and note Docker's iptables rules bypass host firewalls
like `ufw`, which makes the security group the only boundary that actually holds.

Degradation is fail-safe in both directions: a wrong or stale entry means
`X-Forwarded-For` is ignored and limits key on the proxy (everyone shares one
bucket) — never that a forged header is believed. Unparseable entries are reported
at startup and not trusted.

### Credentials: local vs remote (important)

- **Local/dev** — put a PAT in a git-ignored `.env` as `BRAINKB_TOKEN` and skip
  the login tools entirely.
- **Remote/shared** — every user authenticates **per request** with their own
  `Authorization: Bearer <BrainKB token>` header (forwarded by the ALB). A
  baked-in `BRAINKB_TOKEN` is *ignored* on this transport, and there is no
  email/password auto-login, precisely so no caller can inherit a shared identity.
  Never commit credentials or put them in the image; if a genuine service identity
  is ever required, inject it from **AWS Secrets Manager** (ECS `secrets:`), not
  from a baked-in env var, and scope it minimally.

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

3. Auth: call `brainkb_login(email, password)` / `brainkb_globus_login()`
   in-session, or `brainkb_use_token("brainkb_pat_…")`, or have your client send
   an `Authorization: Bearer` header. A container-level `BRAINKB_TOKEN` will
   **not** be used on this transport — see
   [Hardening the hosted remote](#hardening-the-hosted-remote).

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

1. `brainkb_login(email, password)` / `brainkb_globus_login()` (or set `BRAINKB_TOKEN` to a PAT for local use).
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
