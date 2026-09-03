"""Font metrics for the ASS backend + the guards that stop a silent substitution.

Measured facts this encodes (see DEVNOTES):
  - libass follows VSFilter: ASS `Fontsize` is the CELL height
    (usWinAscent+usWinDescent), NOT the em size. So Fontsize = css_px * cell_em.
  - libass renders a VARIABLE font's default instance (Inter defaults to wght=400),
    so a variable Inter would silently render Regular instead of ExtraBold.
  - With no Inter installed, coretext substitutes Helvetica with no warning and
    exit code 0. Only parsing libass's `fontselect:` log proves what happened.
"""
import os

try:
    from fontTools.ttLib import TTFont
    HAVE_FONTTOOLS = True
except ImportError:                       # pragma: no cover
    HAVE_FONTTOOLS = False


class FontError(Exception):
    pass


class FontSpec(object):
    def __init__(self, path, family, postscript, upm, win_asc, win_desc,
                 hhea_asc, hhea_desc, typo_gap, weight, is_variable):
        self.path = path
        self.family = family
        self.postscript = postscript
        self.upm = upm
        self.win_asc = win_asc
        self.win_desc = win_desc
        self.hhea_asc = hhea_asc
        self.hhea_desc = hhea_desc
        self.typo_gap = typo_gap
        self.weight = weight
        self.is_variable = is_variable

    @property
    def cell_em(self):
        return (self.win_asc + self.win_desc) / float(self.upm)

    def ass_fontsize(self, css_px):
        return round(css_px * self.cell_em, 2)

    def baseline_correction(self, css_px):
        """Chrome's baseline-from-linebox-centre minus libass's baseline-from-\\an5-centre.

        The CSS line-height cancels out of Chrome's baseline, so the two agree
        whenever (hhea_asc - hhea_desc) == (win_asc - win_desc) and typo_gap == 0.
        For Inter that is true and this returns 0.0.
        """
        k = css_px / float(self.upm)
        chrome = ((self.hhea_asc + self.typo_gap / 2.0)
                  - (-self.hhea_desc + self.typo_gap / 2.0)) * k / 2.0
        libass = (self.win_asc - self.win_desc) * k / 2.0
        return chrome - libass

    def summary(self):
        return ("%s (%s) upm=%d winAsc=%d winDesc=%d cell_em=%.4f weight=%d variable=%s"
                % (self.family, self.postscript, self.upm, self.win_asc, self.win_desc,
                   self.cell_em, self.weight, self.is_variable))


def load_font_spec(path):
    if not HAVE_FONTTOOLS:
        raise FontError("fontTools not importable; cannot validate %s" % path)
    if not os.path.exists(path):
        raise FontError("font not found: %s" % path)
    f = TTFont(path, lazy=True)
    os2, hhea, head = f["OS/2"], f["hhea"], f["head"]
    family = postscript = None
    for rec in f["name"].names:
        try:
            val = rec.toUnicode()
        except Exception:
            continue
        if rec.nameID == 1 and family is None:
            family = val
        if rec.nameID == 16:              # typographic family wins when present
            family = val
        if rec.nameID == 6 and postscript is None:
            postscript = val
    spec = FontSpec(
        path=path, family=family, postscript=postscript,
        upm=head.unitsPerEm, win_asc=os2.usWinAscent, win_desc=os2.usWinDescent,
        hhea_asc=hhea.ascender, hhea_desc=abs(hhea.descender),
        typo_gap=getattr(os2, "sTypoLineGap", 0), weight=os2.usWeightClass,
        is_variable="fvar" in f,
    )
    f.close()
    return spec


def assert_burnable(spec):
    """Refuse to burn with a font that would render the wrong thing."""
    if spec.is_variable:
        raise FontError(
            "%s is a VARIABLE font. libass renders the default instance (Inter "
            "defaults to wght=400), so captions would come out Regular, not "
            "ExtraBold. Instance it to a static ExtraBold first." % spec.path)
    if spec.weight < 700:
        raise FontError("%s has usWeightClass=%d; expected >=700 (ExtraBold)."
                        % (spec.path, spec.weight))
    return True
