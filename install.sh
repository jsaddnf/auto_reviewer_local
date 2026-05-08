#!/bin/bash
# autoreviewer installer
#
# Run from inside the cloned project directory:
#   ./install.sh
#
# Installs to:
#   ~/.autoreviewer/      (lib, hooks, prompts, config)
#   ~/.local/bin/autoreviewer (CLI executable)
#
# Does NOT set git config — the per-repo workflow is preferred.
# After installation, run 'autoreviewer install' inside each repo to enable.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOREVIEWER_HOME="${AUTOREVIEWER_HOME:-$HOME/.autoreviewer}"
BIN_DIR="${AUTOREVIEWER_BIN_DIR:-$HOME/.local/bin}"
FORCE="${FORCE:-0}"

# Parse args
while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --help|-h)
            echo "Usage: ./install.sh [--force]"
            echo "  --force  overwrite existing installation"
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

echo ""
echo "🤖 autoreviewer installer"
echo "========================="
echo ""

# ---------- 1. dependency check ----------

check_dep() {
    local name="$1"
    if command -v "$name" >/dev/null 2>&1; then
        echo "  ✅ $name"
        return 0
    else
        echo "  ❌ $name (missing)"
        return 1
    fi
}

echo "Checking dependencies..."
MISSING_DEPS=()

check_dep git || MISSING_DEPS+=("git")
check_dep python3 || MISSING_DEPS+=("python3")
check_dep jq || MISSING_DEPS+=("jq")
check_dep terminal-notifier || MISSING_DEPS+=("terminal-notifier")

# Check claude (Claude Code CLI) — the default review backend.
# Installed separately via Anthropic's official instructions:
#   https://docs.claude.com/en/docs/claude-code
if command -v claude >/dev/null 2>&1; then
    echo "  ✅ claude (Claude Code)"
else
    echo "  ⚠️  claude (Claude Code) not found in PATH. autoreviewer will still"
    echo "      install, but you'll need to install Claude Code (see"
    echo "      https://docs.claude.com/en/docs/claude-code) — or change the"
    echo "      'command' in ~/.autoreviewer/config.json — before reviews will run."
fi

# Check git version >= 2.9 (for core.hooksPath)
GIT_VERSION=$(git --version | awk '{print $3}')
GIT_MAJOR=$(echo "$GIT_VERSION" | cut -d. -f1)
GIT_MINOR=$(echo "$GIT_VERSION" | cut -d. -f2)
if [ "$GIT_MAJOR" -lt 2 ] || ([ "$GIT_MAJOR" -eq 2 ] && [ "$GIT_MINOR" -lt 9 ]); then
    echo ""
    echo "❌ git $GIT_VERSION is too old. autoreviewer needs git >= 2.9 (for core.hooksPath)."
    exit 1
fi

# ---------- 2. install missing deps via brew ----------

# Skip claude (Claude Code) and git/python3 from brew install list — they
# need to be installed separately, not via brew here.
BREW_DEPS=()
for dep in "${MISSING_DEPS[@]:-}"; do
    case "$dep" in
        jq|terminal-notifier) BREW_DEPS+=("$dep") ;;
        git|python3)
            echo ""
            echo "❌ Required tool '$dep' is missing. Please install it first, then rerun."
            exit 1 ;;
    esac
done

if [ ${#BREW_DEPS[@]} -gt 0 ]; then
    echo ""
    if ! command -v brew >/dev/null 2>&1; then
        echo "❌ Homebrew not found, but we need it to install: ${BREW_DEPS[*]}"
        echo ""
        echo "Install Homebrew first:"
        echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        echo ""
        echo "Then rerun this installer."
        exit 1
    fi
    echo "About to install missing dependencies via Homebrew:"
    echo "  brew install ${BREW_DEPS[*]}"
    echo ""
    read -rp "Continue? [Y/n] " yn
    yn="${yn:-Y}"
    case "$yn" in
        y|Y)
            brew install "${BREW_DEPS[@]}"
            ;;
        *)
            echo "Aborted. Install dependencies manually and rerun."
            exit 1
            ;;
    esac
fi

# ---------- 3. handle existing installation ----------

if [ -d "$AUTOREVIEWER_HOME" ] && [ "$FORCE" != "1" ]; then
    echo ""
    echo "⚠️  $AUTOREVIEWER_HOME already exists."
    read -rp "Overwrite? [y/N] " yn
    case "$yn" in
        y|Y) ;;
        *) echo "Aborted. Use --force to skip this prompt."; exit 1 ;;
    esac
fi

# ---------- 4. install files ----------

echo ""
echo "Installing files to $AUTOREVIEWER_HOME ..."
mkdir -p "$AUTOREVIEWER_HOME/hooks" "$AUTOREVIEWER_HOME/lib" "$AUTOREVIEWER_HOME/prompts"

cp "$SCRIPT_DIR/lib/runner.py" "$AUTOREVIEWER_HOME/lib/"
cp "$SCRIPT_DIR/lib/config.sh" "$AUTOREVIEWER_HOME/lib/"
cp "$SCRIPT_DIR/hooks/post-commit" "$AUTOREVIEWER_HOME/hooks/"
cp "$SCRIPT_DIR/hooks/_chain" "$AUTOREVIEWER_HOME/hooks/"
cp "$SCRIPT_DIR/prompts/default.txt" "$AUTOREVIEWER_HOME/prompts/"

