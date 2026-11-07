import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from claude_status.features import REGISTRY, run_enabled
from claude_status.features.auto_compact import PING, AutoCompact, AutoCompactSettings, maybe_compact
from claude_status.features.base import Context, Feature, Settings

TMUX = {"TMUX_PANE": "%1"}
SETTINGS = AutoCompactSettings(enabled=True, tokens=100_000, within=90)


def ctx(tmp_path, **overrides):
    fields = dict(
        session_id="sess-1", total_in=120_000, cache_remaining=60, tool_in_flight=False, background_agents=False,
        awaiting_response=False, last_prompt="hi", env=TMUX, now=1_000_000.0, state_dir=tmp_path,
    )
    return Context(**{**fields, **overrides})


def fire(tmp_path, settings=SETTINGS, typed=False, **overrides):
    spawned = []
    fired = maybe_compact(ctx(tmp_path, **overrides), settings, _spawn=spawned.append, _has_input=lambda env: typed)
    return fired, spawned


def test_fires_and_spawns_terminal_command(tmp_path):
    fired, spawned = fire(tmp_path)
    assert fired
    assert spawned == [["tmux", "send-keys", "-t", "%1", "-l", "/compact", ";", "run-shell", "sleep 0.5", ";",
                        "send-keys", "-t", "%1", "Enter"]]


def typed(spawned):
    return [cmd[5] for cmd in spawned]


def test_token_threshold_picks_compact_or_ping(tmp_path):
    assert typed(fire(tmp_path, session_id="a", total_in=100_000)[1]) == ["/compact"]
    assert typed(fire(tmp_path, session_id="b", total_in=99_999)[1]) == [PING]


def test_window_boundaries(tmp_path):
    assert fire(tmp_path, cache_remaining=90)[0]
    assert not fire(tmp_path, cache_remaining=91)[0]
    assert fire(tmp_path, cache_remaining=1)[0]


def test_never_when_cache_expired_or_unknown(tmp_path):
    assert not fire(tmp_path, cache_remaining=0)[0]
    assert not fire(tmp_path, cache_remaining=None)[0]


def test_not_while_tool_in_flight(tmp_path):
    assert not fire(tmp_path, tool_in_flight=True)[0]


def test_not_while_a_request_awaits_its_reply(tmp_path):
    assert not fire(tmp_path, awaiting_response=True)[0]
    assert not fire(tmp_path, awaiting_response=True, total_in=50_000)[0]
    assert not list(tmp_path.iterdir())


def test_not_while_background_agents_running(tmp_path):
    assert not fire(tmp_path, background_agents=True)[0]
    assert not list(tmp_path.iterdir())


def test_not_while_user_is_typing_and_retries_later(tmp_path):
    fired, spawned = fire(tmp_path, typed=True)
    assert not fired and spawned == []
    assert not list(tmp_path.iterdir())
    assert fire(tmp_path, cache_remaining=55, now=1_000_005.0)[0]


def test_needs_session_id_and_known_terminal(tmp_path):
    assert not fire(tmp_path, session_id="")[0]
    assert not fire(tmp_path, env={})[0]
    assert not list(tmp_path.iterdir())


def test_once_per_cache_expiry(tmp_path):
    assert fire(tmp_path, cache_remaining=60, now=1_000_000.0)[0]
    # later ticks of the same idle period, expiry estimate jittering by a second
    assert not fire(tmp_path, cache_remaining=55, now=1_000_005.0)[0]
    assert not fire(tmp_path, cache_remaining=29, now=1_000_032.0)[0]


def test_fires_again_for_a_new_cache_expiry(tmp_path):
    assert fire(tmp_path, cache_remaining=60, now=1_000_000.0)[0]
    assert fire(tmp_path, cache_remaining=60, now=1_000_000.0 + 3600)[0]


def test_sessions_are_independent(tmp_path):
    assert fire(tmp_path, session_id="a")[0]
    assert fire(tmp_path, session_id="b")[0]


def test_unwritable_marker_means_no_spawn(tmp_path):
    fired, spawned = fire(tmp_path / "missing-dir")
    assert not fired and spawned == []


BELOW = dict(total_in=50_000)


def ping_window(tmp_path, n, settings=SETTINGS, **overrides):
    """Fire in the n-th consecutive cache window (one per hour)."""
    return fire(tmp_path, settings, now=1_000_000.0 + 3600 * n, **{**BELOW, **overrides})


def test_pings_stop_at_the_cap(tmp_path):
    settings = AutoCompactSettings(enabled=True, tokens=100_000, within=90, keep_warm=3)
    assert ping_window(tmp_path, 0, settings, last_prompt="real question")[0]
    assert ping_window(tmp_path, 1, settings, last_prompt=PING)[0]
    assert ping_window(tmp_path, 2, settings, last_prompt=PING)[0]
    assert not ping_window(tmp_path, 3, settings, last_prompt=PING)[0]
    assert not ping_window(tmp_path, 4, settings, last_prompt=PING)[0]


def test_another_prompt_starts_a_new_streak(tmp_path):
    settings = AutoCompactSettings(enabled=True, tokens=100_000, within=90, keep_warm=1)
    assert ping_window(tmp_path, 0, settings)[0]
    assert not ping_window(tmp_path, 1, settings, last_prompt=PING)[0]
    assert ping_window(tmp_path, 2, settings, last_prompt="hi again")[0]
    assert ping_window(tmp_path, 3, settings, last_prompt=None)[0]


def test_keep_warm_zero_disables_pings(tmp_path):
    settings = AutoCompactSettings(enabled=True, tokens=100_000, within=90, keep_warm=0)
    fired, spawned = ping_window(tmp_path, 0, settings)
    assert not fired and spawned == []
    assert not list(tmp_path.iterdir())


def test_ping_once_per_cache_expiry_and_not_while_typing(tmp_path):
    assert not fire(tmp_path, typed=True, **BELOW)[0]
    assert not list(tmp_path.iterdir())
    assert fire(tmp_path, **BELOW)[0]
    assert not fire(tmp_path, cache_remaining=55, now=1_000_005.0, **BELOW)[0]


def test_compact_does_not_count_towards_the_streak(tmp_path):
    assert fire(tmp_path)[0]
    assert not (tmp_path / "claude-keepwarm-sess-1").exists()


def test_feature_registered_and_default_off():
    assert isinstance(REGISTRY["auto_compact"], AutoCompact)
    assert AutoCompactSettings().enabled is False


def test_run_enabled_skips_disabled_features(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(AutoCompact, "run", lambda self, c, s: calls.append(s))
    run_enabled({"auto_compact": AutoCompactSettings(enabled=False)}, ctx(tmp_path))
    assert calls == []
    run_enabled({"auto_compact": SETTINGS}, ctx(tmp_path))
    assert calls == [SETTINGS]


def test_run_enabled_isolates_failures(tmp_path, monkeypatch, capsys):
    class Boom(Feature):
        name = "boom"
        Settings = Settings

        def run(self, c, s):
            raise RuntimeError("kaput")

    seen = []

    class Fine(Feature):
        name = "fine"
        Settings = Settings

        def run(self, c, s):
            seen.append(c)

    monkeypatch.setattr("claude_status.features.REGISTRY", {"boom": Boom(), "fine": Fine()})
    run_enabled({"boom": Settings(enabled=True), "fine": Settings(enabled=True)}, ctx(tmp_path))
    assert len(seen) == 1
    assert "kaput" in capsys.readouterr().err
