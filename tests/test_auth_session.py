"""SessionManager over both sign-in protocols (legacy /idp/* and OIDC).

The oidc wire functions are replaced with fakes; nothing touches the network
except the callback tests, which talk to the real loopback server.
"""

import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from tclauncher import auth, oidc
from tclauncher.auth import LaunchAuth, SessionCheck, SessionManager
from tclauncher.backend import BackendClient
from tclauncher.config import ConfigManager
from tclauncher.oidc import OidcUnavailable, Protocol, TokenRefused, Tokens

ISSUER = "http://issuer"


def _tokens(n: int = 2) -> Tokens:
    return Tokens(access_token=f"a{n}", refresh_token=f"r{n}", expires_in=60, id_token="t")


@pytest.fixture
def config(tmp_path):
    config = ConfigManager(config_file=str(tmp_path / "config.json"))
    config.server_discovery_addr = "http://server"
    return config


def _manager(config, protocol=Protocol.OIDC):
    manager = SessionManager(config, BackendClient(config))
    manager.protocol = protocol
    manager._issuer = ISSUER if protocol is Protocol.OIDC else None
    return manager


def _oidc_session(config, exp=0):
    config.access_token, config.refresh_token, config.exp = "a1", "r1", exp


def _legacy_session(config, exp=None):
    config.session_id, config.refresh_token = "s", "r"
    config.exp = int(time.time()) + 600 if exp is None else exp


def _saved(config):
    reloaded = ConfigManager(config_file=config.config_file)
    reloaded.load()
    return reloaded


# --- protocol detection and stored sessions ---

def test_detect_oidc_drops_legacy_session(config, monkeypatch):
    _legacy_session(config)
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.OIDC, ISSUER))
    manager = SessionManager(config, BackendClient(config))
    assert manager.detect_protocol() is Protocol.OIDC
    assert (config.session_id, config.refresh_token) == ("", "")
    assert _saved(config).session_id == ""
    assert not manager.is_signed_in()


def test_detect_legacy_drops_oidc_session(config, monkeypatch):
    _oidc_session(config)
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.LEGACY, None))
    manager = SessionManager(config, BackendClient(config))
    assert manager.detect_protocol() is Protocol.LEGACY
    assert (config.access_token, config.refresh_token) == ("", "")
    assert _saved(config).access_token == ""


def test_detect_keeps_a_matching_session(config, monkeypatch):
    _oidc_session(config)
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.OIDC, ISSUER))
    manager = SessionManager(config, BackendClient(config))
    manager.detect_protocol()
    assert config.refresh_token == "r1"


def test_unknown_protocol_keeps_stored_session(config, monkeypatch):
    _oidc_session(config)
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.UNKNOWN, None))
    manager = SessionManager(config, BackendClient(config))
    assert manager.detect_protocol() is Protocol.UNKNOWN
    assert config.refresh_token == "r1"
    assert manager.is_signed_in()


def test_oidc_signed_in_means_refresh_token(config):
    manager = _manager(config)
    _oidc_session(config, exp=0)
    assert manager.is_signed_in()
    config.refresh_token = ""
    assert not manager.is_signed_in()


def test_legacy_signed_in_rule_is_unchanged(config):
    manager = _manager(config, Protocol.LEGACY)
    _legacy_session(config)
    assert manager.is_signed_in()
    config.exp = 0
    assert not manager.is_signed_in()
    assert manager.has_refresh_token()


def test_invalidate_clears_both_kinds(config):
    manager = _manager(config)
    config.session_id, config.access_token, config.refresh_token, config.exp = "s", "a", "r", 9
    manager.invalidate_session()
    assert (config.session_id, config.access_token, config.refresh_token, config.exp) == ("", "", "", 0)


# --- validate ---

def test_validate_refused_and_unreachable(config, monkeypatch):
    manager = _manager(config)
    _oidc_session(config)

    def refused(*a, **k):
        raise TokenRefused("gone")

    def down(*a, **k):
        raise OidcUnavailable("down")

    monkeypatch.setattr(oidc, "refresh", refused)
    assert manager.validate() is SessionCheck.REFUSED
    monkeypatch.setattr(oidc, "refresh", down)
    assert manager.validate() is SessionCheck.UNREACHABLE
    assert (config.access_token, config.refresh_token) == ("a1", "r1")


