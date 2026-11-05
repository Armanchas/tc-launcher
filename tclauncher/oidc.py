"""The new server sign-in: OAuth 2.0 / OIDC with PKCE, plus the mod attestation.

UI-free wire protocol only; when to call what is SessionManager's job
(auth.py). The reference client is the server repo's own launcher:
../prospect-og/authkit/authkit/browser.py and launcher/session.py, and the
protocol is described in ../prospect-og/docs/auth.md.

Every call takes the discovery address as `base` and uses fixed paths, as the
reference does. The token and revocation endpoints read form bodies, never
JSON.
"""

import base64
import binascii
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode

import requests

from .backend import REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

CLIENT_ID = "launcher"
SCOPES = "openid profile offline_access"
CALLBACK_PATH = "/auth_result"
#: Seconds of clock drift allowed when checking an ID token's exp and iat.
CLOCK_SKEW = 60


class Protocol(Enum):
    LEGACY = "legacy"
    OIDC = "oidc"
    UNKNOWN = "unknown"


class OidcError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TokenRefused(OidcError):
    """The session is gone for good: invalid_grant, or an unacceptable ID token."""


class OidcUnavailable(OidcError):
    """Anything else: network, timeout, 5xx, other OAuth errors, a malformed answer.
    The session may still be good; try again later."""


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int
    id_token: str


def _url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


#: Answers that mean "no discovery document here": a legacy server.
_NOT_FOUND = {404, 405, 410}


def probe(session: requests.Session, base: str) -> tuple[Protocol, str | None]:
    """Which sign-in the server speaks, and its issuer when it is OIDC.

    LEGACY needs a server that answered and served no discovery document: a
    404/405/410, or a 200 that is not one. The live legacy server sits behind
    Cloudflare and answers 200 with an empty body for every path, so it never
    says 404. Everything else -- a timeout, a 5xx, 408, 429, and 401/403 (a
    firewall or Cloudflare refusing us) -- is UNKNOWN: a server that is down
    or blocking must never be mistaken for an old one.
    """
    try:
        res = session.get(_url(base, "/.well-known/openid-configuration"), timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("Sign-in probe failed: %s", e)
        return Protocol.UNKNOWN, None
    if res.status_code in _NOT_FOUND:
        return Protocol.LEGACY, None
    if res.status_code != 200:
        logger.warning("Sign-in probe answered %s", res.status_code)
        return Protocol.UNKNOWN, None
    try:
        document = res.json()
    except ValueError:
        document = None
    issuer = document.get("issuer") if isinstance(document, dict) else None
    if not isinstance(issuer, str) or not issuer:
        logger.info("Sign-in probe: 200 without a discovery document; legacy server")
        return Protocol.LEGACY, None
    return Protocol.OIDC, issuer


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge), per RFC 7636."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(16)


def authorize_url(base: str, *, redirect_uri: str, state: str, challenge: str) -> str:
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{_url(base, '/oidc/authorize')}?{urlencode(query)}"


def _post_token(session: requests.Session, base: str, form: dict[str, str], issuer: str) -> Tokens:
    try:
        res = session.post(_url(base, "/oidc/token"), data=form, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise OidcUnavailable(f"The sign-in server could not be reached: {e}") from e
    try:
        body = res.json()
    except ValueError:
        body = None
    if res.status_code != 200:
        error = body.get("error", "") if isinstance(body, dict) else ""
        description = body.get("error_description", "") if isinstance(body, dict) else ""
        message = f"The sign-in server answered {res.status_code} {error} {description}".strip()
        if error == "invalid_grant":
            raise TokenRefused(message)
        raise OidcUnavailable(message)
    if not isinstance(body, dict):
        raise OidcUnavailable("The sign-in server sent an unreadable token answer.")
    access_token = body.get("access_token")
    expires_in = body.get("expires_in")
    if not isinstance(access_token, str) or not access_token \
            or not isinstance(expires_in, int) or isinstance(expires_in, bool):
        raise OidcUnavailable("The sign-in server's token answer is missing fields.")
    refresh_token = body.get("refresh_token") or ""
    id_token = body.get("id_token") or ""
    check_id_token(id_token, issuer=issuer)
    return Tokens(access_token=access_token, refresh_token=str(refresh_token),
                  expires_in=expires_in, id_token=str(id_token))


def exchange_code(session: requests.Session, base: str, *, code: str, verifier: str,
                  redirect_uri: str, issuer: str) -> Tokens:
    """Trade the callback's one-time code for the session. The code and verifier are spent."""
    return _post_token(session, base, {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": verifier,
    }, issuer)


def refresh(session: requests.Session, base: str, *, refresh_token: str, issuer: str) -> Tokens:
    """Renew the session. The refresh token is spent; store the answer's."""
    return _post_token(session, base, {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }, issuer)


def revoke(session: requests.Session, base: str, *, token: str) -> None:
    """End a session server-side (RFC 7009). Best effort: never raises."""
    try:
        session.post(_url(base, "/oidc/revoke"), data={"token": token, "client_id": CLIENT_ID},
                     timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logger.warning("Revoking the session failed: %s", e)


def attest(session: requests.Session, base: str, *, access_token: str, digest: str) -> None:
    """Report the installed mods; the game's session token carries the last report."""
    try:
        res = session.put(_url(base, "/launcher/attestation"), json={"mods": digest},
                          headers={"Authorization": f"Bearer {access_token}"},
                          timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise OidcUnavailable(f"The mod report could not be sent: {e}") from e
    if not 200 <= res.status_code < 300:
        raise OidcUnavailable(f"The server refused the mod report ({res.status_code}).")


def _claims(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error) as e:
        raise TokenRefused(f"The sign-in answer carried an unreadable ID token: {e}") from e
    if not isinstance(claims, dict):
        raise TokenRefused("The sign-in answer carried an unreadable ID token.")
    return claims


def check_id_token(id_token: str, *, issuer: str, now: float | None = None) -> None:
    """Accept the ID token per OIDC Core 3.1.3.7, or raise TokenRefused.

    Checks iss, aud, exp and iat (CLOCK_SKEW either way) and that sub is
    present. The signature is not checked, which item 6 allows for a token
    taken straight from the token endpoint -- as the reference launcher does.
    """
    claims = _claims(id_token)
    now = time.time() if now is None else now
    missing = [k for k in ("iss", "sub", "aud", "exp", "iat") if k not in claims]
    if missing:
        raise TokenRefused(f"The ID token lacks {', '.join(missing)}.")
    if claims["iss"] != issuer:
        raise TokenRefused("The ID token names another issuer.")
    aud = claims["aud"]
    if not (aud == CLIENT_ID or (isinstance(aud, list) and CLIENT_ID in aud)):
        raise TokenRefused("The ID token was issued to another client.")
    try:
        exp, iat = float(claims["exp"]), float(claims["iat"])
    except (TypeError, ValueError) as e:
        raise TokenRefused("The ID token's times are unreadable.") from e
    if exp < now - CLOCK_SKEW:
        raise TokenRefused("The ID token has expired.")
    if iat > now + CLOCK_SKEW:
        raise TokenRefused("The ID token was issued in the future.")
