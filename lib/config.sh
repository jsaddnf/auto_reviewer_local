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
