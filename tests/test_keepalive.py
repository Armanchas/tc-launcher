"""SessionKeepAlive: refreshes the OIDC session while the game runs."""

import threading
import time

from tclauncher.auth import SessionKeepAlive


class FakeSessionManager:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.called = threading.Event()

    def refresh_if_due(self, digest, margin=20):
        self.calls.append((digest, margin))
        self.called.set()
        answer = self.answers.pop(0) if self.answers else True
        if isinstance(answer, Exception):
            raise answer
        return answer


def _wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_tick_delegates_with_digest_and_margin():
    fake = FakeSessionManager([True])
    assert SessionKeepAlive(fake, "d").tick() is True
    assert fake.calls == [("d", 20)]


def test_loop_stops_when_refused():
    fake = FakeSessionManager([True, False])
    keepalive = SessionKeepAlive(fake, "d", interval=0.01)
    keepalive.start()
    assert _wait_for(lambda: not keepalive._thread.is_alive())
    time.sleep(0.05)
    assert len(fake.calls) == 2


def test_stop_ends_the_loop_promptly():
    fake = FakeSessionManager([])
    keepalive = SessionKeepAlive(fake, "d", interval=30)
    keepalive.start()
    assert fake.called.wait(1)
    started = time.monotonic()
    keepalive.stop()
    assert _wait_for(lambda: not keepalive._thread.is_alive())
    assert time.monotonic() - started < 1


def test_an_exception_in_tick_does_not_kill_the_loop():
    fake = FakeSessionManager([RuntimeError("boom"), True])
    keepalive = SessionKeepAlive(fake, "d", interval=0.01)
    keepalive.start()
    assert _wait_for(lambda: len(fake.calls) >= 3)
    keepalive.stop()


def test_thread_is_a_daemon():
    keepalive = SessionKeepAlive(FakeSessionManager([]), "d", interval=30)
    keepalive.start()
    assert keepalive._thread.daemon
    keepalive.stop()


def test_is_running_tracks_the_loop():
    fake = FakeSessionManager([True, False])
    keepalive = SessionKeepAlive(fake, "d", interval=0.01)
    assert not keepalive.is_running()
    keepalive.start()
    assert keepalive.is_running() or len(fake.calls) >= 2
    assert _wait_for(lambda: not keepalive.is_running())

    other = SessionKeepAlive(FakeSessionManager([]), "d", interval=30)
    other.start()
    assert other.is_running()
    other.stop()
    assert not other.is_running()
