#!/bin/sh
# Re-take the README screenshots from the running page (inference-vitals start, with a model working).
# WHAT=training takes the training screenshot instead: run it during or just after a fine-tune that
# inference-vitals follows (inference-vitals train -- ...), so the page is in training mode.
# Uses Google Chrome's headless mode with a throwaway profile, so your own Chrome is not touched.
set -eu
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT="$(cd "$(dirname "$0")" && pwd)/screenshots"
PROFILE="$(mktemp -d)"
URL="http://127.0.0.1:3000/?range=${RANGE:-15m}"  # the page; it also takes ?theme= and ?view= (the tab shown)
mkdir -p "$OUT"

shot() { # name, url, width, height
  "$CHROME" --headless=new --hide-scrollbars --user-data-dir="$PROFILE" --window-size="$3,$4" \
    --force-device-scale-factor=2 --virtual-time-budget=25000 --screenshot="$OUT/$1.png" "$2" 2>/dev/null
  echo "$OUT/$1.png"
}

shot warm-up "$URL" 800 600 && rm -f "$OUT/warm-up.png"  # a first load in a fresh profile can come back empty
if [ "${WHAT:-run}" = training ]; then
  shot training-dark "$URL&theme=dark&view=favourites" 1512 945
else
  shot at-a-glance-dark "$URL&theme=dark&view=favourites" 1512 945   # a 14-inch MacBook's screen
  shot at-a-glance-light "$URL&theme=light&view=favourites" 1512 945
  shot memory "$URL&theme=dark&view=memory" 1512 945
  shot power "$URL&theme=dark&view=hardware" 1512 945
fi
rm -rf "$PROFILE"