chmod +x "$AUTOREVIEWER_HOME/hooks/post-commit"
chmod +x "$AUTOREVIEWER_HOME/hooks/_chain"
chmod +x "$AUTOREVIEWER_HOME/lib/runner.py"

# Generate one-line shims for every standard client-side git hook (except
# post-commit which has its own special script with the review trigger).
# Each shim just exec's _chain so the project's original hook (if any) runs.
STANDARD_HOOKS=(
    applypatch-msg pre-applypatch post-applypatch
    pre-commit pre-merge-commit prepare-commit-msg commit-msg
    pre-rebase post-checkout post-merge pre-push
    pre-auto-gc post-rewrite sendemail-validate post-index-change
)
for h in "${STANDARD_HOOKS[@]}"; do
    cat > "$AUTOREVIEWER_HOME/hooks/$h" <<EOF
#!/bin/bash
exec "\$HOME/.autoreviewer/hooks/_chain" $h "\$@"
EOF
    chmod +x "$AUTOREVIEWER_HOME/hooks/$h"
done
echo "  ✅ generated ${#STANDARD_HOOKS[@]} hook shims"

# Create default config if not exists
if [ ! -f "$AUTOREVIEWER_HOME/config.json" ]; then
    cat > "$AUTOREVIEWER_HOME/config.json" <<EOF
{
  "enabled": true,
  "command": "claude",
  "command_args": ["-p"],
  "prompt_file": "$AUTOREVIEWER_HOME/prompts/default.txt",
  "notification": "terminal-notifier",
  "notify_threshold": "low",
  "notify_start": true,
  "language": "zh",
  "auto_open": "on_high",
  "timeout_seconds": 600,
  "disabled_repos": [],
  "source_dir": "$SCRIPT_DIR"
}
EOF
    echo "  ✅ created config.json"
else
    # Always refresh source_dir so 'autoreviewer update' knows where to pull.
    # Also fill in any new fields introduced after the user's first install
    # — using `has()` not `//=`, because `//=` treats `false` as missing and
    # would silently flip a user's `notify_start: false` back to true.
    if command -v jq >/dev/null 2>&1; then
        tmp=$(mktemp)
        # Use has() not //=, because //= treats `false`/`0` as missing and
        # would silently flip a user's deliberate `notify_start: false` or
        # `timeout_seconds: 0` back to the default. has() respects
        # explicit user values.
        jq --arg s "$SCRIPT_DIR" '
            .source_dir = $s
            | (if has("notify_start")    then . else .notify_start    = true end)
            | (if has("language")        then . else .language        = "zh" end)
            | (if has("timeout_seconds") then . else .timeout_seconds = 600 end)
        ' "$AUTOREVIEWER_HOME/config.json" > "$tmp" \
            && mv "$tmp" "$AUTOREVIEWER_HOME/config.json"
        echo "  ✅ updated source_dir + ensured new fields in config.json"
    fi
fi

# ---------- 5. install CLI executable ----------

mkdir -p "$BIN_DIR"
cp "$SCRIPT_DIR/bin/autoreviewer" "$BIN_DIR/autoreviewer"
chmod +x "$BIN_DIR/autoreviewer"

if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    echo ""
    echo "ℹ️  $BIN_DIR is not in your current PATH. Adding it to your shell rc..."
    # Delegate to the CLI we just installed (use absolute path since PATH
    # isn't set yet). path-hook install is idempotent and writes a marker
    # block so 'autoreviewer uninstall' can clean it up later.
    "$BIN_DIR/autoreviewer" path-hook install "$BIN_DIR" || {
        echo "⚠️  path-hook install failed. Manually add this to your shell rc:"
        echo "     export PATH=\"$BIN_DIR:\$PATH\""
    }
fi

# ---------- 6. legacy global core.hooksPath check ----------

CURRENT_HOOKS=$(git config --global --get core.hooksPath 2>/dev/null || echo "")
TARGET_HOOKS="$AUTOREVIEWER_HOME/hooks"
if [ "$CURRENT_HOOKS" = "$TARGET_HOOKS" ] || [ "$CURRENT_HOOKS" = "$TARGET_HOOKS/" ]; then
    echo ""
    echo "ℹ️  You have a previous GLOBAL install (git config --global core.hooksPath = $CURRENT_HOOKS)."
    echo "   That still works — every repo on your machine inherits autoreviewer."
    echo "   To switch to the per-repo model (recommended), run:"
    echo "     git config --global --unset core.hooksPath"
    echo "   then 'autoreviewer install' inside each repo you want reviewed."
fi

# ---------- 7. done ----------

echo ""
echo "🎉 autoreviewer tool installed!"
echo ""
echo "Source recorded at: $SCRIPT_DIR"
echo "→ Next time, just run 'autoreviewer update' to pull the latest."
echo ""
echo "Per-repo model (recommended): you opt repos in one at a time."
echo ""
echo "Next steps:"
echo "  cd /path/to/your/repo"
echo "  autoreviewer install      # enable autoreviewer for this repo"
echo "  autoreviewer status       # check that it's enabled"
echo ""
echo "Then 'git commit' in that repo will trigger a background review and pop a"
echo "clickable macOS notification when done."
echo ""
echo "Other commands:"
echo "  autoreviewer update               # pull latest source + reinstall"
echo "  autoreviewer install --uninstall  # opt this repo out"
echo "  autoreviewer disable              # global pause (without uninstalling)"
echo "  AUTOREVIEWER_SKIP=1 git commit    # skip review for a single commit"
echo ""
