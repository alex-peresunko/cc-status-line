import json
import pathlib
import sys

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
# compute_cost
# ---------------------------------------------------------------------------

CACHE = {
    "models": {
        "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
        "claude-3-haiku-20240307-v1:0": {"input": 0.25, "output": 1.25},
    },
    "fallback_by_family": {
        "haiku":  {"input": 0.80,  "output": 4.00},
        "sonnet": {"input": 3.00,  "output": 15.00},
        "opus":   {"input": 15.00, "output": 75.00},
    },
}


@pytest.fixture()
def cache_file(tmp_path):
    p = tmp_path / "model-pricing-cache.json"
    p.write_text(json.dumps(CACHE))
    return p


def test_compute_cost_zero_tokens(cache_file):
    assert sl.compute_cost("claude-sonnet-4-6", 0, 0, cache_file) == ""


def test_compute_cost_exact_model(cache_file):
    # 1M in @ $3 + 0 out = $3.00
    result = sl.compute_cost("claude-sonnet-4-6", 1_000_000, 0, cache_file)
    assert result == "$3.00"


def test_compute_cost_family_fallback_haiku(cache_file):
    # model not in cache but contains "haiku" → use haiku fallback
    result = sl.compute_cost("claude-haiku-unknown", 1_000_000, 0, cache_file)
    assert result == "$0.80"


def test_compute_cost_family_fallback_opus(cache_file):
    result = sl.compute_cost("claude-opus-something", 0, 1_000_000, cache_file)
    assert result == "$75.00"


def test_compute_cost_missing_cache(tmp_path):
    missing = tmp_path / "no-such-file.json"
    # Falls back to hardcoded defaults: $3/$15
    result = sl.compute_cost("any-model", 1_000_000, 0, missing)
    assert result == "$3.00"


def test_compute_cost_combined(cache_file):
    # 1M in @ $3 + 1M out @ $15 = $18.00
    result = sl.compute_cost("claude-sonnet-4-6", 1_000_000, 1_000_000, cache_file)
    assert result == "$18.00"


# ---------------------------------------------------------------------------
# session_timer
# ---------------------------------------------------------------------------

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
    model_id="claude-sonnet-4-6",
    cwd="/tmp/myproject",
    repo="owner/repo",
    session_id="sess-123",
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
        _compute_cost_fn=lambda mid, tin, tout: "$0.15",
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
        _compute_cost_fn=lambda mid, tin, tout: "$0.15",
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
        _compute_cost_fn=lambda mid, tin, tout: "$0.15",
    )
    combined = joined(segs)
    assert "⚙ Bash" in combined


def test_build_segments_no_session_id():
    segs = make_segments(session_id="", turn_out=0, total_in=0)
    # Should not crash; model should still be present
    assert any("claude-sonnet-4-6" in strip_ansi(s) for s in segs)


# ---------------------------------------------------------------------------
# parse_input
# ---------------------------------------------------------------------------

def test_parse_input_vendor_prefix_stripped():
    data = {"model": {"id": "us.anthropic.claude-sonnet-4-6", "display_name": "Claude"}}
    result = sl.parse_input(data)
    assert result["model_id"] == "claude-sonnet-4-6"


def test_parse_input_empty_dict():
    result = sl.parse_input({})
    assert result["model"] == "Claude"
    assert result["model_id"] == ""
    assert result["total_in"] == 0
