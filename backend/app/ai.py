"""Gemini-powered tutor: per-question explanations and post-mock coaching.

Calls the Gemini REST API directly (streamGenerateContent over SSE) so the key stays
on the server and no extra SDK is needed. Figure-based questions are sent with
their images inlined, so the model can reason about the diagram itself.
"""
from __future__ import annotations

import base64
import json
import re
from collections import OrderedDict
from typing import AsyncIterator

import httpx

from . import config
from .catalog import EXAMS, SUBJECTS, chapter_label, stage_name

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
IMAGE_RE = re.compile(r"\[IMAGE:\s*([^\]\s]+)\s*\]|<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", re.I)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
MAX_IMAGES = 6

TUTOR_PROMPT = """You are an expert tutor for Indian SSC exams (CGL, CHSL, CPO, GD, MTS, Selection Post, Stenographer).
Explain like a top coaching teacher: short numbered steps, the fastest exam shortcut where one exists, and why each tempting wrong option is wrong.
Use Markdown (bold, lists, small tables). Keep the first explanation under about 250 words unless the student asks for more depth.
Write maths in plain text with Unicode symbols (×, ÷, √, ², ⅓, →). Never use LaTeX: no $, \text, \frac or \times. The app cannot render it.
The official answer key is authoritative. If the official solution looks wrong or incomplete, say so politely and explain your reasoning.
Reply in English. Switch to Hindi or Hinglish only when the student's own message is written that way."""

COACH_PROMPT = """You are a performance coach for Indian SSC exam aspirants.
You analyse one mock test result and give specific, actionable advice grounded in the numbers you are given.
Mention SSC negative marking where it matters. Use Markdown with short headings and bullets. Stay under about 450 words."""


class AIError(RuntimeError):
    def __init__(self, message: str, retriable: bool = False):
        super().__init__(message)
        self.retriable = retriable


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


_image_cache: OrderedDict[str, dict] = OrderedDict()


async def _inline_image(client: httpx.AsyncClient, url: str) -> dict | None:
    if url in _image_cache:
        return _image_cache[url]
    try:
        resp = await client.get(url, headers={"User-Agent": BROWSER_UA}, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError:
        return None
    mime = resp.headers.get("content-type", "image/png").split(";")[0]
    if not mime.startswith("image/"):
        return None
    part = {"inlineData": {"mimeType": mime, "data": base64.b64encode(resp.content).decode()}}
    _image_cache[url] = part
    while len(_image_cache) > 64:
        _image_cache.popitem(last=False)
    return part


def _label_images(text: str, owner: str, figures: list[tuple[str, str]]) -> str:
    """Swap image markers for readable labels ('[Option 2 figure]') and collect (label, url)."""
    def repl(m: re.Match) -> str:
        url = m.group(1) or m.group(2)
        if url.startswith("data:"):
            return ""
        label = f"[{owner} figure]" if owner.startswith("Option") else f"[{owner} figure {len(figures) + 1}]"
        figures.append((label, url))
        return label
    return IMAGE_RE.sub(repl, text or "")


async def _context_parts(client: httpx.AsyncClient, q: dict, chosen: int | None) -> list[dict]:
    figures: list[tuple[str, str]] = []
    question = _label_images(q["question"], "Question", figures)
    options = [_label_images(o, f"Option {i + 1}", figures) for i, o in enumerate(q["options"])]
    solution = _label_images(q.get("solution") or "", "Solution", [])  # solution figures are not sent
    lines = [
        f"Exam: {EXAMS.get(q['exam'], q['exam'])} {stage_name(q['exam'], q['stage'])} ({q.get('year') or 'year n/a'})",
        f"Subject: {SUBJECTS.get(q['subject'], q['subject'])} > {chapter_label(q.get('chapter'))}",
        "", "Question:", question, "", "Options:",
        *[f"{i + 1}. {o}" for i, o in enumerate(options)],
        "", f"Official answer: option {q['answer'] + 1} ({options[q['answer']]})",
    ]
    if chosen is None:
        lines.append("The student has not answered yet.")
    elif chosen == q["answer"]:
        lines.append(f"The student chose option {chosen + 1}, which is correct.")
    else:
        lines.append(f"The student chose option {chosen + 1} ({options[chosen]}), which is wrong.")
    if solution:
        lines += ["", "Official solution (may be terse or cut off):", solution[:4000]]
    if q.get("related"):
        # Retrieved by the RAG index: lets the tutor point at real PYQs of the same type.
        lines += ["", "Related previous-year questions from the question bank (cite them as practice if useful):"]
        lines += [f"- [{r['source']}] {_plain(r['question'])[:280]} (answer: {_plain(r['answer_text'])[:80]})"
                  for r in q["related"]]
    parts: list[dict] = [{"text": "\n".join(lines)}]
    for label, url in figures[:MAX_IMAGES]:
        image = await _inline_image(client, url)
        if image:
            parts += [{"text": label}, image]
    return parts


async def _stream(client: httpx.AsyncClient, system: str, contents: list[dict]) -> AsyncIterator[str]:
    """Stream from the first configured model that answers."""
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192},
    }
    failures = []
    for model in config.GEMINI_MODELS:
        started = False
        try:
            async for text in _stream_model(client, model, body):
                started = True
                yield text
            if started:
                return
            failures.append(f"{model}: empty response")  # seen when a model is overloaded
        except AIError as e:
            if started or not e.retriable:
                raise
            failures.append(f"{model}: {e}")
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if started:
                raise
            failures.append(f"{model}: {e.__class__.__name__}")
    raise AIError("Gemini is busy right now, please retry in a minute. (" + "; ".join(failures)[:400] + ")")


