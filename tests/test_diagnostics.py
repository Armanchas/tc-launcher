from tclauncher import diagnostics


def test_relevant_env_is_allowlisted_not_a_full_dump():
    env = {"STEAM_COMPAT_CLIENT_INSTALL_PATH": "/steam", "GAMEID": "umu-480",
           "AWS_SECRET_ACCESS_KEY": "hunter2", "HOME": "/home/u"}
    lines = diagnostics.relevant_env(env)
    assert "STEAM_COMPAT_CLIENT_INSTALL_PATH=/steam" in lines
    assert "GAMEID=umu-480" in lines
    blob = " ".join(lines)
    assert "hunter2" not in blob, "allowlist leaked an unrelated secret"
    assert "HOME" not in blob


def test_login_summary_reports_counts_without_names(tmp_path):
    vdf = tmp_path / "config" / "loginusers.vdf"
    vdf.parent.mkdir()
    vdf.write_text(
        '"users"\n{\n"76561198000000001"\n{\n'
        '"AccountName" "secretperson"\n"MostRecent" "1"\n}\n}\n'
    )
    summary = diagnostics.steam_login_summary(str(tmp_path))
    assert "1 account(s)" in summary
    assert "most-recent set: yes" in summary
    assert "secretperson" not in summary, "login summary leaked an account name"


def test_login_summary_warns_about_offline_mode(tmp_path):
    vdf = tmp_path / "config" / "loginusers.vdf"
    vdf.parent.mkdir()
    vdf.write_text('"76561198000000001"\n{\n"WantsOfflineMode" "1"\n}\n')
    assert "WantsOfflineMode" in diagnostics.steam_login_summary(str(tmp_path))


def test_missing_steam_path_is_not_an_error():
    assert "unknown" in diagnostics.steam_login_summary("")


def test_skeleton_includes_platform_lines_and_is_delimited():
    text = diagnostics.format_launch_diagnostics({"GAMEID": "umu-480"}, "/game")
    assert text.startswith("=== launch diagnostics ===")
    assert text.rstrip().endswith("=== end diagnostics ===")
    assert "launcher = TCLauncher" in text
    assert "relevant env:" in text


def test_diagnostics_never_raise_on_junk_input():
    # It is wrapped in a try/except at the call site, but it should not need it.
    assert diagnostics.format_launch_diagnostics({}, "")
