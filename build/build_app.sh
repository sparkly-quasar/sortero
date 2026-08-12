#!/bin/bash
# Build Sortero.app. Run from the repo root:  ./build/build_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=./.venv
[ -d "$VENV" ] || { echo "no venv - see README"; exit 1; }

echo "==> regenerating icon"
"$VENV/bin/python" build/gen_icon.py

echo "==> building app bundle"
rm -rf build/dist build/work dist/Sortero.app
# --specpath makes relative paths resolve against it, so pass the icon absolute
ICON="$(cd build && pwd)/Sortero.icns"
"$VENV/bin/pyinstaller" \
  --noconfirm --clean --windowed \
  --name Sortero \
  --icon "$ICON" \
  --osx-bundle-identifier com.sortero.app \
  --distpath build/dist \
  --workpath build/work \
  --specpath build \
  --hidden-import mutagen \
  --collect-submodules mutagen \
  run.py

APP="build/dist/Sortero.app"
[ -d "$APP" ] || { echo "build failed"; exit 1; }

# Ad-hoc sign so macOS will run it locally without a Gatekeeper prompt loop.
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "(codesign skipped)"

echo "==> built $APP"
du -sh "$APP"
