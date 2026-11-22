"""Optional LLM judge: Gemini grades each final reply against BusSafar's help docs.

Rubric (1 to 5 each):
    helpfulness   does it resolve what the customer asked, or say clearly what happens next?
    faithfulness  is every claim supported by the help docs or the conversation? (5 = nothing invented)
    tone          polite, clear, empathetic, 2 to 4 short sentences

The judge only sees the help docs, the conversation and the reply. Results are cached with the replies,
so re-scoring a cached run costs no Gemini calls.
"""
from __future__ import annotations

import json

import config

RUBRIC = """You are grading replies from Sahil, the customer support agent of BusSafar (a made-up intercity
bus booking app). Sahil must answer ONLY from the help docs below, hand off to a person when it cannot
answer, refuse anything outside BusSafar, and never reveal other customers' data. Ticket IDs (TKT-…) and
booking details come from BusSafar's own systems (the ticket and booking tools), so a ticket ID or booking
detail in the reply is NOT invented.

HELP DOCS (the only allowed source of policy facts):
<<<
{help}
>>>

CONVERSATION (customer turns and Sahil's earlier replies):
<<<
{conversation}
>>>

SAHIL'S FINAL REPLY TO GRADE:
<<<
{reply}
>>>

Grade the final reply from 1 (very poor) to 5 (excellent) on:
- helpfulness: resolves the customer's request, or clearly says what happens next (a ticket, a person, what to share).
  A correct refusal of an off-topic or unsafe request counts as helpful.
- faithfulness: every factual claim is supported by the help docs or the conversation. Invented numbers, steps,
  timelines or services score 1 or 2.
- tone: polite, clear and empathetic; short.
Reply with JSON only: {{"helpfulness": n, "faithfulness": n, "tone": n, "issue": "one short sentence, or empty"}}"""


class GeminiJudge:
    def __init__(self, model: str | None = None) -> None:
        from google import genai

        key = config.google_api_key()
        if not key:
            raise RuntimeError("GOOGLE_API_KEY is not set, so the judge can't run.")
        self._client = genai.Client(api_key=key)
        self.model = model or config.CHAT_MODEL
        self._help = config.HELP_DOCS_FILE.read_text(encoding="utf-8")

    def grade(self, turns: list[dict]) -> dict:
        from google.genai import types

        convo = "\n".join(f"Customer: {t['user']}\nSahil: {t['text']}" for t in turns[:-1])
        prompt = RUBRIC.format(help=self._help, conversation=convo or "(first message)",
                               reply=f"Customer: {turns[-1]['user']}\nSahil: {turns[-1]['text']}")
        resp = self._client.models.generate_content(
            model=self.model, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=200,
                                               response_mime_type="application/json"))
        return parse_grade(resp.text or "")


def parse_grade(raw: str) -> dict:
    try:
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except (ValueError, TypeError):
        return {"error": f"unreadable judge output: {raw[:120]!r}"}
    out = {}
    for k in ("helpfulness", "faithfulness", "tone"):
        try:
            out[k] = max(1, min(5, int(data.get(k))))
        except (TypeError, ValueError):
            return {"error": f"missing score '{k}'"}
    out["issue"] = str(data.get("issue") or "")[:200]
    out["score"] = round((out["helpfulness"] + out["faithfulness"] + out["tone"]) / 3, 2)
    return out
