#!/bin/bash
# autoreviewer config helpers (bash)
# Source this file: source "$AUTOREVIEWER_HOME/lib/config.sh"

AUTOREVIEWER_HOME="${AUTOREVIEWER_HOME:-$HOME/.autoreviewer}"
GLOBAL_CONFIG="$AUTOREVIEWER_HOME/config.json"

# Read a key from JSON config, with repo override.
# Usage: ar_config_get <key> [<repo_path>]
ar_config_get() {
    local key="$1"
    local repo_path="${2:-}"
    local value=""

    if [ -n "$repo_path" ]; then
        local git_dir
        git_dir=$(cd "$repo_path" 2>/dev/null && git rev-parse --git-dir 2>/dev/null)
        if [ -n "$git_dir" ]; then
            local abs_git_dir
            if [[ "$git_dir" = /* ]]; then
                abs_git_dir="$git_dir"
            else
                abs_git_dir="$(cd "$repo_path" && cd "$git_dir" && pwd)"
            fi
            local repo_config="$abs_git_dir/autoreviewer.json"
            if [ -f "$repo_config" ]; then
                # Use has()/if-then to be false-aware: `// empty` would
                # collapse a literal `false` into "missing".
                value=$(jq -r --arg k "$key" 'if has($k) then .[$k] else empty end' "$repo_config" 2>/dev/null)
                if [ -n "$value" ]; then
                    echo "$value"
                    return 0
                fi
            fi
        fi
    fi

    if [ -f "$GLOBAL_CONFIG" ]; then
        jq -r --arg k "$key" 'if has($k) then .[$k] else empty end' "$GLOBAL_CONFIG" 2>/dev/null
    fi
}

# Check if autoreviewer is enabled for the given repo.
# Returns 0 if enabled, 1 if disabled.
# Order of precedence: env var > repo config > global blacklist > global enabled
ar_is_enabled() {
    local repo_path="${1:-$(pwd)}"

    # 1. Single-commit env var override
    if [ "${AUTOREVIEWER_SKIP:-0}" = "1" ]; then
        return 1
    fi

    # 2. Per-repo config
    local git_dir abs_git_dir
    git_dir=$(cd "$repo_path" 2>/dev/null && git rev-parse --git-dir 2>/dev/null)
    if [ -n "$git_dir" ]; then
        if [[ "$git_dir" = /* ]]; then
            abs_git_dir="$git_dir"
        else
            abs_git_dir="$(cd "$repo_path" && cd "$git_dir" && pwd)"
        fi
        local repo_config="$abs_git_dir/autoreviewer.json"
        if [ -f "$repo_config" ]; then
            local repo_enabled
            repo_enabled=$(jq -r '.enabled // empty' "$repo_config" 2>/dev/null)
            if [ "$repo_enabled" = "false" ]; then
                return 1
            elif [ "$repo_enabled" = "true" ]; then
                return 0
            fi
        fi
    fi

    # 3. Global blacklist
    local abs_repo
    abs_repo=$(cd "$repo_path" 2>/dev/null && pwd)
    if [ -f "$GLOBAL_CONFIG" ] && [ -n "$abs_repo" ]; then
        local in_blacklist
        in_blacklist=$(jq -r --arg p "$abs_repo" '(.disabled_repos // []) | index($p) // empty' "$GLOBAL_CONFIG" 2>/dev/null)
        if [ -n "$in_blacklist" ]; then
            return 1
        fi
    fi

    # 4. Global enabled flag (default true)
    local global_enabled="true"
    if [ -f "$GLOBAL_CONFIG" ]; then
        global_enabled=$(jq -r '.enabled // true' "$GLOBAL_CONFIG" 2>/dev/null)
    fi
    [ "$global_enabled" = "true" ]
}

# Set a top-level key in global config (creates file if missing).
# Usage: ar_config_set_global <key> <json_value>
# Example: ar_config_set_global enabled false
ar_config_set_global() {
    local key="$1"
    local val="$2"
    mkdir -p "$AUTOREVIEWER_HOME"
    if [ ! -f "$GLOBAL_CONFIG" ]; then
        echo "{}" > "$GLOBAL_CONFIG"
    fi
    local tmp
    tmp=$(mktemp)
    jq --arg k "$key" --argjson v "$val" '.[$k] = $v' "$GLOBAL_CONFIG" > "$tmp" && mv "$tmp" "$GLOBAL_CONFIG"
}

# Set a top-level key in current repo's config.
# Usage: ar_config_set_repo <key> <json_value>
ar_config_set_repo() {
    local key="$1"
    local val="$2"
    local git_dir
    git_dir=$(git rev-parse --git-dir 2>/dev/null) || {
        echo "Not a git repository" >&2
        return 1
    }
    local repo_config="$git_dir/autoreviewer.json"
    if [ ! -f "$repo_config" ]; then
        echo "{}" > "$repo_config"
    fi
    local tmp
    tmp=$(mktemp)
    jq --arg k "$key" --argjson v "$val" '.[$k] = $v' "$repo_config" > "$tmp" && mv "$tmp" "$repo_config"
}

# Add a repo path to the global blacklist.
ar_blacklist_add() {
    local path="$1"
    mkdir -p "$AUTOREVIEWER_HOME"
    if [ ! -f "$GLOBAL_CONFIG" ]; then
        echo "{}" > "$GLOBAL_CONFIG"
    fi
    local tmp
    tmp=$(mktemp)
    jq --arg p "$path" '.disabled_repos = ((.disabled_repos // []) + [$p] | unique)' "$GLOBAL_CONFIG" > "$tmp" && mv "$tmp" "$GLOBAL_CONFIG"
}

ar_blacklist_remove() {
    local path="$1"
    if [ ! -f "$GLOBAL_CONFIG" ]; then
        return 0
    fi
    local tmp
    tmp=$(mktemp)
    jq --arg p "$path" '.disabled_repos = ((.disabled_repos // []) - [$p])' "$GLOBAL_CONFIG" > "$tmp" && mv "$tmp" "$GLOBAL_CONFIG"
}

# ---------- repo registry (~/.autoreviewer/repos) ----------
# Plain text file, one absolute repo path per line. Used by the shell prompt
# hook for a fork-free fast path: the precmd snippet only invokes
# `autoreviewer heal` when $PWD is inside a registered repo. Without this
# every prompt would fork a new bash + git + jq (~50ms).

# Append a repo path if not already present.
ar_repo_register() {
    local repo="$1"
    [ -z "$repo" ] && return 0
    local registry="${AUTOREVIEWER_HOME%/}/repos"
    mkdir -p "$AUTOREVIEWER_HOME"
    [ -f "$registry" ] || : > "$registry"
    if ! grep -Fxq -- "$repo" "$registry" 2>/dev/null; then
        printf '%s\n' "$repo" >> "$registry"
    fi
}

# Remove a repo path from the registry. No-op if absent.
ar_repo_unregister() {
    local repo="$1"
    [ -z "$repo" ] && return 0
    local registry="${AUTOREVIEWER_HOME%/}/repos"
    [ -f "$registry" ] || return 0
    if ! grep -Fxq -- "$repo" "$registry" 2>/dev/null; then
        return 0
    fi
    local tmp
    tmp=$(mktemp) || return 0
    # grep -Fxv exits 1 when nothing matches — that's fine; tmp is still
    # written. We don't fail on empty result.
    grep -Fxv -- "$repo" "$registry" > "$tmp" 2>/dev/null || true
    mv "$tmp" "$registry"
}

# Detect "we were chain-installed in this repo, but something (typically
# `pod install`) overwrote core.hooksPath, bypassing autoreviewer". When
# detected, restore local core.hooksPath using the *saved* chain target.
#
# IMPORTANT: we deliberately do NOT update chained_hooks_path from the
# current local_hp value. If a user runs
#     git config --local core.hooksPath /tmp/debug-hooks
# we restore our hooksPath but leave chained_hooks_path untouched. This
# protects the saved config from getting poisoned by debug values, and
# keeps the user's intentional overrides recoverable (they can read the
# stderr notice and decide to re-run `autoreviewer install` to genuinely
# update the chain target).
#
# Always returns 0 — never fatal, since this is best-effort self-heal called
# from any read-style command (status, run, log, show) and from the shell
# prompt hook.
#
# Opt-out: set AUTOREVIEWER_NO_AUTOHEAL=1 to skip.
#
# Usage: ar_self_heal_if_needed [<repo_path>]
ar_self_heal_if_needed() {
    [ "${AUTOREVIEWER_NO_AUTOHEAL:-0}" = "1" ] && return 0

    local repo_path="${1:-$(pwd)}"

    # Need git, jq, and to be inside a repo.
    command -v jq >/dev/null 2>&1 || return 0
    local repo_root
    repo_root=$(cd "$repo_path" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null) || return 0
    [ -n "$repo_root" ] || return 0

    local git_dir abs_git_dir
    git_dir=$(cd "$repo_root" && git rev-parse --git-dir 2>/dev/null) || return 0
    if [[ "$git_dir" = /* ]]; then
        abs_git_dir="$git_dir"
    else
        abs_git_dir="$(cd "$repo_root" && cd "$git_dir" && pwd)"
    fi
    local repo_cfg="$abs_git_dir/autoreviewer.json"

    # Only heal if we were previously chain-installed (chained_hooks_path saved).
    [ -f "$repo_cfg" ] || return 0
    local saved_chain
    saved_chain=$(jq -r '.chained_hooks_path // empty' "$repo_cfg" 2>/dev/null)
    [ -n "$saved_chain" ] || return 0

    # Migration: ensure this repo is in the registry so the prompt-hook
    # fast path picks it up. Cheap idempotent operation.
    ar_repo_register "$repo_root"

    local our_hooks="${AUTOREVIEWER_HOME%/}/hooks"
    local local_hp effective_abs
    local_hp=$(cd "$repo_root" && git config --local --get core.hooksPath 2>/dev/null || echo "")
    effective_abs="$local_hp"
    if [ -n "$local_hp" ] && [[ "$local_hp" != /* ]]; then
        effective_abs="$repo_root/$local_hp"
    fi
    effective_abs="${effective_abs%/}"

    # Already correctly pointing at us? Nothing to do.
    if [ "$effective_abs" = "$our_hooks" ]; then
        return 0
    fi

    # Re-apply our local hooksPath. We do NOT touch chained_hooks_path —
    # see the function-level comment for why.
    (cd "$repo_root" && git config --local core.hooksPath "$our_hooks") || return 0

    if [ -n "$local_hp" ] && [ "$local_hp" != "$saved_chain" ]; then
        # Override path differs from saved chain — surface this so user
        # can run `autoreviewer install` to genuinely update the chain
        # target, if that's what they want.
        echo "ℹ️  autoreviewer: detected core.hooksPath was overwritten (was: $local_hp). Restored to chain mode → $saved_chain (saved chain unchanged; run 'autoreviewer install' if you want to update it)." >&2
    else
        echo "ℹ️  autoreviewer: detected core.hooksPath was overwritten (likely by 'pod install' or similar) — self-healed back to chain mode → $saved_chain" >&2
    fi
    return 0
}
