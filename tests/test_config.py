import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from claude_status import config
from claude_status.features import REGISTRY

TEMPLATE = pathlib.Path(__file__).parent.parent / "config" / "statusline.default.toml"


def load(tmp_path, toml=None, **env):
    path = tmp_path / "statusline.toml"
    if toml is not None:
        path.write_text(toml)
    return config.load({"CLAUDE_STATUS_CONFIG": str(path), **env})


def test_defaults_without_file(tmp_path):
    cfg = load(tmp_path)
    assert cfg == config.defaults()
    assert cfg.error is None
    assert not any(s.enabled for s in cfg.features.values())


def test_file_overrides_defaults(tmp_path):
    cfg = load(tmp_path, """
[display]
bar_width = 20
[features.auto_compact]
enabled = true
tokens = 1000
""")
    assert cfg.display.bar_width == 20
    assert cfg.display.ctx_warn == 50
    assert cfg.features["auto_compact"].enabled is True
    assert cfg.features["auto_compact"].tokens == 1000
    assert cfg.features["auto_compact"].within == 30
    assert cfg.error is None


def test_env_beats_file(tmp_path):
    cfg = load(tmp_path, "[cache]\nttl = 600\n", CLAUDE_STATUS_CACHE_TTL="3600")
    assert cfg.cache.ttl == 3600


def test_env_not_an_integer(tmp_path):
    cfg = load(tmp_path, CLAUDE_STATUS_CTX_WARN="lots")
    assert "CLAUDE_STATUS_CTX_WARN" in cfg.error
    assert cfg.display == config.Display()


def test_invalid_toml_falls_back_to_defaults(tmp_path):
    cfg = load(tmp_path, "[display\n")
    assert cfg.error
    assert cfg.display == config.Display()


def test_errors_turn_every_feature_off(tmp_path):
    cfg = load(tmp_path, "[features.auto_compact]\nenabled = true\n[display]\nbar_width = 'x'\n")
    assert cfg.error
    assert not cfg.features["auto_compact"].enabled


def test_unknown_inputs_are_errors(tmp_path):
    assert "unknown key 'colour'" in load(tmp_path, "[display]\ncolour = 1\n").error
    assert "unknown key 'nope'" in load(tmp_path, "[features.nope]\nenabled = true\n").error
    assert "unknown key 'nope'" in load(tmp_path, "[features.auto_compact]\nnope = 1\n").error
    assert "unknown table [nope]" in load(tmp_path, "[nope]\n").error


def test_wrong_type_is_error(tmp_path):
    assert "enabled must be bool" in load(tmp_path, "[features.auto_compact]\nenabled = 1\n").error
    assert "tokens must be int" in load(tmp_path, "[features.auto_compact]\ntokens = true\n").error


def test_shipped_template_matches_code_defaults():
    raw = tomllib.loads(TEMPLATE.read_text())
    cfg = config._parse(raw)
    assert cfg == config.defaults()
    assert set(raw["features"]) == set(REGISTRY)


def test_default_path_follows_claude_config_dir(tmp_path):
    (tmp_path / "statusline.toml").write_text("[display]\nbar_width = 7\n")
    assert config.load({"CLAUDE_CONFIG_DIR": str(tmp_path)}).display.bar_width == 7


def test_explicit_config_wins_over_claude_config_dir(tmp_path):
    (tmp_path / "statusline.toml").write_text("[display]\nbar_width = 7\n")
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[display]\nbar_width = 9\n")
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path), "CLAUDE_STATUS_CONFIG": str(explicit)}
    assert config.load(env).display.bar_width == 9
