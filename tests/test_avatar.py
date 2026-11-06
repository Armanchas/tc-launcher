"""Steam avatar lookup: local Steam cache, then the public profile, else None."""

import os

import pytest
import requests

from tclauncher import avatar, platforms

STEAM_ID = "76561198000000000"
FULL = "https://avatars.steamstatic.com/abc_full.jpg"
XML = f"<profile><steamID64>{STEAM_ID}</steamID64><avatarFull><![CDATA[{FULL}]]></avatarFull></profile>"


class _Resp:
    def __init__(self, status=200, text="", content=b""):
        self.status_code = status
        self.text = text
        self.content = content


class FakeSession:
    def __init__(self, answers):
        self.answers = dict(answers)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        assert kwargs.get("timeout"), "every request needs a timeout"
        answer = self.answers.get(url.split("?")[0] if "steamcommunity" in url else url)
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else _Resp(404)


def _profile_url():
    return f"https://steamcommunity.com/profiles/{STEAM_ID}"


def _good_session(image=b"\xff\xd8jpeg"):
    return FakeSession({_profile_url(): _Resp(text=XML), FULL: _Resp(content=image)})


def test_rejects_non_numeric_steam_id(tmp_path):
    session = _good_session()
    for bad in ("../../etc", "", "123", "7656119800000000x"):
        assert avatar.resolve_avatar(bad, session=session, steam_dir=str(tmp_path)) is None
    assert session.urls == []


def test_prefers_local_steam_cache(tmp_path):
    cache = tmp_path / "steam" / "config" / "avatarcache"
    cache.mkdir(parents=True)
    (cache / f"{STEAM_ID}.png").write_bytes(b"png")
    session = _good_session()
    path = avatar.resolve_avatar(STEAM_ID, session=session, steam_dir=str(tmp_path / "steam"))
    assert path == str(cache / f"{STEAM_ID}.png")
    assert session.urls == []


def test_steam_cache_avatar_absent(tmp_path):
    assert avatar.steam_cache_avatar(None, STEAM_ID) is None
    assert avatar.steam_cache_avatar(str(tmp_path), STEAM_ID) is None


def test_parse_profile_xml():
    assert avatar.parse_profile_xml(XML) == FULL
    assert avatar.parse_profile_xml(XML.replace("https://", "http://")) is None
    assert avatar.parse_profile_xml("<oops") is None
    assert avatar.parse_profile_xml("<profile/>") is None


def test_fetch_downloads_and_caches(tmp_path):
    session = _good_session()
    path = avatar.fetch_avatar(session, STEAM_ID, dest_dir=str(tmp_path), now=1000.0)
    assert path is not None and open(path, "rb").read() == b"\xff\xd8jpeg"
    os.utime(path, (1000.0, 1000.0))
    calls = len(session.urls)
    assert avatar.fetch_avatar(session, STEAM_ID, dest_dir=str(tmp_path), now=1000.0 + 60) == path
    assert len(session.urls) == calls


def test_fetch_refreshes_when_stale(tmp_path):
    session = _good_session()
    path = avatar.fetch_avatar(session, STEAM_ID, dest_dir=str(tmp_path), now=1000.0)
    os.utime(path, (1000.0, 1000.0))
    session.answers[FULL] = _Resp(content=b"new")
    assert avatar.fetch_avatar(session, STEAM_ID, dest_dir=str(tmp_path),
                               now=1000.0 + avatar.MAX_AGE + 1) == path
    assert open(path, "rb").read() == b"new"


@pytest.mark.parametrize("failure", [
    requests.ConnectionError("down"),
    _Resp(500),
    _Resp(text="<oops"),
])
def test_fetch_failure_returns_stale_or_none(tmp_path, failure):
    session = FakeSession({_profile_url(): failure})
    assert avatar.fetch_avatar(session, STEAM_ID, dest_dir=str(tmp_path), now=1000.0) is None
    stale = tmp_path / f"{STEAM_ID}.img"
    stale.write_bytes(b"old")
    os.utime(stale, (0, 0))
    assert avatar.fetch_avatar(session, STEAM_ID, dest_dir=str(tmp_path),
                               now=avatar.MAX_AGE * 10) == str(stale)


def test_fetch_rejects_oversized_image(tmp_path):
    session = _good_session(image=b"x" * (avatar.MAX_BYTES + 1))
    assert avatar.fetch_avatar(session, STEAM_ID, dest_dir=str(tmp_path), now=1000.0) is None
    assert not (tmp_path / f"{STEAM_ID}.img").exists()


def test_platforms_steam_install_path_dispatches(monkeypatch):
    module = platforms._runner_module()
    monkeypatch.setattr(module, "find_steam_install_path", lambda: "/x/steam")
    assert platforms.steam_install_path() == "/x/steam"
