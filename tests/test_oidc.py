"""The OIDC wire protocol (tclauncher/oidc.py), against a recording fake session."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from tclauncher import oidc
from tclauncher.oidc import OidcUnavailable, Protocol, TokenRefused

BASE = "http://server:40001"
ISSUER = "http://issuer:40001"


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class FakeSession:
    """Records every call; answers with `answer` (a _Resp or an exception)."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def _call(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def get(self, url, **kw):
        return self._call("GET", url, **kw)

    def post(self, url, **kw):
        return self._call("POST", url, **kw)

    def put(self, url, **kw):
        return self._call("PUT", url, **kw)


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def make_id_token(iss=ISSUER, aud="launcher", exp=None, iat=None, sub="player", drop=(), extra=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": sub,
              "exp": now + 60 if exp is None else exp,
              "iat": now if iat is None else iat}
    claims.update(extra or {})
    for key in drop:
        claims.pop(key)
    return f"{_b64({'alg': 'EdDSA'})}.{_b64(claims)}.sig"


def token_answer(**overrides):
    payload = {"access_token": "a1", "refresh_token": "r2", "expires_in": 60,
               "token_type": "Bearer", "id_token": make_id_token()}
    payload.update(overrides)
    return _Resp(200, payload)


# --- probe ---

def test_probe_200_with_issuer_is_oidc():
    s = FakeSession(_Resp(200, {"issuer": ISSUER}))
    assert oidc.probe(s, BASE) == (Protocol.OIDC, ISSUER)
    method, url, kw = s.calls[0]
    assert method == "GET" and url == f"{BASE}/.well-known/openid-configuration"
    assert kw.get("timeout")


@pytest.mark.parametrize("status", [404, 405, 410])
def test_probe_not_found_answers_are_legacy(status):
    assert oidc.probe(FakeSession(_Resp(status)), BASE) == (Protocol.LEGACY, None)


@pytest.mark.parametrize("answer", [
    requests.Timeout(), requests.ConnectionError("down"),
    _Resp(500, {}), _Resp(502), _Resp(503, {}), _Resp(408), _Resp(429),
    # A firewall or Cloudflare refusing us says nothing about the protocol.
    _Resp(401), _Resp(403),
])
def test_probe_unreachable_or_refused_is_unknown(answer):
    assert oidc.probe(FakeSession(answer), BASE) == (Protocol.UNKNOWN, None)


def test_probe_200_without_a_discovery_document_is_legacy():
    """The live legacy server (behind Cloudflare) answers 200 with an empty
    body for every path, so it never says 404. A server that answered and
    served no discovery document does not speak OIDC."""
    assert oidc.probe(FakeSession(_Resp(200, ValueError("empty"))), BASE) == (Protocol.LEGACY, None)
    assert oidc.probe(FakeSession(_Resp(200, {})), BASE) == (Protocol.LEGACY, None)
    assert oidc.probe(FakeSession(_Resp(200, ["not", "an", "object"])), BASE) == (Protocol.LEGACY, None)


# --- PKCE / authorize ---

def test_pkce_pair_is_s256():
    verifier, challenge = oidc.pkce_pair()
    assert len(verifier) == 43
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def test_new_state_is_random():
    assert oidc.new_state() != oidc.new_state()


def test_authorize_url_carries_all_parameters():
    url = oidc.authorize_url(BASE + "/", redirect_uri="http://127.0.0.1:5/auth_result",
                             state="st", challenge="ch")
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{BASE}/oidc/authorize"
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert query == {
        "response_type": "code", "client_id": "launcher",
        "redirect_uri": "http://127.0.0.1:5/auth_result",
        "scope": "openid profile offline_access", "state": "st",
        "code_challenge": "ch", "code_challenge_method": "S256",
    }


# --- token endpoint ---

def test_exchange_code_posts_form():
    s = FakeSession(token_answer())
    tokens = oidc.exchange_code(s, BASE, code="c", verifier="v",
                                redirect_uri="http://127.0.0.1:5/auth_result", issuer=ISSUER)
    method, url, kw = s.calls[0]
    assert method == "POST" and url == f"{BASE}/oidc/token"
    assert kw["data"] == {"grant_type": "authorization_code", "client_id": "launcher",
                          "redirect_uri": "http://127.0.0.1:5/auth_result",
                          "code": "c", "code_verifier": "v"}
    assert "json" not in kw and kw.get("timeout")
    assert tokens == oidc.Tokens(access_token="a1", refresh_token="r2", expires_in=60,
                                 id_token=tokens.id_token)


def test_refresh_posts_form():
    s = FakeSession(token_answer())
    oidc.refresh(s, BASE, refresh_token="r1", issuer=ISSUER)
    _, url, kw = s.calls[0]
    assert url == f"{BASE}/oidc/token"
    assert kw["data"] == {"grant_type": "refresh_token", "client_id": "launcher",
                          "refresh_token": "r1"}


