#!/usr/bin/env bash
# Installs the leifslab.com start page as a new release next to the current one and
# switches the `current` link to it. Keeps the live services.json and favicon.
# Prints the command that switches back.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
SITE="${LEIFSLAB_HOME_DIR:-$HOME/media-server-stack/config/caddy/data/leifslab-home}"
LINK="$SITE/current"

for f in index.html styles.css app.js theme.js fetch_icons.py; do
  [ -s "$SRC/$f" ] || { echo "Missing $f next to this script; nothing changed." >&2; exit 1; }
done
OLD="$(readlink -f "$LINK")" || { echo "Cannot resolve $LINK; nothing changed." >&2; exit 1; }
[ -f "$OLD/services.json" ] || { echo "No services.json in $OLD; nothing changed." >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp "$SRC"/index.html "$SRC"/styles.css "$SRC"/app.js "$SRC"/theme.js "$STAGE/"
cp "$OLD/services.json" "$STAGE/"                       # keep the live service list
[ -f "$OLD/favicon.svg" ] && cp "$OLD/favicon.svg" "$STAGE/"   # keep the existing icon

# Tool logos: copied into the site (the CSP only allows same-origin images). Never fatal.
python3 "$SRC/fetch_icons.py" "$STAGE" "$OLD" "$SRC/icons" || echo "Logo step skipped; cards will use lettered tiles."

NAME="redesign-$(date +%Y%m%d-%H%M%S)"
DEST="$SITE/releases/$NAME"
sudo cp -r "$STAGE" "$DEST"
sudo chmod -R a+rX "$DEST"
sudo ln -sfn "releases/$NAME" "$LINK"

echo "New homepage is live: $DEST"
echo "To go back to the old page, run:"
echo "  sudo ln -sfn releases/$(basename "$OLD") $LINK"