def test_validate_success_stores_rotated_tokens_without_attesting(config, monkeypatch):
    manager = _manager(config)
    _oidc_session(config)
    config.session_id = "stale"
    seen = {}

    def fake_refresh(session, base, *, refresh_token, issuer):
        seen.update(refresh_token=refresh_token, issuer=issuer, base=base)
        return _tokens()

    attested = []
    monkeypatch.setattr(oidc, "refresh", fake_refresh)
    monkeypatch.setattr(oidc, "attest", lambda *a, **k: attested.append(k))
    before = int(time.time())
    assert manager.validate() is SessionCheck.VALID
    assert seen == {"refresh_token": "r1", "issuer": ISSUER, "base": "http://server"}
    assert (config.access_token, config.refresh_token, config.session_id) == ("a2", "r2", "")
    assert before + 60 <= config.exp <= int(time.time()) + 60
    assert _saved(config).refresh_token == "r2"
    assert attested == []


def test_validate_unknown_protocol_is_unreachable(config):
    assert _manager(config, Protocol.UNKNOWN).validate() is SessionCheck.UNREACHABLE


# --- prepare_launch ---

def test_prepare_launch_outcomes(config, monkeypatch):
    manager = _manager(config)
    attested = []

    def attest_ok(session, base, *, access_token, digest):
        attested.append((access_token, digest))

    def raise_(exc):
        def f(*a, **k):
            raise exc
        return f

    _oidc_session(config)
    monkeypatch.setattr(oidc, "refresh", raise_(TokenRefused("gone")))
    assert manager.prepare_launch("d") is LaunchAuth.SESSION_REFUSED

    _oidc_session(config)
    monkeypatch.setattr(oidc, "refresh", raise_(OidcUnavailable("down")))
    assert manager.prepare_launch("d") is LaunchAuth.AUTH_UNREACHABLE
    assert config.refresh_token == "r1"

    monkeypatch.setattr(oidc, "refresh", lambda *a, **k: _tokens())
    monkeypatch.setattr(oidc, "attest", raise_(OidcUnavailable("401")))
    assert manager.prepare_launch("d") is LaunchAuth.ATTESTATION_FAILED
    assert config.refresh_token == "r2"

    monkeypatch.setattr(oidc, "refresh", lambda *a, **k: _tokens(3))
    monkeypatch.setattr(oidc, "attest", attest_ok)
    assert manager.prepare_launch("d") is LaunchAuth.OK
    assert attested == [("a3", "d")]


def test_prepare_launch_redetects_an_unknown_protocol(config, monkeypatch):
    manager = _manager(config, Protocol.UNKNOWN)
    _oidc_session(config)
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.UNKNOWN, None))
    assert manager.prepare_launch("d") is LaunchAuth.AUTH_UNREACHABLE

    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.OIDC, ISSUER))
    monkeypatch.setattr(oidc, "refresh", lambda *a, **k: _tokens())
    monkeypatch.setattr(oidc, "attest", lambda *a, **k: None)
    assert manager.prepare_launch("d") is LaunchAuth.OK


def test_legacy_paths_unchanged(config, monkeypatch):
    manager = _manager(config, Protocol.LEGACY)
    _legacy_session(config)
    calls = []
    monkeypatch.setattr(manager.backend, "check_session", lambda: calls.append("check") or False)
    monkeypatch.setattr(manager.backend, "refresh_session", lambda: calls.append("refresh") or True)
    assert manager.prepare_launch("d") is LaunchAuth.OK
    assert manager.validate() is SessionCheck.VALID
    assert calls == ["check", "refresh", "check", "refresh"]
    monkeypatch.setattr(manager.backend, "refresh_session", lambda: False)
    assert manager.prepare_launch("d") is LaunchAuth.SESSION_REFUSED
    assert manager.validate() is SessionCheck.REFUSED


# --- keep-alive refresh ---

def test_refresh_if_due_respects_margin(config, monkeypatch):
    manager = _manager(config)
    calls = []
    monkeypatch.setattr(oidc, "refresh", lambda *a, **k: calls.append("refresh") or _tokens())
    monkeypatch.setattr(oidc, "attest",
                        lambda session, base, *, access_token, digest: calls.append(("attest", digest)))

    _oidc_session(config, exp=int(time.time()) + 30)
    assert manager.refresh_if_due("d") is True
    assert calls == []

    _oidc_session(config, exp=int(time.time()) + 20)
    assert manager.refresh_if_due("d") is True
    assert calls == ["refresh", ("attest", "d")]


def test_refresh_if_due_refused_clears_session(config, monkeypatch):
    manager = _manager(config)
    _oidc_session(config, exp=0)

    def refused(*a, **k):
        raise TokenRefused("gone")

    monkeypatch.setattr(oidc, "refresh", refused)
    assert manager.refresh_if_due("d") is False
    assert config.refresh_token == ""
    assert _saved(config).refresh_token == ""