def test_invalid_grant_is_refused():
    s = FakeSession(_Resp(400, {"error": "invalid_grant", "error_description": "gone"}))
    with pytest.raises(TokenRefused):
        oidc.refresh(s, BASE, refresh_token="r", issuer=ISSUER)


@pytest.mark.parametrize("answer", [
    _Resp(400, {"error": "invalid_client"}),
    _Resp(503, ValueError("html")),
    requests.ConnectionError("down"),
])
def test_other_token_errors_are_unavailable(answer):
    with pytest.raises(OidcUnavailable):
        oidc.refresh(FakeSession(answer), BASE, refresh_token="r", issuer=ISSUER)


@pytest.mark.parametrize("answer", [
    _Resp(200, {}),
    _Resp(200, ValueError("html")),
    _Resp(200, {"access_token": "a", "expires_in": "soon", "id_token": make_id_token()}),
])
def test_token_response_missing_fields_is_unavailable(answer):
    with pytest.raises(OidcUnavailable):
        oidc.refresh(FakeSession(answer), BASE, refresh_token="r", issuer=ISSUER)


# --- ID token ---

@pytest.mark.parametrize("token", [
    make_id_token(iss="http://elsewhere"),
    make_id_token(aud="console"),
    make_id_token(aud=["console"]),
    # An hour off, not just past the 60 s skew: these are built at collection
    # time, seconds before the test runs.
    make_id_token(exp=int(time.time()) - 3600),
    make_id_token(iat=int(time.time()) + 3600),
    make_id_token(drop=("sub",)),
    "garbage",
    "",
])
def test_bad_id_token_is_refused(token):
    with pytest.raises(TokenRefused):
        oidc.check_id_token(token, issuer=ISSUER)


def test_good_id_tokens_pass():
    oidc.check_id_token(make_id_token(), issuer=ISSUER)
    oidc.check_id_token(make_id_token(aud=["launcher", "other"]), issuer=ISSUER)
    # Within the 60 s clock skew either way.
    now = int(time.time())
    oidc.check_id_token(make_id_token(exp=now - 30, iat=now + 30), issuer=ISSUER, now=now)
    with pytest.raises(TokenRefused):
        oidc.check_id_token(make_id_token(exp=now - 61), issuer=ISSUER, now=now)
    with pytest.raises(TokenRefused):
        oidc.check_id_token(make_id_token(iat=now + 61), issuer=ISSUER, now=now)


def test_token_answer_with_bad_id_token_is_refused():
    s = FakeSession(token_answer(id_token=make_id_token(iss="http://elsewhere")))
    with pytest.raises(TokenRefused):
        oidc.refresh(s, BASE, refresh_token="r", issuer=ISSUER)


# --- revoke / attest ---

def test_revoke_posts_form_and_swallows_errors():
    s = FakeSession(_Resp(200, {}))
    assert oidc.revoke(s, BASE, token="r") is None
    method, url, kw = s.calls[0]
    assert method == "POST" and url == f"{BASE}/oidc/revoke"
    assert kw["data"] == {"token": "r", "client_id": "launcher"}
    assert oidc.revoke(FakeSession(requests.ConnectionError("down")), BASE, token="r") is None


def test_attest_puts_json_with_bearer():
    s = FakeSession(_Resp(200, {"success": True}))
    oidc.attest(s, BASE, access_token="a", digest="d")
    method, url, kw = s.calls[0]
    assert method == "PUT" and url == f"{BASE}/launcher/attestation"
    assert kw["json"] == {"mods": "d"}
    assert kw["headers"]["Authorization"] == "Bearer a"
    assert kw.get("timeout")


@pytest.mark.parametrize("answer", [_Resp(401, {}), _Resp(500, {}), requests.Timeout()])
def test_attest_failures_are_unavailable(answer):
    with pytest.raises(OidcUnavailable):
        oidc.attest(FakeSession(answer), BASE, access_token="a", digest="d")


# --- identity (steam_id + name from the profile scope) ---

def test_identity_reads_claims():
    token = make_id_token(extra={"steam_id": "76561198000000000", "name": " Armin "})
    assert oidc.identity(token) == ("76561198000000000", "Armin")


def test_identity_int_steam_id_ok():
    token = make_id_token(extra={"steam_id": 76561198000000000, "name": "A"})
    assert oidc.identity(token) == ("76561198000000000", "A")


@pytest.mark.parametrize("bad", ["../../x", "123", "", None, 7656119800000000012, True, ["765611980000000000"]])
def test_identity_rejects_bad_steam_id(bad):
    token = make_id_token(extra={"steam_id": bad, "name": "A"})
    assert oidc.identity(token)[0] == ""


def test_identity_name_is_bounded():
    token = make_id_token(extra={"steam_id": "76561198000000000", "name": "x" * 200})
    assert len(oidc.identity(token)[1]) == 64
    token = make_id_token(extra={"steam_id": "76561198000000000", "name": 5})
    assert oidc.identity(token)[1] == ""


def test_identity_garbage_token():
    assert oidc.identity("x") == ("", "")
    assert oidc.identity("") == ("", "")
