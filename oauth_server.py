# -*- coding: utf-8 -*-
"""MCP OAuth for connector apps (Perplexity, claude.ai, ChatGPT, Cursor, ...).

Those apps connect to a remote MCP server by URL alone: they read the OAuth
metadata, register themselves (RFC 7591 dynamic client registration), send the
user to /authorize, and swap the code for a token. Without this the app asks for
a client_id/secret by hand — "Server does not support automatic registration".

The token the app ends up holding IS the user's BrainKB credential (an SSO
refresh token, or a PAT the user pasted). It arrives as 'Authorization: Bearer'
on every MCP request, which server.py already resolves per caller — so OAuth adds
a way to obtain a token, not a second way of checking one.

Everything is STATELESS, so it survives restarts and works behind several
replicas without a database:
  * client_id  — the registered metadata, HMAC-signed; the secret is derived.
  * transaction, auth code, refresh token — Fernet blobs (encrypted and
    authenticated), each tagged with its kind and bound to its client.
Keys come from MCP_OAUTH_SECRET. Without it a random per-process key is used,
which works for one replica but logs every client out on restart.

User sign-in reuses usermanagement's CLI OAuth (Globus / ORCID / GitHub): the
browser goes there with return_to=<this server>/oauth/callback and comes back with
a one-time code. An older usermanagement that ignores return_to shows the code
instead; the sign-in page accepts it pasted, or a Personal Access Token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode, urlparse

from cryptography.fernet import Fernet, InvalidToken
from pydantic import AnyHttpUrl
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken,
    RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.server.auth.routes import build_metadata, create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

_TX_TTL = 15 * 60       # sign-in page -> provider -> back
_CODE_TTL = 5 * 60      # authorization code
_REFRESH_TTL = 30 * 86400
_SCOPE = "brainkb"
_COOKIE = "bk_oauth_tx"
_PROVIDERS = (("globus", "Globus"), ("orcid", "ORCID"), ("github", "GitHub"))
# Redirect schemes a registered client may use. https anywhere; http only to
# loopback (desktop apps); custom schemes (cursor://, vscode://) for native apps.
_BANNED_SCHEMES = {"javascript", "data", "file", "vbscript", "blob", "about"}
_MAX_CLIENT_ID = 3000


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _jwt_exp(token: str) -> Optional[int]:
    """`exp` of a JWT, unverified (only used to report expires_in); None otherwise."""
    try:
        return int(json.loads(_unb64(token.split(".")[1])).get("exp"))
    except Exception:
        return None


def _redirect_ok(uri: str) -> bool:
    p = urlparse(uri)
    scheme = (p.scheme or "").lower()
    if not scheme or scheme in _BANNED_SCHEMES or p.fragment:
        return False
    if scheme == "http":
        return p.hostname in ("localhost", "127.0.0.1", "::1")
    if scheme == "https":
        return bool(p.hostname)
    return True


def _redirect_label(uri: str) -> str:
    p = urlparse(uri)
    return p.hostname or f"{p.scheme}:// app"


class _Code(AuthorizationCode):
    credential: str


class _Refresh(RefreshToken):
    credential: str
    res: Optional[str] = None   # own field: RefreshToken.resource is missing in older SDKs


class BrainKBOAuthProvider:
    """OAuthAuthorizationServerProvider backed by signed/encrypted blobs."""

    def __init__(self, secret: bytes):
        self._mac = hashlib.sha256(b"brainkb-mcp-oauth/mac\x00" + secret).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(
            hashlib.sha256(b"brainkb-mcp-oauth/enc\x00" + secret).digest()))
        self._used: Dict[str, float] = {}   # consumed auth codes (per replica, best effort)
        self._lock = threading.Lock()

    # ---- blobs --------------------------------------------------------------
    def seal(self, kind: str, data: Dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps({"k": kind, **data}).encode()).decode()

    def open(self, kind: str, blob: str, ttl: int) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(self._fernet.decrypt(blob.encode(), ttl=ttl))
        except (InvalidToken, ValueError, TypeError):
            return None
        return data if data.get("k") == kind else None

    def _cid_tag(self, client_id: str) -> str:
        return _b64(hashlib.sha256(client_id.encode()).digest()[:12])

    def _secret_for(self, client_id: str) -> str:
        return _b64(hmac.new(self._mac, b"secret\x00" + client_id.encode(), "sha256").digest())

    # ---- clients ------------------------------------------------------------
    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in (client_info.redirect_uris or [])]
        if not uris or len(uris) > 10:
            raise RegistrationError("invalid_redirect_uri", "register 1 to 10 redirect_uris")
        bad = [u for u in uris if not _redirect_ok(u)]
        if bad:
            raise RegistrationError(
                "invalid_redirect_uri",
                "redirect_uris must be https, http to loopback, or an app scheme: " + ", ".join(bad))
        meta = {
            "r": uris,
            "m": client_info.token_endpoint_auth_method,
            "n": (client_info.client_name or "")[:80],
            "s": client_info.scope,
            "i": client_info.client_id_issued_at,
        }
        body = _b64(json.dumps(meta, separators=(",", ":")).encode())
        sig = _b64(hmac.new(self._mac, body.encode(), "sha256").digest()[:16])
        client_id = f"bkc.{body}.{sig}"
        if len(client_id) > _MAX_CLIENT_ID:
            raise RegistrationError("invalid_client_metadata", "client metadata too large")
        # The SDK generated a random id/secret and returns this same object, so
        # replacing them here is what the client receives.
        client_info.client_id = client_id
        if client_info.client_secret:
            client_info.client_secret = self._secret_for(client_id)

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        try:
            tag, body, sig = client_id.split(".")
        except (AttributeError, ValueError):
            return None
        want = _b64(hmac.new(self._mac, body.encode(), "sha256").digest()[:16])
        if tag != "bkc" or not hmac.compare_digest(sig, want):
            return None
        try:
            meta = json.loads(_unb64(body))
            return OAuthClientInformationFull(
                client_id=client_id,
                client_secret=None if meta["m"] == "none" else self._secret_for(client_id),
                client_id_issued_at=meta.get("i"),
                redirect_uris=meta["r"],
                token_endpoint_auth_method=meta["m"],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                client_name=meta.get("n") or None,
                scope=meta.get("s"),
            )
        except Exception:
            return None

    # ---- authorize ----------------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        tx = self.seal("tx", {
            "c": client.client_id,
            "n": client.client_name or "",
            "ru": str(params.redirect_uri),
            "rx": params.redirect_uri_provided_explicitly,
            "cc": params.code_challenge,
            "st": params.state,
            "sc": params.scopes or [_SCOPE],
            "rs": params.resource,
        })
        # Relative: the SDK handler has no request to build an origin from, and
        # the browser resolves it against this server either way.
        return "/oauth/login?" + urlencode({"tx": tx})

    def issue_code(self, tx: Dict[str, Any], credential: str) -> str:
        code = self.seal("code", {
            "c": self._cid_tag(tx["c"]), "ru": tx["ru"], "rx": tx["rx"], "cc": tx["cc"],
            "sc": tx["sc"], "rs": tx.get("rs"), "cr": credential,
        })
        return construct_redirect_uri(tx["ru"], code=code, state=tx.get("st"))

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        d = self.open("code", authorization_code, _CODE_TTL)
        if not d or d["c"] != self._cid_tag(client.client_id or ""):
            return None
        with self._lock:
            if authorization_code_key(authorization_code) in self._used:
                return None
        return _Code(
            code=authorization_code, scopes=d["sc"], expires_at=time.time() + _CODE_TTL,
            client_id=client.client_id, code_challenge=d["cc"], redirect_uri=d["ru"],
            redirect_uri_provided_explicitly=d["rx"], resource=d.get("rs"), credential=d["cr"],
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: _Code) -> OAuthToken:
        now = time.time()
        key = authorization_code_key(authorization_code.code)
        with self._lock:
            for k, t in list(self._used.items()):
                if t < now:
                    self._used.pop(k, None)
            if key in self._used:
                raise TokenError("invalid_grant", "authorization code already used")
            self._used[key] = now + _CODE_TTL
        return self._tokens(client, authorization_code.credential, authorization_code.scopes,
                            authorization_code.resource)

    # ---- refresh ------------------------------------------------------------
    def _tokens(self, client, credential: str, scopes: List[str], resource: Optional[str]) -> OAuthToken:
        exp = _jwt_exp(credential)
        now = int(time.time())
        if exp is not None and exp <= now:
            raise TokenError("invalid_grant", "the BrainKB sign-in has expired; sign in again")
        refresh_exp = min(exp, now + _REFRESH_TTL) if exp else now + _REFRESH_TTL
        refresh = self.seal("rt", {"c": self._cid_tag(client.client_id), "sc": scopes,
                                   "rs": resource, "cr": credential, "e": refresh_exp})
        return OAuthToken(
            access_token=credential,
            token_type="Bearer",
            expires_in=(exp - now) if exp else None,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        d = self.open("rt", refresh_token, _REFRESH_TTL)
        if not d or d["c"] != self._cid_tag(client.client_id or ""):
            return None
        return _Refresh(token=refresh_token, client_id=client.client_id or "", scopes=d["sc"],
                        expires_at=d.get("e"), res=d.get("rs"), credential=d["cr"])

    async def exchange_refresh_token(self, client, refresh_token: _Refresh, scopes: List[str]) -> OAuthToken:
        return self._tokens(client, refresh_token.credential, scopes or refresh_token.scopes,
                            refresh_token.res)

    # ---- unused by this server ---------------------------------------------
    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        # /mcp is not wrapped in the SDK's RequireAuthMiddleware: bearer tokens are
        # resolved per call by server.py, which also accepts callers without one.
        return None

    async def revoke_token(self, token) -> None:
        return None


def authorization_code_key(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@dataclass
class Hooks:
    """What the routes need from server.py, passed in to avoid a circular import."""
    origin: Callable[[Request], str]                 # public https origin of this server
    start_login: Callable[[str, str], str]           # (provider, return_to) -> authorize URL
    redeem_code: Callable[[str], Optional[str]]      # paste-code -> refresh token
    check_pat: Callable[[str], bool]                 # PAT -> valid?
    rate_ok: Callable[[Request], bool]


def _secret() -> bytes:
    raw = os.getenv("MCP_OAUTH_SECRET", "").strip()
    if raw:
        return raw.encode()
    print("[brainkb-mcp] WARNING: MCP_OAUTH_SECRET is not set — connector-app OAuth "
          "uses a random per-process key, so registered apps must reconnect after "
          "every restart and it cannot run on more than one replica.",
          file=sys.stderr, flush=True)
    return secrets.token_bytes(32)


# --------------------------------------------------------------------------- #
# sign-in page
# --------------------------------------------------------------------------- #

_PAGE_CSS = """
:root{--ink:#0b1628;--muted:#5b6b82;--line:#e3e8f0;--blue:#1d4ed8;--bg:#f6f8fc;--card:#fff;--err:#b42318}
@media (prefers-color-scheme:dark){:root{--ink:#e7edf7;--muted:#9aa8bd;--line:#26344c;--blue:#6d9bff;--bg:#0b1628;--card:#111f36;--err:#ff8a7a}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px 16px;
  background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.card{width:100%;max-width:440px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:28px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;margin-bottom:18px}
.brand img{width:32px;height:32px}
h1{font-size:1.25rem;margin:0 0 6px}
p{margin:0 0 14px;color:var(--muted)}
.to{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:0 0 18px;font-size:.9rem;color:var(--muted)}
.to b{color:var(--ink);word-break:break-all}
button{width:100%;font:inherit;font-weight:600;padding:11px 14px;border-radius:10px;cursor:pointer;margin:0 0 10px;
  border:1px solid var(--line);background:var(--card);color:var(--ink)}
button.primary{background:var(--blue);border-color:var(--blue);color:#fff}
button:hover{border-color:var(--blue)}
details{margin-top:8px;border-top:1px solid var(--line);padding-top:12px}
summary{cursor:pointer;color:var(--muted);font-size:.9rem}
label{display:block;font-size:.85rem;color:var(--muted);margin:12px 0 4px}
input{width:100%;font:inherit;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
.err{color:var(--err);margin:0 0 14px}
.fine{font-size:.8rem;margin-top:14px}
"""


def _page(title: str, inner: str, status: int = 200) -> HTMLResponse:
    body = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_PAGE_CSS}</style></head><body>"
        "<main class='card'><div class='brand'><img src='/logo.png' alt=''>BrainKB</div>"
        f"{inner}</main></body></html>"
    )
    return HTMLResponse(body, status_code=status, headers={
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        # No form-action: browsers apply it to the redirect after a submit, and
        # these forms redirect to the sign-in provider and then to the app.
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                                   "frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer",
    })


def _error(msg: str, status: int = 400) -> HTMLResponse:
    return _page("BrainKB sign-in", f"<h1>Sign-in could not continue</h1><p class='err'>{html.escape(msg)}</p>"
                 "<p>Go back to your app and start connecting again.</p>", status)


def _login_page(tx_blob: str, tx: Dict[str, Any], error: str = "") -> HTMLResponse:
    app = html.escape(tx.get("n") or "An app")
    dest = html.escape(_redirect_label(tx["ru"]))
    t = html.escape(tx_blob, quote=True)
    buttons = "".join(
        f"<button class='{'primary' if i == 0 else ''}' name='provider' value='{p}'>Continue with {label}</button>"
        for i, (p, label) in enumerate(_PROVIDERS))
    err = f"<p class='err'>{html.escape(error)}</p>" if error else ""
    inner = (
        f"<h1>Connect {app} to BrainKB</h1>"
        "<p>Sign in with your BrainKB account. The app will then act as you: it can read what you can read "
        "and ingest where you can write.</p>"
        f"<div class='to'>After sign-in, access is sent to <b>{dest}</b>. Continue only if that is the app you are connecting.</div>"
        f"{err}"
        f"<form method='post' action='/oauth/login'><input type='hidden' name='tx' value='{t}'>{buttons}</form>"
        "<details><summary>Have a one-time code or an access token?</summary>"
        f"<form method='post' action='/oauth/login'><input type='hidden' name='tx' value='{t}'>"
        "<label for='code'>One-time code (shown after signing in)</label>"
        "<input id='code' name='code' autocomplete='one-time-code' placeholder='ABCD-EFGH-…'>"
        "<label for='pat'>or a Personal Access Token</label>"
        "<input id='pat' name='pat' type='password' autocomplete='off' placeholder='brainkb_pat_…'>"
        "<p></p><button class='primary'>Continue</button></form></details>"
        "<p class='fine'>If the sign-in page shows you a code instead of returning here, press Back and paste it above.</p>"
    )
    return _page(f"Connect {tx.get('n') or 'an app'} to BrainKB", inner)


def build_routes(hooks: Hooks) -> List[Route]:
    """All OAuth routes: discovery, register/authorize/token, sign-in page, callback."""
    provider = BrainKBOAuthProvider(_secret())
    reg = ClientRegistrationOptions(enabled=True, default_scopes=[_SCOPE])
    rev = RevocationOptions(enabled=False)

    # The SDK's register/authorize/token handlers do the protocol checks (PKCE,
    # redirect_uri matching, client auth). Its metadata route is dropped: the
    # issuer must follow the Host (main vs sandbox), so it is served below.
    sdk = [r for r in create_auth_routes(provider, AnyHttpUrl("https://mcp.brainkb.org"),
                                         client_registration_options=reg, revocation_options=rev)
           if not r.path.startswith("/.well-known/")]

    cors = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"}

    async def as_metadata(request: Request) -> Response:
        origin = hooks.origin(request)
        meta = build_metadata(AnyHttpUrl(origin), None, reg, rev)
        meta.scopes_supported = [_SCOPE]
        meta.token_endpoint_auth_methods_supported = ["client_secret_post", "client_secret_basic", "none"]
        return JSONResponse(meta.model_dump(mode="json", exclude_none=True), headers=cors)

    async def pr_metadata(request: Request) -> Response:
        origin = hooks.origin(request)
        return JSONResponse({
            "resource": f"{origin}/mcp",
            "authorization_servers": [origin],
            "scopes_supported": [_SCOPE],
            "bearer_methods_supported": ["header"],
            "resource_name": "BrainKB",
        }, headers=cors)

    def finish(tx: Dict[str, Any], credential: str) -> Response:
        resp = RedirectResponse(provider.issue_code(tx, credential), status_code=302)
        resp.delete_cookie(_COOKIE, path="/oauth")
        return resp

    async def login_get(request: Request) -> Response:
        blob = request.query_params.get("tx", "")
        tx = provider.open("tx", blob, _TX_TTL)
        if not tx:
            return _error("This sign-in link has expired or is invalid.")
        return _login_page(blob, tx)

    async def login_post(request: Request) -> Response:
        form = await request.form()
        blob = str(form.get("tx") or "")
        tx = provider.open("tx", blob, _TX_TTL)
        if not tx:
            return _error("This sign-in link has expired or is invalid.")
        if not hooks.rate_ok(request):
            return _login_page(blob, tx, "Too many attempts. Wait a minute and try again.")

        prov = str(form.get("provider") or "")
        if prov:
            if prov not in dict(_PROVIDERS):
                return _login_page(blob, tx, "Unknown sign-in provider.")
            try:
                url = await run_in_threadpool(hooks.start_login, prov, f"{hooks.origin(request)}/oauth/callback")
            except Exception:
                return _login_page(blob, tx, f"Could not start {dict(_PROVIDERS)[prov]} sign-in. "
                                             "Try another provider.")
            resp = RedirectResponse(url, status_code=303)
            # The transaction rides in a cookie across the provider round-trip:
            # usermanagement returns only ?code=. Lax is sent on that top-level GET.
            resp.set_cookie(_COOKIE, blob, max_age=_TX_TTL, path="/oauth", httponly=True,
                            secure=request.url.scheme == "https" or hooks.origin(request).startswith("https"),
                            samesite="lax")
            return resp

        code = str(form.get("code") or "").strip()
        pat = str(form.get("pat") or "").strip()
        if code:
            refresh = await run_in_threadpool(hooks.redeem_code, code)
            if not refresh:
                return _login_page(blob, tx, "That code is invalid, expired, or already used.")
            return finish(tx, refresh)
        if pat:
            if not pat.startswith("brainkb_pat_") or not await run_in_threadpool(hooks.check_pat, pat):
                return _login_page(blob, tx, "That access token was not accepted.")
            return finish(tx, pat)
        return _login_page(blob, tx, "Choose a sign-in option.")

    async def callback(request: Request) -> Response:
        blob = request.cookies.get(_COOKIE, "")
        tx = provider.open("tx", blob, _TX_TTL)
        if not tx:
            return _error("Sign-in took too long or cookies are blocked. Start connecting again from your app.")
        if not hooks.rate_ok(request):
            return _login_page(blob, tx, "Too many attempts. Wait a minute and try again.")
        code = request.query_params.get("code", "")
        refresh = await run_in_threadpool(hooks.redeem_code, code) if code else None
        if not refresh:
            return _login_page(blob, tx, "Sign-in did not complete. Try again.")
        return finish(tx, refresh)

    return sdk + [
        Route("/.well-known/oauth-authorization-server", as_metadata, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server/mcp", as_metadata, methods=["GET"]),
        Route("/.well-known/openid-configuration", as_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", pr_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", pr_metadata, methods=["GET"]),
        Route("/oauth/login", login_get, methods=["GET"]),
        Route("/oauth/login", login_post, methods=["POST"]),
        Route("/oauth/callback", callback, methods=["GET"]),
    ]
