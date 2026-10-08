#!/usr/bin/env python3
"""Offline checks for `src/gemini.py`'s model chain and its error sentences.

Why this file exists: the model choice has now broken teammates TWICE, in
opposite directions, and neither failure was visible from the machine that
shipped it.

    v1.2.19 and earlier   a pinned model      -> 404 once Google stopped
                                                 serving it to new keys
    v1.2.19 .. v1.3.0     a floating alias    -> 429 on free keys, because the
                                                 alias followed Google onto a
                                                 model with no free tier yet

Both are per-key: a paid key sails through both. So the chain that replaced them
is tested here with a fake transport instead of a real one — no network, no key,
and every branch reachable, including the ones only a free-tier key would hit.

Run:  ./venv/bin/python scripts/test_gemini.py
"""
from __future__ import annotations

import io
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import gemini  # noqa: E402

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example/x", code, "err", {}, io.BytesIO(body.encode("utf-8")))


QUOTA_BODY_PER_MINUTE = """{
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and billing details.",
    "status": "RESOURCE_EXHAUSTED",
    "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
      "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                      "quotaValue": "0"}]}]
  }
}"""

QUOTA_BODY_PER_DAY = QUOTA_BODY_PER_MINUTE.replace("PerMinute", "PerDay")


class FakeTransport:
    """Stands in for urlopen. `plan` maps a model name to a code — or to a list
    of codes, used up one request at a time (then 200): `[503]` is a model
    that is busy once and then answers."""

    def __init__(self, plan: dict, body: str = ""):
        self.plan = plan
        self.body = body
        self.calls: list[str] = []
        self.requests: list = []
        self.slept = 0.0

    def urlopen(self, req, timeout=None, context=None):
        model = req.full_url.split("/models/")[1].split(":")[0]
        self.calls.append(model)
        self.requests.append(req)
        outcome = self.plan.get(model, 200)
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if outcome else 200
        if outcome != 200:
            raise http_error(outcome, self.body)
        return _Resp('{"candidates":[{"content":{"parts":[{"text":'
                     '"{\\"ok\\":true}"}]},"finishReason":"STOP"}]}')

    def sleep(self, s):
        self.slept += s


class _Resp:
    def __init__(self, text):
        self._t = text.encode("utf-8")

    def read(self):
        return self._t

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run(plan: dict, body: str = "", model: str = None, key: str = "KEY", **kw):
    """Call generate_json through a fake transport; return (result, transport)."""
    fake = FakeTransport(plan, body)
    real_open, real_sleep = gemini.urllib.request.urlopen, gemini.time.sleep
    gemini.urllib.request.urlopen = fake.urlopen
    gemini.time.sleep = fake.sleep
    gemini._WORKING_MODEL = None
    try:
        out = gemini.generate_json(key, "p", SCHEMA,
                                   model=model or gemini.DEFAULT_MODEL, **kw)
        return out, fake, None
    except Exception as e:
        return None, fake, e
    finally:
        gemini.urllib.request.urlopen = real_open
        gemini.time.sleep = real_sleep
        gemini._WORKING_MODEL = None


