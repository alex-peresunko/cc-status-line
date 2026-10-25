import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

# Allow importing statusline.py from the project root
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import statusline as sl


# ---------------------------------------------------------------------------
# format_tokens
# ---------------------------------------------------------------------------

def test_format_tokens_zero():
    assert sl.format_tokens(0) == "0"

def test_format_tokens_small():
    assert sl.format_tokens(42) == "42"
    assert sl.format_tokens(999) == "999"

def test_format_tokens_k():
    assert sl.format_tokens(1_000) == "1k"
    assert sl.format_tokens(123_456) == "123k"
    assert sl.format_tokens(999_999) == "999k"

def test_format_tokens_M():
    assert sl.format_tokens(1_000_000) == "1M"
    assert sl.format_tokens(2_400_000) == "2.4M"
    assert sl.format_tokens(1_400_000) == "1.4M"
    assert sl.format_tokens(10_000_000) == "10M"


# ---------------------------------------------------------------------------
# format_countdown / cache_remaining
# ---------------------------------------------------------------------------

def test_format_countdown():
    assert sl.format_countdown(0) == "0:00"
    assert sl.format_countdown(59) == "0:59"
    assert sl.format_countdown(60) == "1:00"
    assert sl.format_countdown(222) == "3:42"
    assert sl.format_countdown(300) == "5:00"


NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def entry(kind, seconds_ago, **extra):
    ts = datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return json.dumps({"type": kind, "timestamp": ts, **extra})


