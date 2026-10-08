"""Gemini over plain HTTPS — the one transport the app uses.

No SDK: a POST to `generativelanguage.googleapis.com` with `urllib`, the key in
the `x-goog-api-key` header. Two entry points, matching the two shapes the app
asks for:

    generate_text(...)  -> str    free-form answer (Camera Prompts)
    generate_json(...)  -> dict   `response_schema`-constrained answer, with
                                  retry/backoff on 429/503 (Script Animator)

This module has **no Qt and no app imports**, so it stays testable offline and
importable from anywhere. Callers own their own threading (both run it off the
UI thread) and may pass `on_event` to hear what the transport is doing while
they wait — see `OnEvent`.

⚠️ Why one module: the transport used to exist twice — once in `camera_page`
with a hardened SSL context, once in `animator_page` with none. The two drifted,
and the animator's calls could fail to verify Google's chain on Python.org macOS
and Windows builds. Add a caller here, not another copy.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# A CHAIN of named models, tried in order — not a single pin and not a floating
# alias. Both of those have already failed here, in opposite directions:
#
#   * a pin ("gemini-2.5-flash") went 404 when Google stopped serving it to
#     newly-created keys, so every teammate on a fresh key was dead;
#   * the alias that replaced it ("gemini-flash-latest") follows Google onto
#     whatever Flash launched most recently — which is the model LEAST likely to
#     have free-tier quota switched on yet. Free keys then get an instant 429
#     "you exceeded your current quota" on the first request of the day.
#
# Both failures are per-key and per-model, and both are invisible from a paid
# key. A chain survives them without a release: the good model is tried first,
# and a key that can't use it falls through to one it can. Every entry is
# verified to accept the exact request shape this module sends (response_schema,
# seed, thinkingBudget: 0) — `gemini-flash-lite-latest`, for one, does not.
MODEL_CHAIN = ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.1-flash-lite")
DEFAULT_MODEL = MODEL_CHAIN[0]

# The chain member that last answered. The Animator makes two calls per build
# and expects the same cut from the same script, so once a model has worked it
# is tried first for the rest of the session rather than re-walking the chain.
_WORKING_MODEL: "str | None" = None

# Gemini answers a demand spike with 503 ("high demand … try again later") and
# throttling with 429. Both clear on their own in a second or two, so they must
# not surface as a failure.
RETRY_CODES = (429, 500, 502, 503, 504)
BACKOFF_S = (2, 5, 10)

# What the transport is doing while the caller waits — the waits and fallbacks
# above used to be invisible: the Animator's status sat on "Reading the copy…"
# through 17 s of backoff, and its countdown said a few seconds were left.
#
#   on_event("attempt",  {"model": m, "n": 1})              a request goes out
#   on_event("backoff",  {"model": m, "seconds": 5, "code": 503})
#                                                           sleeping, then retry
#   on_event("fallback", {"from": m, "to": m2, "code": 429}) next model in the chain
#
# `n` counts the requests to that model, from 1. A plain callable, so the caller
# decides how to cross threads: it is called synchronously on the calling thread
# (so it must return at once), and a listener that raises is ignored — it can't
# break a call.
OnEvent = Optional[Callable[[str, dict], None]]


def _tell(on_event: OnEvent, kind: str, info: dict) -> None:
    if on_event is None:
        return
    try:
        on_event(kind, info)
    except Exception:
        pass


# --- the key ---------------------------------------------------------------

# The two shapes a Gemini key comes in. AI Studio handed out "AIza…" keys (39
# characters, always) until 28 May 2026, and "AQ.…" auth keys — no fixed
# length — since. An AQ. key must travel in the header: Google is retiring it
# from the `?key=` URL parameter, which is where this module used to put it.
_KEY_SHAPE = re.compile(r"AIza[0-9A-Za-z_\-]{35}|AQ\.[0-9A-Za-z_\-]{20,}")
# Cut before every key prefix first: an AQ. key has no fixed length, so a
# second key glued onto one would otherwise read as part of it.
_KEY_START = re.compile(r"(?=AIza)|(?=AQ\.)")


def key_tokens(text: str) -> list[str]:
    """Every key-shaped run in `text`, in order. Two keys end to end are two."""
    found = []
    for part in _KEY_START.split(text or ""):
        m = _KEY_SHAPE.match(part)
        if m:
            found.append(m.group(0))
    return found


def clean_key(text: str, previous: str = "") -> str:
    """The one key in whatever was pasted, given the key saved before it.

    A paste brings more than the key: whitespace, quotes, a `GEMINI_API_KEY=`
    copied out of a .env — or the saved key itself, because Settings shows it
    masked and a paste lands next to it instead of over it. Google refuses two
    keys run together on every model, and the saved string still looks fine
    as a row of dots. So the new key wins: the key-shaped
    runs that aren't the saved one, the last of them.

    Text with no key shape in it is kept as typed. Google has changed the shape
    once already; refusing to save a third one would be worse than letting
    Google say no.
    """
    text = (text or "").strip()
    found = key_tokens(text)
    if not found:
        return text.strip('"\'').strip()
    fresh = [k for k in found if k != (previous or "").strip()]
    return (fresh or found)[-1]


def key_shape(key: str) -> str:
    """What a saved key looks like, without saying what it is."""
    key = (key or "").strip()
    found = key_tokens(key)
    if len(found) > 1:
        return "%d keys run together" % len(found)
    if found and found[0] == key:
        return "AQ. auth key" if key.startswith("AQ.") else "AIza key"
    if found:
        return "a key with other text around it"
    return "not shaped like a Gemini key"


# --- TLS -------------------------------------------------------------------

_CTX: "ssl.SSLContext | None" = None


def ssl_context() -> ssl.SSLContext:
    """An SSLContext that can verify Google's chain on every platform we ship.

    Priority: certifi (bundles Mozilla's CA list) → the macOS system bundle →
    the Windows cert stores (ROOT + CA) → Python's default. The default alone
    fails on Python.org macOS builds and some Windows installs, which is why
    `certifi` is pinned in requirements.txt. Built once and reused.
    """
    global _CTX
    if _CTX is not None:
        return _CTX

    try:
        import certifi
        _CTX = ssl.create_default_context(cafile=certifi.where())
        return _CTX
    except ImportError:
        pass

    ctx = ssl.create_default_context()

    if sys.platform == "darwin":
        import os
        for bundle in ("/etc/ssl/cert.pem",
                       "/opt/homebrew/etc/ca-certificates/cert.pem",
                       "/usr/local/etc/ca-certificates/cert.pem"):
            if os.path.exists(bundle):
                try:
                    ctx.load_verify_locations(bundle)
                except Exception:
                    pass
                break

    elif sys.platform == "win32":
        import base64
        import textwrap
        for store in ("ROOT", "CA"):
            try:
                for cert_der, enc, _trust in ssl.enum_certificates(store):
                    if enc != "x509_asn":
                        continue
                    pem = ("-----BEGIN CERTIFICATE-----\n"
                           + textwrap.fill(base64.b64encode(cert_der).decode("ascii"), 64)
                           + "\n-----END CERTIFICATE-----\n")
                    try:
                        ctx.load_verify_locations(cadata=pem)
                    except Exception:
                        pass
            except Exception:
                pass

    _CTX = ctx
    return _CTX


# --- transport -------------------------------------------------------------

class GeminiError(RuntimeError):
    """Anything the caller should show the user verbatim.

    Carries the HTTP status too, so `_post` can tell "this model won't work for
    this key right now" (404/429/5xx — try the next one) from "this request is
    wrong" (400 — trying another model would only waste two more round
    trips)."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


