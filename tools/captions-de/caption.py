#!/usr/bin/env python3
"""
Generate TikTok-style captions (SRT) from a video file.
German is the default; English, Polish, French, Italian and Spanish (as
spoken in Spain) are selected with --language and adapt transcription,
prompts, brand spelling and the formatting safety nets to that language.

Usage:
    python caption.py video.mp4
    python caption.py video.mp4 --out custom_name.srt
    python caption.py video.mp4 --language es     # Spanish (also: en, pl, fr, it)
    python caption.py video.mp4 --no-ai           # skip Gemini, use heuristic only
    python caption.py video.mp4 --model medium    # use smaller Whisper model
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Windows consoles default to the legacy cp1252 codec, which can't encode the
# non-ASCII characters we print (arrows, accented language names, …). Force
# UTF-8 on our own streams so output never crashes mid-job, regardless of how
# the script was launched (app or shell).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

_SCRIPT_DIR = Path(__file__).resolve().parent
_ENV_PATH = _SCRIPT_DIR / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        if _k and _v and _k not in os.environ:
            os.environ[_k] = _v

LINE_MAX = 20
LEAD_MAX = 0.150
TRAIL_MIN = 0.0
# Shortest a single-line caption may stay on screen before single-line splitting
# prefers a (readable) two-line caption instead. Matches merge_short_durations.
MIN_PIECE_DUR = 0.6

# ─── Real-screen line width model ────────────────────────────────────────────
# A line is budgeted by *real width*, not by raw character count: narrow German
# letters (i, l, t, r, f, …) cost ~half a wide letter (m, w), so 25 narrow chars
# can fit where 20 wide ones do not.
#
# The BUDGET is the width at which the renderer itself wraps. Getting it wrong is
# not cosmetic: a line over budget is re-wrapped downstream, and a 2-line caption
# lands on screen as 3 or 4 lines. It was 22.5 for a long time, taken from an old
# CapCut style (black text on a white rounded box, measured on a 720-px frame:
# ≈26 px per unit + 15 px padding/side). The house style is now white text with a
# black stroke in a box that wraps at `line_max_width` = 0.82 of the frame, and
# that font is ~12% wider per unit. Measured on the rendered C1040:
#     "und außer Herzrasen hat"     19.5 u  fits
#     "ich habe mich gerade so"     19.9 u  fits
#     "mit meinem Arzt gestritten"  20.7 u  WRAPS
#     "und außer Herzrasen hat mir" 22.4 u  WRAPS
# so the real ceiling sits just above 20. Re-measure (one screenshot of a burned
# caption is enough) if the caption font or `line_max_width` ever changes.
NARROW_CHARS = set("iIlíj.,'!:;|ftr ()[]-")
WIDE_CHARS = set("mwMW—")
LINE_W_MAX = 20.0     # max width units on one visible line (~82% of frame width)
ORPHAN_W_MAX = 7.0    # a single word this narrow is too short to stand alone
# A multi-word line can wrap at a space, so it may use the full LINE_W_MAX. A
# single long compound word CANNOT — if its real (bold CapCut) width tops the
# line it spills mid-word onto an ugly 3rd line ("Schilddrüsenunterfunktion",
# 21 units / 25 chars, did exactly that). So a solo compound is hyphenated with a
# safety margin BELOW the line budget; shorter ones ("Wassereinlagerungen",
# 17.4) still sit whole on their own line.
SOLO_WORD_W_MAX = 17.5


def text_width(s: str) -> float:
    """Approximate the rendered width of a string in proportional "units"
    (1.0 ≈ one average glyph). Used everywhere a raw len() used to gate the
    line length, so packing reflects real on-screen space."""
    total = 0.0
    for c in s:
        if c in NARROW_CHARS:
            total += 0.5
        elif c in WIDE_CHARS:
            total += 1.4
        else:
            total += 1.0
    return total

# Closed-class German function words for the TikTok house-style "lowercase even
# at caption start" rule (used by normalize_case). Intentionally EVERGREEN: only
# closed classes (articles, pronouns, possessives, demonstratives, question /
# relative words, conjunctions, prepositions, modal & auxiliary verbs, and
# function particles/adverbs). Open-class words — nouns, lexical verbs,
# adjectives — are deliberately NOT listed: German grammar already lowercases
# them and the Gemini prompt (rule V) enforces it, so enumerating them would
# only overfit to one video's vocabulary.
GERMAN_LOWERCASE = {
    # articles & negation determiners
    "der","die","das","den","dem","des","ein","eine","einen","einem","eines","einer",
    "kein","keine","keinen","keinem","keiner","keines",
    # personal pronouns
    "ich","du","er","es","wir","sie","man",
    "mich","dich","sich","mir","dir","ihm","ihn","uns","euch","ihnen",
    # possessive pronouns
    "mein","meine","meinen","meinem","meiner","meines",
    "dein","deine","deinen","deinem","deiner","deines",
    "sein","seine","seinen","seinem","seiner","seines",
    "ihr","ihre","ihren","ihrem","ihrer","ihres",
    "unser","unsere","unseren","unserem","unserer","unseres",
    "euer","eure","euren","eurem","eurer","eures",
    # demonstratives & quantifying determiners
    "diese","dieser","dieses","diesen","diesem",
    "jene","jener","jenes","jenen","jenem",
    "alle","allen","aller","allem","beide","beiden",
    "manche","mancher","manches","manchen","manchem",
    "einige","einiger","einigen","mehrere","mehreren",
    "viel","viele","vielen","wenig","wenige","selber","selbst",
    # question / relative words
    "wer","wen","wem","was","wie","wo","wann","warum","wieso","weshalb",
    "welche","welcher","welches","welchen","welchem","wohin","woher",
    "worüber","worauf","wozu","wofür","womit",
    # conjunctions
    "und","oder","aber","denn","doch","sondern","weil","dass","wenn","als","ob",
    "obwohl","während","bevor","nachdem","damit","sodass","falls","sofern","indem",
    # prepositions & contracted prepositions
    "an","auf","aus","bei","durch","für","gegen","in","mit","nach","ohne",
    "seit","über","um","unter","vor","zu","zwischen","zum","zur","vom",
    "ins","ans","aufs","beim","im","am",
    # da-/wo- pronominal adverbs
    "dadurch","dafür","davon","darum","dabei","dazu","daran","darauf","darin",
    # function particles / adverbs
    "ja","nein","nicht","auch","nur","schon","noch","immer","nie","niemals",
    "dann","jetzt","hier","da","dort","heute","gestern","morgen","bitte","danke",
    "also","zwar","eben","halt","mal","wohl","etwa","etwas","alles","nichts",
    "vielleicht","natürlich","leider","endlich","trotzdem","einfach",
    "sehr","ganz","ziemlich","so","wirklich","überhaupt","sogar","gerade",
    "mehr","weniger","meist","genug","wieder","oft","manchmal","fast","kaum","gleich",
    # auxiliary & modal verbs
    "bin","bist","ist","sind","seid","war","warst","waren","wart","sei","wäre","wären",
    "habe","hast","hat","haben","habt","hatte","hattest","hatten","hattet","hätte","hätten",
    "werde","wirst","wird","werden","werdet","wurde","wurden","würde","würden",
    "kann","kannst","können","könnt","konnte","konnten","könnte","könnten",
    "muss","musst","müssen","müsst","musste","mussten",
    "darf","darfst","dürfen","dürft","durfte","durften",
    "soll","sollst","sollen","sollt","sollte","sollten",
    "will","willst","wollen","wollt","wollte","wollten",
    "mag","magst","mögen","mögt","möchte","möchten",
    # more closed-class function words (prepositions, distributive determiners,
    # connective adverbs) that commonly OPEN a caption and so were being left
    # capitalised — all evergreen function words, not video vocabulary
    "von","bis","ab","je","pro","samt","gegenüber",
    "jeder","jede","jedes","jeden","jedem","jegliche","jeglicher",
    "egal","insgesamt","irgendwann","irgendwie","irgendwo","irgendwas",
    "sonst","deshalb","deswegen","dennoch","jedoch","allerdings","außerdem",
    "ohnehin","sowieso","eigentlich","bereits","überall","nirgends",
}

# Formal-address homographs: same spelling as lowercase function words above,
# but capitalized when they mean the formal "you" (Sie/Ihr/Ihnen…). Gemini
# (prompt rule V) sets their case from context; the safety net must NOT blindly
# lowercase them or it would destroy the formal address. Defers to Gemini for
# these specific words, so the same code serves both Duzen and Siezen content.
GERMAN_FORMAL_HOMOGRAPHS = {
    "sie", "ihr", "ihre", "ihren", "ihrem", "ihrer", "ihres", "ihnen",
}

# Optional per-project overrides to hyphenate specific long words at a chosen
# boundary. Empty by default (evergreen) — auto_hyphenate falls back to the
# generic compound-prefix split below.
FORCE_HYPHEN: dict = {}

# First-elements used to hyphenate an over-long German compound at a meaningful
# boundary ("Geschwindigkeits-begrenzung"). A mix of GENERIC high-frequency
# German compound stems (so this works for any video) plus the brand's health
# domain (intentional — the brand is thyroid-focused). It only ever affects
# words longer than one line, so extra entries are harmless.
COMPOUND_PREFIXES = [
    # generic high-frequency German compound first-elements
    "Lebens", "Arbeits", "Zukunfts", "Sicherheits", "Wirtschafts",
    "Gesellschafts", "Geschwindigkeits", "Versicherungs", "Verantwortungs",
    "Erfahrungs", "Behandlungs", "Untersuchungs", "Entscheidungs",
    "Ernährungs", "Bewegungs", "Gewohnheits", "Bedürfnis",
    "Haupt", "Grund", "Gesamt", "Gemeinschafts",
    # health / brand domain (thyroid)
    "Gesundheits", "Schilddrüsen", "Schilddrüse", "Stoffwechsel",
    "Stoffwechselstörung", "Umwandlungs", "Umwandlung",
    "Wassereinlag", "Wasserein", "Konzentrations", "Konzentration",
    "Hormon", "Hormonhaushalt", "Gewichts", "Gewichtsverlust",
    "Gewichtszunahme", "Blutdruck", "Antriebs", "Energie",
    "Magen", "Darm", "Leber", "Nieren", "Knochen", "Gelenks", "Muskel", "Herz",
    "Hashimoto",
]


def auto_hyphenate(word: str) -> str:
    # Hyphenate a solo compound word whose rendered width approaches the line
    # budget (SOLO_WORD_W_MAX) — it can't wrap at a space, so left whole it
    # spills mid-word onto a 3rd line. A genuinely short compound — even a
    # long-looking one like "Schilddrüsenwerte" or "Wassereinlagerungen" — stays
    # whole on its own line with NO hyphen. The hyphen exists solely to keep an
    # over-long word from overflowing its line.
    if "\n" in word or text_width(word) <= SOLO_WORD_W_MAX:
        return word
    if word in FORCE_HYPHEN:
        return FORCE_HYPHEN[word]
    if "-" in word:
        return word
    for prefix in sorted(COMPOUND_PREFIXES, key=len, reverse=True):
        for cand in (prefix, prefix.lower()):
            if word.startswith(cand) and len(word) > len(cand) + 4:
                return word[:len(cand)] + "-" + word[len(cand):]
    return word


def apply_auto_hyphenation(text: str) -> str:
    if ACTIVE_LANG != "de":
        return text
    parts = []
    for chunk in text.split("\n"):
        words = chunk.split()
        words = [auto_hyphenate(w) for w in words]
        parts.append(" ".join(words))
    return "\n".join(parts)


# A "soft" compound hyphen is a line-break hyphen the model inserted INSIDE a
# German compound: a letter, a hyphen, optionally a newline, then a LOWERCASE
# continuation (auto_hyphenate splits compounds exactly this way). Real hyphens
# keep an uppercase letter or digit on the right — E-Mail, T-Shirt, 100-Meter,
# Work-Life-Balance — so they are left intact, as is the German suspended
# compound "Nord- und Südseite" (a space follows the hyphen there).
_SOFT_HYPHEN_RE = re.compile(r"(?<=\w)-\n?([a-zäöüß])")


def join_soft_hyphens(text: str) -> str:
    """Undo a model-inserted compound line-break hyphen so a word that fits one
    line is shown WHOLE (no mid-line hyphen). Auto-hyphenation only exists to
    break a word ACROSS two lines, never to show a hyphen inside one line.
    German-only: other languages never auto-hyphenate, and their real lowercase
    hyphens ("est-ce que", "quatre-vingts", "biało-czerwony") must survive."""
    if ACTIVE_LANG != "de":
        return text
    return _SOFT_HYPHEN_RE.sub(r"\1", text)


# A hyphen at the end of a line ("Schilddrüsen-\nbehandlungen", "Geld-\nZurück")
# is a word broken across two lines. When such a caption is flattened to one
# line, the "\n" must NOT become a space — that would leave a stray "wort- wort"
# whose hyphen no longer breaks anything (the word now sits whole on one line).
_LINEBREAK_HYPHEN_RE = re.compile(r"-[ \t]*\n[ \t]*")


def flatten_lines(text: str) -> str:
    """Collapse a caption's line break(s) onto one line for re-packing. A "\n"
    that directly follows a hyphen rejoins with NO space (the word was split
    across lines); join_soft_hyphens then drops the hyphen for a lowercase
    compound continuation ("Schilddrüsenbehandlungen"), while a real hyphen
    before an uppercase/digit continuation ("Geld-Zurück") is kept. Every other
    "\n" becomes a normal space. This is the single safe way to flatten — a bare
    text.replace("\\n", " ") would smuggle a stray mid-line hyphen into the .srt."""
    return join_soft_hyphens(_LINEBREAK_HYPHEN_RE.sub("-", text)).replace("\n", " ")


# A soft compound hyphen is only meaningful at the END of a line — it breaks an
# over-long word across two lines. If packing ends up with both halves on the
# SAME line, the hyphen breaks nothing and must vanish (the word reads whole).
# Matches a hyphen between two word chars with the continuation on the same line;
# a hyphen before a space ("Muskel- und"), an uppercase/digit ("Geld-Zurück",
# "90-Tage") or a line end is left intact.
_MIDLINE_HYPHEN_RE = re.compile(r"(?<=\w)-([a-zäöüß])")


def drop_midline_hyphens(text: str) -> str:
    """Collapse a soft compound hyphen that packing left mid-line (both halves on
    one visible line) back into the whole word. End-of-line hyphens — the half
    before a "\\n" — are kept, since there the hyphen really does break the word
    across the two lines. German-only, like join_soft_hyphens — other languages'
    lowercase hyphens are real spelling, never soft breaks."""
    if ACTIVE_LANG != "de":
        return text
    return "\n".join(_MIDLINE_HYPHEN_RE.sub(r"\1", ln) for ln in text.split("\n"))


# Apostrophe variants a transcriber or a model may emit — the typographic ’,
# the modifier letter ʼ, and the accents people type instead. To a reader they
# are all the same mark, but only the straight ' survives clean_for_output, so a
# curly one used to DELETE itself: "don’t" came out "dont", "l’ho" came out
# "lho". English lives on contractions (and French/Italian on elision), so they
# are folded to the straight form before anything else looks at the word.
_APOSTROPHE_RE = re.compile("[\u2019\u2018\u02bc\u02b9\u2032\u00b4\u0060]")


def normalize_apostrophes(s: str) -> str:
    return _APOSTROPHE_RE.sub("'", s)


def strip_punct(w: str) -> str:
    # \w in UNICODE mode covers letters from all the languages we care about
    # (ä, é, ñ, à, ç, ü, ...).
    return re.sub(r"[^\w'\-]", "", normalize_apostrophes(w), flags=re.UNICODE)


def clean_for_output(w: str) -> str:
    # Allow Unicode word characters + the punctuation we want to preserve.
    # Spanish opens a question with ¿ and an exclamation with ¡. The ? survives
    # here, so its ¿ does too; the ! never does, so neither may its ¡ — keeping
    # it put a dangling "¡qué bien" on screen with nothing to close it.
    return re.sub(r'[^\w\'\-?%/&"¿]', "", normalize_apostrophes(w),
                  flags=re.UNICODE)


# Active language is set by main() at startup. Default is German so existing
# call sites and tests behave as before.
ACTIVE_LANG = "de"

# Words the CURRENT video uses as a noun/name — learned from capitalised
# mid-caption occurrences by learn_and_relabel_case(). normalize_case consults
# it so the static function-word list never force-lowercases a word this video
# clearly capitalises (e.g. the noun "Morgen" vs the adverb "morgen"). Empty
# until learning runs, so default behaviour is unchanged.
LEARNED_UPPER: set = set()

# Set True by recase_with_ai() when a dedicated Gemini casing pass has corrected
# the captions' capitalisation. While True, normalize_case() trusts that result
# (German grammar from the model) and stops force-lowercasing from the static
# function-word list, which can only mishandle homographs (Morgen/morgen, Sie/sie).
CASE_FIXED_BY_AI = False

# Caption length mode, set by main() at startup. "hybrid" (default) is the
# long-standing behaviour: a natural mix of 1- and 2-line captions. "1" asks
# the segmenter for one line per caption (shorter, more numerous units); the
# packing safety nets are unchanged, so an indivisible unit that can't fit one
# line (e.g. a long German compound) still falls back to its required 2-line
# form. Default "hybrid" keeps existing call sites / tests bit-for-bit.
LINE_MODE = "hybrid"


def normalize_case(word: str) -> str:
    out = clean_for_output(word)
    if not out:
        return out
    # German-only: lowercase function words even at the start of a caption
    # (TikTok-German house style).
    if ACTIVE_LANG == "de" and not CASE_FIXED_BY_AI:
        low = strip_punct(word).lower()
        if (low and low in GERMAN_LOWERCASE and low not in GERMAN_FORMAL_HOMOGRAPHS
                and low not in LEARNED_UPPER and out[0].isupper()):
            return out[0].lower() + out[1:]
    return out


def insert_compound_hyphens(text: str) -> str:
    # Compound-noun hyphenation is a German-specific concern. Other languages
    # don't have the long-compound problem and shouldn't get auto-hyphens.
    if ACTIVE_LANG != "de":
        return text
    return " ".join(auto_hyphenate(w) for w in text.split())


def tokenize_for_packing(text: str):
    tokens = []
    for word in text.split():
        parts = re.findall(r"[^-]+-?", word)
        merged = []
        for p in parts:
            if merged and merged[-1].endswith("-") and len(merged[-1].rstrip("-")) < 5:
                merged[-1] += p
            else:
                merged.append(p)
        for j, p in enumerate(merged):
            sep = " " if j == len(merged) - 1 else ""
            tokens.append((p, sep))
    return tokens


def pack_lines(text: str) -> str:
    if text_width(text) <= LINE_W_MAX:
        return text
    tokens = tokenize_for_packing(text)
    n = len(tokens)
    if n < 2:
        return text

    def build(start, end):
        out = ""
        for i in range(start, end):
            t, s = tokens[i]
            out += t
            if i < end - 1:
                out += s
        return out

    # Only splits where BOTH halves fit the budget are candidates. There used to be
    # a "relaxed" pool here that accepted up to 1.35x the budget when no real split
    # existed — it turned a caption that is simply too long for two lines into two
    # over-wide lines, which the renderer then wrapped again into three or four.
    # A caption this long is a segmentation problem (enforce_two_lines splits it);
    # if one still reaches here, three lines inside the budget beat two outside it,
    # because the renderer leaves those alone.
    strict = []
    for split in range(1, n):
        a = build(0, split).rstrip()
        b = build(split, n).rstrip()
        wa, wb = text_width(a), text_width(b)
        if max(wa, wb) <= LINE_W_MAX:
            strict.append((a, b, abs(wa - wb)))

    if strict:
        strict.sort(key=lambda x: x[2])
        return strict[0][0] + "\n" + strict[0][1]

    lines, current = [], ""
    for t, sep in tokens:
        prospective = (current + t).rstrip()
        if not current:
            current = t + sep
        elif text_width(prospective) <= LINE_W_MAX:
            current += t + sep
        else:
            lines.append(current.rstrip())
            current = t + sep
    if current:
        lines.append(current.rstrip())
    return "\n".join(lines)


def format_caption(text: str) -> str:
    return pack_lines(insert_compound_hyphens(text))


def fmt_time(t: float) -> str:
    if t < 0:
        t = 0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def get_video_duration(video_path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-i", str(video_path)],
        capture_output=True, text=True,
    )
    m = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not m:
        raise RuntimeError(f"Could not read duration from {video_path}")
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def _is_apple_silicon() -> bool:
    """True on Apple Silicon hardware — even when this process is itself running
    translated under Rosetta (where platform.machine() lies and reports x86_64).
    hw.optional.arm64 reflects the CPU, not the running architecture."""
    try:
        out = subprocess.run(
            ["sysctl", "-in", "hw.optional.arm64"],
            capture_output=True, text=True,
        )
        return out.stdout.strip() == "1"
    except Exception:
        return False


def run_whisperx(video_path: Path, model: str, output_dir: Path,
                  language: str = "de") -> Path:
    whisperx_bin = shutil.which("whisperx")
    if not whisperx_bin:
        venv_whisperx = Path.home() / "whisperx" / "bin" / "whisperx"
        if venv_whisperx.exists():
            whisperx_bin = str(venv_whisperx)
        else:
            venv_whisperx = Path.home() / "whisperx" / "Scripts" / "whisperx.exe"
            if venv_whisperx.exists():
                whisperx_bin = str(venv_whisperx)
    if not whisperx_bin:
        sys.exit("Error: whisperx not found. Run install.py first.")

    print(f"Transcribing {video_path.name} with WhisperX ({model}, lang={language})...")
    cmd = [
        whisperx_bin, str(video_path),
        "--model", model,
        "--language", language,
        "--device", "cpu",
        "--compute_type", "int8",
        "--vad_method", "silero",
        "--output_format", "json",
        "--output_dir", str(output_dir),
    ]
    # NO --hotwords. Handing the canonical terms to the transcriber as hint
    # phrases looks free and is not: measured on one window of a real clip,
    # same audio and same settings, `--hotwords "miavola, L-Thyroxin"` cut the
    # transcription from 98 words to 85 and ended it on an invented
    # "L-Thyroxin" (score 0.12) where the audio says "Nährstoffe in zwei
    # kleinen Kapseln am Tag". The bias makes the decoder spend the chunk on
    # the hinted word and abandon the rest — and a 30-second chunk collapsed
    # that way is 30 seconds of missing captions. Brand spelling is repaired
    # AFTER the fact instead, by apply_canonical_terms(), which cannot lose a
    # word because it only rewrites one that is already there.
    # The ~/whisperx interpreter is a *universal* binary, but torch is installed
    # arm64-only. If anything in our launch chain runs under Rosetta (a Terminal
    # opened with Rosetta, an x86_64 parent process, a stale "Open using Rosetta"
    # flag…), the universal python inherits that preference and starts as x86_64
    # -> torch's dylibs fail to load ("incompatible architecture, have arm64,
    # need x86_64"). Pin the encode to the native slice so it always matches the
    # installed torch. We probe the *hardware* via sysctl rather than
    # platform.machine(), because the latter reports "x86_64" when we ourselves
    # are running translated — exactly the case we need to catch.
    if sys.platform == "darwin" and _is_apple_silicon():
        cmd = ["arch", "-arm64", *cmd]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        sys.exit(
            "Error: WhisperX failed to load the model. If the log above shows "
            "'mkl_malloc: failed to allocate memory', the machine ran out of "
            "RAM for the large-v3 model — close other apps and try again, or "
            "use a machine with more memory (large-v3 needs ~3–4 GB free)."
        )
    # WhisperX writes <stem>.json. Move it to a per-language cache file so
    # switching the language doesn't reuse the wrong transcription.
    default_path = output_dir / (video_path.stem + ".json")
    target = output_dir / f"{video_path.stem}.{language}.json"
    if default_path.exists() and default_path != target:
        if target.exists():
            target.unlink()
        default_path.rename(target)
    if not target.exists():
        raise RuntimeError(f"WhisperX did not produce {target}")
    return target


#: A silence this long INSIDE a monologue is not a pause — it is audio the
#: transcriber lost. Measured on a real 92 s clip: WhisperX decodes in ~30 s
#: chunks, one chunk collapsed, and the file came back with a 30.4 s hole whose
#: only residue was a 0.2 s hallucinated word (score 0.04) at its edge. The
#: breathing pauses in the same clip were all under 0.5 s. Nothing in WhisperX
#: retries a collapsed chunk, so the caption tool has to notice and ask again.
GAP_SUSPECT = 5.0
#: Context given to the re-ask on each side. Generous on purpose: a chunk
#: collapses at the EDGE of what it was given, and with 1.0 s the collapse ate
#: 1.4 s of real speech at the end of the recovered window. The padding is
#: discarded anyway, so it is the cheapest place to spend the risk.
GAP_PAD = 2.5
#: A file gets at most this many re-asks, so a pathological clip cannot spawn a
#: queue of transcriptions.
GAP_REPAIRS_MAX = 3


def find_gaps(words: list, threshold: float = GAP_SUSPECT) -> list:
    """Windows BETWEEN two transcribed words that produced no words at all.

    Only internal holes: the quiet before the first word and after the last one
    is ordinary ad topping and tailing, not a lost chunk."""
    gaps = []
    prev = None
    for w in words:
        if prev is not None and w["start"] - prev > threshold:
            gaps.append((prev, w["start"]))
        prev = w["end"] if prev is None else max(prev, w["end"])
    return gaps


def _extract_window(video_path: Path, start: float, end: float, dest: Path) -> bool:
    """16 kHz mono WAV of one window — what the transcriber wants anyway."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}",
             "-to", f"{end:.3f}", "-i", str(video_path),
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)],
            check=True,
        )
        return dest.exists()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False


