import pathlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class Context:
    """Snapshot of one status line tick. Features read the session only through this."""
    session_id: str
    total_in: int
    cache_remaining: int | None
    tool_in_flight: bool
    env: Mapping[str, str]
    now: float
    state_dir: pathlib.Path


@dataclass(frozen=True)
class Settings:
    """Base for per-feature settings; subclasses add fields, every one with a default."""
    enabled: bool = False


class Feature:
    name: ClassVar[str]
    Settings: ClassVar[type[Settings]]

    def run(self, ctx: Context, settings) -> None:
        raise NotImplementedError
