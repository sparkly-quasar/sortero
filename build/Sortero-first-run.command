#!/bin/bash
# Sortero — macOS first run
#
# Sortero is signed ad-hoc rather than notarised (that needs a paid Apple
# Developer account). When macOS downloads the zip it stamps a "quarantine"
# flag on the app AND on every one of the ~450 files inside it. Approving the
# app in System Settings only clears the flag on the outer bundle, so the
# loader stalls checking each nested library — the app bounces in the Dock and
# no window ever appears.
#
# This script clears that flag from the whole bundle and launches the app.
# It changes nothing else, and does not need your password.

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"

for CANDIDATE in "$DIR/Sortero.app" "/Applications/Sortero.app" "$HOME/Applications/Sortero.app"; do
    if [ -d "$CANDIDATE" ]; then
        APP="$CANDIDATE"
        break
    fi
done

if [ -z "${APP:-}" ]; then
    echo "Couldn't find Sortero.app next to this script or in /Applications."
    echo "Move this script beside Sortero.app and run it again."
    read -r -p "Press return to close."
    exit 1
fi

echo "Clearing the quarantine flag on:"
echo "  $APP"
echo

if xattr -dr com.apple.quarantine "$APP" 2>/dev/null; then
    echo "Done."
else
    echo "Couldn't clear it automatically."
    echo
    echo "If Sortero is in /Applications, macOS asks permission before another"
    echo "program may modify apps there. Either approve the prompt and run this"
    echo "again, or run the app from your Downloads folder instead."
    read -r -p "Press return to close."
    exit 1
fi

echo "Launching Sortero…"
open "$APP"
sleep 1
