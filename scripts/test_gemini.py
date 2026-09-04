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
    """Stands in for urlopen. `plan` maps a model name to a code or a payload."""

    def __init__(self, plan: dict, body: str = ""):
        self.plan = plan
        self.body = body
        self.calls: list[str] = []
        self.slept = 0.0

    def urlopen(self, req, timeout=None, context=None):
        model = req.full_url.split("/models/")[1].split(":")[0]
        self.calls.append(model)
        outcome = self.plan.get(model, 200)
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


def run(plan: dict, body: str = "", model: str = None, **kw):
    """Call generate_json through a fake transport; return (result, transport)."""
    fake = FakeTransport(plan, body)
    real_open, real_sleep = gemini.urllib.request.urlopen, gemini.time.sleep
    gemini.urllib.request.urlopen = fake.urlopen
    gemini.time.sleep = fake.sleep
    gemini._WORKING_MODEL = None
    try:
        out = gemini.generate_json("KEY", "p", SCHEMA,
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

    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("ALL GEMINI CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
