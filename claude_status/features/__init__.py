import sys
from collections.abc import Mapping

from claude_status.features.auto_compact import AutoCompact
from claude_status.features.base import Context, Feature, Settings

REGISTRY: dict[str, Feature] = {f.name: f for f in (AutoCompact(),)}


def run_enabled(settings: Mapping[str, Settings], ctx: Context) -> None:
    """Run each enabled feature; one failing never affects the others or the status line."""
    for name, feature in REGISTRY.items():
        if not settings[name].enabled:
            continue
        try:
            feature.run(ctx, settings[name])
        except Exception as e:
            print(f"claude-status: feature {name!r} failed: {e}", file=sys.stderr)
