"""Steam sign-in, over whichever protocol the server speaks.

Legacy servers: the browser is sent to {discovery}/steam/openid/login with a
redirect back to a short-lived local HTTP server, which exchanges the returned
code for a session via the backend's /idp endpoints.

New servers (../prospect-og HEAD): OAuth 2.0 / OIDC with PKCE through the same
loopback server, plus the mod attestation; the wire protocol is in oidc.py.

detect_protocol() picks one per server by probing for an OIDC discovery
document. Which protocol a stored session belongs to is inferred from the
config: a session_id is legacy, an access_token is OIDC.
"""

import hmac
import html
import logging
import threading
import time
from enum import Enum
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from . import oidc
from .backend import BackendClient
from .config import ConfigManager
from .desktop import open_url
from .oidc import OidcError, OidcUnavailable, Protocol, TokenRefused, Tokens

logger = logging.getLogger(__name__)

SESSION_REQUEST_FAIL = """<html>
    <head><title>The Cycle Launcher authentication</title></head>
    <body>
        <h1>Login failed!</h1>
        <p>Reason: %s</p>
    </body>
</html>
"""
SESSION_REQUEST_SUCCESS = """<html>
    <head><title>The Cycle Launcher authentication</title></head>
    <body>
        <h1>Login complete!</h1>
        <p>You may close this browser tab.</p>
    </body>
</html>
"""


class SessionCheck(Enum):
    VALID = "valid"
    REFUSED = "refused"          # the server ended the session; sign in again
    UNREACHABLE = "unreachable"  # could not tell; keep the session


class LaunchAuth(Enum):
    OK = "ok"
    SESSION_REFUSED = "session_refused"
    AUTH_UNREACHABLE = "auth_unreachable"
    ATTESTATION_FAILED = "attestation_failed"
    # The server switched protocol and the stored session was dropped: show
    # "Log in with Steam" quietly, no "session expired" dialog.
    SIGNED_OUT = "signed_out"


class _CallbackHandler(BaseHTTPRequestHandler):
    session_manager: "SessionManager" = None

    def log_message(self, format, *args):
        logger.info("auth callback: " + format % args)

    def _respond(self, status: int, body: str):
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != oidc.CALLBACK_PATH:
            self._respond(404, SESSION_REQUEST_FAIL % "Unknown path.")
            return
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if self.session_manager.protocol is Protocol.OIDC:
            try:
                status, body = self.session_manager._complete_oidc_login(query)
            except Exception as e:
                logger.exception(e)
                status, body = 400, SESSION_REQUEST_FAIL % "Unexpected error occurred."
            self._respond(status, body)
            return
        if "code" not in query:
            self._respond(400, SESSION_REQUEST_FAIL % "Missing 'code' query parameter.")
            return
        try:
            success, message = self.session_manager._complete_login(query["code"])
        except Exception as e:
            logger.exception(e)
            success, message = False, "Unexpected error occurred."
        if success:
            self._respond(200, SESSION_REQUEST_SUCCESS)
        else:
            self._respond(400, SESSION_REQUEST_FAIL % message)