# A model answering with one of these is making a statement about ITSELF —
# it's retired (404), this key has no quota on it (429), or it is overloaded
# right now (5xx: "This model is currently experiencing high demand"). Google
# sheds load per model, so another model in the chain may well answer. Anything
# else is about the request, and repeating it against a different model would
# just fail three times instead of once.
OVERLOADED = (500, 502, 503, 504)
MODEL_FATAL = (404, 429) + OVERLOADED


def models_to_try(model: str) -> tuple[str, ...]:
    """The models `_post` should walk, in order, for a caller asking `model`.

    An explicit model — a caller's argument, or GEMINI_MODEL in the environment
    — is a pin: it is tried alone, because someone who names a model wants that
    model's answer and wants to be told when it can't be had. Only the default
    fans out to the chain, led by whatever already worked this session.
    """
    override = (os.environ.get("GEMINI_MODEL") or "").strip()
    if override:
        return (override,)
    if model != DEFAULT_MODEL:
        return (model,)
    if _WORKING_MODEL and _WORKING_MODEL in MODEL_CHAIN:
        return (_WORKING_MODEL,) + tuple(
            m for m in MODEL_CHAIN if m != _WORKING_MODEL)
    return MODEL_CHAIN


def _post(api_key: str, model: str, body: dict, timeout: int,
          retries: bool, on_event: OnEvent = None) -> dict:
    """POST one generateContent request; return the parsed envelope.

    Walks `models_to_try()` and returns the first model's answer. A model that
    isn't the last one gets no backoff on a 404 or 429 and one short retry on a
    5xx — waiting 17 seconds on a model that is out of quota or overloaded,
    when the next model in the chain would answer at once, is the failure this
    is here to avoid.
    """
    global _WORKING_MODEL
    models = models_to_try(model)
    last: "GeminiError | None" = None

    for i, name in enumerate(models):
        more = i < len(models) - 1
        try:
            payload = _post_one(api_key, name, body, timeout,
                                retries=retries, leaving=more,
                                on_event=on_event)
        except GeminiError as e:
            if more and e.code in MODEL_FATAL:
                last = e
                _tell(on_event, "fallback",
                      {"from": name, "to": models[i + 1], "code": e.code})
                continue
            raise
        _WORKING_MODEL = name
        return payload

    raise last or GeminiError("No response from Gemini.")