async def _stream_model(client: httpx.AsyncClient, model: str, body: dict) -> AsyncIterator[str]:
    url = f"{API_ROOT}/{model}:streamGenerateContent?alt=sse"
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}
    timeout = httpx.Timeout(45, connect=10)
    async with client.stream("POST", url, json=body, headers=headers, timeout=timeout) as resp:
        if resp.status_code != 200:
            detail = (await resp.aread()).decode(errors="replace")
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            retriable = resp.status_code in (404, 429, 500, 502, 503, 504)
            raise AIError(f"HTTP {resp.status_code}: {detail[:200]}", retriable=retriable)
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            chunk = json.loads(line[5:])
            for cand in chunk.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    if part.get("text") and not part.get("thought"):
                        yield part["text"]


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", IMAGE_RE.sub("[figure]", text or "")).strip()


async def generate_json(system: str, prompt: str, schema: dict, temperature: float = 0.7) -> object:
    """One structured (JSON-schema constrained) completion, with the same model fallback."""
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 16384,
                             "responseMimeType": "application/json", "responseSchema": schema},
    }
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}
    failures = []
    async with httpx.AsyncClient() as client:
        for model in config.GEMINI_MODELS:
            try:
                resp = await client.post(f"{API_ROOT}/{model}:generateContent", json=body, headers=headers,
                                         timeout=httpx.Timeout(120, connect=10))
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                failures.append(f"{model}: {e.__class__.__name__}")
                continue
            if resp.status_code != 200:
                if resp.status_code in (404, 429, 500, 502, 503, 504):
                    failures.append(f"{model}: HTTP {resp.status_code}")
                    continue
                raise AIError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            parts = (resp.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            try:
                return json.loads(text)
            except ValueError:
                failures.append(f"{model}: invalid JSON")
    raise AIError("Gemini is busy right now, please retry in a minute. (" + "; ".join(failures)[:400] + ")")


ASK_PROMPT = """You are an expert mentor for Indian SSC exams answering a student's doubt.
You are given numbered previous-year questions (PYQs) retrieved from real SSC papers, each with its official answer and a solution excerpt.
Ground your answer in them: explain the concept and the fastest exam method, and cite PYQs inline as [1], [2] when you use them.
If the PYQs do not cover the doubt, say so briefly and answer from general knowledge.
Finish with a line "Practise next:" naming the 2-3 most useful PYQ numbers.
Use Markdown and plain Unicode maths (×, ÷, √, ²). Never use LaTeX ($, \text, \frac): the app cannot render it. Stay under about 300 words.
Reply in English unless the student writes in Hindi or Hinglish."""


async def ask(query: str, sources: list[dict], history: list[dict]) -> AsyncIterator[str]:
    """Retrieval-augmented answer to a free-form doubt, citing the retrieved PYQs."""
    blocks = []
    for s in sources:
        opts = "; ".join(f"{i + 1}) {_plain(o)[:80]}" for i, o in enumerate(s["options"]))
        blocks.append(f"[{s['n']}] {s['source']}\nQ: {_plain(s['question'])[:500]}\nOptions: {opts}\n"
                      f"Answer: option {s['answer'] + 1}\nSolution: {_plain(s.get('solution') or '')[:700]}")
    context = "Retrieved PYQs:\n\n" + "\n\n".join(blocks)
    contents = [{"role": "user", "parts": [{"text": context}]},
                {"role": "model", "parts": [{"text": "I have read the PYQs. What is your doubt?"}]}]
    for m in history[-8:]:
        contents.append({"role": "model" if m["role"] == "model" else "user", "parts": [{"text": m["text"]}]})
    contents.append({"role": "user", "parts": [{"text": query}]})
    async with httpx.AsyncClient() as client:
        async for text in _stream(client, ASK_PROMPT, contents):
            yield text


async def explain(q: dict, chosen: int | None, messages: list[dict]) -> AsyncIterator[str]:
    """Stream a tutor reply. `messages` is the chat so far, ending with the student's turn."""
    async with httpx.AsyncClient() as client:
        contents = []
        for i, m in enumerate(messages):
            role = "model" if m["role"] == "model" else "user"
            parts = [{"text": m["text"]}]
            if i == 0:
                parts = await _context_parts(client, q, chosen) + [{"text": "Student: " + m["text"]}]
            contents.append({"role": role, "parts": parts})
        async for text in _stream(client, TUTOR_PROMPT, contents):
            yield text


def _mock_report(title: str, minutes: int, elapsed_sec: int | None, result: dict, chapters: list[dict]) -> str:
    acc = result.get("accuracy")
    lines = [
        f"Mock: {title}",
        f"Score: {result['score']} / {result['max_score']}",
        f"Questions: {result['total']} | attempted {result['attempted']} | correct {result['correct']} | "
        f"wrong {result['wrong']} | skipped {result['skipped']} | accuracy {round(acc * 100) if acc is not None else 'n/a'}%",
        f"Time allowed: {minutes} min | time used: {round((elapsed_sec or 0) / 60)} min",
        "", "Sections (attempted/total, correct, wrong, score/max, minutes spent):",
    ]
    for s in result["sections"]:
        lines.append(f"- {s['part']} / {s['name']}: {s['attempted']}/{s['total']}, {s['correct']} correct, "
                     f"{s['wrong']} wrong, {s['score']}/{s['max_score']}, {round(s['ms'] / 60000, 1)} min")
    lines += ["", "Chapters (correct / wrong / skipped, avg seconds per question):"]
    for c in chapters:
        lines.append(f"- {SUBJECTS.get(c['subject'], c['subject'])} > {chapter_label(c['chapter'])}: "
                     f"{c['correct']} / {c['wrong']} / {c['skipped']}, {c['avg_sec']}s")
    lines += ["", "Give: 1) a diagnosis of where marks were lost (negative marking, accuracy vs attempts, time split); "
              "2) the three chapters to fix first with concrete practice targets; "
              "3) a strategy for the next mock (section order, time per section, when to skip); "
              "4) a 7-day study plan."]
    return "\n".join(lines)


async def review_mock(title: str, minutes: int, elapsed_sec: int | None, result: dict,
                      chapters: list[dict]) -> AsyncIterator[str]:
    contents = [{"role": "user", "parts": [{"text": _mock_report(title, minutes, elapsed_sec, result, chapters)}]}]
    async with httpx.AsyncClient() as client:
        async for text in _stream(client, COACH_PROMPT, contents):
            yield text
