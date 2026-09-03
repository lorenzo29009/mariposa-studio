#!/usr/bin/env bash
# Vendor a STATIC ExtraBold Inter for the `ass` caption backend.
#
# Why this is a separate, explicit step:
#   * libass on this machine silently substitutes Helvetica when Inter is absent
#     (proven: `fontselect: (Inter, 400, 0) -> /System/Library/Fonts/Helvetica.ttc`,
#     exit 0, no warning). Shipping without the real font would change the look of
#     every deliverable without telling you.
#   * @remotion/google-fonts ships NO font binary — it fetches a VARIABLE woff2 at
#     render time. libass renders a variable font's DEFAULT instance, and Inter
#     defaults to wght=400, so handing libass the variable file would silently
#     render Regular instead of ExtraBold. It must be instanced to a static face.
#
# This downloads a font, so it is left for you to run deliberately.
set -euo pipefail
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$SKILL/template/fonts/Inter-ExtraBold.ttf"
mkdir -p "$(dirname "$OUT")"

if [ -f "$OUT" ]; then echo "already present: $OUT"; else
  # Prefer the exact file Remotion uses, so glyphs match the previous output.
  PROJ="${1:-}"
  URL=""
  if [ -n "$PROJ" ] && [ -d "$PROJ/node_modules/@remotion/google-fonts" ]; then
    URL=$(cd "$PROJ" && node -p "require('@remotion/google-fonts/Inter').getInfo().fonts.normal['800'].latin" 2>/dev/null || echo "")
  fi
  TMP=$(mktemp -d)
  if [ -n "$URL" ]; then
    echo "fetching the variable Inter that Remotion uses: $URL"
    curl -sSL "$URL" -o "$TMP/inter.woff2"
    # needs brotli to decompress woff2 -> use an ephemeral env, do not touch system python3.9
    uvx --with fonttools --with brotli python - "$TMP/inter.woff2" "$OUT" <<'PY'
import sys
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
f = TTFont(sys.argv[1])
axes = [(a.axisTag, a.minValue, a.defaultValue, a.maxValue) for a in f["fvar"].axes]
print("axes:", axes)
loc = {"wght": 800}
opsz = [a.maxValue for a in f["fvar"].axes if a.axisTag == "opsz"]
if opsz:
    loc["opsz"] = max(opsz)     # Chrome clamps optical size at this font-size
instancer.instantiateVariableFont(f, loc, inplace=True, updateFontNames=True)
f.flavor = None
f.save(sys.argv[2])
print("wrote", sys.argv[2])
PY
  else
    echo "Could not resolve the Remotion font URL." >&2
    echo "Download Inter from https://fonts.google.com/specimen/Inter and place the" >&2
    echo "STATIC ExtraBold face (static/Inter_28pt-ExtraBold.ttf) at:" >&2
    echo "  $OUT" >&2
    exit 1
  fi
  rm -rf "$TMP"
fi

echo "--- verifying ---"
python3 "$SKILL/scripts/check_font.py"