def write_transcript(tmp_path, *lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def remaining(path, ttl=300):
    return sl.cache_remaining(path, ttl, _now=lambda: NOW)


def test_cache_remaining_fresh(tmp_path):
    path = write_transcript(tmp_path, entry("assistant", 100))
    assert remaining(path) == 200


def test_cache_remaining_expired(tmp_path):
    path = write_transcript(tmp_path, entry("assistant", 1000))
    assert remaining(path) == 0


def test_cache_remaining_custom_ttl(tmp_path):
    path = write_transcript(tmp_path, entry("assistant", 100))
    assert remaining(path, ttl=3600) == 3500


def test_cache_remaining_uses_newest_assistant(tmp_path):
    path = write_transcript(tmp_path, entry("assistant", 250), entry("assistant", 50))
    assert remaining(path) == 250


def test_cache_remaining_ignores_sidechain(tmp_path):
    path = write_transcript(
        tmp_path, entry("assistant", 200), entry("assistant", 10, isSidechain=True)
    )
    assert remaining(path) == 100


def test_cache_remaining_ignores_non_assistant_entries(tmp_path):
    path = write_transcript(
        tmp_path, entry("assistant", 200), entry("user", 5), entry("attachment", 1)
    )
    assert remaining(path) == 100


def test_cache_remaining_tolerates_malformed_lines(tmp_path):
    path = write_transcript(tmp_path, entry("assistant", 100), "{not json", '{"type": "assistant"}')
    assert remaining(path) == 200


def test_cache_remaining_none_cases(tmp_path):
    assert remaining("") is None
    assert remaining(str(tmp_path / "missing.jsonl")) is None
    assert remaining(write_transcript(tmp_path, entry("user", 5))) is None


def test_cache_remaining_reads_only_tail(tmp_path):
    filler = entry("user", 0, pad="x" * 1000)
    lines = [entry("assistant", 100)] + [filler] * 400
    assert remaining(write_transcript(tmp_path, *lines)) is None
    lines = [filler] * 400 + [entry("assistant", 100)]
    assert remaining(write_transcript(tmp_path, *lines)) == 200


def test_cache_left_prefers_expires_at(tmp_path):
    path = write_transcript(tmp_path, entry("assistant", 100))
    assert sl.cache_left(NOW + 3000, path, 300, _now=lambda: NOW) == 3000
    assert sl.cache_left(NOW - 5, path, 300, _now=lambda: NOW) == 0


def test_cache_left_falls_back_to_transcript(tmp_path):
    path = write_transcript(tmp_path, entry("assistant", 100))
    assert sl.cache_left(None, path, 300, _now=lambda: NOW) == 200
    assert sl.cache_left(None, "", 300, _now=lambda: NOW) is None


# ---------------------------------------------------------------------------
# tool_in_flight / background_agents_running
# ---------------------------------------------------------------------------

def tool_use(*ids, **extra):
    blocks = [{"type": "tool_use", "id": i, "name": "Bash"} for i in ids]
    return entry("assistant", 10, message={"content": blocks}, **extra)


def tool_result(*ids, **extra):
    blocks = [{"type": "tool_result", "tool_use_id": i} for i in ids]
    return entry("user", 9, message={"content": blocks}, **extra)


def prompt(text="hi"):
    return entry("user", 30, message={"content": text})


def in_flight(tmp_path, *lines):
    return sl.tool_in_flight(write_transcript(tmp_path, *lines))


def test_tool_in_flight_call_without_result(tmp_path):
    assert in_flight(tmp_path, prompt(), tool_use("a"))


def test_tool_in_flight_call_answered(tmp_path):
    assert not in_flight(tmp_path, prompt(), tool_use("a"), tool_result("a"))


def test_tool_in_flight_parallel_calls_need_every_result(tmp_path):
    assert in_flight(tmp_path, prompt(), tool_use("a", "b"), tool_result("a"))
    assert not in_flight(tmp_path, prompt(), tool_use("a", "b"), tool_result("a"), tool_result("b"))


def test_tool_in_flight_idle_after_final_answer(tmp_path):
    text = entry("assistant", 5, message={"content": [{"type": "text", "text": "done"}]})
    assert not in_flight(tmp_path, prompt(), tool_use("a"), tool_result("a"), text)


def test_tool_in_flight_ignores_unanswered_calls_of_earlier_turns(tmp_path):
    assert not in_flight(tmp_path, tool_use("old"), prompt("next"), tool_use("a"), tool_result("a"))


def test_tool_in_flight_ignores_sidechain(tmp_path):
    assert not in_flight(tmp_path, prompt(), tool_use("sub", isSidechain=True))
    assert in_flight(tmp_path, prompt(), tool_use("a"), tool_result("sub", isSidechain=True))


def test_tool_in_flight_ignores_other_entry_types(tmp_path):
    blob = entry("api-request-blob", 1, message={"content": [{"type": "tool_use", "id": "x"}]})
    assert not in_flight(tmp_path, prompt(), blob)


def test_tool_in_flight_unreadable_or_empty(tmp_path):
    assert not sl.tool_in_flight("")
    assert not sl.tool_in_flight(str(tmp_path / "missing.jsonl"))
    assert not in_flight(tmp_path, "not json", "[1, 2]", "")


def launched(agent_id, **extra):
    return entry("user", 20, message={"content": [{"type": "tool_result"}]},
                 toolUseResult={"isAsync": True, "status": "async_launched", "agentId": agent_id}, **extra)


def finished(*agent_ids, **extra):
    body = "".join(f"<task-id>{a}</task-id>\n<status>completed</status>\n" for a in agent_ids)
    return entry("user", 5, message={"content": f"<task-notification>\n{body}</task-notification>"}, **extra)


def agents_running(tmp_path, *lines):
    return sl.background_agents_running(write_transcript(tmp_path, *lines))


def test_background_agent_running_until_notified(tmp_path):
    assert agents_running(tmp_path, prompt(), launched("a"))
    assert not agents_running(tmp_path, prompt(), launched("a"), finished("a"))


def test_background_agents_need_every_notification(tmp_path):
    assert agents_running(tmp_path, launched("a"), launched("b"), finished("a"))
    assert not agents_running(tmp_path, launched("a"), launched("b"), finished("a"), finished("b"))


def test_background_agents_span_turns(tmp_path):
    assert agents_running(tmp_path, prompt(), launched("a"), prompt("next"), tool_use("t"), tool_result("t"))


def test_background_agent_ignores_unrelated_notifications_and_sidechain(tmp_path):
    assert agents_running(tmp_path, launched("a"), finished("other"))
    assert not agents_running(tmp_path, launched("a", isSidechain=True))
    assert agents_running(tmp_path, launched("a"), finished("a", isSidechain=True))


def test_foreground_tool_results_are_not_background_agents(tmp_path):
    assert not agents_running(tmp_path, prompt(), tool_use("a"), tool_result("a"))


def test_background_agents_unreadable_or_empty(tmp_path):
    assert not sl.background_agents_running("")
    assert not sl.background_agents_running(str(tmp_path / "missing.jsonl"))
    assert not agents_running(tmp_path, "not json", "[1, 2]", "")


# ---------------------------------------------------------------------------
# session_usage
# ---------------------------------------------------------------------------

def _assistant(msg_id, out, cache_read=0, cache_write=0, inp=0, **extra):
    usage = {"input_tokens": inp, "output_tokens": out,
             "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_write}
    return json.dumps({"type": "assistant", "message": {"id": msg_id, "usage": usage}, **extra}) + "\n"


def output_tokens(session_id, transcript, tmp_path):
    return sl.session_usage(session_id, str(transcript), _tmp_dir=tmp_path)["output_tokens"]


def test_usage_no_transcript(tmp_path):
    assert sl.session_usage("s", "", _tmp_dir=tmp_path) == dict.fromkeys(sl.USAGE_KEYS, 0)
    assert output_tokens("s", tmp_path / "missing.jsonl", tmp_path) == 0


def test_usage_sums_each_field(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(_assistant("a", 10, cache_read=1000, cache_write=200, inp=3)
                 + _assistant("b", 20, cache_read=1200, cache_write=50, inp=2))
    assert sl.session_usage("s", str(t), _tmp_dir=tmp_path) == {
        "output_tokens": 30,
        "cache_read_input_tokens": 2200, "cache_creation_input_tokens": 250,
    }


def test_usage_dedupes_lines_of_one_response(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        _assistant("a", 100, cache_read=500) + _assistant("a", 100, cache_read=500)
        + json.dumps({"type": "user", "message": {}}) + "\n"
        + _assistant("b", 50) + _assistant("b", 50) + _assistant("b", 50)
    )
    usage = sl.session_usage("s", str(t), _tmp_dir=tmp_path)
    assert (usage["output_tokens"], usage["cache_read_input_tokens"]) == (150, 500)


def test_usage_skips_sidechain(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(_assistant("a", 100) + _assistant("x", 999, isSidechain=True))
    assert output_tokens("s", t, tmp_path) == 100


def test_usage_repeated_ticks_do_not_recount(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(_assistant("a", 100))
    for _ in range(3):
        assert output_tokens("s", t, tmp_path) == 100


def test_usage_incremental(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(_assistant("a", 100))
    output_tokens("s", t, tmp_path)
    with t.open("a") as f:
        f.write(_assistant("a", 100) + _assistant("b", 40))
    assert output_tokens("s", t, tmp_path) == 140


def test_usage_partial_line_read_later(tmp_path):
    t = tmp_path / "t.jsonl"
    line = _assistant("a", 100)
    t.write_text(line[:20])
    assert output_tokens("s", t, tmp_path) == 0
    t.write_text(line)
    assert output_tokens("s", t, tmp_path) == 100


def test_usage_restarts_when_transcript_shrinks(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(_assistant("a", 100) + _assistant("b", 100))
    output_tokens("s", t, tmp_path)
    t.write_text(_assistant("c", 7))
    assert output_tokens("s", t, tmp_path) == 7


@pytest.mark.parametrize("legacy", ["5304276", '{"path": "%s", "offset": 9999, "total": 5, "last_id": "a", "last_out": 1}'])
def test_usage_ignores_legacy_state(tmp_path, legacy):
    t = tmp_path / "t.jsonl"
    t.write_text(_assistant("a", 100))
    (tmp_path / "claude-out-tokens-s").write_text(legacy.replace("%s", str(t)))
    assert output_tokens("s", t, tmp_path) == 100


def test_usage_no_session_id(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(_assistant("a", 100))
    assert output_tokens("", t, tmp_path) == 100
    assert list(tmp_path.iterdir()) == [t]


# ---------------------------------------------------------------------------
# build_segments (integration-style, injecting stubs)
# ---------------------------------------------------------------------------

USAGE = {"output_tokens": 56_789,
         "cache_read_input_tokens": 11_200_000, "cache_creation_input_tokens": 300_000}

BASE_FIELDS = dict(
    model="claude-sonnet-4-6",
    cwd="/tmp/myproject/sub",
    project="myproject",
    session_id="sess-123",
    transcript_path="/tmp/t.jsonl",
    total_in=123_456,
    ctx_size=1_000_000,
    cache_expires_at=None,
    cache_misses=0,
    used_pct=30.0,
    five_pct=None,
    week_pct=None,
    effort="",
)


def make_segments(**overrides):
    fields = {**BASE_FIELDS, **overrides}
    return sl.build_segments(
        **fields,
        _git_branch_fn=lambda cwd: "main",
        _usage_fn=lambda sid, path: USAGE,
        _cache_left_fn=lambda expires_at, path, ttl: 222,
    )


def strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


def joined(segments) -> str:
    return " | ".join(strip_ansi(s) for s in segments)


def test_build_segments_model_present():
    segs = make_segments()
    assert any("claude-sonnet-4-6" in strip_ansi(s) for s in segs)


def test_build_segments_project_and_branch():
    assert strip_ansi(make_segments()[1]) == "myproject:main"


def test_build_segments_project_without_git():
    segs = sl.build_segments(
        **BASE_FIELDS,
        _git_branch_fn=lambda cwd: "",
        _usage_fn=lambda sid, path: USAGE,
        _cache_left_fn=lambda expires_at, path, ttl: 222,
    )
    assert strip_ansi(segs[1]) == "myproject"


def test_build_segments_tokens():
    assert "↓56k R11.2M W300k" in joined(make_segments())


def test_build_segments_tokens_absent_without_usage():
    segs = sl.build_segments(
        **BASE_FIELDS,
        _git_branch_fn=lambda cwd: "main",
        _usage_fn=lambda sid, path: dict.fromkeys(sl.USAGE_KEYS, 0),
        _cache_left_fn=lambda expires_at, path, ttl: None,
    )
    assert "↓" not in joined(segs)


def test_build_segments_ctx_bar_shows_context_size():
    assert "███░░░░░░░ 123k/1M (30%) |" in joined(make_segments())
    assert "███░░░░░░░ 123k (30%) |" in joined(make_segments(ctx_size=0))
    assert "███░░░░░░░ 30% |" in joined(make_segments(total_in=0))


def test_build_segments_ctx_bar_green():
    segs = make_segments(used_pct=20.0)
    bar_seg = next(s for s in segs if "░" in s or "█" in s)
    assert sl.GREEN in bar_seg


def test_build_segments_ctx_bar_yellow():
    segs = make_segments(used_pct=60.0)
    bar_seg = next(s for s in segs if "░" in s or "█" in s)
    assert sl.YELLOW in bar_seg


def test_build_segments_ctx_bar_red():
    segs = make_segments(used_pct=80.0)
    bar_seg = next(s for s in segs if "░" in s or "█" in s)
    assert sl.RED in bar_seg


def test_build_segments_rate_limits():
    segs = make_segments(five_pct=45.0, week_pct=20.0)
    combined = joined(segs)
    assert "5h:45%" in combined
    assert "7d:20%" in combined


def test_build_segments_model_with_effort():
    assert [strip_ansi(x) for x in make_segments(effort="high")[:2]] == ["🧠 claude-sonnet-4-6 high", "myproject:main"]
    assert strip_ansi(make_segments()[0]) == "🧠 claude-sonnet-4-6"


def cache_segment(remaining):
    segs = sl.build_segments(
        **BASE_FIELDS,
        _git_branch_fn=lambda cwd: "main",
        _usage_fn=lambda sid, path: USAGE,
        _cache_left_fn=lambda expires_at, path, ttl: remaining,
    )
    return next((s for s in segs if "⏳" in s or "⌛" in s), None)


def test_build_segments_cache_countdown():
    assert strip_ansi(cache_segment(222)) == "⏳ 3:42"


def test_build_segments_cache_colors():
    assert sl.GREEN in cache_segment(121)
    assert sl.YELLOW in cache_segment(120)
    assert sl.YELLOW in cache_segment(30)
    assert sl.RED in cache_segment(29)


def test_build_segments_cache_expired():
    seg = cache_segment(0)
    assert strip_ansi(seg) == "⌛ expired"
    assert sl.DIM in seg


def test_build_segments_cache_absent():
    assert cache_segment(None) is None


def test_build_segments_cache_misses():
    assert "⚠" not in joined(make_segments())
    assert "⏳ 3:42 | ⚠ 1 miss" in joined(make_segments(cache_misses=1))
    assert "⚠ 3 misses" in joined(make_segments(cache_misses=3))


def test_build_segments_passes_ttl_and_path():
    seen = []
    sl.build_segments(
        **BASE_FIELDS,
        cache_ttl=3600,
        _git_branch_fn=lambda cwd: "main",
        _usage_fn=lambda sid, path: USAGE,
        _cache_left_fn=lambda expires_at, path, ttl: seen.append((expires_at, path, ttl)),
    )
    assert seen == [(None, "/tmp/t.jsonl", 3600)]


def test_build_segments_no_session_id():
    segs = make_segments(session_id="")
    # Should not crash; model should still be present
    assert any("claude-sonnet-4-6" in strip_ansi(s) for s in segs)


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------

def test_build_context_maps_fields(tmp_path):
    seen = []
    ctx = sl.build_context(
        {**BASE_FIELDS, "session_id": "s1", "cache_expires_at": 99},
        cache_ttl=3600,
        env={"TMUX_PANE": "%2"},
        _cache_left_fn=lambda expires_at, path, ttl: seen.append((expires_at, path, ttl)) or 77,
        _tool_in_flight_fn=lambda path: path == "/tmp/t.jsonl",
        _background_agents_fn=lambda path: path != "/tmp/t.jsonl",
        _now=lambda: 42.0,
    )
    assert seen == [(99, "/tmp/t.jsonl", 3600)]
    assert (ctx.session_id, ctx.total_in, ctx.cache_remaining, ctx.tool_in_flight) == ("s1", 123_456, 77, True)
    assert ctx.background_agents is False
    assert ctx.env == {"TMUX_PANE": "%2"}
    assert ctx.now == 42.0


# ---------------------------------------------------------------------------
# parse_input
# ---------------------------------------------------------------------------

def test_parse_input_prompt_cache_and_window_size():
    data = {"prompt_cache": {"expires_at": 1792961243, "misses": 2},
            "context_window": {"context_window_size": 200000}}
    result = sl.parse_input(data)
    assert (result["cache_expires_at"], result["cache_misses"], result["ctx_size"]) == (1792961243, 2, 200000)
    expiries = {"misses": 4, "miss_causes": {"ttl_expired_1h": 2, "cache_scope_or_ttl_changed": 1}}
    assert sl.parse_input({"prompt_cache": expiries})["cache_misses"] == 2
    empty = sl.parse_input({})
    assert (empty["cache_expires_at"], empty["cache_misses"], empty["ctx_size"]) == (None, 0, 0)


def test_parse_input_project_is_start_folder():
    workspace = {"current_dir": "/home/u/repo/src", "project_dir": "/home/u/repo/"}
    assert sl.parse_input({"workspace": workspace})["project"] == "repo"
    assert sl.parse_input({"workspace": {"current_dir": "/home/u/other"}})["project"] == "other"


def test_parse_input_empty_dict():
    result = sl.parse_input({})
    assert result["model"] == "Claude"
    assert result["transcript_path"] == ""
    assert result["total_in"] == 0
