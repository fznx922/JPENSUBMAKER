"""Japanese → English with a local LLM (Ollama) or any OpenAI-compatible chat server.

Cues are translated in numbered batches with the previous lines as context, so pronouns, names and tone carry over
between lines. The model answers in JSON {"1": "...", ...}; anything missing or broken is retried in smaller batches
and finally line by line.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable

import requests

from .asr import Cancelled

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_LINE_RE = re.compile(r"^\s*\"?(\d+)\"?\s*[\.:)\]、：]\s*\"?(.*?)\"?\s*,?\s*$")


class TranslatorError(RuntimeError):
    pass


SYSTEM_PROMPT = """You are an expert Japanese-to-English subtitle translator for anime, dramas, films, variety shows and streams.

Rules:
- Translate every numbered Japanese line into natural, idiomatic, concise English that reads well as a subtitle.
- Keep exactly the same numbering. One English line per Japanese line. Never merge, split, skip or reorder lines.
- The lines come from speech recognition and may contain mistakes or be sentence fragments; translate the most plausible meaning and keep fragments as fragments.
- Use the context lines (already translated) only to understand who is speaking and what is happening. Do not translate them again.
- Write names in standard Hepburn romanization, given name first as spoken.
{honorifics}
- Keep the tone: casual stays casual, polite stays polite, rude stays rude. Interjections like えっ or あの become natural English ("Huh?", "Um,").
- Output only JSON: an object whose keys are the line numbers as strings and whose values are the English lines. No notes, no explanations."""

HONORIFICS_KEEP = "- Keep Japanese honorifics attached to names (-san, -kun, -chan, -sama, -senpai, -sensei)."
HONORIFICS_DROP = "- Drop honorifics or render them naturally in English (Mr./Ms., or nothing)."


@dataclass
class LLMConfig:
    backend: str = "ollama"                    # ollama | openai
    url: str = "http://127.0.0.1:11434"
    model: str = "gemma4:12b-it-qat"
    api_key: str = ""
    temperature: float = 0.3
    keep_honorifics: bool = True
    context: str = ""                          # what the video is, names…
    glossary: str = ""                         # "日本語 = English" per line
    batch: int = 16
    context_lines: int = 6
    num_ctx: int = 8192
    keep_alive: str = "10m"
    timeout: float = 300.0


class LLMClient:
    def __init__(self, cfg: LLMConfig, log: Callable[[str], None] | None = None):
        self.cfg = cfg
        self.log = log or (lambda m: None)
        self._no_think = cfg.backend == "ollama"     # cleared if the server rejects the field
        self._json_mode = True
        self.session = requests.Session()

    # ------------------------------------------------------------------ transport
    def chat(self, system: str, user: str, json_mode: bool = True, max_tokens: int | None = None) -> str:
        if self.cfg.backend == "ollama":
            return self._ollama(system, user, json_mode, max_tokens)
        return self._openai(system, user, json_mode, max_tokens)

    def _ollama(self, system: str, user: str, json_mode: bool, max_tokens: int | None) -> str:
        body: dict = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "keep_alive": self.cfg.keep_alive,
            "options": {"temperature": self.cfg.temperature, "num_ctx": self.cfg.num_ctx},
        }
        if max_tokens:
            body["options"]["num_predict"] = max_tokens
        if json_mode and self._json_mode:
            body["format"] = "json"
        if self._no_think:
            body["think"] = False
        url = self.cfg.url.rstrip("/") + "/api/chat"
        try:
            r = self.session.post(url, json=body, timeout=self.cfg.timeout)
        except requests.RequestException as e:
            raise TranslatorError(f"Cannot reach Ollama at {self.cfg.url} — is it running? ({e.__class__.__name__})") from e
        if r.status_code == 400 and "think" in r.text.lower() and self._no_think:
            self._no_think = False
            return self._ollama(system, user, json_mode, max_tokens)
        if r.status_code == 404 and "not found" in r.text.lower():
            raise TranslatorError(f"Ollama does not have the model '{self.cfg.model}'. Run: ollama pull {self.cfg.model}")
        if r.status_code >= 400:
            raise TranslatorError(f"Ollama error {r.status_code}: {r.text[:300]}")
        data = r.json()
        return (data.get("message") or {}).get("content", "")

    def _openai(self, system: str, user: str, json_mode: bool, max_tokens: int | None) -> str:
        body: dict = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.cfg.temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_mode and self._json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        url = self.cfg.url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        try:
            r = self.session.post(url, json=body, headers=headers, timeout=self.cfg.timeout)
        except requests.RequestException as e:
            raise TranslatorError(f"Cannot reach the server at {self.cfg.url} ({e.__class__.__name__})") from e
        if r.status_code == 400 and "response_format" in r.text and self._json_mode:
            self._json_mode = False                       # server without JSON mode: rely on the prompt
            return self._openai(system, user, json_mode, max_tokens)
        if r.status_code >= 400:
            raise TranslatorError(f"Server error {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as e:
            raise TranslatorError(f"Unexpected response: {r.text[:300]}") from e

    def list_models(self) -> list[str]:
        try:
            if self.cfg.backend == "ollama":
                r = self.session.get(self.cfg.url.rstrip("/") + "/api/tags", timeout=5)
                r.raise_for_status()
                return sorted(m["name"] for m in r.json().get("models", []))
            headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
            r = self.session.get(self.cfg.url.rstrip("/") + "/models", headers=headers, timeout=5)
            r.raise_for_status()
            return sorted(m["id"] for m in r.json().get("data", []))
        except (requests.RequestException, ValueError, KeyError) as e:
            raise TranslatorError(f"Cannot list models at {self.cfg.url}: {e}") from e

    def unload(self) -> None:
        """Free the model's VRAM now (Ollama only) so the speech model can load for the next file."""
        if self.cfg.backend != "ollama":
            return
        try:
            self.session.post(self.cfg.url.rstrip("/") + "/api/generate",
                              json={"model": self.cfg.model, "keep_alive": 0}, timeout=15)
        except requests.RequestException:
            pass

    # ------------------------------------------------------------------ translation
    def system_prompt(self) -> str:
        s = SYSTEM_PROMPT.format(honorifics=HONORIFICS_KEEP if self.cfg.keep_honorifics else HONORIFICS_DROP)
        if self.cfg.context.strip():
            s += f"\n\nAbout this video: {self.cfg.context.strip()}"
        gl = [ln.strip() for ln in self.cfg.glossary.splitlines() if ln.strip()]
        if gl:
            s += "\n\nGlossary (always use these renderings):\n" + "\n".join(f"- {g}" for g in gl)
        return s

    def translate_batch(self, lines: list[str], context: list[tuple[str, str]]) -> dict[int, str]:
        user = ""
        if context:
            user += "Context (previous lines, already translated — do not output these):\n"
            user += "\n".join(f"{ja} → {en}" for ja, en in context) + "\n\n"
        user += "Translate these lines:\n"
        user += "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
        user += '\n\nAnswer with JSON only, e.g. {"1": "...", "2": "..."}.'
        raw = self.chat(self.system_prompt(), user, json_mode=True, max_tokens=max(256, 80 * len(lines)))
        return parse_numbered(raw, len(lines))

    def translate_lines(self, lines: list[str], progress: Callable[[float, str], None] | None = None,
                        cancelled: Callable[[], bool] | None = None) -> list[str]:
        out: list[str | None] = [None] * len(lines)
        b = max(1, self.cfg.batch)
        i = 0
        while i < len(lines):
            if cancelled and cancelled():
                raise Cancelled()
            chunk = lines[i:i + b]
            ctx = [(lines[k], out[k] or "") for k in range(max(0, i - self.cfg.context_lines), i) if out[k]]
            got = self._with_retries(chunk, ctx)
            for k, t in got.items():
                out[i + k] = t
            i += len(chunk)
            if progress:
                progress(i / len(lines), out[i - 1] or "")
        return [o if o is not None else "" for o in out]

    def _with_retries(self, chunk: list[str], ctx: list[tuple[str, str]]) -> dict[int, str]:
        result: dict[int, str] = {}
        for attempt in range(2):
            try:
                got = self.translate_batch(chunk, ctx)
            except TranslatorError:
                if attempt == 1:
                    raise
                time.sleep(1.0)
                continue
            result.update({k: v for k, v in got.items() if v.strip()})
            if len(result) == len(chunk):
                return result
        missing = [k for k in range(len(chunk)) if k not in result]
        if len(chunk) > 1:
            self.log(f"  {len(missing)} line(s) came back missing — retrying them one by one")
        for k in missing:
            try:
                got = self.translate_batch([chunk[k]], ctx)
                if got.get(0, "").strip():
                    result[k] = got[0]
            except TranslatorError as e:
                self.log(f"  line failed: {e}")
        return result

    def translate_live(self, line: str, context: list[tuple[str, str]]) -> str:
        """One utterance for the live overlay: short output, minimal latency."""
        got = self.translate_batch([line], context[-3:])
        return got.get(0, "")