class SessionManager:
    def __init__(self, config: ConfigManager, backend: BackendClient):
        self.config = config
        self.backend = backend
        self.server: HTTPServer | None = None
        self._server_host: str | None = None
        self.redirect_url: str | None = None
        self.on_successful_login: Callable | None = None
        self.protocol = Protocol.UNKNOWN
        self._issuer: str | None = None
        # This OIDC attempt's state and PKCE verifier; the callback clears both.
        self._state: str | None = None
        self._verifier: str | None = None
        # The refresh token rotates on every use: two refreshes at once would
        # spend it twice, and the server signs the player out on the second.
        self._lock = threading.Lock()

    def _base(self) -> str:
        if not self.config.server_discovery_addr:
            raise RuntimeError("Server discovery address not specified")
        return self.config.server_discovery_addr.rstrip("/")

    # --- protocol ---

    def detect_protocol(self) -> Protocol:
        """Probe the server (blocking). Drops a stored session of the other kind.

        UNKNOWN (server unreachable) changes nothing, so an outage never wipes
        a session.
        """
        if not self.config.server_discovery_addr:
            return self.protocol
        protocol, issuer = oidc.probe(self.backend.session, self._base())
        if protocol is Protocol.UNKNOWN:
            return self.protocol
        self.protocol, self._issuer = protocol, issuer
        kind = self._stored_kind()
        if kind is not None and kind is not protocol:
            # The one quiet re-login after a server switches protocol.
            logger.info("Dropping a stored %s session on a %s server", kind.value, protocol.value)
            self.invalidate_session()
            self.config.save()
        return self.protocol

    def _stored_kind(self) -> Protocol | None:
        if self.config.access_token:
            return Protocol.OIDC
        if self.config.session_id:
            return Protocol.LEGACY
        return None

    # --- login ---

    def initiate_login(self, on_successful_login: Callable):
        """Open the sign-in page in the browser; on_successful_login is
        called from the callback-server thread once the session is saved."""
        self.on_successful_login = on_successful_login
        if self.protocol is Protocol.OIDC:
            self._ensure_server("127.0.0.1")
            self._verifier, challenge = oidc.pkce_pair()
            self._state = oidc.new_state()
            open_url(oidc.authorize_url(self._base(), redirect_uri=self.redirect_url,
                                        state=self._state, challenge=challenge))
        elif self.protocol is Protocol.LEGACY:
            self._ensure_server("localhost")
            open_url(
                f"{self.config.server_discovery_addr}/steam/openid/login?launcher_redirect={self.redirect_url}"
            )
        else:
            raise RuntimeError("Sign-in protocol not known yet; detect_protocol() first")

    def _ensure_server(self, host: str):
        # RFC 8252: the OIDC redirect uses the IP literal; the legacy flow keeps
        # the localhost URL it always sent.
        if self.server is not None and self._server_host != host:
            self._shutdown_server()
        if self.server is None:
            self._start_callback_server(host)

    def _start_callback_server(self, host: str):
        handler = type("BoundHandler", (_CallbackHandler,), {"session_manager": self})
        self.server = HTTPServer((host, 0), handler)
        self._server_host = host
        port = self.server.server_address[1]
        self.redirect_url = f"http://{host}:{port}{oidc.CALLBACK_PATH}"
        logger.info(f"Auth callback server on {self.redirect_url}")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _shutdown_server(self):
        if self.server is not None:
            server = self.server
            self.server = None
            self.redirect_url = None
            threading.Thread(target=server.shutdown, daemon=True).start()

    def _complete_login(self, code: str) -> tuple[bool, str]:
        data = self.backend.exchange_code(code)
        if not data.get("success"):
            return False, data.get("message", "Unknown error")
        self.config.session_id = data["session_id"]
        self.config.access_token = ""
        # The legacy protocol never says who signed in.
        self.config.steam_id = self.config.steam_name = ""
        self.config.refresh_token = data["refresh_token"]
        self.config.exp = data["exp"]
        self.config.save()
        self._shutdown_server()
        if self.on_successful_login is not None:
            self.on_successful_login()
        return True, ""

    def _complete_oidc_login(self, query: dict[str, str]) -> tuple[int, str]:
        """Handle the browser's return; returns (HTTP status, page)."""
        # A callback must carry this attempt's state before anything in it is
        # trusted (RFC 8252 8.9). A foreign one leaves the attempt open.
        if self._state is None or not hmac.compare_digest(query.get("state", ""), self._state):
            return 400, SESSION_REQUEST_FAIL % "The sign-in response did not match the request."
        verifier, redirect_uri = self._verifier, self.redirect_url
        self._state = self._verifier = None
        self._shutdown_server()
        if "error" in query:
            reason = query.get("error_description") or query["error"]
            return 400, SESSION_REQUEST_FAIL % html.escape(reason)
        if "code" not in query:
            return 400, SESSION_REQUEST_FAIL % "Missing 'code' query parameter."
        try:
            tokens = oidc.exchange_code(self.backend.session, self._base(), code=query["code"],
                                        verifier=verifier or "", redirect_uri=redirect_uri or "",
                                        issuer=self._issuer or "")
        except OidcError as e:
            logger.warning("Sign-in failed: %s", e.message)
            return 400, SESSION_REQUEST_FAIL % html.escape(e.message)
        self._store(tokens)
        if self.on_successful_login is not None:
            self.on_successful_login()
        return 200, SESSION_REQUEST_SUCCESS

    # --- session state ---

    def has_active_session(self) -> bool:
        return self.config.session_id != "" and self.config.exp > int(time.time())

    def has_refresh_token(self) -> bool:
        return self.config.refresh_token != ""

    def is_signed_in(self) -> bool:
        """An OIDC session lives 60 s, so there "signed in" means a refresh
        token to renew it with; a legacy session keeps its old rule."""
        if self._stored_kind() is Protocol.OIDC:
            return self.has_refresh_token()
        return self.has_active_session()

    def is_valid_backend_session(self) -> bool:
        """Legacy only."""
        return self.backend.check_session() or self.backend.refresh_session()

    def invalidate_session(self):
        self.config.session_id = ""
        self.config.access_token = ""
        self.config.refresh_token = ""
        self.config.exp = 0
        self.config.steam_id = ""
        self.config.steam_name = ""

    def _store(self, tokens: Tokens):
        self.config.access_token = tokens.access_token
        self.config.refresh_token = tokens.refresh_token
        self.config.exp = int(time.time()) + tokens.expires_in
        self.config.session_id = ""
        # A refresh answer may come without an ID token: keep the last identity.
        steam_id, name = oidc.identity(tokens.id_token)
        if steam_id:
            self.config.steam_id, self.config.steam_name = steam_id, name
        self.config.save()

    def _refresh(self):
        """Run the refresh grant and store the answer. Caller holds _lock."""
        if not self.config.refresh_token:
            raise TokenRefused("No refresh token stored.")
        self._store(oidc.refresh(self.backend.session, self._base(),
                                 refresh_token=self.config.refresh_token,
                                 issuer=self._issuer or ""))

    # --- checks ---

    def validate(self) -> SessionCheck:
        """Is the stored session still good? Blocking. Under OIDC this
        refreshes but does not attest: the mod list may not be loaded yet,
        and Play attests anyway."""
        if self.protocol is Protocol.LEGACY:
            return SessionCheck.VALID if self.is_valid_backend_session() else SessionCheck.REFUSED
        if self.protocol is not Protocol.OIDC:
            return SessionCheck.UNREACHABLE
        with self._lock:
            try:
                self._refresh()
            except TokenRefused as e:
                logger.warning("Session refused: %s", e.message)
                return SessionCheck.REFUSED
            except OidcUnavailable as e:
                logger.warning("Session check deferred: %s", e.message)
                return SessionCheck.UNREACHABLE
        return SessionCheck.VALID

    def prepare_launch(self, digest: str) -> LaunchAuth:
        """Right before starting the game: refresh, then report the mods.
        Blocking. A server whose protocol is still unknown is probed again."""
        if self.protocol is Protocol.UNKNOWN:
            self.detect_protocol()
        if self.protocol is Protocol.LEGACY:
            if self.is_valid_backend_session():
                return LaunchAuth.OK
            # A launcher left open across the server's switch still thinks
            # legacy; the refusal is then the deleted /idp/* endpoints.
            if self.detect_protocol() is Protocol.OIDC:
                return LaunchAuth.SIGNED_OUT
            return LaunchAuth.SESSION_REFUSED
        if self.protocol is not Protocol.OIDC:
            return LaunchAuth.AUTH_UNREACHABLE
        if not self.has_refresh_token():
            # Nothing to refresh: typically a legacy session the probe just dropped.
            return LaunchAuth.SIGNED_OUT
        with self._lock:
            try:
                self._refresh()
            except TokenRefused as e:
                logger.warning("Session refused: %s", e.message)
                return LaunchAuth.SESSION_REFUSED
            except OidcUnavailable as e:
                logger.warning("Session refresh failed: %s", e.message)
                return LaunchAuth.AUTH_UNREACHABLE
            try:
                oidc.attest(self.backend.session, self._base(),
                            access_token=self.config.access_token, digest=digest)
            except OidcUnavailable as e:
                logger.warning("Mod report failed: %s", e.message)
                return LaunchAuth.ATTESTATION_FAILED
        return LaunchAuth.OK

    def refresh_if_due(self, digest: str, margin: int = 20) -> bool:
        """Keep-alive while the game runs: refresh and re-attest when the
        session has `margin` seconds or less left. Returns False only when the
        server refused the session (which is then cleared)."""
        if self.protocol is not Protocol.OIDC:
            return True
        with self._lock:
            # Re-checked under the lock: whoever waited behind a refresh finds
            # the session renewed and skips, instead of spending the old token.
            if self.config.exp - time.time() > margin:
                return True
            try:
                self._refresh()
            except TokenRefused as e:
                logger.warning("Session refused during play: %s", e.message)
                self.invalidate_session()
                self.config.save()
                return False
            except OidcUnavailable as e:
                logger.warning("Keep-alive refresh failed: %s", e.message)
                return True
            try:
                oidc.attest(self.backend.session, self._base(),
                            access_token=self.config.access_token, digest=digest)
            except OidcUnavailable as e:
                logger.warning("Keep-alive mod report failed: %s", e.message)
        return True

    def revoke(self, refresh_token: str) -> None:
        """End the session server-side on log out. Blocking, best effort."""
        if self.protocol is Protocol.OIDC and refresh_token:
            oidc.revoke(self.backend.session, self._base(), token=refresh_token)


class SessionKeepAlive:
    """Keeps an OIDC session alive from Play until the game exits.

    The server ends the launcher's session 60 s after its last refresh, and the
    game logs in with it only once it has started -- under Proton, especially
    on a first run, that is often later than 60 s. A refresh renews the same
    server session in place, so refreshing before each expiry means the game
    finds a live session whenever it logs in.

    A daemon thread, like the game watcher and the presence tailer (never
    run_worker's pool: a session-long loop would starve it). It must never
    stop or delay the game: every failure is logged and retried next tick;
    only a refused session ends the loop.
    """

    def __init__(self, session_manager: "SessionManager", digest: str, *,
                 interval: float = 5.0, margin: int = 20):
        self.session_manager = session_manager
        self.digest = digest
        self.interval = interval
        self.margin = margin
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> bool:
        return self.session_manager.refresh_if_due(self.digest, self.margin)

    def _run(self):
        while not self._stop.is_set():
            try:
                if not self.tick():
                    logger.info("Keep-alive stopped: the session was refused")
                    return
            except Exception as e:
                logger.exception(e)
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="session-keepalive", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()
