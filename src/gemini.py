"""Gemini over plain HTTPS — the one transport the app uses.

No SDK: a POST to `generativelanguage.googleapis.com` with `urllib`. Two entry
points, matching the two shapes the app asks for:

    generate_text(...)  -> str    free-form answer (Camera Prompts)
    generate_json(...)  -> dict   `response_schema`-constrained answer, with
                                  retry/backoff on 429/503 (Script Animator)

This module has **no Qt and no app imports**, so it stays testable offline and
importable from anywhere. Callers own their own threading (both current callers
run it inside a `QObject` worker on a `QThread`).

⚠️ Why one module: the transport used to exist twice — once in `camera_page`
with a hardened SSL context, once in `animator_page` with none. The two drifted,
and the animator's calls could fail to verify Google's chain on Python.org macOS
and Windows builds. Add a caller here, not another copy.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

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
    this key" (404/429 — try the next one) from "this request is wrong" (400 —
    trying another model would only waste two more round trips)."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


# A model answering with one of these is making a statement about ITSELF and
# this key — it's retired, or the key has no quota on it. Another model in the
# chain may well answer. Anything else is about the request, and repeating it
# against a different model would just fail three times instead of once.
MODEL_FATAL = (404, 429)


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
          retries: bool) -> dict:
    """POST one generateContent request; return the parsed envelope.

    Walks `models_to_try()` and returns the first model's answer. A 404 or 429
    from a model that isn't the last one costs no backoff at all — waiting 17
    seconds on a model this key has no quota for, when the next model in the
    chain would answer at once, is the failure this is here to avoid.
    """
    global _WORKING_MODEL
    models = models_to_try(model)
    last: "GeminiError | None" = None

    for i, name in enumerate(models):
        more = i < len(models) - 1
        try:
            payload = _post_one(api_key, name, body, timeout,
                                retries=retries, quota_is_fatal=more)
        except GeminiError as e:
            if more and e.code in MODEL_FATAL:
                last = e
                continue
            raise
        _WORKING_MODEL = name
        return payload

    raise last or GeminiError("No response from Gemini.")


def _post_one(api_key: str, model: str, body: dict, timeout: int, *,
              retries: bool, quota_is_fatal: bool) -> dict:
    """One model's turn: POST, with backoff on the codes worth waiting out."""
    url = f"{API_ROOT}/{model}:generateContent?key={api_key}"
    data = json.dumps(body).encode("utf-8")
    backoff = BACKOFF_S if retries else ()

    for attempt in range(len(backoff) + 1):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            spent = attempt >= len(backoff)
            if (e.code not in RETRY_CODES or spent
                    or (e.code == 429 and quota_is_fatal)):
                raise _http_error(e, model) from e
            time.sleep(backoff[attempt])

    raise GeminiError("No response from Gemini.")


def _http_error(e: urllib.error.HTTPError, model: str = "") -> GeminiError:
    try:
        detail = e.read().decode("utf-8", "ignore")[:600]
    except Exception:
        detail = ""
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
                  timeout: int = 45) -> str:
    """A free-form answer. Thinking is off — these prompts don't need it."""
    payload = _post(api_key, model, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }, timeout=timeout, retries=False)

    text, _ = _answer_text(payload)
    if not text:
        raise GeminiError(f"Empty response. Raw: {payload}")
    return text


def generate_json(api_key: str, prompt: str, schema: dict, *,
                  model: str = DEFAULT_MODEL, temperature: float = 0,
                  seed: int = 7, max_output_tokens: int = 48000,
                  timeout: int = 120) -> dict:
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
    }, timeout=timeout, retries=True)

    text, finish = _answer_text(payload)
    try:
        return json.loads(text)
    except Exception as e:
        if finish == "MAX_TOKENS":
            raise GeminiError("The script is too long for one pass — the answer "
                              "was cut off. Build it in two halves.") from e
        raise GeminiError(f"Couldn't parse the response: {e}\n{text[:300]}") from e
