#!/bin/sh
# Re-take the README screenshots from a running dashboard (inference-vitals start, with a model working).
# Uses Google Chrome's headless mode with a throwaway profile, so your own Chrome is not touched.
set -eu
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT="$(cd "$(dirname "$0")" && pwd)/screenshots"
PROFILE="$(mktemp -d)"
URL="http://127.0.0.1:3000/d/inference-vitals?orgId=1&from=${FROM:-now-30m}&to=${TO:-now}&kiosk"  # FROM/TO: Grafana times, e.g. epoch ms
mkdir -p "$OUT"

shot() { # name, url, width, height
  "$CHROME" --headless=new --hide-scrollbars --user-data-dir="$PROFILE" --window-size="$3,$4" \
    --force-device-scale-factor=2 --virtual-time-budget=25000 --screenshot="$OUT/$1.png" "$2" 2>/dev/null
  echo "$OUT/$1.png"
}

shot at-a-glance-dark "$URL&theme=dark" 1512 900
shot at-a-glance-light "$URL&theme=light" 1512 900
shot memory "$URL&theme=dark&viewPanel=31" 1200 600
shot power "$URL&theme=dark&viewPanel=37" 1200 600
rm -rf "$PROFILE"
