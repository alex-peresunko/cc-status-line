"""Status line configuration: built-in defaults < TOML file < legacy CLAUDE_STATUS_* env vars.

Any problem in the file or env falls back to defaults for everything (so features stay off)
and is reported in `Config.error`.
"""
import dataclasses
import os
import pathlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

from claude_status.features import REGISTRY
from claude_status.features.base import Settings

CACHE_TTL = 300
DEFAULT_PATH = pathlib.Path.home() / ".claude" / "statusline.toml"


@dataclass(frozen=True)
class Display:
    ctx_warn: int = 50
    ctx_crit: int = 80
    bar_width: int = 10


@dataclass(frozen=True)
class Cache:
    ttl: int = CACHE_TTL


@dataclass(frozen=True)
class Config:
    display: Display
    cache: Cache
    features: dict[str, Settings]
    error: str | None = None


_ENV_OVERRIDES = {
    "CLAUDE_STATUS_CTX_WARN":  ("display", "ctx_warn"),
    "CLAUDE_STATUS_CTX_CRIT":  ("display", "ctx_crit"),
    "CLAUDE_STATUS_BAR_WIDTH": ("display", "bar_width"),
    "CLAUDE_STATUS_CACHE_TTL": ("cache", "ttl"),
}


class ConfigError(ValueError):
    pass


def defaults(error: str | None = None) -> Config:
    return Config(Display(), Cache(), {n: f.Settings() for n, f in REGISTRY.items()}, error)


def _table(raw: Mapping, key: str, allowed: set[str], where: str) -> Mapping:
    table = raw.get(key, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[{where}] must be a table")
    unknown = sorted(table.keys() - allowed)
    if unknown:
        raise ConfigError(f"[{where}]: unknown key {unknown[0]!r}")
    return table


def _section(cls, raw: Mapping, key: str, where: str):
    names = {f.name for f in dataclasses.fields(cls)}
    table = _table(raw, key, names, where)
    base = cls()
    for name, value in table.items():
        expected = type(getattr(base, name))
        if type(value) is not expected:
            raise ConfigError(f"[{where}] {name} must be {expected.__name__}")
    return cls(**table)


def _parse(raw: Mapping) -> Config:
    unknown = sorted(raw.keys() - {"display", "cache", "features"})
    if unknown:
        raise ConfigError(f"unknown table [{unknown[0]}]")
    features_raw = _table(raw, "features", set(REGISTRY), "features")
    return Config(
        display=_section(Display, raw, "display", "display"),
        cache=_section(Cache, raw, "cache", "cache"),
        features={
            name: _section(feature.Settings, features_raw, name, f"features.{name}")
            for name, feature in REGISTRY.items()
        },
    )


def _apply_env(config: Config, env: Mapping[str, str]) -> Config:
    for var, (section, key) in _ENV_OVERRIDES.items():
        if var not in env:
            continue
        try:
            value = int(env[var])
        except ValueError:
            return defaults(f"{var} must be an integer")
        config = dataclasses.replace(
            config, **{section: dataclasses.replace(getattr(config, section), **{key: value})}
        )
    return config


def load(env: Mapping[str, str] = os.environ) -> Config:
    path = pathlib.Path(env.get("CLAUDE_STATUS_CONFIG") or DEFAULT_PATH)
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raw = {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        return defaults(f"{path.name}: {e}")
    try:
        config = _parse(raw)
    except ConfigError as e:
        return defaults(f"{path.name}: {e}")
    return _apply_env(config, env)
