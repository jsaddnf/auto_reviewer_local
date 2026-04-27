#!/bin/bash
# autoreviewer uninstaller
set -u

AUTOREVIEWER_HOME="${AUTOREVIEWER_HOME:-$HOME/.autoreviewer}"

echo "Uninstalling autoreviewer..."
read -rp "This will remove $AUTOREVIEWER_HOME and unset the global git hooks path. Continue? [y/N] " yn
case "$yn" in
    y|Y) ;;
    *) echo "Cancelled."; exit 0 ;;
esac

CURRENT=$(git config --global --get core.hooksPath 2>/dev/null || echo "")
if [ "$CURRENT" = "$AUTOREVIEWER_HOME/hooks" ] || [ "$CURRENT" = "$AUTOREVIEWER_HOME/hooks/" ]; then
    git config --global --unset core.hooksPath
    echo "  ✅ unset core.hooksPath"
elif [ -n "$CURRENT" ]; then
    echo "  ⚠️  core.hooksPath is $CURRENT (not us); leaving alone"
fi

rm -rf "$AUTOREVIEWER_HOME"
rm -f "$HOME/.local/bin/autoreviewer"
rm -f "/usr/local/bin/autoreviewer" 2>/dev/null

echo "✅ Uninstalled. (Per-repo .git/reviews/ data is preserved.)"
