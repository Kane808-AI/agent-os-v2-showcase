#!/bin/sh
# Install the Atlas Telegram owner-channel service as a macOS LaunchAgent.
#
# The service runs telegram-chat with --standing-approval: the owner grants
# standing approval for model replies into their own chat. Installing this
# service IS that grant; do not install it for anyone but the repository
# owner, and uninstall it to withdraw the grant:
#   launchctl bootout gui/$(id -u)/com.agentos.atlas-telegram
#
# Usage: install.sh <owner_user_id> [bot_ref] [tenant_id] [business_id]
set -eu

OWNER_USER_ID="${1:?usage: install.sh <owner_user_id> [bot_ref] [tenant_id] [business_id]}"
BOT_REF="${2:-agentos-atlas}"
TENANT_ID="${3:-tenant-owner}"
BUSINESS_ID="${4:-business-owner}"

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="$(command -v python3.14 || command -v python3)"
TEMPLATE="$REPO/deployment/telegram-service/com.agentos.atlas-telegram.plist.template"
TARGET="$HOME/Library/LaunchAgents/com.agentos.atlas-telegram.plist"

for secret in telegram.env anthropic.env; do
    if [ ! -f "$REPO/data/local-pilot/secrets/$secret" ]; then
        echo "missing $REPO/data/local-pilot/secrets/$secret" >&2
        exit 1
    fi
done

mkdir -p "$REPO/logs" "$REPO/state"
sed -e "s|__REPO__|$REPO|g" \
    -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__BOT_REF__|$BOT_REF|g" \
    -e "s|__OWNER_USER_ID__|$OWNER_USER_ID|g" \
    -e "s|__TENANT_ID__|$TENANT_ID|g" \
    -e "s|__BUSINESS_ID__|$BUSINESS_ID|g" \
    "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$(id -u)/com.agentos.atlas-telegram" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "installed and started com.agentos.atlas-telegram"
echo "logs: $REPO/logs/atlas-telegram.log"
