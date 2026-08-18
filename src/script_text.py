"""The language layer under the Animator: words, sentences and seams.

No Qt, no network, no notion of a clip - everything here is about the copy
itself, and `script_packer` sits on top of it deciding where the cuts go.

  * syllables and sentence splitting (the fallback timing formula counts them;
    the measured clock in `speech_clock` does not)
  * where a sentence may legally be cut, and how good each seam is - graded on
    the same 0-3 LINK scale as a seam between two sentences, so the packer's
    dynamic program can price both with one rule
  * the pronunciation map (written -> spoken)
  * the copy guards: `verbatim_gaps` (did the model rewrite the copy?) and
    `leftover_symbols` (did a digit survive into something to be spoken?)

The LINK grades live here because they describe how one sentence relates to the
one before it. What a cut *costs* is a packing decision and lives next door.
"""

from __future__ import annotations

import re

__all__ = [
    "CONNECTORS", "DEFAULT_PRONUNCIATION", "LINK_INSEPARABLE",
    "LINK_NEW_POINT", "LINK_NEW_SECTION", "LINK_SAME_THOUGHT", "NUMERALS",
    "PRONUNCIATION", "RESUMPTIONS", "SEAM_CLAUSE", "SEAM_CONNECTOR",
    "SEAM_MARK", "STANDALONE_OPENERS", "SUBORDINATORS", "WEAK_RESUMPTIONS",
    "apply_pronunciation", "count_syllables",
    "fragment_sentence", "in_vocabulary", "infer_link", "leftover_symbols",
    "numeral_re", "openers_for", "parse_pronunciation", "pronunciation_for",
    "split_sentences", "verbatim_gaps", "word_forms",
]

# ── Link grades ──────────────────────────────────────────────────────────────
# How a sentence relates to the one before it. Gemini grades every sentence;
# these are *costs*, not vetoes, so a run that has to break somewhere breaks at
# the cheapest seam instead of wherever the arithmetic ran out.
LINK_INSEPARABLE = 0   # cannot open a shot: answers it, continues it, "Aber …"
LINK_SAME_THOUGHT = 1  # same thought, better kept together
LINK_NEW_POINT = 2     # a new point in the same section — a clean cut
LINK_NEW_SECTION = 3   # a new section of the ad — the best place to cut

# Coordinating conjunctions: a legal cut point inside a sentence. Coordinating
# ONLY — a subordinating conjunction (`perché`, `porque`, `car`) leaves a fragment
# behind, and it is already reachable as the far better clause seam below.
CONNECTORS: dict[str, tuple[str, ...]] = {
    "German":  ("und", "aber", "denn", "oder", "sondern"),
    "English": ("and", "but", "so", "or", "yet"),
    "Spanish": ("y", "e", "pero", "mas", "o", "u", "sino"),
    "French":  ("et", "mais", "ou", "donc", "car", "or"),
    "Italian": ("e", "ma", "o", "oppure", "però", "anzi"),
    "Polish":  ("i", "a", "ale", "lub", "albo", "więc", "czyli"),
}

# Words that open a subordinate clause. German closes such a clause with a comma,
# and that comma is the other legal cut point — it is where the speaker breathes,
# and both halves still stand as whole statements.
SUBORDINATORS: dict[str, tuple[str, ...]] = {
    "German":  ("wenn", "weil", "dass", "obwohl", "während", "damit", "um",
                "falls", "sobald", "bevor", "nachdem", "bis", "seit", "da"),
    "English": ("if", "because", "that", "although", "while", "so", "before",
                "after", "until", "since", "when", "once"),
    "Spanish": ("si", "porque", "que", "aunque", "mientras", "para", "cuando",
                "como", "antes", "después", "hasta", "desde", "ya"),
    "French":  ("si", "parce", "que", "bien", "pendant", "pour", "quand",
                "comme", "avant", "après", "jusque", "depuis", "puisque"),
    "Italian": ("se", "perché", "che", "mentre", "quando", "come", "dopo",
                "prima", "finché", "affinché", "benché", "sebbene", "poiché",
                "siccome", "dove"),
    "Polish":  ("jeśli", "jeżeli", "bo", "ponieważ", "że", "choć", "chociaż",
                "gdy", "kiedy", "dopóki", "zanim", "aby", "żeby", "gdyby"),
}

