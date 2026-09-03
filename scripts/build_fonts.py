#!/usr/bin/env python3
"""Build the static brand TTFs Qt can load, from the variable woff2 sources.

Qt's QFontDatabase cannot read woff2, and it matches families by name +
OS/2 weight class — so we decompress each variable source and *instance* it
at the weights the design system actually uses, writing one plain TTF per
weight into ``brand/fonts/``.

Sources live in ``brand/fonts/_src/`` so this is reproducible from a clone
with no reference to anybody's Desktop.

Run once after changing the sources or the weight list:

    ./venv/bin/python -m pip install --target /tmp/ft fonttools brotli
    PYTHONPATH=/tmp/ft ./venv/bin/python scripts/build_fonts.py

fontTools is a *build* dependency only — the app never imports it.
"""
from __future__ import annotations

from pathlib import Path

try:
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
except ModuleNotFoundError:                                  # pragma: no cover
    raise SystemExit(
        "fontTools is missing. This is a build-only dependency:\n"
        "  ./venv/bin/python -m pip install --target /tmp/ft fonttools brotli\n"
        "  PYTHONPATH=/tmp/ft ./venv/bin/python scripts/build_fonts.py"
    )

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "brand" / "fonts"
SRC_DIR = FONT_DIR / "_src"

# Standard style names Qt maps onto a weight. Keep to these four: an
# invented subfamily ("Book", "Heavy") makes Qt spawn a separate family.
STYLE = {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold"}

# family name -> (source woff2, weights to cut)
FAMILIES = {
    # Display face: headings, tool names, the wordmark. 500 is unused today
    # but cut anyway so a lighter heading is a token change, not a build.
    "Cabinet Grotesk": ("CabinetGrotesk-Variable.woff2", (500, 600, 700)),
    # Interface face: body, labels, buttons, meta.
    "Satoshi": ("Satoshi-Variable.woff2", (400, 500, 600, 700)),
}


def _set_names(font: TTFont, family: str, weight: int) -> None:
    """Rewrite the name table so Qt sees one family with four weights."""
    style = STYLE[weight]
    name = font["name"]
    full = f"{family} {style}"
    ps = f"{family.replace(' ', '')}-{style}"
    # RIBBI slots (1/2) must stay within Regular/Bold/Italic/Bold Italic for
    # legacy matching; anything else goes in the typographic slots (16/17).
    ribbi = style if style in ("Regular", "Bold") else "Regular"
    for nid, value in (
        (1, family if ribbi == "Regular" else family),
        (2, ribbi),
        (3, f"Mariposa Studio: {full}"),
        (4, full),
        (6, ps),
        (16, family),
        (17, style),
    ):
        name.setName(value, nid, 3, 1, 0x409)   # Windows / Unicode BMP / en-US
        name.setName(value, nid, 1, 0, 0)       # Mac Roman
    # Drop the variable-font leftovers: instance records and the axis name
    # strings would otherwise advertise a family that no longer exists.
    for nid in list({r.nameID for r in name.names}):
        if nid >= 256:
            name.removeNames(nameID=nid)


def build() -> int:
    written: list[Path] = []
    for family, (src_name, weights) in FAMILIES.items():
        src = SRC_DIR / src_name
        if not src.exists():
            raise SystemExit(f"missing source font: {src}")
        for weight in weights:
            font = TTFont(src)                      # woff2 in, ttf out
            font.flavor = None                      # stop it re-compressing
            instancer.instantiateVariableFont(font, {"wght": weight}, inplace=True)
            font["OS/2"].usWeightClass = weight
            # fsSelection: bit 0 ITALIC, bit 5 BOLD, bit 6 REGULAR. Exactly
            # one of BOLD/REGULAR may be set, and it must agree with macStyle.
            os2 = font["OS/2"]
            os2.fsSelection &= ~0b0110_0001
            os2.fsSelection |= 0b0010_0000 if weight >= 700 else 0b0100_0000
            font["head"].macStyle = 1 if weight >= 700 else 0
            _set_names(font, family, weight)
            out = FONT_DIR / f"{family.replace(' ', '')}-{weight}.ttf"
            font.save(out)
            font.close()
            written.append(out)
            print(f"  wrote {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB)")

    print(f"\n{len(written)} static TTF(s) in {FONT_DIR.relative_to(ROOT)} — "
          "design.load_fonts() picks them up by glob, no code change needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