def test_refresh_if_due_survives_outages(config, monkeypatch):
    manager = _manager(config)
    _oidc_session(config, exp=0)

    def down(*a, **k):
        raise OidcUnavailable("down")

    monkeypatch.setattr(oidc, "refresh", down)
    assert manager.refresh_if_due("d") is True
    monkeypatch.setattr(oidc, "refresh", lambda *a, **k: _tokens())
    monkeypatch.setattr(oidc, "attest", down)
    assert manager.refresh_if_due("d") is True
    assert config.refresh_token == "r2"


def test_refresh_if_due_is_a_no_op_on_legacy(config, monkeypatch):
    manager = _manager(config, Protocol.LEGACY)
    _legacy_session(config, exp=0)
    monkeypatch.setattr(oidc, "refresh", lambda *a, **k: pytest.fail("no OIDC on legacy"))
    assert manager.refresh_if_due("d") is True


def test_concurrent_refreshes_spend_the_token_once(config, monkeypatch):
    manager = _manager(config)
    _oidc_session(config, exp=0)
    spent = []
    counter = iter(range(2, 100))

    def fake_refresh(session, base, *, refresh_token, issuer):
        if refresh_token in spent:
            raise TokenRefused("reused")
        spent.append(refresh_token)
        time.sleep(0.05)
        return _tokens(next(counter))

    monkeypatch.setattr(oidc, "refresh", fake_refresh)
    monkeypatch.setattr(oidc, "attest", lambda *a, **k: None)
    results = []
    threads = [threading.Thread(target=lambda: results.append(manager.refresh_if_due("d")))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True, True]
    assert spent == ["r1"]
    assert config.refresh_token == "r2"


# --- revoke ---

def test_revoke_only_on_oidc(config, monkeypatch):
    revoked = []
    monkeypatch.setattr(oidc, "revoke", lambda session, base, *, token: revoked.append(token))
    _manager(config).revoke("r1")
    _manager(config, Protocol.LEGACY).revoke("r1")
    _manager(config).revoke("")
    assert revoked == ["r1"]


# --- the OIDC browser callback (real loopback server) ---

@pytest.fixture
def login(config, monkeypatch):
    """Start an OIDC sign-in; returns (manager, authorize-URL query, events)."""
    opened = []
    monkeypatch.setattr(auth, "open_url", opened.append)
    events = []
    manager = _manager(config)
    manager.initiate_login(lambda: events.append("signed_in"))
    query = {k: v[0] for k, v in parse_qs(urlparse(opened[0]).query).items()}
    yield manager, query, events
    manager._shutdown_server()


def test_oidc_redirect_uses_ip_literal(login):
    manager, query, _ = login
    redirect = urlparse(query["redirect_uri"])
    assert redirect.hostname == "127.0.0.1" and redirect.path == "/auth_result"
    assert query["redirect_uri"] == manager.redirect_url


def test_callback_foreign_state_keeps_attempt(login, config, monkeypatch):
    manager, query, events = login
    exchanged = []

    def fake_exchange(session, base, *, code, verifier, redirect_uri, issuer):
        exchanged.append((code, redirect_uri, issuer))
        return _tokens()

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    url = query["redirect_uri"]
    wrong = requests.get(url, params={"code": "c", "state": "not-it"}, timeout=5)
    assert wrong.status_code == 400 and exchanged == []
    right = requests.get(url, params={"code": "c", "state": query["state"]}, timeout=5)
    assert right.status_code == 200
    assert exchanged == [("c", url, ISSUER)]
    assert events == ["signed_in"]
    assert (config.access_token, config.refresh_token) == ("a2", "r2")


def test_callback_error_is_escaped_and_closes_attempt(login, monkeypatch):
    manager, query, events = login
    monkeypatch.setattr(oidc, "exchange_code", lambda *a, **k: pytest.fail("no exchange on error"))
    res = requests.get(query["redirect_uri"],
                       params={"error": "access_denied", "error_description": "<b>no</b>",
                               "state": query["state"]}, timeout=5)
    assert res.status_code == 400
    assert "&lt;b&gt;no&lt;/b&gt;" in res.text and "<b>no</b>" not in res.text
    assert manager._state is None and events == []


def test_failed_exchange_shows_reason(login, monkeypatch):
    manager, query, events = login

    def refused(*a, **k):
        raise TokenRefused("bad <code>")

    monkeypatch.setattr(oidc, "exchange_code", refused)
    res = requests.get(query["redirect_uri"], params={"code": "c", "state": query["state"]}, timeout=5)
    assert res.status_code == 400 and "bad &lt;code&gt;" in res.text
    assert events == []