def repair_gaps(video_path: Path, words: list, model: str, output_dir: Path,
                language: str, json_path: Path) -> list:
    """Ask the transcriber again for every hole, and merge what comes back.

    A hole is one collapsed decode chunk, and the same audio transcribes
    perfectly when it is the only thing asked about — that is the whole repair:
    cut the window out, transcribe it on its own, shift the timings back onto
    the clip's timeline, and keep the words that fall inside the hole.

    The merged result is written back into the cached transcription, because the
    cache is what the next run reads: without that, a clip that lost 30 seconds
    kept losing them on every re-run."""
    gaps = find_gaps(words)
    if not gaps:
        return words
    import tempfile
    recovered: list = []
    for i, (a, b) in enumerate(gaps):
        if i >= GAP_REPAIRS_MAX:
            print(f"⚠ {len(gaps) - i} more silent window(s) left as they are "
                  f"({GAP_REPAIRS_MAX} re-asks is the cap)")
            break
        print(f"⚠ No words between {a:.1f}s and {b:.1f}s — {b - a:.1f}s of audio "
              f"produced nothing. Asking again for that window...")
        lo = max(0.0, a - GAP_PAD)
        hi = b + GAP_PAD
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "window.wav"
            if not _extract_window(video_path, lo, hi, wav):
                print("  ...could not cut that window out; leaving it as it is")
                continue
            try:
                win_json = run_whisperx(wav, model, Path(tmp), language=language)
                win_words = load_words(win_json)
            except (RuntimeError, SystemExit, OSError, ValueError) as e:
                print(f"  ...the re-ask failed ({e}); leaving the window as it is")
                continue
        got = []
        for w in win_words:
            start = w["start"] + lo
            end = w["end"] + lo
            # Only what belongs to the hole: the padding exists to give the
            # model context, not to re-transcribe the neighbours.
            if start >= a - 0.05 and end <= b + 0.05:
                got.append({**w, "start": round(start, 3), "end": round(end, 3)})
        if not got:
            print("  ...nothing was said in there after all")
            continue
        print(f"  ...recovered {len(got)} words")
        recovered.extend(got)

    if not recovered:
        return words
    merged = sorted(words + recovered, key=lambda w: w["start"])
    _rewrite_cache(json_path, recovered)
    print(f"Transcription repaired: {len(words)} words -> {len(merged)}")
    return merged


def _rewrite_cache(json_path: Path, recovered: list) -> None:
    """Fold the recovered words into the cached transcription, as one segment
    per repaired window, so a re-run starts from the repaired text."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        seg = {
            "start": recovered[0]["start"],
            "end": recovered[-1]["end"],
            "text": " " + " ".join(w["word"] for w in recovered),
            "words": recovered,
        }
        data["segments"] = sorted(list(data.get("segments", [])) + [seg],
                                  key=lambda s: s.get("start", 0.0))
        if "word_segments" in data:
            data["word_segments"] = sorted(
                list(data["word_segments"]) + recovered,
                key=lambda w: w.get("start", 0.0))
        json_path.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")
    except (OSError, ValueError, KeyError, IndexError) as e:
        # The repair itself already worked; a cache we could not update only
        # costs the next run another re-ask.
        print(f"  (could not update the cached transcription: {e})")


#: An alignment score this low is not a quiet word — it is a word the decoder
#: invented. Measured on a clip that lost a chunk: its three junk words scored
#: 0.024, 0.043 and 0.060 while the lowest REAL word in the same file scored
#: 0.327, half the scale away. Whisper leaves this residue at the edge of a
#: collapsed chunk, and one of them reached the .srt as a caption reading
#: "in 2 L-Thyroxin" in the middle of a product pitch — the brand's own
#: competitor, invented, on screen. Truncated is survivable; wrong is not.
JUNK_SCORE = 0.15

#: Words one letter long, per language. German has none, so the score above was
#: calibrated on words that have acoustic substance. Spanish's "y", "o", "a" are
#: a few milliseconds of vowel, and the Spanish aligner scores a real one 0.004 —
#: below every invented word the threshold exists for — so it silently deleted
#: the "y" of "siempre frías y la báscula". A word this short is only junk where
#: junk lives: at the edge of a hole, not between two words of running speech.
SHORT_WORDS_BY_LANG = {
    "es": {"y", "e", "o", "u", "a"},
}
#: How close both neighbours must be for a short word to count as running speech.
SHORT_WORD_NEIGHBOUR = 1.0


def _in_running_speech(prev, w, nxt) -> bool:
    """A short word flanked on both sides by words within SHORT_WORD_NEIGHBOUR."""
    short = SHORT_WORDS_BY_LANG.get(ACTIVE_LANG, ())
    if strip_punct(w.get("word", "")).lower() not in short:
        return False
    if prev is None or nxt is None:
        return False
    return (w["start"] - prev["end"] <= SHORT_WORD_NEIGHBOUR
            and nxt["start"] - w["end"] <= SHORT_WORD_NEIGHBOUR)


def load_words(json_path: Path):
    data = json.load(open(json_path, encoding="utf-8"))
    words = []
    junk = []
    timed = [w for seg in data["segments"] for w in seg.get("words", [])
             if "start" in w and "end" in w]
    for i, w in enumerate(timed):
        if w.get("score", 1.0) < JUNK_SCORE:
            prev = timed[i - 1] if i > 0 else None
            nxt = timed[i + 1] if i + 1 < len(timed) else None
            if not _in_running_speech(prev, w, nxt):
                junk.append(w)
                continue
        words.append(w)
    if junk:
        # Never silently: a dropped word is a decision about the copy, and the
        # operator is the one who knows whether that word was really said.
        shown = ", ".join(f"{w['word'].strip()!r} at {w['start']:.1f}s "
                          f"(score {w.get('score', 0):.2f})" for w in junk[:6])
        print(f"Dropped {len(junk)} word(s) the decoder invented: {shown}")
    return words


# ─── Per-language Gemini prompts ─────────────────────────────────────────────
LANGUAGE_META = {
    "de": {"name": "German"},
    "en": {
        "name": "English",
        "aux_examples": '"has gone", "is making", "will run", "have been"',
        "modal_examples": '"can go", "should know", "must finish", "would help"',
        "neg_examples": '"never had", "didn\'t think", "no idea", "nothing left"',
        "prep_examples": '"with the doctor", "for people with", "in the city"',
        "art_examples": '"the problem", "a doctor", "this medicine", "my idea"',
        "idiom_examples": '"Brain Fog", "Levothyroxine", "Health Journey", "fun fact"',
        "conjunctions": "and, but, or, so, because, when, if, while, although",
        "list_example": '"cold hands, brain fog, hair loss"',
        "capitalization": (
            "Standard English capitalization: capitalize proper nouns and the "
            "first word of a sentence. Mid-sentence captions can start lowercase "
            "if grammatically appropriate. Never capitalize random words."
        ),
        "punctuation_extra": "",
    },
    # Spanish as spoken in SPAIN. Two things set it apart from the generic
    # prompt, and both are optional keys the other languages don't carry, so
    # their prompts stay byte-for-byte what they were:
    #   * "variety" — the transcriber and the model both lean Latin American,
    #     and a "fix" of vosotros/os into ustedes/les rewrites what was said;
    #   * "extra_units" — an unstressed pronoun sits BEFORE its verb ("se me
    #     olvida", "os lo digo") and is as inseparable from it as an article
    #     is from its noun. No other rule names it, and it is the most common
    #     bad break in Spanish captions.
    "es": {
        "name": "Spanish",
        "variety": (
            "This is Castilian Spanish as spoken in Spain. Keep the vosotros "
            "forms (sois, tenéis, os), the pronoun os, and Spain's vocabulary "
            "exactly as spoken — never convert them to Latin American usage."
        ),
        "aux_examples": '"ha hecho", "está haciendo", "va a correr", "han ido"',
        "modal_examples": '"puede ir", "debería saber", "tengo que terminar", "hay que tomar"',
        "neg_examples": '"no me gusta", "nunca tuve", "ni idea", "nada más"',
        "prep_examples": '"con el médico", "para personas con", "en la ciudad", "del cuerpo"',
        "art_examples": '"el problema", "una doctora", "estos medicamentos", "mi idea"',
        "idiom_examples": '"niebla mental", "L-Tiroxina", "fun fact"',
        "conjunctions": "y, pero, o, porque, cuando, si, mientras, aunque, así que",
        "list_example": '"manos frías, niebla mental, caída del pelo"',
        # German writes spoken numbers as digits, and the nets that keep "2
        # Kapseln" together only know digits. Without the same rule "dos
        # cápsulas" came out as two captions.
        "numbers": (
            'Write spoken cardinal numbers as DIGITS: "dos cápsulas" → "2 '
            'cápsulas", "noventa días" → "90 días", "veinte por ciento" → '
            '"20%". "un" and "una" stay words.'
        ),
        "extra_units": (
            'Unstressed pronoun + the verb it precedes: e.g., "me duele", '
            '"se me olvida", "os lo digo", "no te lo pierdas". Never end a '
            'caption or a line on me, te, se, nos, os, lo, la, le or les.'
        ),
        "capitalization": (
            "Standard Spanish capitalization: capitalize proper nouns and "
            "sentence starts only. Days, months, nationalities and languages "
            "stay lowercase, and so does usted. Don't capitalize words randomly."
        ),
        "punctuation_extra": (", and the opening ¿ of every question "
                              "(an ¡ goes, together with its exclamation mark)"),
    },
    "fr": {
        "name": "French",
        "aux_examples": '"a fait", "est allé", "va courir", "ont été"',
        "modal_examples": '"peut aller", "devrait savoir", "doit finir"',
        "neg_examples": '"n\'ai jamais", "pas du tout", "rien à faire"',
        "prep_examples": '"chez le médecin", "pour les gens avec", "dans la ville"',
        "art_examples": '"le problème", "une doctrice", "ces médicaments", "mon idée"',
        "idiom_examples": '"Brain Fog", "L-Thyroxine", "fun fact"',
        "conjunctions": "et, mais, ou, donc, parce que, quand, si, bien que",
        "list_example": '"mains froides, brouillard mental, perte de cheveux"',
        "capitalization": (
            "Standard French capitalization: capitalize proper nouns and "
            "sentence starts only. Days, months, nationalities and languages "
            "stay lowercase. Don't capitalize words randomly."
        ),
        "punctuation_extra": "",
    },
    "it": {
        "name": "Italian",
        "aux_examples": '"ha fatto", "è andato", "sta correndo", "sono stati"',
        "modal_examples": '"può andare", "dovrebbe sapere", "devo finire"',
        "neg_examples": '"non ho mai", "per niente", "nessuna idea"',
        "prep_examples": '"dal medico", "per le persone con", "in città"',
        "art_examples": '"il problema", "una dottoressa", "questi medicinali", "la mia idea"',
        "idiom_examples": '"Brain Fog", "L-Tiroxina", "fun fact"',
        "conjunctions": "e, ma, o, perché, quando, se, mentre, anche se",
        "list_example": '"mani fredde, nebbia mentale, caduta dei capelli"',
        "capitalization": (
            "Standard Italian capitalization: capitalize proper nouns and "
            "sentence starts only. Days, months, nationalities and languages "
            "stay lowercase. Don't capitalize words randomly."
        ),
        "punctuation_extra": "",
    },
    "pl": {
        "name": "Polish",
        "aux_examples": '"będzie działać", "została zbadana", "jest robione", "byłam zmęczona"',
        "modal_examples": '"może iść", "powinnaś wiedzieć", "muszę skończyć", "chcę schudnąć"',
        "neg_examples": '"nigdy nie miałam", "nie pomyślałam", "żadnych efektów", "nic więcej"',
        "prep_examples": '"u lekarza", "dla osób z", "w mieście", "po badaniach"',
        "art_examples": '"ten problem", "moja lekarka", "te leki", "każdy poranek"',
        "idiom_examples": '"Brain Fog", "L-tyroksyna", "fun fact"',
        "conjunctions": "i, ale, albo, więc, bo, że, kiedy, jeśli, chociaż",
        "list_example": '"zimne ręce, mgła mózgowa, wypadanie włosów"',
        "capitalization": (
            "Standard Polish capitalization: capitalize proper nouns and "
            "sentence starts only. Days, months, languages and adjectives of "
            "nationality stay lowercase. The polite forms Pan/Pani/Państwo are "
            "capitalized in direct address. Don't capitalize words randomly."
        ),
        "punctuation_extra": "",
    },
}


def build_generic_prompt(lang_code: str, words: list, video_context: str) -> str:
    meta = LANGUAGE_META[lang_code]
    numbered = "\n".join(f"[{i}] {w['word']}" for i, w in enumerate(words))
    ctx_line = f"Context: {video_context}" if video_context else ""
    # Optional per-language lines — absent keys add nothing, so a language
    # without them gets exactly the prompt it always had.
    extra_units = (f"\nL2. {meta['extra_units']}" if meta.get("extra_units") else "")
    variety = (f" {meta['variety']}" if meta.get("variety") else "")
    numbers = (f"\nP2. {meta['numbers']}" if meta.get("numbers") else "")
    prompt = f"""You are a TikTok-style {meta['name']} caption editor. Split the transcription below into short, well-paced captions for a vertical 9:16 video.