# Latin Extended-A is in the class so Polish keeps its letters (ą ć ę ł ń ś ź ż);
# without it a word split at every one of them and both the syllable count and
# every table lookup came apart.
_LETTERS = "a-zà-öø-ÿœæßĀ-ſ"
_VOWELS = "aeiouyäöüàáâãåèéêëìíîïòóôõøùúûœæąę"
_WORD_RE = re.compile(f"[{_LETTERS}]+", re.IGNORECASE)
_VOWEL_GROUP_RE = re.compile(f"[{_VOWELS}]+")
_SENT_END_RE = re.compile(r"([.!?…]+[\"'»”’\)\]]*)(\s+|$)")
_ABBREV_TAIL_RE = re.compile(r"(?:^|\s)[^\W\d_]\.$", re.UNICODE)

# ── Syllables ────────────────────────────────────────────────────────────────

# Languages where a run of vowels is regularly more than one syllable. Counting
# vowel *groups* is right for German and English and badly wrong for these: Italian
# "aiutano" is a-iu-ta-no and "idea" is i-de-a, both of which a group count reads a
# syllable short — and short is the dangerous direction, since it is what ships copy
# the clip can't hold.
_HIATUS_LANGUAGES = ("Italian", "Spanish")
_WEAK_VOWELS = frozenset("iuy")           # the glide half of a diphthong
_WEAK_ACCENTED = frozenset("íìîïúùûü")    # …unless it carries the stress


def _group_syllables(group: str) -> int:
    """Syllables in one run of vowels, by the Romance strong/weak rule.

    Three things break a group in two, and only three: an accented weak vowel
    (`farmacìa`), a weak vowel that another vowel follows, so it is a glide onto
    the next syllable (`a-iu-ta-no`), and two strong vowels meeting (`pa-e-se`,
    `i-de-a`). Everything else is a diphthong and stays one syllable (`mai`,
    `tuo`, `zio-ne`).

    What it cannot see is stress that the writing doesn't mark: `tiroide` is
    ti-ro-i-de to a speaker and ti-roi-de here. That is a syllable short on a
    handful of words, against a group count that was short on most of them.
    """
    n = 1
    for k in range(1, len(group)):
        prev, cur = group[k - 1], group[k]
        following = k + 1 < len(group)
        if cur in _WEAK_ACCENTED:
            n += 1
        elif cur in _WEAK_VOWELS and following:
            n += 1
        elif cur not in _WEAK_VOWELS and prev not in _WEAK_VOWELS:
            n += 1
    return n


def _word_syllables(word: str, language: str) -> int:
    """Syllables in one word — vowel-group counting with per-language tweaks.

    Approximate by design (a full dictionary would be a dependency), but stable
    and close enough for German, which is what the tool is used for. Nothing
    user-facing shows a syllable count: this feeds the pace model only."""
    w = "".join(ch for ch in word.lower() if ch.isalpha())
    if not w:
        return 0
    groups = _VOWEL_GROUP_RE.findall(w)
    n = len(groups)
    if language == "English":
        # Silent terminal "e" ("time", "make") — but not "-le" ("table") or "-ee".
        if n > 1 and w.endswith("e") and not w.endswith(("le", "ee", "ye", "oe", "ie")):
            n -= 1
    elif language == "German":
        # "-tion"/"-sion" is spoken as two syllables (Na-ti-on), one vowel group.
        n += len(re.findall(r"[ts]ion", w))
    elif language in _HIATUS_LANGUAGES:
        n = sum(_group_syllables(g) for g in groups)
    return max(1, n)


def count_syllables(text: str, language: str = "German") -> int:
    """Total syllables in a stretch of spoken text."""
    return sum(_word_syllables(w, language) for w in _WORD_RE.findall(text.lower()))


# ── Sentences ────────────────────────────────────────────────────────────────