def _post_one(api_key: str, model: str, body: dict, timeout: int, *,
              retries: bool, leaving: bool, on_event: OnEvent = None) -> dict:
    """One model's turn: POST, with backoff on the codes worth waiting out.

    `leaving` means another model is next in line. Then a 429 is not waited
    out at all and a 5xx only once, briefly: the full 17-second backoff
    belongs to the last model, whose failure is the user's.

    The key goes in the header, never the URL — an AQ. key is only accepted
    there, and a URL is what a traceback prints.
    """
    url = f"{API_ROOT}/{model}:generateContent"
    data = json.dumps(body).encode("utf-8")
    backoff = BACKOFF_S if retries else ()

    for attempt in range(len(backoff) + 1):
        _tell(on_event, "attempt", {"model": model, "n": attempt + 1})
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json",
                                     "x-goog-api-key": api_key})
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            waits = len(backoff)
            if leaving:
                waits = 0 if e.code == 429 else min(1, waits)
            if e.code not in RETRY_CODES or attempt >= waits:
                raise _http_error(e, model, api_key) from e
            _tell(on_event, "backoff", {"model": model,
                                        "seconds": backoff[attempt],
                                        "code": e.code})
            time.sleep(backoff[attempt])

    raise GeminiError("No response from Gemini.")