LAYOUT (hard — CapCut renders captions at large font; lines wider than ~24 chars wrap awkwardly):
A. Group words into NATURAL caption units (a short clause / breath group, ~4–7 words). Render a unit on 1 line if it fits ~22 chars, else on 2 lines (literal "\\n"). Aim for a natural mix of 1- and 2-line captions — don't force one line, don't pad short units into two, and avoid 1–2 word fragments.
B. Each visible line: AT MOST ~24 characters (including spaces). Wide letters (m, w) take more room than narrow ones (i, l, t); keep wide-letter lines shorter.
C. Each caption: AT MOST ~48 characters total visible text.
D. NEVER break a word in the middle. Words stay intact.
E. A long single word (>24 chars) goes alone in its own caption.

INSEPARABLE SEMANTIC UNITS (these phrases MUST NEVER be split across a caption boundary OR across a "\\n" inside a caption — both apply equally):
F. Article / possessive / demonstrative + noun: e.g., {meta['art_examples']}. Never end a caption with an article, possessive or demonstrative.
G. Preposition + its noun phrase: e.g., {meta['prep_examples']}. Never end a caption with a preposition.
H. Adjective + noun, adverb + adjective/verb.
I. Auxiliary + participle: e.g., {meta['aux_examples']}.
J. Modal + infinitive: e.g., {meta['modal_examples']}.
K. Negation + element it negates: e.g., {meta['neg_examples']}.
L. Idiomatic units, product names and English borrowings: e.g., {meta['idiom_examples']}.{extra_units}

CAPTION BOUNDARY RULES:
M. A caption MUST end at a natural prosodic/clause boundary: end of sentence, end of clause, after a comma that opens a new clause, or before a coordinating conjunction ({meta['conjunctions']}) when the caption already has ≥3 words.
N. Lists are MANDATORY one-item-per-caption. Example: {meta['list_example']} → each item its own caption. Never combine list items.
O. GROUP INTO NATURAL UNITS (~4–7 words): a caption is a breath group / short clause, not a 1–2 word fragment. Combine adjacent words and inseparable units until a natural pause (clause/sentence boundary). Render on 1 line if short, 2 lines if longer — a natural mix is expected, neither forced to one line nor padded to two.
O2. NEVER leave a single short word (e.g. "to", "and", "is", "so", a 1–4 letter word) alone as its own caption. Attach it to the adjacent caption it belongs with. EXCEPTIONS that DO stand alone: a word the speaker repeats for emphasis, and each item of a list.

TEXT RULES:
P. Fix obvious Whisper transcription errors. Never add or skip words.{variety}{numbers}
Q. {meta['capitalization']}
R. Remove periods, commas, semicolons, colons, exclamation marks. KEEP question marks, percent signs (%), slashes (/), ampersands (&), quotation marks{meta['punctuation_extra']}.
{project_terms_block()}
Input (numbered words):
{numbered}

{ctx_line}

Return JSON array only, no markdown. Each element:
{{"start": <word_index>, "end": <word_index_inclusive>, "text": "<caption text with \\n if needed>"}}

