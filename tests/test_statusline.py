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
    assert sl.format_tokens(1_000_000) == "1.0M"
    assert sl.format_tokens(1_400_000) == "1.4M"
    assert sl.format_tokens(10_000_000) == "10.0M"


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


# ---------------------------------------------------------------------------
# session_timer
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


def test_session_timer_empty_id():
    assert sl.session_timer("") == ""


def test_session_timer_creates_file(tmp_path):
    sid = "test-session-abc"
    result = sl.session_timer(sid, _tmp_dir=tmp_path)
    assert result == "0m"
    assert (tmp_path / f"claude-start-{sid}").exists()


def test_session_timer_minutes(tmp_path):
    import time
    sid = "sess-minutes"
    (tmp_path / f"claude-start-{sid}").write_text(str(int(time.time()) - 125))
    result = sl.session_timer(sid, _tmp_dir=tmp_path)
    assert result == "2m"


def test_session_timer_hours(tmp_path):
    import time
    sid = "sess-hours"
    (tmp_path / f"claude-start-{sid}").write_text(str(int(time.time()) - 3720))
    result = sl.session_timer(sid, _tmp_dir=tmp_path)
    assert result == "1h 2m"


# ---------------------------------------------------------------------------
# accumulate_output_tokens
# ---------------------------------------------------------------------------

def test_accumulate_no_session():
    assert sl.accumulate_output_tokens("", 500) == 500


def test_accumulate_first_turn(tmp_path):
    sid = "acc-first"
    result = sl.accumulate_output_tokens(sid, 300, _tmp_dir=tmp_path)
    assert result == 300
    assert (tmp_path / f"claude-out-tokens-{sid}").read_text() == "300"


def test_accumulate_subsequent_turns(tmp_path):
    sid = "acc-sub"
    (tmp_path / f"claude-out-tokens-{sid}").write_text("300")
    result = sl.accumulate_output_tokens(sid, 150, _tmp_dir=tmp_path)
    assert result == 450


# ---------------------------------------------------------------------------
# build_segments (integration-style, injecting stubs)
# ---------------------------------------------------------------------------

BASE_FIELDS = dict(
    model="claude-sonnet-4-6",
    cwd="/tmp/myproject",
    repo="owner/repo",
    session_id="sess-123",
    transcript_path="/tmp/t.jsonl",
    total_in=50_000,
    turn_out=1_000,
    used_pct=30.0,
    five_pct=None,
    week_pct=None,
    effort="",
)


def make_segments(**overrides):
    fields = {**BASE_FIELDS, **overrides}
    return sl.build_segments(
        **fields,
        _git_info_fn=lambda cwd: ("main", ""),
        _session_timer_fn=lambda sid: "5m",
        _accumulate_fn=lambda sid, out: out,
        _active_tool_fn=lambda sid: "",
        _cache_remaining_fn=lambda path, ttl: 222,
    )


def strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


def joined(segments) -> str:
    return " | ".join(strip_ansi(s) for s in segments)


def test_build_segments_model_present():
    segs = make_segments()
    assert any("claude-sonnet-4-6" in strip_ansi(s) for s in segs)


def test_build_segments_location_and_branch():
    segs = make_segments()
    combined = joined(segs)
    assert "owner/repo" in combined
    assert "main" in combined


def test_build_segments_git_clean():
    segs = make_segments()
    combined = joined(segs)
    assert "✓" in combined


def test_build_segments_git_dirty():
    def dirty_git(cwd):
        return "main", " M file.py\n?? new.py\n"
    segs = sl.build_segments(
        **BASE_FIELDS,
        _git_info_fn=dirty_git,
        _session_timer_fn=lambda sid: "5m",
        _accumulate_fn=lambda sid, out: out,
        _active_tool_fn=lambda sid: "",
        _cache_remaining_fn=lambda path, ttl: 222,
    )
    combined = joined(segs)
    assert "~1" in combined
    assert "?1" in combined


def test_build_segments_tokens():
    segs = make_segments(total_in=123_456, turn_out=1_000)
    combined = joined(segs)
    assert "↑123k" in combined


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


def test_build_segments_effort():
    segs = make_segments(effort="high")
    combined = joined(segs)
    assert "🧠 high" in combined


def test_build_segments_active_tool():
    segs = sl.build_segments(
        **BASE_FIELDS,
        _git_info_fn=lambda cwd: ("main", ""),
        _session_timer_fn=lambda sid: "5m",
        _accumulate_fn=lambda sid, out: out,
        _active_tool_fn=lambda sid: "Bash",
        _cache_remaining_fn=lambda path, ttl: 222,
    )
    combined = joined(segs)
    assert "⚙ Bash" in combined


def cache_segment(remaining):
    segs = sl.build_segments(
        **BASE_FIELDS,
        _git_info_fn=lambda cwd: ("main", ""),
        _session_timer_fn=lambda sid: "5m",
        _accumulate_fn=lambda sid, out: out,
        _active_tool_fn=lambda sid: "",
        _cache_remaining_fn=lambda path, ttl: remaining,
    )
    return next((s for s in segs if "cache" in s), None)


def test_build_segments_cache_countdown():
    assert strip_ansi(cache_segment(222)) == "cache 3:42"


def test_build_segments_cache_colors():
    assert sl.GREEN in cache_segment(121)
    assert sl.YELLOW in cache_segment(120)
    assert sl.YELLOW in cache_segment(30)
    assert sl.RED in cache_segment(29)


def test_build_segments_cache_expired():
    seg = cache_segment(0)
    assert strip_ansi(seg) == "cache expired"
    assert sl.DIM in seg


def test_build_segments_cache_absent():
    assert cache_segment(None) is None


def test_build_segments_passes_ttl_and_path():
    seen = []
    sl.build_segments(
        **BASE_FIELDS,
        cache_ttl=3600,
        _git_info_fn=lambda cwd: ("main", ""),
        _session_timer_fn=lambda sid: "5m",
        _accumulate_fn=lambda sid, out: out,
        _active_tool_fn=lambda sid: "",
        _cache_remaining_fn=lambda path, ttl: seen.append((path, ttl)),
    )
    assert seen == [("/tmp/t.jsonl", 3600)]


def test_build_segments_no_session_id():
    segs = make_segments(session_id="", turn_out=0, total_in=0)
    # Should not crash; model should still be present
    assert any("claude-sonnet-4-6" in strip_ansi(s) for s in segs)


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------

def test_build_context_maps_fields(tmp_path):
    seen = []
    ctx = sl.build_context(
        {**BASE_FIELDS, "session_id": "s1"},
        cache_ttl=3600,
        env={"TMUX_PANE": "%2"},
        _cache_remaining_fn=lambda path, ttl: seen.append((path, ttl)) or 77,
        _tool_in_flight_fn=lambda path: path == "/tmp/t.jsonl",
        _now=lambda: 42.0,
    )
    assert seen == [("/tmp/t.jsonl", 3600)]
    assert (ctx.session_id, ctx.total_in, ctx.cache_remaining, ctx.tool_in_flight) == ("s1", 50_000, 77, True)
    assert ctx.env == {"TMUX_PANE": "%2"}
    assert ctx.now == 42.0


# ---------------------------------------------------------------------------
# parse_input
# ---------------------------------------------------------------------------

def test_parse_input_empty_dict():
    result = sl.parse_input({})
    assert result["model"] == "Claude"
    assert result["transcript_path"] == ""
    assert result["total_in"] == 0