def _http_error(e: urllib.error.HTTPError, model: str = "",
                api_key: str = "") -> GeminiError:
    try:
        detail = e.read().decode("utf-8", "ignore")[:600]
    except Exception:
        detail = ""
    # The key itself. 400 API_KEY_INVALID for a string that isn't an AQ. key,
    # 401 for one that is — and either way the raw JSON on screen was all the
    # user got, so the next step was an error report instead of Settings.
    if (e.code == 401 or (e.code == 400 and (
            "API_KEY_INVALID" in detail or "API key not valid" in detail))):
        if len(key_tokens(api_key)) > 1:
            return GeminiError(
                "The saved Gemini key is two keys run together, so Google "
                "refuses it. Open Settings and paste just the one from Google "
                "AI Studio.", e.code)
        return GeminiError(
            "Google doesn't accept the saved Gemini key — it may be mistyped, "
            "or deleted in Google AI Studio. Paste a fresh one in Settings.",
            e.code)
    if e.code == 429:
        # A per-day quota doesn't clear by waiting a few seconds, and the raw
        # JSON tells the user nothing they can act on.
        if "PerDay" in detail:
            return GeminiError(
                "Gemini's free daily quota for this key is used up. It resets "
                "tomorrow — or add billing to the Google project. A build costs "
                "two requests.", 429)
        # Not a daily cap. Either the per-minute allowance (clears in a minute)
        # or a key with NO free-tier quota on these models at all (never clears
        # by waiting) — and the two are told apart by when it happens, which is
        # something only the person at the keyboard knows. So say both.
        return GeminiError(
            "Gemini refused this key on every model Mariposa can use, for "
            "quota. If you've just run a few builds, wait a minute — the free "
            "tier allows only a handful of requests per minute. If it fails on "
            "the very first build of the day, this key has no free quota for "
            "these models: add billing to the Google project, or set "
            "GEMINI_MODEL in tools/captions-de/.env to a model it can use.",
            429)
    # Overloaded. The chain absorbs a spike on one model, so reaching the user
    # means every model it asked was turned away — or a pin was.
    if e.code in OVERLOADED:
        # Short enough for the Animator's status line, which shows 160
        # characters after its own "Gemini failed — ".
        return GeminiError(
            "Google's servers are overloaded right now (%d on every model "
            "tried). It usually passes in a few minutes — try again then."
            % e.code, e.code)
    # A retired model. The chain should absorb this, so reaching the user means
    # every model in it was refused — or GEMINI_MODEL pins a dead one.
    if e.code == 404 and ("no longer available" in detail or "not found" in detail):
        return GeminiError(
            "Gemini has retired every model this app knows how to ask for. "
            "Update Mariposa Studio — if it still fails, make sure GEMINI_MODEL "
            "isn't pinned to an old model in tools/captions-de/.env.", 404)
    where = f" from {model}" if model else ""
    return GeminiError(f"HTTP {e.code}{where}: {detail[:300]}", e.code)


def _answer_text(payload: dict) -> tuple[str, str]:
    """The concatenated text of candidate 0, plus its finishReason."""
    cands = payload.get("candidates") or []
    if not cands:
        raise GeminiError(f"No candidates returned. Raw: {payload}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    return text, cands[0].get("finishReason") or ""


# --- the two shapes --------------------------------------------------------

def generate_text(api_key: str, prompt: str, *, model: str = DEFAULT_MODEL,
                  temperature: float = 0.6, max_output_tokens: int = 1500,
                  timeout: int = 45, on_event: OnEvent = None) -> str:
    """A free-form answer. Thinking is off — these prompts don't need it."""
    payload = _post(api_key, model, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }, timeout=timeout, retries=False, on_event=on_event)

    text, _ = _answer_text(payload)
    if not text:
        raise GeminiError(f"Empty response. Raw: {payload}")
    return text


def generate_json(api_key: str, prompt: str, schema: dict, *,
                  model: str = DEFAULT_MODEL, temperature: float = 0,
                  seed: int = 7, max_output_tokens: int = 48000,
                  timeout: int = 120, on_event: OnEvent = None) -> dict:
    """A `response_schema`-constrained answer, decoded.

    ⚠️ temperature 0 + a fixed seed + thinking OFF: the user builds the same
    script more than once and expects the same cut both times. Variable
    reasoning paths were the main reason two builds came out different.
    """
    payload = _post(api_key, model, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "seed": seed,
            "maxOutputTokens": max_output_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
    }, timeout=timeout, retries=True, on_event=on_event)

    text, finish = _answer_text(payload)
    try:
        return json.loads(text)
    except Exception as e:
        if finish == "MAX_TOKENS":
            raise GeminiError("The script is too long for one pass — the answer "
                              "was cut off. Build it in two halves.") from e
        raise GeminiError(f"Couldn't parse the response: {e}\n{text[:300]}") from e