def split_sentences(text: str) -> list[str]:
    """Split on . ! ? … only. Used by the pace model and by the fallback packer
    when the model didn't return sentences for a block."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    out: list[str] = []
    start = 0
    for m in _SENT_END_RE.finditer(text):
        chunk = text[start:m.end(1)].strip()
        # "z. B." style abbreviations: a lone letter before the dot is not an end.
        if len(chunk) < 3 or _ABBREV_TAIL_RE.search(chunk):
            continue
        out.append(chunk)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def _clean_word(w: str) -> str:
    return "".join(ch for ch in w.lower() if ch.isalpha())


_APOSTROPHE_RE = re.compile(r"['’´`]")


def word_forms(word: str) -> tuple[str, ...]:
    """The forms one token may be looked up under — elisions included.

    Italian, French and Spanish glue a short word onto the next one and drop its
    vowel: `l'esterno`, `un'amica`, `d'allarme`, `c'è`. `_clean_word` alone fuses
    those into `lesterno`, which matches nothing in any table here — so an Italian
    clause comma read as an ordinary comma and the best seam in the sentence was
    never offered. Both halves are returned as well as the whole, since either can
    be the word that matters (`l'` is the determiner, `esterno` the noun).
    """
    whole = _clean_word(word)
    parts = [p for p in _APOSTROPHE_RE.split(word.strip()) if p.strip()]
    if len(parts) < 2:
        return (whole,)
    forms = [whole] + [_clean_word(p) for p in parts]
    return tuple(dict.fromkeys(f for f in forms if f))


def in_vocabulary(word: str, vocabulary) -> bool:
    """Is this token one of ``vocabulary``, reading through an elision?"""
    return any(form in vocabulary for form in word_forms(word))

# A seam inside a sentence is graded exactly like a seam between two sentences —
# by whether what FOLLOWS it could open a clip on its own. That way the same
# dynamic program decides intra-sentence cuts as decides everything else, and
# prices a bad one accordingly instead of needing its own rules.
SEAM_MARK = LINK_SAME_THOUGHT   # after : ; — — the writer marked the break
SEAM_CLAUSE = LINK_SAME_THOUGHT  # after a subordinate clause closed by a comma
SEAM_CONNECTOR = LINK_INSEPARABLE  # before und / oder — a fragment, not a clause

_MARK_RE = re.compile(r"[:;–—]$")


# What a clause-closing comma is followed by. This is the whole test for a comma
# seam, and it keys off what comes AFTER the comma rather than what came before.
#
# The obvious-looking alternative — "is there a subordinator earlier in the
# sentence?" — is what produced the worst cut this module has made. In
# "…wenn du … mit Gewichtszunahme, Müdigkeit, Gelenkschmerzen oder
# Schlafproblemen kämpfst, dann …" the `wenn` sits within a dozen words of every
# comma in the symptom list, so each of them read as a clause boundary and the cut
# landed inside the list. What actually distinguishes them is trivial once you look
# forward instead of back: a clause resumes with a function word, a list item is a
# bare noun.
RESUMPTIONS: dict[str, tuple[str, ...]] = {
    "German":  ("dann", "dass", "weil", "damit", "sodass", "aber", "denn", "und",
                "oder", "sondern", "wenn", "obwohl", "während", "um", "der",
                "die", "das", "du", "ich", "er", "sie", "es", "wir", "man",
                "deshalb", "deswegen", "trotzdem", "also"),
    "English": ("then", "that", "because", "so", "but", "and", "or", "if",
                "although", "while", "which", "who", "you", "i", "he", "she",
                "it", "we", "they"),
    # Italian, Spanish and Polish drop the subject, so a clause almost never
    # resumes with a pronoun the way German's does — it resumes with a
    # conjunction, an adverb, a negation, or an unstressed object pronoun
    # (`ti ridanno`, `la trovi`, `mi scrivono`). Grading only off subject pronouns
    # is why an Italian sentence had no clause seam at all and could be cut only
    # before `e`, the worst seam in the language.
    "Spanish": ("entonces", "que", "porque", "pero", "y", "o", "si", "aunque",
                "mientras", "cuando", "como", "donde", "luego", "así",
                "además", "también", "sino", "pues", "no", "ya", "tú", "yo",
                "él", "ella", "nosotros", "usted", "me", "te", "se", "le",
                "nos", "lo", "les", "es", "son", "hay", "tienes", "puedes"),
    "French":  ("alors", "que", "parce", "mais", "et", "ou", "si", "bien",
                "pendant", "puis", "ensuite", "ainsi", "donc", "car",
                "pourtant", "ne", "tu", "je", "il", "elle", "nous", "vous",
                "on", "qui", "me", "te", "se", "lui", "leur", "y", "en",
                "c", "ce", "cela", "est", "sont"),
    "Italian": ("allora", "che", "perché", "ma", "e", "o", "oppure", "però",
                "quindi", "dunque", "se", "anche", "mentre", "quando", "come",
                "dove", "dopo", "prima", "finché", "poi", "così", "invece",
                "inoltre", "infatti", "cioè", "anzi", "comunque", "ecco",
                "non", "mi", "ti", "si", "ci", "vi", "ne", "io", "tu", "lui",
                "lei", "noi", "voi", "loro", "è", "sono", "ho", "hai", "ha",
                "devi", "puoi", "vuoi"),
    "Polish":  ("wtedy", "że", "bo", "ponieważ", "ale", "i", "a", "lub",
                "albo", "jeśli", "jeżeli", "choć", "chociaż", "gdy", "kiedy",
                "więc", "dlatego", "czyli", "potem", "jednak", "nie", "już",
                "się", "to", "ja", "ty", "on", "ona", "my", "wy", "oni",
                "jest", "są", "masz", "możesz"),
}

# Determiners: a clause boundary in these languages *and* the way a list item
# starts. German list items are bare nouns ("mit Gewichtszunahme, Müdigkeit,
# Gelenkschmerzen"), so `der/die/das` can sit in RESUMPTIONS unguarded — Italian
# and Spanish items carry an article ("regola l'energia, i capelli e il peso"),
# so the same word is a clause opener only when no list is running around the
# comma. That is why these are a separate set and not more RESUMPTIONS: put them
# in there and every Italian list gets torn down the middle.
WEAK_RESUMPTIONS: dict[str, tuple[str, ...]] = {
    "Italian": ("il", "lo", "la", "i", "gli", "le", "l", "un", "uno", "una",
                "del", "della", "dei", "delle", "questo", "questa", "questi",
                "queste", "quello", "quella", "tutto", "tutta", "tutti"),
    "Spanish": ("el", "la", "los", "las", "un", "una", "unos", "unas", "del",
                "este", "esta", "estos", "estas", "ese", "esa", "esto", "eso",
                "todo", "toda", "todos"),
    "French":  ("le", "la", "les", "un", "une", "des", "du", "ce", "cet",
                "cette", "ces", "tout", "toute", "tous"),
    "Polish":  ("ten", "ta", "to", "ci", "te", "tego", "twój", "twoja",
                "twoje", "cały", "cała"),
}


def _list_running(words: list[str], i: int) -> bool:
    """Is a list of items running across position ``i``?

    "…mit Gewichtszunahme, Müdigkeit, Gelenkschmerzen **oder** Schlafproblemen
    kämpfst…" — cutting there tears the list in half and strands the verb that
    governs it. A run of commas around the position is the tell, and it is worth
    the false negative: refusing a seam only ever costs a flag, while taking this
    one produces a clip that reads as a mistake.
    """
    window = words[max(0, i - 5):i + 4]
    return sum(1 for w in window if w.endswith(",")) >= 2


def _is_list_connector(words: list[str], i: int) -> bool:
    """Is this ``und`` / ``oder`` joining list items rather than clauses?"""
    return _list_running(words, i)


def _list_item_at(words: list[str], i: int, conns: set) -> bool:
    """Is the word at ``i`` the next item of a list rather than a new clause?

    Two tells, and the second is why this exists: a run of commas around the
    position (a list of three or more), or a coordinating conjunction within the
    next couple of words, which is how a list of *two* ends — "regola l'energia,
    i capelli e il peso". The comma count alone cannot see that one: there is only
    ever one comma in it, and cutting at it strands "i capelli" as a clip.
    """
    if _list_running(words, i):
        return True
    return any(in_vocabulary(w, conns) for w in words[i + 1:i + 4])


def _seam_indices(words: list[str], language: str) -> list[tuple[int, int]]:
    """Where a sentence may legally be cut, as ``(word index, link grade)``.

    Four kinds of seam, best first:

    * after a colon, semicolon or dash — the strongest of them, because the
      writer put it there to mark the break. What follows is a whole statement.
    * after a clause closed by a comma, where what follows *resumes* the sentence
      (``Wenn du das kennst, | dann …``). German marks these unambiguously, and
      they are where a person breathes. See `RESUMPTIONS`.
    * after a comma followed by a determiner (``…, | il tuo corpo …``) — the same
      seam, in a language that resumes with an article rather than a pronoun, and
      the only one of the four that has to be guarded against reading the next
      item of a list as a new clause. See `WEAK_RESUMPTIONS`.
    * before a coordinating conjunction (``und``, ``aber``, ``oder`` …). Legal but
      poor: the second half opens on a fragment, so it is graded as unable to open
      a clip and the packer only uses it when there is nothing better.

    A comma separating list items is never a seam.
    """
    conns = set(CONNECTORS.get(language, CONNECTORS["English"]))
    resume = set(RESUMPTIONS.get(language, RESUMPTIONS["English"]))
    weak = set(WEAK_RESUMPTIONS.get(language, ()))
    seams: list[tuple[int, int]] = []
    for i in range(1, len(words)):
        if i < 3 or len(words) - i < 3:
            continue                      # neither half would be a spoken unit
        after_comma = words[i - 1].endswith(",")
        if _MARK_RE.search(words[i - 1]):
            seams.append((i, SEAM_MARK))
        elif after_comma and in_vocabulary(words[i], resume):
            # No list test here, deliberately: a resumption word after the comma
            # already proves this is a clause boundary and not a list item, and
            # applying the test anyway threw away the best seam in the sentence
            # ("… Schlafproblemen kämpfst, | dann liegt das oft daran …") merely
            # because a list happened to sit earlier in the same clause.
            seams.append((i, SEAM_CLAUSE))
        elif (after_comma and in_vocabulary(words[i], weak)
                and not _list_item_at(words, i, conns)):
            # A determiner: a clause opener in a null-subject language, but also
            # how the next list item starts — so this one *is* guarded.
            seams.append((i, SEAM_CLAUSE))
        elif in_vocabulary(words[i], conns) and not _is_list_connector(words, i):
            seams.append((i, SEAM_CONNECTOR))
    return seams


def fragment_sentence(sentence: str, language: str = "German") -> list[dict]:
    """One sentence → the pieces it can be cut into, each with its link grade.

    Cut at *every* legal seam and hand the pieces to the same packer that handles
    whole sentences: it already weighs fill, cut quality and scene count, so it
    will rejoin what it can and break only where it must — and because a seam's
    grade says whether the piece can open a clip, a bad seam costs what a bad cut
    between sentences costs. Nothing here decides the cut; it only offers it.

    The first piece carries no grade (``None``) — it inherits the whole sentence's
    own relationship to the line before it.
    """
    words = sentence.split()
    seams = _seam_indices(words, language) if len(words) >= 6 else []
    if not seams:
        return [{"text": sentence, "link": None}]
    out: list[dict] = []
    start, grade = 0, None
    for at, seam_grade in seams:
        out.append({"text": " ".join(words[start:at]), "link": grade})
        start, grade = at, seam_grade
    out.append({"text": " ".join(words[start:]), "link": grade})
    return out

def infer_link(text: str, language: str = "German") -> int:
    """A local guess at how a sentence relates to the one before it — used only
    on the fallback path, where no model output is available."""
    first = text.strip().split(" ")[0] if text.strip() else ""
    if in_vocabulary(first, _CONTINUATION.get(language, _CONTINUATION["English"])):
        return LINK_INSEPARABLE
    return LINK_NEW_POINT


# Words that say "this sentence cannot open a clip" — the local stand-in for
# Gemini's link grade, used only when a block didn't come back from the model.
# Two kinds are in here: a connective that points backwards (`aber`, `ma`,
# `tradotto`), and a subordinator that leaves a fragment when the sentence really
# is one ("Mentre i tuoi sintomi restano – o peggiorano." was cut into a 2.3s clip
# of its own). Words that *can* legitimately open a sentence of their own are
# deliberately absent even when they are subordinators — Italian "se", "quando",
# "come" and "anche" all start perfectly good clip-opening sentences.
_CONTINUATION: dict[str, tuple[str, ...]] = {
    "German": ("aber", "denn", "und", "oder", "sondern", "deswegen", "deshalb",
               "dass", "trotzdem", "also", "darum", "übersetzt", "erstens",
               "zweitens", "drittens", "egal"),
    "English": ("but", "because", "and", "or", "so", "that", "still", "which",
                "first", "second", "third"),
    "Spanish": ("pero", "porque", "y", "o", "sino", "así", "entonces",
                "además", "luego", "también", "traducido", "primero",
                "segundo", "tercero", "mientras", "aunque", "puesto"),
    "French":  ("mais", "car", "et", "ou", "donc", "alors", "puis", "ensuite",
                "ainsi", "pourtant", "traduit", "premièrement", "pendant",
                "tandis", "bien"),
    "Italian": ("ma", "perché", "e", "o", "oppure", "quindi", "allora",
                "dunque", "però", "poi", "così", "infatti", "cioè", "anzi",
                "inoltre", "tradotto", "primo", "secondo", "terzo",
                "mentre", "finché", "affinché", "sebbene", "benché",
                "poiché", "nonostante"),
    "Polish":  ("ale", "bo", "ponieważ", "i", "a", "lub", "albo", "więc",
                "dlatego", "czyli", "potem", "jednak", "przetłumaczone",
                "pierwsze", "drugie", "podczas", "dopóki", "mimo"),
}

# ── Words that can open a sentence of their own ───────────────────────────────

# NOT the same set as `RESUMPTIONS` ("does this resume the sentence?"): a
# subordinating conjunction resumes a sentence but cannot start one — "Dass sie
# aufwachen." is a fragment, not a sentence. So only coordinating conjunctions,
# adverbs, determiners and pronouns are here, and `dass`/`weil`/`während`/`damit`/
# `wenn`/`um` and their equivalents are deliberately absent: `script_packer`'s
# comma→full-stop tidy fires on this set, and on those words it would ship a
# grammatical error.
#
# In a null-subject language the words that open a sentence are mostly *not*
# pronouns: an Italian sentence opens on a determiner, a negation or an object
# pronoun ("Il tuo medico …", "Non è un caso.", "Ti ridanno i soldi."). With only
# the subject pronouns listed, the tidy never fired in Italian and every clip cut
# out of a long sentence opened lowercase on a comma.
STANDALONE_OPENERS: dict[str, frozenset] = {
    "German":  frozenset(("dann", "deshalb", "deswegen", "darum", "trotzdem",
                          "also", "und", "aber", "denn", "oder", "sondern",
                          "der", "die", "das", "du", "ich", "er", "sie", "es",
                          "wir", "man", "hier", "so", "dabei", "dadurch")),
    "English": frozenset(("then", "so", "and", "but", "or", "you", "i", "he",
                          "she", "it", "we", "they", "this", "that", "here")),
    "Spanish": frozenset(("entonces", "y", "pero", "o", "sino", "así",
                          "además", "luego", "también", "tú", "yo", "él",
                          "ella", "nosotros", "usted", "esto", "eso", "este",
                          "esta", "el", "la", "los", "las", "un", "una", "no",
                          "ya", "te", "se", "me", "es", "son", "hay",
                          "tienes", "puedes")),
    "French":  frozenset(("alors", "et", "mais", "ou", "donc", "ainsi", "puis",
                          "ensuite", "pourtant", "tu", "je", "il", "elle",
                          "nous", "vous", "on", "cela", "ça", "ce", "c",
                          "le", "la", "les", "un", "une", "ne", "est")),
    "Italian": frozenset(("allora", "e", "ma", "o", "oppure", "però", "quindi",
                          "dunque", "poi", "così", "invece", "inoltre",
                          "infatti", "anzi", "comunque", "ecco", "tu", "io",
                          "lui", "lei", "noi", "voi", "loro", "questo",
                          "questa", "questi", "queste", "quello", "quella",
                          "il", "lo", "la", "i", "gli", "le", "l", "un",
                          "uno", "una", "non", "mi", "ti", "ci", "vi", "ne",
                          "è", "sono", "ho", "hai", "ha", "devi", "puoi")),
    "Polish":  frozenset(("więc", "dlatego", "potem", "jednak", "ale", "i",
                          "a", "lub", "albo", "czyli", "to", "ten", "ta",
                          "te", "ja", "ty", "on", "ona", "my", "wy", "oni",
                          "nie", "już", "teraz", "jest", "są", "masz",
                          "możesz")),
}


def openers_for(language: str) -> frozenset:
    return STANDALONE_OPENERS.get(language, STANDALONE_OPENERS["English"])


# ── Spelled-out numbers ───────────────────────────────────────────────────────

# This tool writes every figure out, and a written-out figure is *articulated*,
# not rushed (`script_packer.NUMERAL_PENALTY`). A German and English list only was
# as good as no list at all for the other languages — "duecento milligrammi" and
# "novanta giorni" read as ordinary long words.
#
# Matched at a word start, so a compound comes along ("duecento" via "due",
# "zweitausendvierhundert" via "zwei"). Words that are also something else are
# left out on purpose where the collision is common — Italian "sei" (you are),
# Spanish "un"/French "un" (a) — since a false positive slows a whole sentence.
NUMERALS: dict[str, str] = {
    "German": r"null|eins?|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf"
              r"|zwölf|zwanzig|dreißig|vierzig|fünfzig|sechzig|siebzig|achtzig"
              r"|neunzig|hundert|tausend|million",
    "English": r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
               r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
               r"|hundred|thousand",
    "Italian": r"zero|due|tre|quattro|cinque|sette|otto|nove|dieci|undici"
               r"|dodici|venti|trenta|quaranta|cinquanta|sessanta|settanta"
               r"|ottanta|novanta|cento|mille|mila|milione|miliardo",
    "Spanish": r"cero|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once"
               r"|doce|veinte|treinta|cuarenta|cincuenta|sesenta|setenta"
               r"|ochenta|noventa|cien|ciento|mil|millón",
    "French":  r"zéro|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze"
               r"|vingt|trente|quarante|cinquante|soixante|cent|mille|million",
    # The hundreds are their own words in Polish (dwieście, not "two hundred"),
    # so prefix-matching the units does not reach them.
    "Polish":  r"zero|jeden|dwa|trzy|cztery|pięć|sześć|siedem|osiem|dziewięć"
               r"|dziesięć|dwadzieścia|trzydzieści|czterdzieści|pięćdziesiąt"
               r"|sześćdziesiąt|siedemdziesiąt|osiemdziesiąt|dziewięćdziesiąt"
               r"|sto|dwieście|trzysta|czterysta|pięćset|sześćset|siedemset"
               r"|osiemset|dziewięćset|tysiąc|milion",
}

_NUMERAL_RES: dict[str, "re.Pattern"] = {
    lang: re.compile(rf"\b(?:{words})", re.IGNORECASE)
    for lang, words in NUMERALS.items()
}
# Anything else: every list at once, so a language nobody has tuned still spots a
# numeral rather than none.
_ANY_NUMERAL_RE = re.compile(
    r"\b(?:" + "|".join(NUMERALS.values()) + r")", re.IGNORECASE)


def numeral_re(language: str) -> "re.Pattern":
    """The spelled-out-number pattern for one language."""
    return _NUMERAL_RES.get(language, _ANY_NUMERAL_RE)


# ── Pronunciation map ────────────────────────────────────────────────────────

# Words the video model mispronounces, respelled so it says them correctly —
# **per language**, because a respelling is a phonetic instruction and one
# language's is nonsense in another. `Selen → Selehn` fixes the German word and,
# matched at a word start, turned Italian "Selenio" into "Selehnio" in a shipped
# build: the German map was the only map there was. Italian's entries are the ones
# the director's own corrected script uses.
PRONUNCIATION: dict[str, str] = {
    "German":  "Selen → Selehn\nGlutathion → Glutation\nMiavola → miavòla",
    "English": "Miavola → miavòla",
    "Italian": "Glutatione → glutaTHione\nTarassaco → tàrassaco\n"
               "Miavola → miavòla",
    "Spanish": "Miavola → miavòla",
    "French":  "Miavola → miavòla",
    "Polish":  "Miavola → miavòla",
}

# The German map, kept under its old name: it is what every caller that predates
# the per-language split asks for.
DEFAULT_PRONUNCIATION = PRONUNCIATION["German"]


def pronunciation_for(language: str) -> str:
    """The respelling map for one language. A fixed house setting, not a control.

    A language with nothing to respell still gets the brand name, which the video
    model stresses wrongly in every language ("Miavola" → "miavòla")."""
    return PRONUNCIATION.get(language, PRONUNCIATION["English"])

_PRON_SPLIT_RE = re.compile(r"\s*(?:→|->|=|\|)\s*")


def parse_pronunciation(text: str) -> list[tuple[str, str]]:
    """`Selen → Selehn` lines → [(written, spoken)]. Blank/one-sided lines are
    ignored so a half-typed row never mangles the copy."""
    pairs: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = _PRON_SPLIT_RE.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        written, spoken = parts[0].strip(), parts[1].strip()
        if written and spoken:
            pairs.append((written, spoken))
    return pairs


def apply_pronunciation(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """Respell the mispronounced words. Returns (text, [what changed]).

    Matches at a word start and keeps whatever follows, so German compounds and
    inflections come along ("Selenmangel" → "Selehnmangel")."""
    changed: list[str] = []
    for written, spoken in pairs:
        pattern = re.compile(r"\b" + re.escape(written), re.IGNORECASE)
        text, n = pattern.subn(spoken, text)
        if n:
            changed.append(f"{written} → {spoken}" + (f" ×{n}" if n > 1 else ""))
    return text, changed

# ── Guards ───────────────────────────────────────────────────────────────────

_DIGIT_OR_SYMBOL_RE = re.compile(r"[0-9%€$§&@#]")


def leftover_symbols(text: str) -> str:
    """The digits/symbols still present in a line that should be fully spoken."""
    return "".join(sorted(set(_DIGIT_OR_SYMBOL_RE.findall(text or ""))))


def _same_word(a: str, b: str) -> bool:
    """Same word, allowing for an inflected ending — "Fingernägel" matches
    "Fingernägeln". Without this, every grammar fix reads as a rewrite."""
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and len(long) - len(short) <= 3 and long.startswith(short)


def verbatim_gaps(source: str, produced: str, limit: int = 6,
                  ignore: "set[str] | None" = None) -> list[str]:
    """Words of the source copy that never made it into the spoken version.

    Digits expand ("2.400" → "zweitausendvierhundert") so the two texts can't be
    compared directly; instead every word of the source that carries no digits
    must still appear, in order, in the output. Anything missing means the model
    rewrote copy it was told to leave alone.

    ``ignore`` holds words the model already declared it changed (its reported
    typo fixes) — those are agreed edits, not silent rewrites."""
    skip = {w.lower() for w in (ignore or set())}
    src = [t for t in re.findall(r"[^\W\d_]+", (source or "").lower(), re.UNICODE)
           if len(t) >= 3 and t not in skip]
    out = re.findall(r"[^\W\d_]+", (produced or "").lower(), re.UNICODE)
    missing: list[str] = []
    at = 0
    for token in src:
        found = next((i for i in range(at, len(out)) if _same_word(token, out[i])), None)
        if found is None:
            missing.append(token)
            if len(missing) >= limit:
                break
        else:
            at = found + 1
    return missing