def main():
    first, second, third = gemini.MODEL_CHAIN
    print("chain: %s" % (gemini.MODEL_CHAIN,))

    print("\nthe chain is a chain")
    check("default is the head of the chain", gemini.DEFAULT_MODEL == first)
    check("no floating alias in the chain",
          not any(m.endswith("-latest") for m in gemini.MODEL_CHAIN),
          "a '-latest' alias follows Google onto models with no free tier")
    check("at least two fallbacks", len(gemini.MODEL_CHAIN) >= 3)

    print("\nthe first model answers -> nothing else is tried")
    out, t, err = run({})
    check("returned the answer", out == {"ok": True}, str(err))
    check("one request only", t.calls == [first], str(t.calls))

    print("\n429 on the first model -> the next one is tried, with NO backoff")
    out, t, err = run({first: 429}, QUOTA_BODY_PER_MINUTE)
    check("fell through to the next model", t.calls == [first, second], str(t.calls))
    check("still got an answer", out == {"ok": True}, str(err))
    check("did not sleep on a model it was leaving", t.slept == 0,
          "slept %ss" % t.slept)

    print("\n404 (retired) walks the whole chain")
    out, t, err = run({first: 404, second: 404}, '{"error":"not found"}')
    check("tried all three", t.calls == [first, second, third], str(t.calls))
    check("still got an answer", out == {"ok": True}, str(err))

    print("\n503 'high demand' on the first model -> one short retry, then the next")
    BUSY = ('{"error": {"code": 503, "message": "This model is currently '
            'experiencing high demand. Spikes in demand are usually temporary. '
            'Please try again later.", "status": "UNAVAILABLE"}}')
    out, t, err = run({first: 503}, BUSY)
    check("fell through to the next model", t.calls == [first, first, second],
          str(t.calls))
    check("still got an answer", out == {"ok": True}, str(err))
    check("waited once, briefly", t.slept == gemini.BACKOFF_S[0],
          "slept %ss" % t.slept)

    print("\n503 everywhere -> the last model gets the full backoff, then a sentence")
    out, t, err = run({first: 503, second: 503, third: 503}, BUSY)
    check("every model was tried", [m for m in gemini.MODEL_CHAIN if m in t.calls]
          == list(gemini.MODEL_CHAIN), str(t.calls))
    check("bounded wait", t.slept == 2 * gemini.BACKOFF_S[0] + sum(gemini.BACKOFF_S),
          "slept %ss" % t.slept)
    check("503 carries its code", getattr(err, "code", 0) == 503, repr(err))
    check("says it's Google's side and temporary",
          "overloaded" in str(err) and "few minutes" in str(err), str(err))
    check("fits the Animator's status line whole",
          len("Gemini failed — " + str(err)) <= 160, str(len(str(err))))
    check("is not raw JSON", "UNAVAILABLE" not in str(err), str(err))

    print("\na 503 on Camera Prompts (no retries) also falls through, without waiting")
    fake = FakeTransport({first: 503}, BUSY)
    real_open, real_sleep = gemini.urllib.request.urlopen, gemini.time.sleep
    gemini.urllib.request.urlopen, gemini.time.sleep = fake.urlopen, fake.sleep
    gemini._WORKING_MODEL = None
    try:
        text = gemini.generate_text("KEY", "p")
    finally:
        gemini.urllib.request.urlopen, gemini.time.sleep = real_open, real_sleep
        gemini._WORKING_MODEL = None
    check("answered from the next model", fake.calls == [first, second] and text,
          str(fake.calls))
    check("no sleep", fake.slept == 0, "slept %ss" % fake.slept)

    print("\n400 is about the request, not the model -> fail at once")
    out, t, err = run({first: 400, second: 400, third: 400}, "bad argument")
    check("only the first model was asked", t.calls == [first], str(t.calls))
    check("raised", isinstance(err, gemini.GeminiError), str(err))

    print("\nthe last model still gets its backoff")
    out, t, err = run({first: 429, second: 429, third: 429}, QUOTA_BODY_PER_MINUTE)
    check("every model was tried", t.calls[:3] == list(gemini.MODEL_CHAIN), str(t.calls))
    check("backed off on the last one only", t.slept == sum(gemini.BACKOFF_S),
          "slept %ss" % t.slept)
    check("failed in the end", isinstance(err, gemini.GeminiError))
    check("429 carries its code", getattr(err, "code", 0) == 429)

    print("\nthe 429 sentence tells the user what to do")
    msg = str(err)
    check("mentions the per-minute possibility", "wait a minute" in msg, msg)
    check("mentions the no-free-quota possibility", "no free quota" in msg, msg)
    check("names the escape hatch", "GEMINI_MODEL" in msg, msg)
    check("is not raw JSON", "RESOURCE_EXHAUSTED" not in msg, msg)

    print("\na per-DAY quota keeps its own, different sentence")
    _, _, err = run({first: 429, second: 429, third: 429}, QUOTA_BODY_PER_DAY)
    check("says the day's quota is used up", "resets" in str(err), str(err))

    print("\na pinned model is a pin, not a suggestion")
    out, t, err = run({}, model=second)
    check("only the pinned model is tried", t.calls == [second], str(t.calls))
    _, t, err = run({second: 429}, QUOTA_BODY_PER_MINUTE, model=second)
    check("a pin never switches model", set(t.calls) == {second}, str(t.calls))
    check("a pin still gets its backoff", t.slept == sum(gemini.BACKOFF_S),
          "slept %ss" % t.slept)

    print("\nGEMINI_MODEL in the environment overrides the chain")
    os.environ["GEMINI_MODEL"] = third
    try:
        check("override wins", gemini.models_to_try(gemini.DEFAULT_MODEL) == (third,),
              str(gemini.models_to_try(gemini.DEFAULT_MODEL)))
    finally:
        del os.environ["GEMINI_MODEL"]

    print("\na model that worked is tried first next time")
    gemini._WORKING_MODEL = third
    try:
        check("sticky model leads", gemini.models_to_try(gemini.DEFAULT_MODEL)[0] == third)
        check("the rest still follow",
              set(gemini.models_to_try(gemini.DEFAULT_MODEL)) == set(gemini.MODEL_CHAIN))
    finally:
        gemini._WORKING_MODEL = None

    print("\nthe key travels in the header, never the URL")
    out, t, err = run({})
    req = t.requests[0]
    check("no key in the URL", "key=" not in req.full_url, req.full_url)
    check("x-goog-api-key carries it", req.get_header("X-goog-api-key") == "KEY",
          str(req.header_items()))

    print("\na key Google refuses is a sentence, not JSON, and not a chain walk")
    BAD_KEY = ('{"error": {"code": 400, "message": "API key not valid. Please pass '
               'a valid API key.", "status": "INVALID_ARGUMENT", "details": '
               '[{"reason": "API_KEY_INVALID"}]}}')
    _, t, err = run({first: 400}, BAD_KEY)
    msg = str(err)
    check("only the first model was asked", t.calls == [first], str(t.calls))
    check("points at Settings", "Settings" in msg, msg)
    check("is not raw JSON", "INVALID_ARGUMENT" not in msg, msg)
    AQ = "AQ." + "Ab8RN6" + "x" * 44
    _, t, err = run({first: 401}, '{"error": {"code": 401, "status": '
                    '"UNAUTHENTICATED"}}', key=AQ + AQ)
    check("an AQ. key's 401 gets the same treatment", "Settings" in str(err), str(err))
    check("two keys run together are named as such",
          "two keys run together" in str(err), str(err))

    print("\na pasted key comes out as one key")
    AIZA = "AIza" + "Sy" + "b" * 33
    check("AIza is 39 characters", len(AIZA) == 39)
    check("whitespace and quotes go", gemini.clean_key(' "%s"\n' % AQ) == AQ)
    check("an .env line is reduced to its key",
          gemini.clean_key("GEMINI_API_KEY=" + AQ) == AQ)
    check("old + new keeps the new one",
          gemini.clean_key(AIZA + AQ, previous=AIZA) == AQ)
    check("new + old keeps the new one too",
          gemini.clean_key(AQ + AIZA, previous=AIZA) == AQ)
    AQ2 = "AQ." + "Zz" * 25
    check("two AQ. keys glued together are two",
          gemini.key_tokens(AQ + AQ2) == [AQ, AQ2], str(gemini.key_tokens(AQ + AQ2)))
    check("saving the saved key again keeps it",
          gemini.clean_key(AQ, previous=AQ) == AQ)
    check("an unknown shape is saved as typed",
          gemini.clean_key("  sk-something-else ") == "sk-something-else")
    check("the report can tell the cases apart",
          (gemini.key_shape(AQ), gemini.key_shape(AIZA),
           gemini.key_shape(AIZA + AQ)) ==
          ("AQ. auth key", "AIza key", "2 keys run together"))

    print("\non_event hears the attempts, the waits and the fallbacks, in order")
    seen: list = []
    out, t, err = run({first: [503]}, BUSY, on_event=lambda k, i: seen.append((k, i)))
    check("503 then an answer: still one model", t.calls == [first, first], str(t.calls))
    check("...attempt, backoff, attempt",
          seen == [("attempt", {"model": first, "n": 1}),
                   ("backoff", {"model": first, "seconds": gemini.BACKOFF_S[0],
                                "code": 503}),
                   ("attempt", {"model": first, "n": 2})], str(seen))
    check("...and the answer came back", out == {"ok": True}, str(err))
    check("...after the same sleep as without a listener",
          t.slept == gemini.BACKOFF_S[0], "slept %ss" % t.slept)

    seen = []
    out, t, err = run({first: 404}, '{"error":"not found"}',
                      on_event=lambda k, i: seen.append((k, i)))
    check("404: attempt, fallback, attempt on the next model",
          seen == [("attempt", {"model": first, "n": 1}),
                   ("fallback", {"from": first, "to": second, "code": 404}),
                   ("attempt", {"model": second, "n": 1})], str(seen))
    check("...and the answer came back", out == {"ok": True}, str(err))

    seen = []
    _, t, err = run({first: 503, second: 503, third: 503}, BUSY,
                    on_event=lambda k, i: seen.append((k, i)))
    kinds = [k for k, _ in seen]
    check("503 everywhere: one event per request, wait and hand-over",
          kinds.count("attempt") == len(t.calls)
          and kinds.count("fallback") == len(gemini.MODEL_CHAIN) - 1
          and sum(i["seconds"] for k, i in seen if k == "backoff") == t.slept,
          str(seen))
    check("...and the failure is unchanged", getattr(err, "code", 0) == 503, repr(err))

    print("\nno listener, or a broken one, changes nothing")
    for plan, body in (({}, ""), ({first: [503]}, BUSY), ({first: 404}, "not found"),
                       ({first: 429, second: 429, third: 429}, QUOTA_BODY_PER_MINUTE),
                       ({first: 400}, "bad argument")):
        plain = run({k: (list(v) if isinstance(v, list) else v) for k, v in plan.items()},
                    body)
        heard = run({k: (list(v) if isinstance(v, list) else v) for k, v in plan.items()},
                    body, on_event=lambda k, i: None)

        def boom(k, i):
            raise RuntimeError("listener bug")
        broken = run({k: (list(v) if isinstance(v, list) else v) for k, v in plan.items()},
                     body, on_event=boom)
        same = all((r[0], r[1].calls, r[1].slept, type(r[2]), str(r[2]))
                   == (plain[0], plain[1].calls, plain[1].slept, type(plain[2]),
                       str(plain[2])) for r in (heard, broken))
        check("same calls, sleeps and outcome for %s" % (plan or "a clean answer"),
              same, "plain=%s heard=%s broken=%s" % (
                  (plain[1].calls, plain[2]), (heard[1].calls, heard[2]),
                  (broken[1].calls, broken[2])))

    seen = []
    fake = FakeTransport({first: 503}, BUSY)
    real_open, real_sleep = gemini.urllib.request.urlopen, gemini.time.sleep
    gemini.urllib.request.urlopen, gemini.time.sleep = fake.urlopen, fake.sleep
    gemini._WORKING_MODEL = None
    try:
        gemini.generate_text("KEY", "p", on_event=lambda k, i: seen.append(k))
    finally:
        gemini.urllib.request.urlopen, gemini.time.sleep = real_open, real_sleep
        gemini._WORKING_MODEL = None
    check("generate_text reports too (no retries, so no backoff)",
          seen == ["attempt", "fallback", "attempt"], str(seen))

    print("\nthe captioner, which has its own copy of the transport")
    cap = (os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tools", "captions-de", "caption.py"))
    with open(cap, encoding="utf-8") as fh:
        csrc = fh.read()
    check("no floating alias left in the captioner",
          "gemini-flash-latest" not in csrc,
          "a refusal there does not fail the job — it silently degrades the SRT")
    check("it walks the same chain",
          all(m in csrc for m in gemini.MODEL_CHAIN),
          "chain: %s" % (gemini.MODEL_CHAIN,))
    check("...and falls through on 404/429",
          "404, 429" in csrc or "(404, 429)" in csrc)
    check("...and sends the key in the header",
          "x-goog-api-key" in csrc and "?key=" not in csrc,
          "an AQ. key is not accepted as a ?key= parameter")

    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("ALL GEMINI CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