def test_initiate_login_on_unknown_protocol_raises(config):
    with pytest.raises(RuntimeError):
        _manager(config, Protocol.UNKNOWN).initiate_login(lambda: None)


def test_switching_protocol_restarts_the_callback_server(config, monkeypatch):
    opened = []
    monkeypatch.setattr(auth, "open_url", opened.append)
    manager = _manager(config, Protocol.LEGACY)
    manager.initiate_login(lambda: None)
    assert manager.redirect_url.startswith("http://localhost:")
    manager.protocol, manager._issuer = Protocol.OIDC, ISSUER
    manager.initiate_login(lambda: None)
    assert manager.redirect_url.startswith("http://127.0.0.1:")
    manager.protocol = Protocol.LEGACY
    manager.initiate_login(lambda: None)
    assert manager.redirect_url.startswith("http://localhost:")
    assert "launcher_redirect=http://localhost:" in opened[-1]
    manager._shutdown_server()


# --- the server flips protocol while the launcher is open ---

def test_legacy_refusal_after_a_flip_signs_out_quietly(config, monkeypatch):
    manager = _manager(config, Protocol.LEGACY)
    _legacy_session(config)
    monkeypatch.setattr(manager.backend, "check_session", lambda: False)
    monkeypatch.setattr(manager.backend, "refresh_session", lambda: False)
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.OIDC, ISSUER))
    assert manager.prepare_launch("d") is LaunchAuth.SIGNED_OUT
    assert manager.protocol is Protocol.OIDC
    assert config.session_id == ""


def test_legacy_refusal_on_a_legacy_server_is_still_refused(config, monkeypatch):
    manager = _manager(config, Protocol.LEGACY)
    _legacy_session(config)
    monkeypatch.setattr(manager.backend, "check_session", lambda: False)
    monkeypatch.setattr(manager.backend, "refresh_session", lambda: False)
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.LEGACY, None))
    assert manager.prepare_launch("d") is LaunchAuth.SESSION_REFUSED


def test_reprobe_that_drops_a_legacy_session_signs_out_quietly(config, monkeypatch):
    manager = _manager(config, Protocol.UNKNOWN)
    _legacy_session(config)
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.OIDC, ISSUER))
    monkeypatch.setattr(oidc, "refresh", lambda *a, **k: pytest.fail("nothing to refresh"))
    assert manager.prepare_launch("d") is LaunchAuth.SIGNED_OUT


# --- identity (steam_id + name) ---

STEAM_ID = "76561198000000000"


def _id_token(**claims):
    import base64
    import json
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{body}.sig"


def _tokens_with_identity(n=2, steam_id=STEAM_ID, name="Armin"):
    return Tokens(access_token=f"a{n}", refresh_token=f"r{n}", expires_in=60,
                  id_token=_id_token(steam_id=steam_id, name=name))


def test_store_sets_identity(config):
    _manager(config)._store(_tokens_with_identity())
    assert (config.steam_id, config.steam_name) == (STEAM_ID, "Armin")
    assert (_saved(config).steam_id, _saved(config).steam_name) == (STEAM_ID, "Armin")


def test_refresh_without_id_token_keeps_identity(config):
    manager = _manager(config)
    manager._store(_tokens_with_identity())
    manager._store(Tokens(access_token="a3", refresh_token="r3", expires_in=60, id_token=""))
    assert (config.steam_id, config.steam_name) == (STEAM_ID, "Armin")


def test_invalidate_clears_identity(config):
    manager = _manager(config)
    manager._store(_tokens_with_identity())
    manager.invalidate_session()
    assert (config.steam_id, config.steam_name) == ("", "")


def test_legacy_login_clears_identity(config, monkeypatch):
    config.steam_id, config.steam_name = STEAM_ID, "Armin"
    manager = _manager(config, Protocol.LEGACY)
    monkeypatch.setattr(manager.backend, "exchange_code",
                        lambda code: {"success": True, "session_id": "s", "refresh_token": "r", "exp": 1})
    monkeypatch.setattr(manager, "_shutdown_server", lambda: None)
    ok, _ = manager._complete_login("c")
    assert ok
    assert (config.steam_id, config.steam_name) == ("", "")


def test_probe_drop_clears_identity(config, monkeypatch):
    _oidc_session(config)
    config.steam_id, config.steam_name = STEAM_ID, "Armin"
    monkeypatch.setattr(oidc, "probe", lambda s, base: (Protocol.LEGACY, None))
    SessionManager(config, BackendClient(config)).detect_protocol()
    assert (_saved(config).steam_id, _saved(config).steam_name) == ("", "")