def parse_numbered(raw: str, n: int) -> dict[int, str]:
    """{"1": "a", "2": "b"} or "1. a\\n2. b" → {0: "a", 1: "b"} (0-based, only keys in range)."""
    raw = _THINK_RE.sub("", raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    out: dict[int, str] = {}
    obj = None
    try:
        obj = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
            except ValueError:
                obj = None
    if isinstance(obj, dict):
        # tolerate {"translations": {...}} or {"lines": [...]}
        if len(obj) == 1 and not str(next(iter(obj))).strip().isdigit() \
                and isinstance(next(iter(obj.values())), (dict, list)):
            obj = next(iter(obj.values()))
    if isinstance(obj, list):
        obj = {str(i + 1): v for i, v in enumerate(obj)}
    if isinstance(obj, dict):
        for k, v in obj.items():
            m = re.match(r"\d+", str(k).strip())
            if not m:
                continue
            idx = int(m.group(0)) - 1
            if isinstance(v, dict):
                v = v.get("en") or v.get("english") or v.get("translation") or next(iter(v.values()), "")
            if 0 <= idx < n and isinstance(v, str):
                out[idx] = clean_line(v)
        if out:
            return out
    for line in raw.splitlines():
        m = _LINE_RE.match(line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < n:
                out[idx] = clean_line(m.group(2))
    if not out and n == 1 and raw and "\n" not in raw.strip() and not raw.strip().startswith("{"):
        out[0] = clean_line(raw)
    return out


def clean_line(s: str) -> str:
    s = s.strip().strip('"').strip()
    s = re.sub(r"\s+", " ", s)
    return s
