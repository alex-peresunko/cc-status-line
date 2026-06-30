# claude-status

Claude Code status line script (`statusline.py`) plus `model-pricing-cache.json`,
which `compute_cost()` reads to price tokens per model.

## Updating model-pricing-cache.json

AWS's pricing page (https://aws.amazon.com/bedrock/pricing/) does NOT render literal
prices in its HTML — model rows contain opaque tokens like
`{priceOf!bedrockfoundationmodels/bedrockfoundationmodels!<TOKEN>}` that are resolved
client-side via JS against a JSON pricing file. Plain `WebFetch` on that URL only
returns 1-2 rows because the AI summarizer can't see past the unresolved tokens —
don't bother retrying WebFetch on it.

Do this instead, in one pass:

1. Fetch and decompress the real pricing data (gzip despite the `.json` URL):
   ```
   curl -s "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/bedrockfoundationmodels/USD/current/bedrockfoundationmodels.json" -o /tmp/bedrock_pricing.json.gz
   gunzip -f /tmp/bedrock_pricing.json.gz
   ```
   This yields `{"manifest": ..., "sets": ..., "regions": {...}}`. Use the
   `"US East (N. Virginia)"` region (matches AWS's displayed on-demand table).
   Each entry is `region[token] = {"price": "X.XXXXXXXXXX", ...}`.

2. Get the model-name → token mapping. The AWS pricing page HTML has the literal
   model names and their tokens together (the JS just hasn't substituted prices
   yet), so fetching the saved/rendered HTML and grepping works — WebFetch's AI
   summary strips too much, so do a raw grep instead:
   - If working from a saved HTML file (ask the user to save the page via
     `Cmd+S` / "Save As" if WebFetch isn't yielding raw tokens), search for the
     `Anthropic` on-demand table. It looks like:
     ```
     <tr><td>Claude Opus 4.8</td><td>{priceOf!.../!<INPUT_TOKEN>}</td><td>{priceOf!.../!<OUTPUT_TOKEN>}</td>...
     ```
     There may be multiple "Anthropic models" tables on the page (cross-region
     inference, latency-optimized, batch-only, etc.) — use the **first** one
     under the `id="Anthropic"` heading, subtitled "On-Demand and Batch pricing".
     That's the one whose first two `<td>` columns are
     "Price per 1M input tokens" / "Price per 1M output tokens" at standard rate.
   - Extract input/output tokens for each `Claude ...` row, decode HTML entities
     (`html.unescape`), then look up each token in the JSON from step 1.

3. Map AWS's display names to this repo's model IDs (these are NOT 1:1 — AWS
   uses marketing names, our cache keys use Bedrock model IDs / aliases):
   - "Claude Opus 4.x" → `claude-opus-4-x-...` and the bare alias if one exists
   - "Claude Sonnet 4.x" → `claude-sonnet-4-x-...`
   - "Claude Haiku 4.5" → `claude-haiku-4-5-20251001-v1:0`
   - New/unfamiliar names (e.g. "Claude Fable 5", "Claude Mythos 5") may be
     newer model codenames — add them as new keys using a slugified version of
     the display name (e.g. `claude-fable-5`); skip rows marked `non-GA`.
   - Sonnet pricing has held at $3/$15 per 1M since Claude 3 — if a Sonnet
     variant you need isn't listed by exact name, $3/$15 is a safe default.

4. Update `model-pricing-cache.json`:
   - Edit existing `models` entries whose price changed.
   - Add new entries for any new model names found.
   - Update `fallback_by_family` (`haiku`/`sonnet`/`opus`) to match the
     current-generation rate for that family, in case a future model isn't an
     exact match in `models`.
   - Bump `_updated` to today's date.

5. Verify: `python3 -c "import json; json.load(open('model-pricing-cache.json'))"`
   and `make test` (uses its own fixture cache, won't fail from price changes,
   but confirms nothing else broke).

## Reference: token resolutions used in the 2026-06-30 update

(US East N. Virginia, on-demand, per 1M tokens — input/output)
- Claude Fable 5: $10.00 / $50.00
- Claude Opus 4.8 / 4.7 / 4.6 / 4.5: $5.00 / $25.00
- Claude Sonnet 4.6 / 4.5 / 4: $3.00 / $15.00
- Claude Haiku 4.5: $1.00 / $5.00
- Claude 3.5 Haiku: $0.80 / $4.00
- Claude 3 Haiku: $0.25 / $1.25
- Claude 3 Opus / Opus 4 / Opus 4.1: $15.00 / $75.00 (older tiers, unchanged)
- Claude 2.x: $8.00 / $24.00
- Claude Instant: $0.80 / $2.40

These are point-in-time — always re-resolve from the live JSON rather than
trusting this table for future updates.
