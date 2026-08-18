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
import ssl
import sys
import time
import urllib.error
import urllib.request

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"

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
    """Anything the caller should show the user verbatim."""


def _post(api_key: str, model: str, body: dict, timeout: int,
          retries: bool) -> dict:
    """POST one generateContent request; return the parsed envelope."""
    url = f"{API_ROOT}/{model}:generateContent?key={api_key}"
    data = json.dumps(body).encode("utf-8")
    backoff = BACKOFF_S if retries else ()
    last_error: "Exception | None" = None

    for attempt in range(len(backoff) + 1):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_CODES or attempt >= len(backoff):
                raise _http_error(e) from e
            last_error = e
            time.sleep(backoff[attempt])

    raise last_error or GeminiError("No response from Gemini.")


def _http_error(e: urllib.error.HTTPError) -> GeminiError:
    try:
        detail = e.read().decode("utf-8", "ignore")[:600]
    except Exception:
        detail = ""
    # A per-day quota doesn't clear by waiting a few seconds, and the raw JSON
    # tells the user nothing they can act on.
    if e.code == 429 and "PerDay" in detail:
        return GeminiError(
            "Gemini's free daily quota for this key is used up. It resets "
            "tomorrow — or add billing to the Google project. A build costs "
            "two requests.")
    return GeminiError(f"HTTP {e.code}: {detail[:300]}")


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
