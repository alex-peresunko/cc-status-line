#!/usr/bin/env bash
# Claude Code status line: model | location | ↑in ↓out tokens | ctx% | cost

input=$(cat)

# --- Extract raw values ---
model=$(echo "$input"        | jq -r '.model.display_name // "Claude"')
raw_model_id=$(echo "$input" | jq -r '.model.id // ""')
model_id=$(echo "$raw_model_id" | sed 's/^[a-z]*\.anthropic\.//')

cwd=$(echo "$input"  | jq -r '.workspace.current_dir // ""')
dir=$(basename "$cwd")
repo=$(echo "$input" | jq -r '.workspace.repo | if . then .owner + "/" + .name else empty end')

total_in=$(echo "$input"      | jq -r '.context_window.total_input_tokens // 0')
turn_out=$(echo "$input"      | jq -r '.context_window.current_usage.output_tokens // 0')
session_id=$(echo "$input"    | jq -r '.session_id // empty')

used=$(echo "$input"     | jq -r '.context_window.used_percentage // empty')
five_pct=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
week_pct=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
effort=$(echo "$input"   | jq -r '.effort.level // .effort // empty')

# --- Accumulate output tokens across turns in a per-session temp file ---
# File stores JSON: {"last_input": N, "accumulated_output": N}
# If input drops significantly from last recorded value, /clear was run — reset output counter.
total_out=0
if [ -n "$session_id" ]; then
  acc_file="/tmp/claude-out-tokens-${session_id}"
  [[ "$turn_out" =~ ^[0-9]+$ ]] || turn_out=0
  [[ "$total_in" =~ ^[0-9]+$ ]] || total_in=0
  prev_out=0
  if [ -f "$acc_file" ]; then
    prev_out=$(cat "$acc_file" 2>/dev/null || echo 0)
    [[ "$prev_out" =~ ^[0-9]+$ ]] || prev_out=0
  fi
  total_out=$(( prev_out + turn_out ))
  printf '%s' "$total_out" > "$acc_file"
fi

# --- Helper: format token count compactly ---
fmt_tokens() {
  awk -v n="$1" 'BEGIN {
    if (n >= 1000000)   printf "%.1fM", n/1000000
    else if (n >= 1000) printf "%dk",   int(n/1000)
    else                printf "%d",    n
  }'
}

# --- Cost: exact model lookup in cache, fallback by family ---
cache_file="$HOME/.claude/model-pricing-cache.json"
cost_str=""
if [ "$total_in" -gt 0 ] || [ "$total_out" -gt 0 ] 2>/dev/null; then
  in_rate=""; out_rate=""
  if [ -f "$cache_file" ] && command -v jq >/dev/null 2>&1; then
    in_rate=$(jq -r --arg m "$model_id" '.models[$m].input // empty' "$cache_file")
    out_rate=$(jq -r --arg m "$model_id" '.models[$m].output // empty' "$cache_file")
    if [ -z "$in_rate" ]; then
      if echo "$model_id" | grep -qi haiku;  then family=haiku
      elif echo "$model_id" | grep -qi opus; then family=opus
      else family=sonnet; fi
      in_rate=$(jq -r --arg f "$family" '.fallback_by_family[$f].input'  "$cache_file")
      out_rate=$(jq -r --arg f "$family" '.fallback_by_family[$f].output' "$cache_file")
    fi
  fi
  [ -z "$in_rate"  ] && in_rate=3.00
  [ -z "$out_rate" ] && out_rate=15.00
  cost_str=$(awk -v i="$total_in" -v o="$total_out" -v ir="$in_rate" -v or="$out_rate" 'BEGIN {
    printf "$%.2f", (i/1000000*ir) + (o/1000000*or)
  }')
fi

# --- Build parts ---
parts=()

# 1. Identity: model
parts+=("$(printf '\033[0;36m%s\033[0m' "$model")")

# 2. Identity: location
if [ -n "$repo" ]; then
  parts+=("$(printf '\033[0;34m%s\033[0m' "$repo")")
else
  parts+=("$(printf '\033[0;34m%s\033[0m' "$dir")")
fi

# 3. Session activity: cumulative tokens in / out
if [ "$total_in" -gt 0 ] || [ "$total_out" -gt 0 ] 2>/dev/null; then
  in_fmt=$(fmt_tokens "$total_in")
  out_fmt=$(fmt_tokens "$total_out")
  parts+=("$(printf '\033[0;37m↑%s ↓%s\033[0m' "$in_fmt" "$out_fmt")")
fi

# 4. Context health: ctx% with colour gradient
if [ -n "$used" ]; then
  used_int=$(printf '%.0f' "$used")
  if   [ "$used_int" -ge 80 ]; then color='\033[0;31m'
  elif [ "$used_int" -ge 50 ]; then color='\033[0;33m'
  else                               color='\033[0;32m'; fi
  parts+=("$(printf "${color}ctx:%s%%\033[0m" "$used_int")")
fi

# 5. Cost
[ -n "$cost_str" ] && parts+=("$(printf '\033[0;33m%s\033[0m' "$cost_str")")

# 6. Rate limits (Claude.ai subscriptions only — hidden on Bedrock)
rate_parts=""
[ -n "$five_pct" ] && rate_parts="5h:$(printf '%.0f' "$five_pct")%"
if [ -n "$week_pct" ]; then
  [ -n "$rate_parts" ] && rate_parts="$rate_parts "
  rate_parts="${rate_parts}7d:$(printf '%.0f' "$week_pct")%"
fi
[ -n "$rate_parts" ] && parts+=("$(printf '\033[0;35m%s\033[0m' "$rate_parts")")

# 7. Effort level
[ -n "$effort" ] && parts+=("$(printf '\033[0;36m🧠 %s\033[0m' "$effort")")

# --- Render ---
printf '%s' "${parts[0]}"
for (( i=1; i<${#parts[@]}; i++ )); do
  printf ' \033[0;90m|\033[0m %s' "${parts[$i]}"
done
printf '\n'