word_index refers to the [N] numbers above. Indices must be inside 0..{len(words) - 1}.
"""
    return _single_line(prompt, "Input (numbered words):", _SINGLE_LINE_GENERIC)


def _gemini_generate(prompt: str, retries: int = 3, timeout: int = 180):
    """POST a prompt to Gemini and return the raw text the model emitted, or None.
    Retries transient failures (timeouts, 429/5xx, dropped connections) with a
    short backoff so a single slow response no longer drops the whole job to the
    heuristic fallback — the difference between an AI-quality SRT and a degraded
    one. Permanent errors (no key, 4xx) fail fast without retrying."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    import time
    import urllib.request
    import urllib.error
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "response_mime_type": "application/json"},
    }).encode("utf-8")
    # The same chain, for the same reason, as src/gemini.py's MODEL_CHAIN: a
    # pinned model gets retired for new keys (404), and a "-latest" alias walks
    # onto whichever Flash launched most recently — the one least likely to have
    # free-tier quota (429). Written out here rather than imported: this script
    # runs in its own process and must not import the app.
    #
    # It matters more quietly here than in the Studio. A refusal doesn't fail
    # the job, it drops the whole run to the heuristic segmentation — so a free
    # key silently produced degraded SRTs with nothing on screen saying why.
    pin = os.environ.get("GEMINI_MODEL", "").strip()
    models = [pin] if pin else ["gemini-3.5-flash", "gemini-2.5-flash",
                                "gemini-3.1-flash-lite"]
    for i, model_id in enumerate(models):
        more = i < len(models) - 1
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
        for attempt in range(1, retries + 1):
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if i:
                    print(f"[gemini] using {model_id}")
                return payload["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                # Retired, or no quota for this key on this model. Another model
                # may well answer, and waiting here would only delay finding out.
                if e.code in (404, 429) and more:
                    print(f"[gemini] {model_id} unavailable on this key "
                          f"({e.code}) — trying {models[i + 1]}")
                    break
                transient = e.code in (408, 429, 500, 502, 503, 504)
                print(f"Gemini API error: {e.code} {e.reason}"
                      + (f" — retrying ({attempt}/{retries})" if transient and attempt < retries else ""))
                if not transient:
                    return None
            except Exception as e:
                print(f"Gemini attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(min(2 * attempt, 6))  # 2s, 4s, 6s backoff
    print(f"Gemini gave up after {retries} attempts.")
    return None


def segment_with_ai(words: list, video_context: str = "", language: str = "de") -> list:
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        return None
    if language != "de":
        prompt = build_generic_prompt(language, words, video_context)
    else:
        prompt = _build_german_prompt(words, video_context)

    print(f"Calling Gemini for {LANGUAGE_META[language]['name']} semantic segmentation...")
    text = _gemini_generate(prompt)
    if text is None:
        return None
    try:
        segments = json.loads(text)
        if not isinstance(segments, list):
            return None
        cleaned = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            s = seg.get("start")
            e = seg.get("end")
            t = seg.get("text")
            if not isinstance(s, int) or not isinstance(e, int) or not isinstance(t, str):
                continue
            if s < 0 or e >= len(words) or s > e:
                continue
            cleaned.append({"start": s, "end": e, "text": t})
        return cleaned or None
    except Exception as e:
        print(f"Could not parse Gemini response: {e}")
        return None


def _call_gemini(prompt: str):
    """Send a prompt to Gemini (with retry/backoff via _gemini_generate) and
    return the JSON value the model emitted, or None on any error. Shared by the
    grouping-review and casing passes."""
    text = _gemini_generate(prompt)
    if text is None:
        return None
    try:
        return json.loads(text)
    except Exception as e:
        print(f"Could not parse Gemini response: {e}")
        return None


def _build_review_prompt(draft: list, language: str) -> str:
    name = LANGUAGE_META[language]["name"]
    listing = "\n".join(f"[{i}] {_flat_text(s)}" for i, s in enumerate(draft))
    last = len(draft) - 1
    prompt = f"""You are refining the GROUPING of TikTok-style {name} captions for a vertical video. Below is a DRAFT caption list (already correctly worded). Regroup it into natural caption units by MERGING consecutive draft captions that belong to the same spoken phrase / breath group, so the result reads naturally instead of as choppy 1–2 word fragments.

STRICT — you may ONLY merge consecutive draft captions, or keep a caption as-is. You CANNOT split, reorder, add, remove or change ANY word; you only choose where caption boundaries fall.

Make each final caption a natural unit:
- A short clause / breath group, usually 4–7 words, that reads well as 1 OR 2 on-screen lines (roughly ≤ 45 characters total). Do NOT merge so much that a caption would need more than two lines.
- End each caption at a natural pause (clause end, sentence end, or just before a new clause). Do not end a caption on a conjunction, preposition or article — group it with what follows.
- ALWAYS merge a boundary that splits a tight pair: a number from its unit/noun, an article/possessive/preposition from its noun, or an auxiliary/modal from its verb.
- Never leave a single short word as its own caption — merge it with its phrase.
- KEEP SEPARATE (do not merge): a word repeated for emphasis (it must stand alone), and each item of a list (one per caption).

DRAFT captions:
{listing}

Return JSON array only, no markdown. Each element merges one consecutive range of draft captions:
{{"from": <first_draft_index>, "to": <last_draft_index_inclusive>}}
The ranges MUST be sorted, non-overlapping and contiguous, covering every index from 0 to {last}: the first "from" is 0, the last "to" is {last}, and each "from" equals the previous "to" + 1."""
    return _single_line(prompt, "DRAFT captions:", _SINGLE_LINE_REVIEW)


def review_grouping(segments: list, language: str = "de") -> list:
    """Second AI pass: re-group the draft captions into natural units so the
    output is less choppy. Merge-ONLY and index-based — it can never change a
    word, and timing stays exact because merged captions inherit the draft's
    word-index ranges. Returns the regrouped segments, or the original draft
    unchanged on any failure (network, bad JSON, or a partition that doesn't
    exactly cover the draft)."""
    if len(segments) < 2:
        return segments
    data = _call_gemini(_build_review_prompt(segments, language))
    if not isinstance(data, list) or not data:
        return segments
    ranges = []
    for el in data:
        if not isinstance(el, dict):
            return segments
        a, b = el.get("from"), el.get("to")
        if not isinstance(a, int) or not isinstance(b, int) or a > b:
            return segments
        ranges.append((a, b))
    # The ranges must be a contiguous partition of 0..N-1, or we don't trust it.
    if ranges[0][0] != 0 or ranges[-1][1] != len(segments) - 1:
        return segments
    for (_, b), (c, _) in zip(ranges, ranges[1:]):
        if c != b + 1:
            return segments
    merged = []
    for a, b in ranges:
        group = segments[a:b + 1]
        if len(group) == 1:
            merged.append(dict(group[0]))
        else:
            merged.append({
                "start": group[0]["start"],
                "end": group[-1]["end"],
                "text": " ".join(_flat_text(s) for s in group),
            })
    return merged


# ─── Single-line mode override blocks ───────────────────────────────────────
# Injected into the prompts ONLY when LINE_MODE == "1" (see _single_line). They
# OVERRIDE the "natural 1-2 line mix" guidance with "one line per caption", while
# leaving every inseparable-unit / boundary rule fully in force. Hybrid never
# sees these, so its prompts stay byte-for-byte unchanged.
_SINGLE_LINE_DE = """SINGLE-LINE MODE — this OVERRIDES the line-count guidance above (rules A, S and the examples):
- Produce captions that each fit on ONE line (≤ ~22 characters of visible text). Do NOT aim for a mix of 1- and 2-line captions; aim for ONE line every single time.
- To achieve this, segment into SHORTER, MORE NUMEROUS captions (typically 2–4 words each). Break a long breath group into several short captions at natural word boundaries — after a clause, before a conjunction, or between one inseparable unit and the next.
- EVERY inseparable-unit rule above (F–O) still applies with FULL force: never split an article/possessive/demonstrative+noun, preposition+noun phrase, adverb+adjective/verb, auxiliary+participle, modal+infinitive, negation, separable particle+verb, comparative/quantifier+noun, or an idiom/product/borrowing. Keep each such unit whole inside one caption.
- THE ONLY allowed 2-line caption is a single indivisible unit that is itself wider than one line: a long German compound (rule E) on its required hyphen split. Do NOT emit a "\\n" in any other case. A hyphen goes INSIDE a word ONLY at such a real two-line split; a compound that fits one line is written WHOLE, with NO hyphen.
- Still NEVER strand a lone function word as its own caption — attach it per the rules. Emphasis repeats and list items still stand alone, one per caption.

"""

_SINGLE_LINE_GENERIC = """SINGLE-LINE MODE — this OVERRIDES the line-count guidance above (rules A and O):
- Produce captions that each fit on ONE line (≤ ~22 characters of visible text). Do NOT aim for a mix of 1- and 2-line captions; aim for ONE line every single time.
- To achieve this, segment into SHORTER, MORE NUMEROUS captions (typically 2–4 words each), breaking long groups at natural word boundaries.
- EVERY inseparable-unit rule above (F–L) still applies with FULL force — never split an inseparable unit; keep each one whole inside one caption.
- The ONLY allowed 2-line caption is a single indivisible unit wider than one line (rule E). Do NOT emit a "\\n" in any other case.
- Still NEVER strand a lone short function word as its own caption. Emphasis repeats and list items still stand alone.

"""

_SINGLE_LINE_REVIEW = """SINGLE-LINE MODE — this OVERRIDES the grouping guidance above:
Keep each final caption to ONE line (≤ ~22 characters). Merge ONLY when a draft caption is an incomplete fragment that splits an inseparable unit or strands a single function word; otherwise keep the drafts SEPARATE. Do NOT merge fragments together merely to reach 4–7 words, and never produce a caption that needs two lines (except a single indivisible unit that cannot fit one line).

"""


def _single_line(prompt: str, anchor: str, block: str) -> str:
    """Inject a single-line override `block` just before `anchor` in `prompt`,
    but only in single-line mode. In hybrid mode the prompt is returned
    unchanged (byte-for-byte), so existing behaviour is preserved exactly."""
    if LINE_MODE != "1":
        return prompt
    return prompt.replace(anchor, block + anchor, 1)


def _build_german_prompt(words: list, video_context: str = "") -> str:
    """German caption prompt. Domain-agnostic ("evergreen"): every example is an
    everyday German phrase, so nothing primes the model toward one topic — only
    the grammar rules carry over to any video."""
    numbered = "\n".join(f"[{i}] {w['word']}" for i, w in enumerate(words))
    prompt = f"""You are a TikTok-style German caption editor. Split the transcription below into short, well-paced captions for a vertical video.

Hard constraints (CapCut renders captions at large font on vertical 9:16 video; lines wider than ~24 chars wrap awkwardly):

LAYOUT:
A. Group words into NATURAL caption units — a short complete clause or breath group, usually 4–7 words. Render a unit on 1 line when it fits one line (~22 chars), and on 2 lines (literal "\\n") when the unit is longer. Aim for a NATURAL MIX of 1- and 2-line captions: do NOT force everything onto one line, and do NOT pad a genuinely short unit into two. Avoid 1–2 word fragments — they read as choppy.
B. Each visible line: AT MOST ~24 characters (including spaces). German has many narrow letters (i, l, t, r, f, ä) that take little room, so a line of 24 narrow chars is fine; lines full of wide letters (m, w) should be a little shorter.
C. Therefore each caption: AT MOST ~48 characters total visible text. Going under is fine; going over is not.
D. NEVER break a word in the middle of letters. Words that are NOT German compound nouns stay intact ("Entscheidung", "Erfahrung", "Computer", "Nachbarin" — never split).
E. A German compound word > 24 chars (e.g., "Geschwindigkeitsbegrenzung", "Versicherungsgesellschaft", "Lebensmittelgeschäft", "100-Meter-Staffellauf") goes alone in its own caption and is broken at a meaningful compound boundary, hyphen at end of line 1: "Geschwindigkeits-\\nbegrenzung", "Versicherungs-\\ngesellschaft". This mid-word two-line split of a long compound is REQUIRED and must be kept. A hyphen may appear ONLY at the end of line 1 of such a real two-line split. If a compound fits on ONE line (≤ ~24 chars, e.g. "Wassereinlagerungen"), write it WHOLE with NO hyphen — never insert a hyphen into a word that stays on one line.

INSEPARABLE SEMANTIC UNITS (these phrases MUST NEVER be split across a caption boundary OR across a "\\n" line break inside a caption — both apply EQUALLY):
The "\\n" line break within a caption is just as much a "split" as starting a new caption. ALL inseparable-unit rules below apply to BOTH.
Example check: "es liegt an diesem Problem" (26 chars).
  ❌ Wrong: "es liegt an diesem\\nProblem" — splits "diesem Problem" (demonstrative + noun).
  ✅ Right: "es liegt an\\ndiesem Problem" — line break between clauses, semantic unit intact.
Always ask: would breaking here separate two words that belong together grammatically? If yes, find another break point (a different word boundary within the caption, or split into 2 separate captions).
F. Adverb + adjective/participle: "sehr glücklich", "extrem müde", "ziemlich groß", "wirklich schön", "ganz schön".
G. Adverb + verb form: "dachte immer", "habe nie", "geht gut", "ist auch".
H. Auxiliary + participle: "habe gemacht", "ist gegangen", "wird gebaut", "wurde gefunden".
I. Modal + infinitive: "kann gehen", "muss arbeiten", "sollte helfen", "möchte schlafen".
J. Negation + element it negates: "nie wieder", "nicht gut", "kein Geld", "niemals".
K. Separable verb particle + verb root: "rufe an", "fängt an", "steht auf", "hört zu", "macht mit". The particle stays in the same caption as the verb stem even if they appear in different positions in the sentence.
L. Preposition + its noun phrase (article+adj+noun): "mit dem Auto", "in der Stadt", "bei der Arbeit", "an Weihnachten", "für meine Familie". Never end a caption with a preposition. Never split between preposition and its object.
M. Article/possessive/demonstrative + noun: "der Mann", "eine Idee", "meine Tasche", "diese Sache", "das große Haus". Never end a caption with an article/possessive/demonstrative.
N. Comparative/quantifier + noun: "mehr Geld", "weniger Zeit", "viel Wasser", "20% Rabatt", "zwei Stunden".
O. Tight idiomatic units, product names and English borrowings stay as ONE token: "Social Media", "Fun Fact", "Best Friend", "Work-Life-Balance", brand names.

CAPTION BOUNDARY RULES:
P. A caption MUST end at a natural prosodic/clause boundary: end of sentence, end of clause (after subordinator's verb), after a comma that introduces a new clause, before a coordinating conjunction (und/aber/oder/denn) that starts a new clause.
Q. Lists (items separated by commas, e.g., "Brot, Milch, Eier") → each item its own caption.
R. If forced to choose between a 2-line caption with an unnatural break (e.g., splitting "extrem | müde") and TWO 1-line captions (one with "und das Wetter war", one with "extrem kalt"), CHOOSE THE TWO 1-LINE CAPTIONS.
S. **GROUP INTO NATURAL UNITS (≈4–7 words).** A caption is a natural breath group / short clause — NOT a 1–2 word fragment. Combine adjacent words and inseparable units until you reach a natural pause (clause end, sentence end, or right before a new clause). Render the unit on 1 line if it is short (~22 chars) or on 2 lines (\\n) if it is longer — a natural mix of 1- and 2-line captions is expected, neither forced to one line nor padded to two. NEVER leave a single short word ("tun", "und", "ist", "doch", any 1–4 letter word) alone as its own caption — attach it to the caption it grammatically belongs with. EXCEPTIONS that DO stand alone: a word the speaker repeats for emphasis (e.g. "Nie … Nie", "Endlich … Endlich"), and each item of a list.
   The "inseparable unit" rule means UNITS DON'T SPLIT INTERNALLY — it does NOT mean every unit must be its own caption. Multiple units CAN be combined in one caption.
   Example: "und trotzdem kommst du morgens" (5 words, 30 chars) → ONE caption: "und trotzdem\\nkommst du morgens" (line 1: 12c, line 2: 17c). NOT two captions.
   Example: "immer müder und müder" (4 words) → ONE caption: "immer müder\\nund müder". NOT two captions of one unit each.
   Example: "du fährst seit Jahren jeden Tag zur Arbeit" (8 words) — too long, split into 2 captions at a clause pause, e.g. "du fährst seit Jahren" + "jeden Tag zur Arbeit".
   Rule of thumb: build a natural breath group (~4–7 words) up to a clause/sentence pause, then start a new caption. Short unit → 1 line; longer unit → 2 lines.

TEXT RULES:
T. Fix obvious Whisper transcription errors (only clear ones): wrong word boundaries ("im Stande" → "imstande"), obvious homophones that make no sense in context, and misheard number words (a stray word where a number was clearly spoken). Write spoken cardinal numbers as DIGITS ("sechs Kilo" → "6 Kilo", "achtzig Euro" → "80 Euro").
U. Never add or skip words. EXCEPTION: when the speaker repeats a word for emphasis (e.g. "Nie … Nie"), KEEP the repeated word and give it its OWN caption — do not drop it and do not merge it into the neighbouring caption.
V. Capitalization = write each word EXACTLY as it appears in the MIDDLE of a sentence. Capitalize ONLY words that are inherently capitalized in German: nouns, proper names, and the formal-address words Sie/Ihr/Ihre/Ihren/Ihrem/Ihrer/Ihres/Ihnen. Do NOT capitalize a word just because it starts the caption — lexical verbs, adjectives, adverbs, pronouns, articles, conjunctions and prepositions stay lowercase at caption start (e.g. "trinkst du genug", "gesund bleiben", "wichtig ist", "und dann").
W. Remove periods, commas, semicolons, colons, exclamation marks. KEEP question marks, percent signs (%), slashes (/), ampersands (&), quotation marks.

Editorial rules:
1. Lists are MANDATORY one-item-per-caption. If words are read out as a list (any enumeration, e.g. "Brot, Milch, Eier"), EACH item becomes its OWN caption — even single-word items. Never combine list items.
2. Split before subordinating/coordinating conjunctions ("und", "aber", "oder", "weil", "dass", "denn", "doch", "sondern", "wenn", "als", "ob", "obwohl") when the caption already has ≥3 words.
3. Split after commas, periods, question marks.
4. Keep meaningful units together as ONE token: product/brand names and English borrowings ("Social Media", "Fun Fact", "Best Friend").
5. Never add or skip words. Only correct spelling/word-boundary errors as above. EXCEPTION: an emphatic repetition is kept and gets its own caption.
6. Capitalization = write each word EXACTLY as it appears in the MIDDLE of a sentence. Capitalize ONLY inherently-capitalized words: nouns, proper names, and formal address Sie/Ihr/Ihre/Ihren/Ihrem/Ihrer/Ihres/Ihnen. Do NOT capitalize a word just because it starts the caption — lexical verbs, adjectives, adverbs, pronouns, articles, conjunctions and prepositions stay lowercase at caption start.
7. Remove periods, commas, semicolons, colons, exclamation marks. KEEP question marks, percent signs (%), slashes (/), ampersands (&), quotation marks.
{project_terms_block()}
Examples of good captions (1 or 2 lines, each line ≤ ~24 chars, semantic units intact):
- "ich war gestern\\nim Supermarkt"          ← 2 lines, ok
- "Fun Fact"                                  ← 1 line, borrowing
- "mit dem Auto"                              ← 1 line, prep+noun
- "extrem müde"                               ← 1 line, adv+adj must stay together
- "und das Wetter war"                        ← 1 line, complete clause start
- "Geschwindigkeits-\\nbegrenzung"            ← long compound alone, split at boundary
- "100-Meter-\\nStaffellauf"                  ← long compound alone
- "warum bin ich\\ndann so müde"              ← 2 lines, ok
- "wirklich schön"                            ← 1 line, adv+adj
- "und ich habe leider"                       ← 1 line, then "nie genug Zeit\\ndafür gehabt" follows
- "nie genug Zeit\\ndafür gehabt"             ← 2 lines, negation kept with noun phrase

Examples of BAD splits (NEVER produce these):
- "und das Wetter\\nwar extrem" + "schön"     ← BAD: splits "extrem schön"
- "ich war auch sehr" + "müde"                ← BAD: splits "sehr müde"
- "weil ich dachte\\nimmer"                   ← BAD: splits "dachte immer"
- "seit Jahren in" + "Berlin"                 ← BAD: splits "in Berlin"
- "und ich habe leider\\nnie genug" + "Zeit dafür gehabt"   ← BAD: splits "nie ... Zeit gehabt"

Input (numbered words):
{numbered}

{f"Context: {video_context}" if video_context else ""}

Return JSON array only, no markdown. Each element:
{{"start": <word_index>, "end": <word_index_inclusive>, "text": "<caption text with \\n if needed>"}}

word_index refers to the [N] numbers above. Indices must be inside 0..{len(words)-1}.
"""
    return _single_line(prompt, "Input (numbered words):", _SINGLE_LINE_DE)


BREAK_BEFORE = {"und", "aber", "oder", "denn", "doch", "sondern",
                "weil", "dass", "wenn", "als", "ob", "obwohl", "während",
                "bevor", "nachdem", "damit", "sodass", "falls"}

# Where the no-Gemini fallback may start a new caption, per language. A language
# not listed keeps the German set it always used. Spanish leaves out "que" on
# purpose: it is the second half of "lo que", "así que", "ya que", "para que",
# and a break before it tears every one of them in two; "cuando" is left out
# for "de vez en cuando", which ends a clause.
BREAK_BEFORE_BY_LANG = {
    "de": BREAK_BEFORE,
    "es": {"y", "e", "o", "u", "ni", "pero", "sino", "porque", "aunque",
           "mientras", "pues", "si"},
}


def _break_before() -> set:
    return BREAK_BEFORE_BY_LANG.get(ACTIVE_LANG, BREAK_BEFORE)


def segment_heuristic(words: list, max_words: int = 6) -> list:
    groups = []
    cur = []
    for i, w in enumerate(words):
        cur.append(i)
        raw = w["word"].strip()
        hard_end = bool(re.search(r"[.!?]$", raw))
        soft_end = raw.endswith(",") or raw.endswith(";") or raw.endswith(":")
        nxt = words[i + 1] if i + 1 < len(words) else None
        next_clause = nxt and strip_punct(nxt["word"]).lower() in _break_before()
        should_break = (
            hard_end
            or (soft_end and len(cur) >= 2)
            or (next_clause and len(cur) >= 3)
            or len(cur) >= max_words
        )
        if should_break:
            groups.append(cur)
            cur = []
    if cur:
        if groups and len(cur) == 1:
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    out = []
    for g in groups:
        text_words = [normalize_case(words[i]["word"]) for i in g if strip_punct(words[i]["word"])]
        out.append({"start": g[0], "end": g[-1], "text": " ".join(text_words)})
    return out


def compute_boundaries(segments: list, words: list, video_end: float):
    n = len(segments)
    boundaries = [0.0]
    for i in range(1, n):
        prev_end = words[segments[i - 1]["end"]]["end"]
        next_start = words[segments[i]["start"]]["start"]
        cand = max(prev_end + TRAIL_MIN, next_start - LEAD_MAX)
        cand = min(cand, next_start)
        boundaries.append(cand)
    boundaries.append(video_end)
    return boundaries


#: Longest a caption may stay on screen after its own last word. Captions
#: normally end where the next one starts, which is right while speech runs on
#: and wrong across a hole: one clip whose transcription lost 30 seconds shipped
#: a SINGLE caption that sat on screen for 31 s, because that is where the next
#: word was. Past this the caption ends with its speech and the screen goes
#: clean — an honest gap, not a frozen line.
TAIL_MAX = 1.2


def caption_spans(segments: list, words: list, boundaries: list) -> list:
    """(start, end) per caption: the shared cut points, with an end pulled back
    to its own speech wherever the next caption is far away."""
    spans = []
    for i, seg in enumerate(segments):
        start = boundaries[i]
        end = boundaries[i + 1]
        spoken_end = words[seg["end"]]["end"]
        spans.append((start, max(start + 0.2, min(end, spoken_end + TAIL_MAX))))
    return spans


LINE_BREAK_BAD_LAST = {
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einen", "einem", "eines", "einer",
    "kein", "keine", "keinen", "keinem", "keiner", "keines",
    "mein", "meine", "meinen", "meinem", "meiner", "meines",
    "dein", "deine", "deinen", "deinem", "deiner", "deines",
    "sein", "seine", "seinen", "seinem", "seiner", "seines",
    "ihr", "ihre", "ihren", "ihrem", "ihrer", "ihres",
    "unser", "unsere", "unseren", "unserem", "unserer", "unseres",
    "euer", "eure", "euren", "eurem", "eurer", "eures",
    "dieser", "diese", "dieses", "diesen", "diesem",
    "jener", "jene", "jenes", "jenen", "jenem",
    "welcher", "welche", "welches", "welchen", "welchem",
    "manche", "mancher", "manches", "manchen", "manchem",
    "viele", "vieler", "vielen", "vielem",
    "alle", "aller", "allen", "allem",
}


def fix_line_break(text: str) -> str:
    if "\n" not in text:
        return text
    parts = text.split("\n", 1)
    if len(parts) != 2:
        return text
    line1_words = parts[0].split()
    line2_words = parts[1].split()
    all_words = line1_words + line2_words
    if not line1_words or not line2_words:
        return text

    def clean(w):
        return re.sub(r"[^\wäöüÄÖÜß-]", "", w, flags=re.UNICODE).lower()

    # Never end line 1 on a word that binds to what FOLLOWS — a determiner
    # (LINE_BREAK_BAD_LAST), a binding preposition/subordinator (MOVE_TRAILING),
    # a one-word preposition (FORWARD_PREPS) or an intensifier (FORWARD_INTENS).
    # _no_line_end() is the ACTIVE_LANG's union of all of these, so this keeps
    # "seit über 10 Jahren" / "chez le médecin" / "u lekarza" intact.
    def binds(k):
        """Does the word before break position k bind to the one after it?"""
        w = all_words[k - 1]
        if w.endswith(("?", "!")):
            return False  # it closes a question: it binds to nothing after it
        if _unbound(all_words[k - 2] if k >= 2 else "", w):
            return False
        # A bare number binds to its noun ("2 | cápsulas"), exactly as the
        # caption-level nets already treat it (_binds_forward).
        c = clean(w)
        return (c in _no_line_end() or bool(re.fullmatch(r"\d+([.,]\d+)?", c))
                or _binds_pair(w, all_words[k]))

    current_break = len(line1_words)
    if not binds(current_break):
        return text

    # Backwards first, as this always did — then, only if nothing backwards
    # fits, forwards. Giving up used to leave line 1 ending on the very word
    # this exists to move ("und du im / schlimmsten Fall auch"). Forwards is
    # never preferred when backwards works: replayed over real German and
    # Italian captions, "nearest either way" swapped good breaks for worse
    # ones as often as it fixed bad ones ("cura / con un primo dosaggio").
    order = (list(range(current_break - 1, 0, -1))
             + list(range(current_break + 1, len(all_words))))
    for new_break in order:
        new_line1 = " ".join(all_words[:new_break])
        new_line2 = " ".join(all_words[new_break:])
        if binds(new_break):
            continue
        if max(text_width(new_line1), text_width(new_line2)) <= LINE_W_MAX:
            return new_line1 + "\n" + new_line2

    return text


def normalize_text_preserve_breaks(text: str) -> str:
    lines = text.split("\n")
    out = []
    for line in lines:
        parts = line.split()
        cleaned = [normalize_case(w) for w in parts]
        cleaned = [w for w in cleaned if w]
        out.append(" ".join(cleaned))
    return "\n".join(l for l in out if l)


# The company's own spellings, per market, shipped with the tool so every
# install captions them right without anyone typing a thing. The product is sold
# under a different name in each market, and the ingredient is spelled the way
# that language spells it. A key in .env still wins over these —
# CAPTION_BRAND_<LANG> / CAPTION_TERMS_<LANG>, and for German also the bare
# CAPTION_BRAND / CAPTION_TERMS — and an environment variable set to "" turns a
# market's list off.
HOUSE_BRAND = {
    "de": "miavola",
    "fr": "Conversol",
    "it": "Conversol",
    "es": "El Conversol",
    "pl": "Przetwornik",
}
HOUSE_TERMS = {
    "de": "L-Thyroxin",
    "en": "Levothyroxine, L-Thyroxine",
    "fr": "L-Thyroxine",
    "it": "L-Tiroxina",
    "pl": "L-tyroksyna",
}


def _market_setting(kind: str, lang: str):
    """The .env value for one market, or None when it isn't set at all. The
    bare key is German's (the default market) and no one else's."""
    keys = [f"CAPTION_{kind}_{lang.upper()}"] + ([f"CAPTION_{kind}"] if lang == "de" else [])
    for key in keys:
        value = os.environ.get(key)
        if value is not None:
            return value.strip()
    return None


def _brand_config():
    """The canonical brand spelling for the active market: its .env key, else
    the house name for that market, else German's brand — a brand is usually one
    word everywhere, so a market with no name of its own (English) shows
    German's."""
    for lang in (ACTIVE_LANG, "de"):
        value = _market_setting("BRAND", lang)
        if value is None:
            value = HOUSE_BRAND.get(lang)
        if value is not None:
            return value
    return ""


def _terms_config() -> list:
    """Extra canonical terms (comma-separated) for the active market: its .env
    key, else the house list for that market — and nothing else.

    Unlike the brand, German's terms are never borrowed by another market. A
    term is a SPELLING, and a spelling belongs to one language: falling back put
    German "L-Thyroxin" into the prompt and the repair pass of any market that
    had no list yet, where the repair pass would happily "fix" a correct Spanish
    "L-Tiroxina" into it. A market with no list of its own simply has no terms."""
    raw = _market_setting("TERMS", ACTIVE_LANG)
    if raw is None:
        raw = HOUSE_TERMS.get(ACTIVE_LANG, "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def _term_core(s: str) -> str:
    """Lowercased alphanumeric skeleton of a word/term — spaces, hyphens and
    punctuation removed (umlauts/ß kept, since \\w is Unicode). Lets us compare a
    caption word to a canonical term regardless of spacing, hyphenation or case."""
    return re.sub(r"[^\w]", "", s.lower(), flags=re.UNICODE).replace("_", "")


def _term_variant_re(term: str):
    # Match a term even when WhisperX split, hyphenated or re-cased it ("Mia Vola",
    # "mia-vola", "l-thyroxin"): its alphanumeric letters in order, with an optional
    # space/hyphen allowed between each. Separators/case in the term itself are
    # ignored here — exact spelling/case is restored by substituting the canonical.
    letters = [re.escape(c) for c in term if c.isalnum()]
    if not letters:
        return None
    return re.compile(r"\b" + r"[\s\-]?".join(letters) + r"\b", re.IGNORECASE)


def _levenshtein(a: str, b: str, cap: int) -> int:
    """Edit distance, abandoned early once it exceeds `cap` (returns cap+1 then).
    Inputs are single caption words, so the plain DP is more than fast enough."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(v)
            row_best = min(row_best, v)
        if row_best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _fuzz_cap(n: int) -> int:
    # Edits tolerated when matching a mis-transcription to a canonical term,
    # scaled to the term's length: tight on short terms (a 7-letter brand allows
    # exactly ONE edit, so "Miawola"→"Miavola" but not unrelated 7-letter words),
    # looser on long compounds ("L-tyroxin"→"L-Thyroxin").
    if n <= 7:
        return 1
    if n <= 14:
        return 2
    return 3


def _canonical_terms() -> list:
    """Ordered, de-duplicated canonical spellings to ENFORCE in the output: the
    brand first, then the terms — the house set per market, or what .env says
    instead (see HOUSE_BRAND). Unlike per-video vocabulary, these are STABLE
    brand/domain words (the brand name, a recurring product/ingredient); enforcing
    their exact spelling deterministically is the one place overfitting is wanted."""
    brand = _brand_config()
    terms = _terms_config()
    out, seen = [], set()
    for t in ([brand] if brand else []) + terms:
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def apply_canonical_terms(text: str) -> str:
    """Force every configured canonical term to its exact spelling AND case,
    repairing WhisperX mishearings. Two layers:
      1. exact letters with stray spaces/hyphens/case ("Mia Vola", "l-thyroxin")
         → the canonical form;
      2. a bounded fuzzy pass for near-miss mishearings ("Miawola"→"miavola",
         "L-tyroxin"→"L-Thyroxin"), gated by a first-letter match, a 5-char floor
         and a length-scaled edit-distance cap so ordinary words stay untouched.
    No-op for a market whose brand and terms are both empty."""
    terms = _canonical_terms()
    if not terms:
        return text

    # Layer 1 — separator/case variants of the exact letters.
    for term in terms:
        rx = _term_variant_re(term)
        if rx:
            text = rx.sub(lambda _m, t=term: t, text)

    # Layer 2 — fuzzy, token by token (whitespace and newlines preserved). Only
    # for terms with a 5+ char skeleton, where an edit-distance match is meaningful.
    cores = [(t, _term_core(t)) for t in terms]
    cores = [(t, c) for t, c in cores if len(c) >= 5]
    if not cores:
        return text

    pieces = re.split(r"(\s+)", text)
    for idx, tok in enumerate(pieces):
        if not tok or tok.isspace():
            continue
        m = re.match(r"^(\W*)(.*?)(\W*)$", tok, flags=re.UNICODE)
        prefix, body, suffix = m.group(1), m.group(2), m.group(3)
        core = _term_core(body)
        if len(core) < 5:
            continue
        for term, tcore in cores:
            if core == tcore:
                break  # already the canonical skeleton — keep Layer 1's result
            if core[0] != tcore[0]:
                continue
            cap = _fuzz_cap(len(tcore))
            if _levenshtein(core, tcore, cap) <= cap:
                pieces[idx] = prefix + term + suffix
                break
    return "".join(pieces)


# --------------------------------------------------------------------------- #
# Brand-term repair pass (Gemini)                                              #
# --------------------------------------------------------------------------- #
#
# apply_canonical_terms() below is deterministic and catches a lot, but it is
# structurally blind in three ways: its fuzzy layer requires the SAME FIRST
# LETTER, needs a 5-character skeleton, and works one whitespace token at a
# time. So it repairs "Umwandeler" and cannot repair "Um Wandler" (the word
# split in two), "Ombandler" (the first letter misheard), or a mishearing that
# is close in sound and far in edit distance. It also has no idea whether the
# word makes sense where it sits.
#
# This pass asks Gemini for exactly that judgement, and is built so a wrong
# answer cannot damage the captions: the model returns SUBSTITUTIONS, never
# rewritten text. Each one is checked on our side — the replacement must be a
# configured term, the thing being replaced must really be in that caption, and
# nothing that is already correct may be touched. Anything else is dropped.
# Same shape as the grouping review, which returns index ranges for the same
# reason.

#: The most tokens a single mishearing may span ("L-Thyroxin" heard as
#: "L Tyro xin"). Beyond this we are no longer repairing a word, we are
#: rewriting a phrase, which is not this pass's job.
TERM_SPAN_MAX = 3


def _strip_edges(tok: str) -> tuple:
    """(leading punctuation, bare word, trailing punctuation)."""
    m = re.match(r"^(\W*)(.*?)(\W*)$", tok, flags=re.UNICODE)
    return m.group(1), m.group(2), m.group(3)


def _sub_phrase(text: str, was: str, now: str) -> tuple:
    """Replace whole-token runs equal to `was` with `now`, keeping whatever
    punctuation sat around them. Returns (new_text, replacements)."""
    was_words = [w for w in was.split() if w]
    if not was_words or len(was_words) > TERM_SPAN_MAX:
        return text, 0
    pieces = re.split(r"(\s+)", text)
    idx = [i for i, p in enumerate(pieces) if p and not p.isspace()]
    out, hits, k = list(pieces), 0, 0
    while k + len(was_words) <= len(idx):
        run = idx[k:k + len(was_words)]
        bodies = [_strip_edges(pieces[i])[1] for i in run]
        if bodies == was_words:
            pre = _strip_edges(pieces[run[0]])[0]
            suf = _strip_edges(pieces[run[-1]])[2]
            out[run[0]] = pre + now + suf
            for i in run[1:]:
                out[i] = ""
                if i - 1 >= 0 and out[i - 1].isspace():
                    out[i - 1] = ""
            hits += 1
            k += len(was_words)
        else:
            k += 1
    if not hits:
        return text, 0
    return " ".join("".join(out).split()), hits


def _build_terms_prompt(captions: list, terms: list, language: str) -> str:
    name = LANGUAGE_META.get(language, {}).get("name", language)
    listing = "\n".join(f"[{i}] {t}" for i, t in enumerate(captions))
    return f"""These are finished {name} captions from one video, numbered one per line. The video is about a product whose vocabulary is fixed and listed below.

FIXED SPELLINGS:
{chr(10).join('- ' + t for t in terms)}

Find places where the transcriber GARBLED one of those fixed spellings — it wrote down what it heard, so the word may be split in two, run together with a neighbour, or replaced by a similar-sounding word that makes no sense in this sentence.

RULES:
- Only report a garbled FIXED SPELLING. Ignore every other error: ordinary typos, casing, punctuation, grammar, word choice.
- Only report it when the surrounding words make the intended term unambiguous. If an ordinary word simply resembles a fixed spelling and reads correctly where it is, LEAVE IT — a wrong substitution is far worse than a missed one.
- Never report a word that is already spelled exactly like its fixed spelling.
- "was" must be copied character for character from the caption, and must be between 1 and {TERM_SPAN_MAX} whole words.
- "now" must be one of the FIXED SPELLINGS above, copied exactly.

Captions:
{listing}

Return a JSON array of objects, one per garbled occurrence, each {{"i": <caption number>, "was": "<exact text in that caption>", "now": "<the fixed spelling>"}}. Return [] if every fixed spelling is already correct."""


def repair_terms_with_ai(segments: list, language: str = "de") -> list:
    """Repair brand/product words the transcriber garbled beyond what the
    deterministic pass can reach.

    A no-op for a market with no brand and no terms. Gemini returns
    substitutions rather than text,
    and every one is validated here before it is applied:

      * "now" must be a configured term, exactly as configured;
      * "was" must actually occur in that caption, as whole words;
      * "was" must not already BE a configured term (nothing correct is touched);
      * applying it may only shorten the caption by the words it merges, so no
        word can be invented, dropped or reordered.

    Anything that fails is discarded and that caption is left alone. On a network
    failure, an unparseable answer or no key, the captions come back untouched --
    apply_canonical_terms() still runs afterwards, so this pass can only ever add
    repairs, never remove them."""
    terms = _canonical_terms()
    if not terms or len(segments) < 1:
        return segments
    flats = [" ".join(_flat_text(s).split()) for s in segments]
    data = _call_gemini(_build_terms_prompt(flats, terms, language))
    if not isinstance(data, list):
        print("Term repair: no usable response — keeping the deterministic repair.")
        return segments
    if not data:
        return segments

    canon = {t.lower(): t for t in terms}
    texts = list(flats)
    applied, refused = 0, 0
    for item in data:
        if not isinstance(item, dict):
            refused += 1
            continue
        i, was, now = item.get("i"), item.get("was"), item.get("now")
        if not isinstance(i, int) or not (0 <= i < len(texts)):
            refused += 1
            continue
        if not isinstance(was, str) or not isinstance(now, str):
            refused += 1
            continue
        was, now = was.strip(), now.strip()
        # The replacement must be something the operator actually configured —
        # this is what stops the pass from being a free-text rewriter.
        if now not in terms:
            refused += 1
            continue
        # Never "repair" a word that is already written exactly as configured.
        # This compares the LITERAL text on purpose: _term_core() folds away
        # spaces and hyphens, which is precisely what a split-word mishearing
        # ("Um Wandler") collapses to, so testing skeletons here would refuse
        # the one repair this pass exists for.
        if was in terms:
            refused += 1
            continue
        before = texts[i]
        after, hits = _sub_phrase(before, was, now)
        if not hits:
            refused += 1          # the model quoted text that is not there
            continue
        # A substitution may merge words; it may never add or lose any others.
        # Counted against the words of `now` too: a brand can be two words
        # ("El Conversol"), and assuming one refused every repair to it.
        span = len([w for w in was.split() if w])
        if len(after.split()) != len(before.split()) + (len(now.split()) - span) * hits:
            refused += 1
            continue
        texts[i] = after
        applied += hits

    if refused:
        print(f"Term repair: ignored {refused} unusable suggestion(s).")
    if not applied:
        return segments
    print(f"Term repair: fixed {applied} garbled brand word(s) with Gemini.")
    return [seg if texts[i] == flats[i] else {**seg, "text": texts[i]}
            for i, seg in enumerate(segments)]


def project_terms_block() -> str:
    """An optional prompt section listing project-specific spellings (brand +
    CAPTION_TERMS), injected only when configured. Keeps the base prompt neutral
    and unbiased; Gemini applies these only when the audio matches."""
    brand = _brand_config()
    terms = _terms_config()
    items = ([brand] if brand else []) + terms
    if not items:
        return ""
    return ("\nPROJECT-SPECIFIC SPELLINGS: when the audio clearly says one of "
            "these, spell it EXACTLY like this (otherwise ignore this line): "
            f"{', '.join(items)}.\n")


def finalize_caption(text: str) -> str:
    normalized = apply_canonical_terms(normalize_text_preserve_breaks(text))
    # Collapse any model-inserted in-word compound hyphen back into the whole
    # word, WHEREVER it sits — even mid-line (e.g. "meine Schilddrüsen-werte").
    # A hyphen is only valid at the end of line 1 of a real two-line split, and
    # that split is re-derived below from whole words; it is never shown mid-line.
    whole = join_soft_hyphens(normalized)
    flat = " ".join(flatten_lines(normalized).split())
    if text_width(flat) <= LINE_W_MAX:
        return flat  # one line, whole words, no hyphen
    # Two lines are needed. Keep the model's own break when both halves already
    # fit a line (it reflects a semantic unit, e.g. "meine\nWassereinlagerungen");
    # otherwise re-pack by width. auto_hyphenate (width-gated) only splits a word
    # that is itself wider than a whole line, and the hyphen lands at end of line 1.
    if "\n" in whole:
        parts = [p.strip() for p in whole.split("\n", 1)]
        if len(parts) == 2 and all(text_width(p) <= LINE_W_MAX for p in parts):
            return fix_line_break(whole)
    return drop_midline_hyphens(fix_line_break(pack_lines(apply_auto_hyphenation(flat))))


def _flat_text(seg: dict) -> str:
    """Caption text as a single line (line breaks removed)."""
    return " ".join(flatten_lines(seg["text"]).split())


def _fits_two_lines(text: str) -> bool:
    """True if `text` lands on at most two lines that each fit the width budget.

    Mirrors what finalize_caption actually writes — auto-hyphenation included, so
    a long compound that WILL be broken across the two lines is not counted as
    overflowing. Used both to decide whether a caption can absorb a merged orphan
    and to decide whether it must be split (enforce_two_lines)."""
    lines = pack_lines(apply_auto_hyphenation(text)).split("\n")
    return len(lines) <= 2 and all(text_width(l) <= LINE_W_MAX for l in lines)


def _seg_tokens(seg: dict) -> list:
    return _flat_text(seg).split()


def _norm_word(w: str) -> str:
    return strip_punct(w).lower()


def _is_orphan(seg: dict) -> bool:
    """A caption that is a single word. Any lone word is a merge candidate — we
    don't want a word stranded on its own caption. The exceptions (emphasis
    repetition, list items) are protected in merge_orphans; a long compound that
    can't be combined with a neighbour within two lines simply stays put, since
    no merge will fit it."""
    return len(_seg_tokens(seg)) == 1


# A caption must not END on a word that grammatically binds to the FOLLOWING
# word — it gets moved to the start of the next caption ("so gut dass" + "mein
# Arzt" → "so gut" + "dass mein Arzt"; "…bist für" + "Hosen" → "…bist" + "für
# Hosen"). Two groups:
#   • subordinating conjunctions (open a dependent clause);
#   • forward-binding prepositions/contractions + the intensifier "so".
# Deliberately EXCLUDES coordinating und/oder/aber/denn (often a natural final
# pause, "Klingt super oder?"), "als" (ambiguous), and every separable-verb
# particle (an, auf, aus, ab, zu, vor, nach, ein, um, über, unter, durch) —
# moving those broke verbs like "fallen … aus" and cascaded.
MOVE_TRAILING_CONJ = {
    "dass", "weil", "ob", "wenn", "damit", "sodass",
    "obwohl", "während", "bevor", "nachdem", "falls", "sondern",
}
MOVE_TRAILING_FWD = {
    "für", "mit", "bei", "ohne", "gegen", "zwischen", "wegen", "trotz",
    "statt", "seit", "von", "vom", "zur", "zum", "ins", "im", "am", "beim",
    "ans", "aufs", "so",
}
MOVE_TRAILING = MOVE_TRAILING_CONJ | MOVE_TRAILING_FWD

# Prepositions that bind to the words AFTER them — a line/caption must not end
# on one or it strands the bound phrase ("durch | die Wassereinlagerungen").
# Broader than MOVE_TRAILING (which is about MOVING words between captions); here
# we only choose where NOT to break, so the common one-word prepositions are
# safe to include and keep preposition+noun phrases intact.
FORWARD_PREPS = {
    "in", "an", "auf", "aus", "bei", "mit", "nach", "von", "vor", "zu", "über",
    "unter", "um", "durch", "für", "gegen", "ohne", "seit", "zwischen", "bis",
    "je", "pro", "neben", "hinter", "gegenüber", "ab", "samt", "trotz", "wegen",
    "statt", "gen",
}
# Intensifiers / quantifiers that bind to the following adjective/adverb/noun
# ("sehr | müde", "mehr | Haare") — never end a line on one.
FORWARD_INTENS = {
    "sehr", "ganz", "extrem", "ziemlich", "wirklich", "besonders", "total",
    "recht", "so", "mehr", "weniger", "kaum", "fast", "viel", "wenig",
}
# A line must never END on a forward-binding word: a determiner/possessive
# (LINE_BREAK_BAD_LAST), a binding preposition/subordinator (MOVE_TRAILING),
# a one-word preposition or intensifier, or a bare number (binds to its noun).
NO_LINE_END = LINE_BREAK_BAD_LAST | MOVE_TRAILING | FORWARD_PREPS | FORWARD_INTENS

# ─── Per-language forward-binding words ──────────────────────────────────────
# The German sets above power the language-sensitive safety nets: fix_line_break
# and _binds_forward (where a visible line must NOT end) and
# move_trailing_binders (which trailing word is moved to the next caption).
# English / Spanish / French / Italian / Polish get their own closed-class sets
# so the same nets apply that language's grammar. Like the German lists these
# are EVERGREEN: only closed classes (determiners, prepositions, subordinators,
# intensifiers, negation) — never video vocabulary. Spanish used to have none and
# fell back to the German sets, which contain no Spanish word at all: every net
# was silently off, so a line could end on "de", "la" or "no".

# English — the set is deliberately SPLIT along the two things the nets do,
# because English strands prepositions where German cannot ("what's it for",
# "who are you with"). MOVING a word to the next caption is a real edit, so it
# is limited to words that essentially never end an English clause; choosing
# where a line WRAPS is free, so the strandable prepositions live in the
# no-line-end set below, where a wrong guess costs nothing. Keeping a caption
# off a stranded preposition is Gemini's job (prompt rule G), not this net's.
# Also excluded from MOVE, as homographs: "that" (complementizer vs the pronoun
# in "I didn't expect that"), "like" (preposition vs the verb "I like"), and
# every phrasal-verb particle (up, out, off, on, in, over, down, through,
# back, away) — moving those would break "figure it out" / "gave it up".
MOVE_TRAILING_EN = {
    # subordinators — they open a dependent clause, so the clause follows
    "because", "if", "unless", "whether", "while", "whilst", "although",
    "though", "when", "whenever", "until", "till", "since",
    # prepositions that are (near-)never stranded at the end of a clause
    "into", "onto", "within", "without", "upon", "despite", "during",
    "among", "amongst", "between", "throughout", "besides", "toward",
    "towards", "than",
    # the intensifier / result connector, as in German
    "so",
}
# Determiners, possessives, quantifiers, intensifiers, negation and the
# strandable prepositions: a visible LINE must not end on any of these, but
# none of them is ever moved between captions.
_DET_INTENS_EN = {
    # articles, demonstratives, possessives
    "the", "a", "an", "this", "that", "these", "those",
    "my", "your", "his", "her", "its", "our", "their",
    # quantifiers / determiners
    "no", "every", "each", "any", "some", "both", "either", "neither",
    "another", "much", "many", "few", "several", "most", "all", "half",
    "which", "whose", "what",
    # intensifiers & degree adverbs (they bind to the adjective/adverb/noun)
    "very", "really", "too", "quite", "pretty", "super", "totally",
    "extremely", "fairly", "rather", "almost", "nearly", "hardly", "barely",
    "just", "even", "more", "less", "least", "enough", "such", "way",
    # negation (binds to what it negates)
    "not", "never", "don't", "doesn't", "didn't", "can't", "won't", "isn't",
    "aren't", "wasn't", "weren't", "couldn't", "shouldn't", "wouldn't",
    "haven't", "hasn't", "hadn't", "ain't",
    # prepositions & relativisers — safe here, unsafe to move
    "of", "to", "at", "for", "with", "from", "on", "in", "by", "about",
    "over", "under", "above", "below", "behind", "across", "around", "near",
    "against", "through", "before", "after", "like", "as", "per", "via",
    "who", "whom",
}

# French — subordinators + binding prepositions/partitives safe to MOVE to the
# start of the next caption. The clitic pronoun "en"/"y" homographs are
# deliberately excluded here (post-verbal "il y en a" would break) but "en" is
# still a bad LINE ending, so it appears in the det/intensifier set below.
MOVE_TRAILING_FR = {
    "que", "qui", "quand", "parce", "puisque", "lorsque", "comme",
    "pour", "avec", "sans", "chez", "vers", "contre", "entre", "depuis",
    "dans", "sur", "sous", "de", "du", "des", "à", "au", "aux",
    "très", "si", "plus", "moins", "trop",
}
_DET_INTENS_FR = {
    "le", "la", "les", "un", "une", "ce", "cet", "cette", "ces",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses",
    "notre", "nos", "votre", "vos", "leur", "leurs",
    "quel", "quelle", "quels", "quelles", "chaque", "quelques", "plusieurs",
    "ne", "en", "assez", "tellement", "vraiment", "presque", "peu", "beaucoup",
}

# Italian — "non"/"si" precede their verb, articulated prepositions bind to
# their noun phrase.
MOVE_TRAILING_IT = {
    "che", "chi", "se", "quando", "perché", "mentre", "siccome", "dove",
    "per", "con", "senza", "contro", "verso", "tra", "fra",
    "di", "a", "da", "in", "su",
    "del", "dello", "della", "dei", "degli", "delle",
    "al", "allo", "alla", "ai", "agli", "alle",
    "dal", "dallo", "dalla", "nel", "nella", "nei", "nelle",
    "sul", "sulla", "molto", "più", "meno", "troppo", "così", "non", "si",
}
_DET_INTENS_IT = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "questo", "questa", "questi", "queste",
    "quel", "quello", "quella", "quei", "quegli", "quelle",
    "mio", "mia", "miei", "mie", "tuo", "tua", "tuoi", "tue",
    "suo", "sua", "suoi", "sue", "nostro", "nostra", "nostri", "nostre",
    "vostro", "vostra", "loro", "ogni", "qualche", "alcuni", "alcune",
    "tanto", "poco", "quasi", "davvero", "proprio", "abbastanza",
}

# Polish — prepositions (including the one-letter w/z/o/u), subordinators and
# the pre-verbal negation "nie" all bind forward. No articles in Polish; the
# demonstratives/possessives play that role.
MOVE_TRAILING_PL = {
    "że", "żeby", "aby", "bo", "ponieważ", "gdy", "kiedy", "jeśli",
    "jeżeli", "chociaż", "czy",
    "dla", "z", "ze", "w", "we", "na", "do", "od", "o", "u", "za", "po",
    "przed", "przez", "przy", "bez", "pod", "nad", "między",
    "bardzo", "zbyt", "nie",
}
_DET_INTENS_PL = {
    "ten", "ta", "to", "te", "tego", "tej", "tych", "tym", "tą",
    "taki", "taka", "takie", "każdy", "każda", "każde",
    "żaden", "żadna", "żadne",
    "mój", "moja", "moje", "twój", "twoja", "twoje",
    "swój", "swoja", "swoje", "nasz", "nasza", "nasze",
    "jego", "jej", "ich", "kilka", "wiele", "więcej", "mniej",
    "który", "która", "które", "których",
    "tak", "trochę", "prawie", "całkiem", "naprawdę", "dość", "tylko",
}

# Spanish (Spain) — MOVED only when the word can essentially never end a Spanish
# clause. Spanish writing helps here: the words that CAN end one carry an accent
# that tells them apart ("¿por qué?", "creo que sí", "¿cuándo?", "¿dónde?", "té",
# "sé"), so the unaccented "que", "si", "donde" are always the start of what
# follows. Deliberately NOT moved, as homographs or clause-final in common use:
# "como" (the verb "I eat", in a diet ad: "lo que como"), "cuando" ("de vez en
# cuando"), "entre" (the verb, "que entre"), "más"/"menos" ("nada más", "un poco
# más"), and every coordinator — like German's und/oder, a natural pause.
MOVE_TRAILING_ES = {
    # subordinators — the clause follows
    "que", "porque", "si", "aunque", "mientras", "donde", "sino",
    # prepositions and their contractions — the noun phrase follows
    "a", "al", "de", "del", "en", "con", "sin", "para", "por", "contra",
    "hacia", "hasta", "desde", "sobre", "tras", "ante", "durante", "mediante",
    # intensifiers that never close a phrase
    "muy", "tan",
}
# Never the last word of a LINE, never moved between captions: determiners,
# possessives (Spain's vosotros set included), quantifiers, the unstressed
# pronouns that precede their verb ("se me | olvida" is the classic bad break),
# negation, and the prepositions/subordinators too ambiguous to move. A wrong
# guess here only moves a line break, which costs nothing.
_DET_INTENS_ES = {
    # articles
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo",
    # demonstratives (the determiner forms — "esto"/"eso" are pronouns and can
    # close a clause, so they are not here)
    "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
    "aquel", "aquella", "aquellos", "aquellas",
    # possessives — unaccented "tu" is "your"; "tú" (you) is not in here
    "mi", "mis", "tu", "tus", "su", "sus", "nuestro", "nuestra", "nuestros",
    "nuestras", "vuestro", "vuestra", "vuestros", "vuestras",
    # quantifiers that open a noun phrase
    "cada", "algún", "alguna", "algunos", "algunas", "ningún", "ninguna",
    "varios", "varias", "muchos", "muchas", "pocos", "pocas", "cualquier",
    # unstressed object pronouns — separate words only BEFORE a verb, since
    # after one they are written onto it ("dámelo"); "sé"/"té" carry an accent
    "me", "te", "se", "nos", "os", "le", "les",
    # "ni" binds to what it negates. "no" does NOT go here, although it usually
    # precedes its verb: "Pero no.", "creo que no" and the tag "¿no?" end a
    # clause all the time, and with the full stops gone a guard on "no" pushed
    # the line break into the next sentence ("Pero / no Se me caía el pelo").
    "ni",
    # a number the model left spelled out still belongs to its noun, exactly as
    # a digit does ("dos | cápsulas")
    "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez",
    "once", "doce", "quince", "veinte", "treinta", "cuarenta", "cincuenta",
    "sesenta", "setenta", "ochenta", "noventa", "cien", "doscientos",
    "trescientos", "quinientos", "mil",
    # degree words that bind to what follows
    "más", "menos", "casi", "súper", "super",
    # too ambiguous to move (see above), still never the end of a line
    "como", "cuando", "entre", "según", "bajo",
}
# Pronouns, auxiliaries, modals, coordinators and discourse words — like
# _PRONOUNS_AUX_EN, not part of the nets, only what separates a function word
# from a content word for the emphasis check.
_PRONOUNS_AUX_ES = {
    "yo", "tú", "él", "ella", "usted", "nosotros", "nosotras", "vosotros",
    "vosotras", "ellos", "ellas", "ustedes", "mí", "ti", "sí", "conmigo",
    "contigo", "esto", "eso", "aquello", "algo", "nada", "nadie", "alguien",
    "todo", "toda", "todos", "todas", "mucho", "mucha", "poco", "poca", "otro",
    "otra", "otros", "otras", "qué", "cuál", "quién", "cómo", "cuándo",
    "dónde", "cuánto",
    "soy", "eres", "es", "somos", "sois", "son", "era", "eras", "éramos",
    "erais", "eran", "fue", "fui", "fueron", "sea", "ser", "estar",
    "estoy", "estás", "está", "estamos", "estáis", "están", "estaba",
    "estaban", "he", "has", "ha", "hemos", "habéis", "han", "había", "habían",
    "hay", "haya", "voy", "vas", "va", "vamos", "vais", "van",
    "puedo", "puedes", "puede", "podemos", "podéis", "pueden", "podría",
    "quiero", "quieres", "quiere", "debo", "debes", "debe", "debería",
    "tengo", "tienes", "tiene", "tenemos", "tenéis", "tienen", "sé", "sabes",
    "y", "e", "o", "u", "pero", "pues", "entonces", "luego", "también",
    "tampoco", "ya", "aquí", "ahí", "allí", "hoy", "ayer", "mañana", "ahora",
    "siempre", "nunca", "jamás", "solo", "sólo", "bien", "así", "bueno",
    "vale", "claro",
}
#: Every Spanish closed-class word we know — the counterpart of GERMAN_LOWERCASE
#: and ENGLISH_FUNCTION, used only to tell a function word from a content word.
SPANISH_FUNCTION = MOVE_TRAILING_ES | _DET_INTENS_ES | _PRONOUNS_AUX_ES

MOVE_TRAILING_BY_LANG = {
    "de": MOVE_TRAILING,
    "en": MOVE_TRAILING_EN,
    "es": MOVE_TRAILING_ES,
    "fr": MOVE_TRAILING_FR,
    "it": MOVE_TRAILING_IT,
    "pl": MOVE_TRAILING_PL,
}
NO_LINE_END_BY_LANG = {
    "de": NO_LINE_END,
    "en": MOVE_TRAILING_EN | _DET_INTENS_EN,
    "es": MOVE_TRAILING_ES | _DET_INTENS_ES,
    "fr": MOVE_TRAILING_FR | _DET_INTENS_FR,
    "it": MOVE_TRAILING_IT | _DET_INTENS_IT,
    "pl": MOVE_TRAILING_PL | _DET_INTENS_PL,
}

# Pronouns, auxiliaries, modals and coordinators. NOT part of the nets above —
# a line may end on "I have" — but needed to tell a function word from a
# content word (see _function_words / split_emphasis_repeats).
_PRONOUNS_AUX_EN = {
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "us", "them",
    "myself", "yourself", "himself", "herself", "itself", "ourselves",
    "themselves", "mine", "yours", "hers", "ours", "theirs", "there", "here",
    "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "can", "could", "shall", "should", "will", "would", "may", "might",
    "must", "gonna", "wanna", "gotta",
    "and", "or", "but", "then", "also", "yes", "yeah", "okay", "ok",
}
#: Every English closed-class word we know — the counterpart of the German
#: GERMAN_LOWERCASE list, used only where a function word must be told apart
#: from a content word. Never used to choose a break or move a word.
ENGLISH_FUNCTION = MOVE_TRAILING_EN | _DET_INTENS_EN | _PRONOUNS_AUX_EN


def _move_trailing() -> set:
    return MOVE_TRAILING_BY_LANG.get(ACTIVE_LANG, MOVE_TRAILING)


def _no_line_end() -> set:
    return NO_LINE_END_BY_LANG.get(ACTIVE_LANG, NO_LINE_END)


def _function_words() -> set:
    """The active language's closed-class vocabulary, where the list is complete
    enough to separate function words from content words. German, English and
    Spanish have one; the others return an empty set, and callers then fall back
    to their narrower, language-agnostic bracket."""
    if ACTIVE_LANG == "de":
        return GERMAN_LOWERCASE
    if ACTIVE_LANG == "en":
        return ENGLISH_FUNCTION
    if ACTIVE_LANG == "es":
        return SPANISH_FUNCTION
    return set()


# A word that travels WITH the binder it precedes. Spanish builds its most common
# conjunctions and relatives as "<word> que" — "lo que", "así que", "ya que",
# "para que", "el que" — and moving only the "que" leaves the first half behind:
# "sé lo" + "que quieres" splits the unit the move exists to protect. So when the
# moved word is the key here, a preceding word in its set comes along
# ("sé" + "lo que quieres"). Keyed per language and per moved word, so no other
# language's mover changes: German "fällt aus, weil" must never carry "aus".
MOVE_CARRY_BY_LANG = {
    "es": {"que": {"lo", "el", "la", "los", "las", "así", "ya", "para", "sin",
                   "hasta", "de", "en", "con", "a", "antes", "después", "desde",
                   # "es que", "lo peor era que", and the periphrases of must
                   "es", "era", "fue", "hay", "tengo", "tienes", "tiene",
                   "tenemos", "tenéis", "tienen"}},
}


def _carried(prev_tok: str, moved_tok: str) -> bool:
    carry = MOVE_CARRY_BY_LANG.get(ACTIVE_LANG, {}).get(_norm_word(moved_tok), ())
    return _norm_word(prev_tok) in carry


def move_trailing_binders(segments: list) -> list:
    """Never end a caption on a forward-binding word (subordinating conjunction,
    a binding preposition, or "so"). Move the trailing one to the START of the
    next caption so the bound pair stays together — but only when the next
    caption still fits two lines afterwards, and never for a sentence-final
    token (e.g. "oder?"). Word index ranges shift with it so timing stays
    correct. See MOVE_CARRY_BY_LANG for the one case that moves two words."""
    segs = [dict(s) for s in segments]
    for i in range(len(segs) - 1):
        toks = _seg_tokens(segs[i])
        last = toks[-1] if toks else ""
        if not (len(toks) >= 2 and _norm_word(last) in _move_trailing()
                and not re.search(r"[?!.]", last)):
            continue
        # How many trailing words go: the binder, plus the word it completes,
        # as long as the caption keeps at least one word of its own.
        n = 1
        if _carried(toks[-2], last):
            # The pair goes together or not at all: moving only the "que" of
            # "así que" is the split this exists to prevent. It also needs a
            # word of the caption to stay behind, and a word index to give up.
            if len(toks) < 3 or segs[i]["end"] - 1 <= segs[i]["start"]:
                continue
            n = 2
        nxt = segs[i + 1]
        candidate = " ".join(toks[-n:]) + " " + _flat_text(nxt)
        if not _fits_two_lines(candidate):
            continue  # moving it would overflow the next caption — leave as is
        word_idx = segs[i]["end"] - (n - 1)
        segs[i]["text"] = " ".join(toks[:-n])
        segs[i]["end"] = max(segs[i]["start"], word_idx - 1)
        nxt["text"] = candidate
        nxt["start"] = word_idx
    return segs


def split_emphasis_repeats(segments: list) -> list:
    """Isolate an emphatic repetition the segmenter glued to the end of a
    caption. When a caption's LAST word is a short CONTENT word that also appears
    in the PREVIOUS caption — a deliberate repeat like "… zweimal extra" / "…
    anpasst Zweimal" — peel that last word onto its own caption so the repeat
    lands standalone for emphasis. The peeled word is protected from re-merging
    by merge_orphans (it sets "_keep").

    Guards against false positives: the word must be short (≤ORPHAN_W_MAX) and a
    CONTENT word — function words (und, die, nicht, mehr, … / the, and, just, …)
    repeat constantly and are never peeled. A language with no function-word
    list of its own (fr/it/pl) falls back to the narrow "repeats the previous
    caption's first word" bracket, since another language's list can't filter
    it safely."""
    out: list = []
    for i, seg in enumerate(segments):
        toks = _seg_tokens(seg)
        prev = segments[i - 1] if i > 0 else None
        prev_toks = _seg_tokens(prev) if prev else []
        last = _norm_word(toks[-1]) if toks else ""
        is_repeat = False
        if (len(toks) >= 2 and prev_toks and last
                and text_width(toks[-1]) <= ORPHAN_W_MAX):
            func = _function_words()
            if func and last not in func:
                is_repeat = last in {_norm_word(t) for t in prev_toks}
            elif _binds_forward(toks[-1]):
                # An article, preposition or clitic belongs to what follows, so
                # it is never an emphatic repeat. The narrow bracket below still
                # peeled one: Spanish "…sino la" lost its "la" to a caption of its
                # own because the caption before happened to open on "la" too.
                # ("Nie … Nie" is untouched: "nie" binds nothing.)
                is_repeat = False
            else:
                is_repeat = last == _norm_word(prev_toks[0])
        if is_repeat:
            head = dict(seg)
            head["text"] = " ".join(toks[:-1])
            head["end"] = max(seg["start"], seg["end"] - 1)
            tail = dict(seg)
            tail["text"] = toks[-1]
            tail["start"] = seg["end"]
            tail["end"] = seg["end"]
            tail["_keep"] = True  # don't let merge_orphans glue it back
            out.append(head)
            out.append(tail)
            continue
        out.append(seg)
    return out


def merge_orphans(segments: list) -> list:
    """Tetris pass: never strand a meaningless single short word ("tun", "und",
    "ist") on its own caption. Merge it into an adjacent multi-word caption —
    preferring the previous one, falling back to the next — as long as the
    merged text still fits in ≤2 lines of real width. Word index ranges are
    extended so timing stays correct, and the text is re-packed in
    finalize_caption().

    Two kinds of deliberate single-word captions are KEPT standalone, never
    merged:
      • emphasis repetition — the lone word repeats a word in an adjacent
        caption (the creator said it twice on purpose, e.g. "Zweimal");
      • list items — a run of single-word captions (ingredient/symptom lists
        are one item per caption: "Artischocke" / "Selen" / …).
    A deliberately-standalone long compound is also preserved (it isn't an
    orphan), so the German two-line compound split is untouched."""
    if len(segments) < 2:
        return segments
    segs = [dict(s) for s in segments]
    n = len(segs)

    for i, seg in enumerate(segs):
        if not _is_orphan(seg):
            continue
        word = _norm_word(_flat_text(seg))
        prev = segs[i - 1] if i > 0 else None
        nxt = segs[i + 1] if i + 1 < n else None
        neighbour_words = set()
        if prev:
            neighbour_words |= {_norm_word(t) for t in _seg_tokens(prev)}
        if nxt:
            neighbour_words |= {_norm_word(t) for t in _seg_tokens(nxt)}
        is_repeat = word in neighbour_words
        in_list = (prev is not None and len(_seg_tokens(prev)) == 1) or \
                  (nxt is not None and len(_seg_tokens(nxt)) == 1)
        if is_repeat or in_list:
            seg["_keep"] = True

    def mergeable(seg: dict) -> bool:
        return _is_orphan(seg) and not seg.get("_keep")

    # Pass 1 — pull a mergeable orphan back into the previous multi-word caption.
    out: list = []
    for seg in segs:
        if out and mergeable(seg) and len(_seg_tokens(out[-1])) >= 2:
            prev = out[-1]
            combined = _flat_text(prev) + " " + _flat_text(seg)
            if _fits_two_lines(combined):
                prev["text"] = combined
                prev["end"] = seg["end"]
                continue
        out.append(seg)

    # Pass 2 — otherwise join it to the next multi-word caption.
    res: list = []
    i = 0
    while i < len(out):
        seg = out[i]
        if mergeable(seg) and i + 1 < len(out) and len(_seg_tokens(out[i + 1])) >= 2:
            nxt = out[i + 1]
            combined = _flat_text(seg) + " " + _flat_text(nxt)
            if _fits_two_lines(combined):
                merged = dict(nxt)
                merged["text"] = combined
                merged["start"] = seg["start"]
                res.append(merged)
                i += 2
                continue
        res.append(seg)
        i += 1

    for seg in res:
        seg.pop("_keep", None)
    return res


# Words after which a trailing number is a LABEL/ordinal (a complete unit), not
# a quantity that binds to a following noun — so "Nummer 1" must NOT be glued to
# the next sentence.
NUMBER_LABELS = {
    "nummer", "nr", "teil", "punkt", "schritt", "kapitel", "tag", "woche",
    "folge", "runde", "phase", "level", "tipp", "grund", "regel", "platz",
}

NUMBER_LABELS_BY_LANG = {
    "de": NUMBER_LABELS,
    "en": {
        "number", "no", "nr", "part", "point", "step", "chapter", "day",
        "week", "episode", "phase", "level", "tip", "reason", "rule",
        "place", "round", "lesson", "mistake", "myth", "sign",
    },
    # "no" is left out: in Spanish it is the negation long before it is "nº".
    "es": {
        "número", "nº", "parte", "punto", "paso", "capítulo", "día",
        "semana", "episodio", "fase", "nivel", "consejo", "motivo", "razón",
        "regla", "puesto", "lugar", "truco", "error", "mito", "señal",
    },
    "fr": {
        "numéro", "no", "partie", "point", "étape", "chapitre", "jour",
        "semaine", "épisode", "phase", "niveau", "astuce", "raison", "règle",
        "place", "conseil",
    },
    "it": {
        "numero", "nr", "parte", "punto", "passo", "capitolo", "giorno",
        "settimana", "episodio", "fase", "livello", "consiglio", "motivo",
        "regola", "posto",
    },
    "pl": {
        "numer", "nr", "część", "punkt", "krok", "rozdział", "dzień",
        "tydzień", "odcinek", "etap", "poziom", "wskazówka", "powód",
        "zasada", "miejsce", "rada",
    },
}


def _number_labels() -> set:
    return NUMBER_LABELS_BY_LANG.get(ACTIVE_LANG, NUMBER_LABELS)


def merge_split_numbers(segments: list) -> list:
    """Keep a number with its unit/noun. If a caption ends on a bare number and
    merging it with the next caption fits two lines, merge them: "ich habe 6" +
    "Kilo abgenommen" → "ich habe 6 Kilo abgenommen". A number used as a label
    ("Nebenwirkung Nummer 1") is left alone."""
    out: list = []
    i = 0
    while i < len(segments):
        seg = dict(segments[i])
        toks = _seg_tokens(seg)
        if (toks and re.fullmatch(r"\d+([.,]\d+)?", _norm_word(toks[-1]))
                and not (len(toks) >= 2 and _norm_word(toks[-2]) in _number_labels())
                and i + 1 < len(segments)):
            nxt = segments[i + 1]
            combined = _flat_text(seg) + " " + _flat_text(nxt)
            if _fits_two_lines(combined):
                merged = dict(nxt)
                merged["text"] = combined
                merged["start"] = seg["start"]
                out.append(merged)
                i += 2
                continue
        out.append(seg)
        i += 1
    return out


def merge_short_durations(segments: list, words: list, min_dur: float = 0.6) -> list:
    """Merge a multi-word caption that would be on screen for less than min_dur
    seconds into a neighbour, so it stays long enough to read. Tries the NEXT
    caption first (a too-brief caption is usually the start of the upcoming
    phrase — e.g. "einen Mann" → "einen Mann der nicht mehr mitkommt"), then the
    previous one. Single-word captions are left to merge_orphans, so emphasis
    repeats / list items aren't touched here."""
    if len(segments) < 2:
        return segments

    def dur(seg: dict) -> float:
        try:
            d = words[seg["end"]]["end"] - words[seg["start"]]["start"]
            return d if d > 0 else min_dur
        except Exception:
            return min_dur  # unknown timing → treat as fine, never merge

    def too_short(seg: dict) -> bool:
        return len(_seg_tokens(seg)) >= 2 and dur(seg) < min_dur

    # Pass 1 — fold a too-brief caption into the NEXT one when it fits.
    out: list = []
    i = 0
    while i < len(segments):
        seg = dict(segments[i])
        if too_short(seg) and i + 1 < len(segments):
            nxt = segments[i + 1]
            combined = _flat_text(seg) + " " + _flat_text(nxt)
            if _fits_two_lines(combined):
                merged = dict(nxt)
                merged["text"] = combined
                merged["start"] = seg["start"]
                out.append(merged)
                i += 2
                continue
        out.append(seg)
        i += 1

    # Pass 2 — otherwise fold any still-too-brief caption into the previous one.
    res: list = []
    for seg in out:
        if res and too_short(seg):
            prev = res[-1]
            combined = _flat_text(prev) + " " + _flat_text(seg)
            if _fits_two_lines(combined):
                prev["text"] = combined
                prev["end"] = seg["end"]
                continue
        res.append(seg)
    return res


#: Pairs that bind although neither word does on its own. Spanish "no" cannot be
#: a no-line-end word (see _DET_INTENS_ES), but "no" + an unstressed pronoun is
#: always the start of a verb phrase — "no lo pienses", "no me gusta", "no se
#: mueve" — and "Así que no / lo pienses más" is exactly the break to avoid.
PAIR_BINDERS_BY_LANG = {
    "es": ({"no"}, {"me", "te", "se", "nos", "os", "lo", "la", "le", "los",
                    "las", "les"}),
}


def _binds_pair(tok: str, nxt: str) -> bool:
    first, second = PAIR_BINDERS_BY_LANG.get(ACTIVE_LANG, ((), ()))
    if _norm_word(tok) not in first or _norm_word(nxt) not in second:
        return False
    # A capital pronoun after "no" opens the NEXT sentence ("Pero no. Se me
    # caía el pelo" with its full stop gone) — Spanish capitalises nothing
    # else mid-caption, so here the capital is the full stop.
    return not strip_punct(nxt)[:1].isupper()


#: Word pairs after which the second word binds NOTHING, although on its own it
#: would. Spanish "así que" is a connector ("so"), and what follows it is a main
#: clause, not the subordinate one a bare "que" opens: "Así que / no lo pienses
#: más" is the right break, and treating "que" as binding forced "Así / que…".
UNBOUND_AFTER_BY_LANG = {
    "es": {("así", "que")},
}


def _unbound(prev: str, tok: str) -> bool:
    pairs = UNBOUND_AFTER_BY_LANG.get(ACTIVE_LANG, ())
    return bool(prev) and (_norm_word(prev), _norm_word(tok)) in pairs


def _binds_forward(tok: str, nxt: str = "", prev: str = "") -> bool:
    """True if `tok` binds to what FOLLOWS it, so a line/caption must not end on
    it: a determiner/preposition/intensifier (the ACTIVE_LANG's no-line-end set)
    or a bare number (which binds to its noun, "10 | Jahren")."""
    if tok.endswith(("?", "!")):
        return False  # it closes a sentence — "¿verdad que no?" | "pues…"
    if _unbound(prev, tok):
        return False
    w = _norm_word(tok)
    return (w in _no_line_end() or bool(re.fullmatch(r"\d+([.,]\d+)?", w))
            or bool(nxt and _binds_pair(tok, nxt)))


def _split_one_line(tokens: list) -> list:
    """Split a token list into the fewest consecutive one-line pieces, cutting at
    the most BALANCED safe boundary — never right after a word that binds to what
    follows. Balancing avoids stranding a lone short word as a sub-second caption
    (the greedy "pack then strand the remainder" failure). A run that cannot be
    broken safely (a bound pair, or a word wider than a line) is returned whole
    and rendered on two lines by finalize_caption. Returns a list of token lists."""
    if text_width(" ".join(tokens)) <= LINE_W_MAX or len(tokens) < 2:
        return [tokens]
    best = None
    for k in range(1, len(tokens)):
        if _binds_forward(tokens[k - 1], tokens[k],
                          tokens[k - 2] if k >= 2 else ""):
            continue  # can't end a line on a forward-binding word
        left = tokens[:k]
        if text_width(" ".join(left)) > LINE_W_MAX:
            continue  # left half must itself fit one line to make progress
        cost = abs(text_width(" ".join(left)) - text_width(" ".join(tokens[k:])))
        if best is None or cost < best[0]:
            best = (cost, k)
    if best is None:
        return [tokens]  # no safe one-line break — keep whole (two-line caption)
    k = best[1]
    return [tokens[:k]] + _split_one_line(tokens[k:])


def _split_two_lines(tokens: list) -> list:
    """Split a token list into the fewest consecutive pieces that each fit TWO
    lines, cutting at the most BALANCED safe boundary — never right after a word
    that binds to what follows. The two-line counterpart of _split_one_line: it
    fires only on a caption too long for two lines, where the alternative is a
    line over the budget that the renderer wraps again into three or four. A run
    that cannot be broken safely is returned whole. Returns a list of token lists."""
    if len(tokens) < 2 or _fits_two_lines(" ".join(tokens)):
        return [tokens]
    best = None
    for k in range(1, len(tokens)):
        if _binds_forward(tokens[k - 1], tokens[k],
                          tokens[k - 2] if k >= 2 else ""):
            continue  # can't end a caption on a forward-binding word
        left = tokens[:k]
        if not _fits_two_lines(" ".join(left)):
            continue  # left piece must itself fit to make progress
        cost = abs(text_width(" ".join(left)) - text_width(" ".join(tokens[k:])))
        if best is None or cost < best[0]:
            best = (cost, k)
    if best is None:
        return [tokens]  # no safe break — keep whole
    k = best[1]
    return [tokens[:k]] + _split_two_lines(tokens[k:])


def _enforce_width(segments: list, words: list, chunker, fits_whole) -> list:
    """Split every caption `chunker` can break into narrower pieces, re-deriving
    each piece's word-index range so timing stays correct. A piece that genuinely
    cannot be reduced (a bound pair, or a single word wider than the budget) is
    kept whole and laid out by finalize_caption. Shared by the one-line and the
    two-line enforcement so both stay deterministic rather than relying on the
    model to count characters."""
    out = []
    for seg in segments:
        flat = " ".join(flatten_lines(seg["text"]).split())
        tokens = flat.split()
        s, e = seg["start"], seg["end"]
        chunks = chunker(tokens) if len(tokens) >= 2 else [tokens]
        # Need one distinct word index per chunk; if the caption already fits,
        # can't be reduced, or spans fewer words than chunks, leave it whole.
        if fits_whole(flat) or len(chunks) < 2 or len(chunks) > (e - s + 1):
            out.append({**seg, "text": flat})
            continue
        # Map token cut points to word indices ~1:1 within [s, e], strictly
        # increasing and reserving one index per remaining chunk so it stays valid.
        cuts, acc = [], 0
        for ch in chunks:
            cuts.append(acc)
            acc += len(ch)
        starts = [s]
        for j in range(1, len(chunks)):
            st = max(starts[-1] + 1, min(s + cuts[j], e - (len(chunks) - 1 - j)))
            starts.append(st)
        if starts[-1] > e or any(starts[k] >= starts[k + 1] for k in range(len(starts) - 1)):
            out.append({**seg, "text": flat})  # degenerate mapping → don't split
            continue
        pieces = []
        for j, ch in enumerate(chunks):
            en = e if j == len(chunks) - 1 else starts[j + 1] - 1
            pieces.append({**seg, "start": starts[j], "end": en, "text": " ".join(ch)})
        # Duration guard: a piece that flashes by too briefly to read is WORSE than
        # one wide caption. If splitting would create such a piece, keep the caption
        # whole (finalize lays it out as best it can).
        def _piece_dur(p):
            try:
                d = words[p["end"]]["end"] - words[p["start"]]["start"]
                return d if d > 0 else MIN_PIECE_DUR
            except Exception:
                return MIN_PIECE_DUR  # unknown timing → don't block the split
        if any(_piece_dur(p) < MIN_PIECE_DUR for p in pieces):
            out.append({**seg, "text": flat})
            continue
        out.extend(pieces)
    return out


def enforce_single_line(segments: list, words: list) -> list:
    """Single-line mode: split any caption wider than one line into several
    one-line captions at safe, balanced word boundaries. A piece that genuinely
    cannot fit one line is kept whole and rendered on two lines."""
    return _enforce_width(segments, words, _split_one_line,
                          lambda t: text_width(t) <= LINE_W_MAX)


def enforce_two_lines(segments: list, words: list) -> list:
    """Hybrid mode: split any caption too long for TWO lines into several captions.

    The SOP is two lines. Gemini is asked for 4-7 word units but nothing enforced
    it, so a 10-word / 59-char caption reached the packer, which had no honest way
    to lay it out — it emitted two over-wide lines and the renderer wrapped them
    into four. This is the enforcement: fix the grouping, not the packing."""
    return _enforce_width(segments, words, _split_two_lines, _fits_two_lines)


def learn_and_relabel_case(segments: list) -> list:
    """German casing without hard-coded vocabulary. German capitalises nouns
    everywhere, so casing is reliable in NON-initial caption positions; only the
    first word of a caption is ambiguous (the model tends to capitalise it just
    because it starts the line). So: learn each word's casing from non-initial
    occurrences, then fix the caption-initial word to match. Also populates
    LEARNED_UPPER so normalize_case won't force-lowercase a word this video
    clearly uses as a noun (e.g. "Morgen" vs the adverb "morgen"). Evergreen — it
    adapts to each video's own words instead of a fixed list."""
    global LEARNED_UPPER
    if ACTIVE_LANG != "de":
        return segments
    upper, lower = Counter(), Counter()
    for seg in segments:
        for line in seg["text"].split("\n"):
            for pos, tok in enumerate(line.split()):
                if pos == 0 or not tok[:1].isalpha():
                    continue  # initial casing is unreliable; skip non-words
                key = strip_punct(tok).lower()
                if key:
                    (upper if tok[:1].isupper() else lower)[key] += 1
    # A word is a "noun in this video" with ≥2 capitalised mid-caption sightings
    # and a capital-leaning majority (so a homograph like Morgen/morgen resolves
    # to its dominant use). Lowercasing is conservative: only words never once
    # seen capitalised mid-caption, so a noun is never accidentally lowered.
    LEARNED_UPPER = {k for k, n in upper.items() if n >= 2 and n > lower.get(k, 0)}
    learned_lower = {k for k, n in lower.items() if n >= 1 and upper.get(k, 0) == 0}

    def relabel_first(tok: str) -> str:
        if not tok[:1].isalpha():
            return tok
        key = strip_punct(tok).lower()
        if not key or key in GERMAN_FORMAL_HOMOGRAPHS:
            return tok  # leave Sie/Ihr to the model's context
        if (tok[:1].isupper() and key not in LEARNED_UPPER
                and (key in GERMAN_LOWERCASE or key in learned_lower)):
            return tok[:1].lower() + tok[1:]
        if tok[:1].islower() and key in LEARNED_UPPER:
            return tok[:1].upper() + tok[1:]
        return tok

    out = []
    for idx, seg in enumerate(segments):
        lines = seg["text"].split("\n")
        first = lines[0].split()
        if first:
            if idx == 0:
                # The hook: caption 0 is a genuine sentence start, not a
                # mid-sentence fragment — always capitalise it.
                w = first[0]
                if w[:1].isalpha():
                    first[0] = w[:1].upper() + w[1:]
            else:
                first[0] = relabel_first(first[0])
            lines[0] = " ".join(first)
        out.append({**seg, "text": "\n".join(lines)})
    return out


def _build_recase_prompt(captions: list) -> str:
    listing = "\n".join(f"[{i}] {t}" for i, t in enumerate(captions))
    last = len(captions) - 1
    return f"""You are fixing ONLY the capitalisation of German TikTok caption fragments (numbered below, one per line). Return the SAME captions with correct German casing.

RULES:
- Capitalise nouns and proper names — German capitalises every noun, anywhere ("Morgen", "Ärzte", "Wassereinlagerungen", "Dosis", "Antonio Bianco").
- A noun stays a noun after a determiner or quantifier — capitalise it: "jeden Morgen", "jeden Tag", "jede Woche", "am Abend", "die Dosis", "ihre Werte" (the noun "Werte" is capital regardless of the word before it). Watch the time-of-day nouns "Morgen/Abend/Mittag/Tag/Nacht" — capital as nouns ("jeden Morgen"), lowercase ONLY as the adverb "morgens/abends" or "morgen" meaning tomorrow.
- Capitalise the formal-address words Sie, Ihr, Ihre, Ihren, Ihrem, Ihrer, Ihres, Ihnen when they mean the formal "you". Use the SURROUNDING captions as context: e.g. a doctor quoted speaking to the patient ("… der gleiche Satz: ihre Werte sind doch in Ordnung") is formal → "Ihre Werte". Keep "sie/ihr" lowercase only when they clearly mean she / they / her.
- EVERYTHING ELSE is lowercase, INCLUDING THE FIRST WORD of a caption. These are mid-sentence fragments — never capitalise a word just because it starts the line. Verbs, adjectives, adverbs, pronouns, articles, prepositions and conjunctions stay lowercase at the start ("bis", "egal", "von", "jeden", "trinkst", "gesund", "und").
- EXCEPTION: caption [0] is the video's opening line (the hook) — it is a genuine sentence start, not a mid-sentence fragment. Its first word MUST be capitalised like any normal German sentence start, even if the same word would stay lowercase elsewhere (e.g. "Bis", "Trinkst", "Und"). This exception applies ONLY to caption [0].
- Change ONLY letter case. Do NOT add, remove, reorder, split, merge or respell any word. Do NOT change any digit, punctuation mark or spacing.

Captions:
{listing}

Return a JSON array of EXACTLY {last + 1} strings — caption [0] first … caption [{last}] last — each the corresponding caption with corrected capitalisation and otherwise IDENTICAL (same words, same order, same punctuation)."""


def recase_with_ai(segments: list, language: str = "de") -> list:
    """Final casing pass — the path to near-zero casing revisions. Asks Gemini to
    correct ONLY the capitalisation of the finished German captions (the task it
    is most reliable at when it is not also segmenting, rewording or line-breaking
    at the same time). Each returned caption is validated WORD-FOR-WORD: accepted
    only if it has the same words in the same order (case-insensitively), so the
    model can never change, drop or reorder a word — only its letter case. On a
    confident result CASE_FIXED_BY_AI is set, so normalize_case trusts it instead
    of the static list (which mishandles homographs like Morgen/morgen). Falls
    back silently to the deterministic casing on any failure or --no-ai."""
    global CASE_FIXED_BY_AI
    if language != "de" or len(segments) < 1:
        return segments
    flats = [" ".join(_flat_text(s).split()) for s in segments]
    data = _call_gemini(_build_recase_prompt(flats))
    if not isinstance(data, list) or len(data) != len(flats):
        print("Casing pass: no usable response — keeping deterministic casing.")
        return segments

    def words_key(t: str) -> list:
        return [strip_punct(w).lower() for w in t.split() if strip_punct(w)]

    out, fixed = [], 0
    for seg, original, cased in zip(segments, flats, data):
        if isinstance(cased, str) and words_key(cased) == words_key(original):
            out.append({**seg, "text": " ".join(cased.split())})
            fixed += 1
        else:
            out.append(seg)  # word mismatch → keep this caption's deterministic casing
    # Only trust the pass when it confidently validated; otherwise fall back fully
    # to the deterministic casing so we never lose the safety net to a bad call.
    if fixed < len(flats) * 0.8:
        print(f"Casing pass: low confidence ({fixed}/{len(flats)}) — keeping deterministic casing.")
        return segments
    CASE_FIXED_BY_AI = True
    print(f"Casing pass: {fixed}/{len(flats)} captions recased by Gemini.")
    return out


def write_srt(segments: list, spans: list, out_path: Path):
    """`spans` is one (start, end) per caption — see caption_spans()."""
    with open(out_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments):
            txt = finalize_caption(seg["text"])
            start, end = spans[i]
            f.write(f"{i+1}\n{fmt_time(start)} --> {fmt_time(end)}\n{txt}\n\n")


def main():
    global ACTIVE_LANG, LINE_MODE
    parser = argparse.ArgumentParser(
        description="Generate TikTok-style captions from a video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("video", help="Path to video file (mp4, mov, etc.)")
    parser.add_argument("--out", default=None, help="Output SRT path (default: <video>.srt)")
    parser.add_argument("--no-ai", action="store_true", help="Skip Gemini, use heuristic only")
    parser.add_argument("--model", default="large-v3", help="Whisper model (default: large-v3)")
    parser.add_argument("--context", default="", help="Optional context hint for Gemini")
    parser.add_argument("--language", default="de",
                        choices=["de", "en", "es", "fr", "it", "pl"],
                        help="Language: de (default), en, pl, fr, it, es")
    parser.add_argument("--no-repair", action="store_true",
                        help="Don't re-ask the transcriber about silent windows")
    parser.add_argument("--lines", default="hybrid", choices=["hybrid", "1"],
                        help="Caption length: hybrid (default, natural 1-2 line "
                             "mix) or 1 (one line per caption)")
    args = parser.parse_args()

    ACTIVE_LANG = args.language
    LINE_MODE = args.lines

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        sys.exit(f"Video not found: {video_path}")

    out_path = Path(args.out).expanduser().resolve() if args.out else video_path.with_suffix(".srt")
    out_dir = video_path.parent

    duration = get_video_duration(video_path)
    print(f"Video duration: {duration:.2f}s")
    print(f"Language       : {LANGUAGE_META[args.language]['name']} ({args.language})")
    print(f"Caption length : {'one line per caption' if LINE_MODE == '1' else 'hybrid (1-2 lines)'}")

    # Per-language cache so switching language doesn't reuse the wrong transcription.
    json_path = out_dir / f"{video_path.stem}.{args.language}.json"
    if not json_path.exists():
        json_path = run_whisperx(video_path, args.model, out_dir, language=args.language)
    else:
        print(f"Reusing existing transcription: {json_path.name}")

    words = load_words(json_path)
    print(f"Loaded {len(words)} words.")
    # A hole in the transcription is a lost chunk, not a silent clip: ask again
    # for that window before anything downstream reads the words.
    if not args.no_repair:
        words = repair_gaps(video_path, words, args.model, out_dir,
                            args.language, json_path)

    segments = None
    used_ai = False
    if not args.no_ai:
        segments = segment_with_ai(words, args.context, language=args.language)
        used_ai = segments is not None

    if segments is None:
        if not args.no_ai:
            print("Falling back to heuristic segmentation.")
        else:
            print("Using heuristic segmentation.")
        # Single-line mode wants shorter, more numerous captions, so cap the
        # heuristic groups tighter; hybrid keeps the long-standing default of 6.
        segments = segment_heuristic(words, max_words=3 if LINE_MODE == "1" else 6)

    # Second AI pass: re-group the draft into natural units (merge-only, never
    # changes words). Skipped for the heuristic fallback / --no-ai.
    if used_ai:
        print("Reviewing caption grouping with Gemini...")
        segments = review_grouping(segments, language=args.language)

    segments = move_trailing_binders(segments)
    segments = merge_split_numbers(segments)
    segments = split_emphasis_repeats(segments)
    segments = merge_orphans(segments)
    segments = merge_short_durations(segments, words)
    # Deterministically break any still-too-wide caption into pieces (timing
    # re-derived), so we don't rely on the model to count: one-line pieces in
    # single-line mode, two-line pieces otherwise. Without this a caption the
    # model grouped too generously reached the packer with no way to lay it out,
    # and came out as over-wide lines the renderer wrapped a second time.
    if LINE_MODE == "1":
        segments = enforce_single_line(segments, words)
    else:
        segments = enforce_two_lines(segments, words)
    # Brand words, before the casing pass so it sees the repaired spelling and
    # before write_srt, whose finalize_caption() runs apply_canonical_terms()
    # last (exact case wins) and only then packs the lines — a repair that
    # changes a line's width must be re-laid-out, not shipped over budget.
    # Self-guards to a no-op with no key, no configured terms, or any failure.
    if not args.no_ai and os.environ.get("GEMINI_API_KEY", "").strip():
        segments = repair_terms_with_ai(segments, language=args.language)
    # German casing — two layers. First the deterministic learner (always; sets
    # LEARNED_UPPER, fixes obvious caption-initial words; the offline floor).
    segments = learn_and_relabel_case(segments)
    # Then a dedicated Gemini casing pass that fixes the rest using real German
    # grammar (word-for-word validated; this is what gets casing to ~99%). Runs
    # whenever a key is present — INDEPENDENT of segmentation, so even a heuristic
    # fallback (e.g. the segmentation call timed out) still gets AI-quality casing.
    # Self-guards to a no-op without a key / for non-German / on any API failure.
    if args.language == "de" and os.environ.get("GEMINI_API_KEY", "").strip():
        print("Fixing German capitalization with Gemini...")
        segments = recase_with_ai(segments, language=args.language)
    boundaries = compute_boundaries(segments, words, duration)
    spans = caption_spans(segments, words, boundaries)
    write_srt(segments, spans, out_path)
    print(f"Wrote {len(segments)} captions to {out_path}")
    # Say it once more at the end: a hole the re-ask could not fill is the one
    # thing about a finished .srt that cannot be seen from the file's size.
    left = find_gaps(words)
    for a, b in left:
        print(f"⚠ {b - a:.1f}s with no captions ({a:.1f}s–{b:.1f}s) — "
              f"the transcriber heard nothing there")


if __name__ == "__main__":
    main()
