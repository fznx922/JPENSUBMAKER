"""User settings: one JSON file in the per-user config folder, loaded into a dataclass.

Unknown keys in the file are ignored and missing keys take their defaults, so an older settings file keeps
working after an upgrade.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "JPENSubMaker"


def default_output_dir() -> str:
    videos = Path.home() / "Videos"
    return str((videos if videos.exists() else Path.home()) / "JPEN Subs")


# ASR models offered in the GUI. Every one is a CTranslate2 model faster-whisper can load by name or Hugging Face id;
# "qwen3-asr" is the separate Qwen engine (optional install). The value is the label shown to the user.
ASR_MODELS: dict[str, str] = {
    "large-v3": "Whisper large-v3 — accurate, can also translate directly (≈4.5 GB fp16)",
    "kotoba-tech/kotoba-whisper-v2.0-faster": "Kotoba-Whisper v2.0 — Japanese-tuned, 6× faster (≈1.5 GB)",
    "large-v3-turbo": "Whisper large-v3-turbo — fast, transcribe only (≈1.6 GB)",
    "large-v2": "Whisper large-v2 — older, often better at direct translation",
    "medium": "Whisper medium — light (≈1.5 GB)",
    "qwen3-asr": "Qwen3-ASR 1.7B + forced aligner — best Japanese accuracy (optional install, ≈5 GB)",
}
if getattr(sys, "frozen", False):          # the .exe ships without PyTorch, which Qwen3-ASR needs
    ASR_MODELS.pop("qwen3-asr")

# Translation backends.
TRANSLATORS: dict[str, str] = {
    "ollama": "Local LLM via Ollama (best quality)",
    "openai": "OpenAI-compatible server (LM Studio, llama.cpp, vLLM, cloud APIs)",
    "whisper": "Whisper built-in translate (no LLM, fastest, lower quality)",
    "none": "No translation (Japanese subtitles only)",
}

# Suggested Ollama models for a 12 GB card. The LLM runs after the ASR model has been unloaded, so it may use most of
# the card. The list is only a suggestion — the model field in the GUI accepts any tag.
LLM_SUGGESTIONS: list[str] = [
    "gemma4:12b-it-qat",
    "qwen3:14b",
    "gemma3:12b",
    "qwen3:8b",
    "gemma4:e4b-it-qat",
    "qwen3:4b",
]

SUB_MODES = {
    "en": "English only",
    "bilingual": "English + Japanese",
    "ja": "Japanese only",
}


@dataclass
class Settings:
    # --- speech recognition
    asr_model: str = "large-v3"
    asr_custom_model: str = ""            # a local CTranslate2 folder or HF id; overrides asr_model when set
    device: str = "cuda"                  # cuda | cpu
    compute_type: str = "float16"         # float16 | int8_float16 | int8 (cpu) — int8_float16 halves VRAM
    beam_size: int = 5
    vad_filter: bool = True
    context_prompt: str = ""              # names / topic; biases the ASR and goes to the translator

    # --- translation
    translator: str = "ollama"
    llm_url: str = "http://127.0.0.1:11434"
    ollama_auto: bool = True              # start Ollama, or download a private copy, when none is running
    llm_model: str = "gemma4:12b-it-qat"
    openai_url: str = "http://127.0.0.1:1234/v1"
    openai_model: str = ""
    openai_api_key: str = ""
    keep_honorifics: bool = True
    glossary: str = ""                    # "日本語 = English" per line
    llm_batch: int = 16                   # lines per request
    llm_temperature: float = 0.3

    # --- output
    sub_mode: str = "en"
    formats: list[str] = field(default_factory=lambda: ["srt"])
    save_next_to_video: bool = True
    output_dir: str = field(default_factory=default_output_dir)
    url_keep_video: bool = True           # links: keep the downloaded video (≤1080p) beside the subtitles
    cookies_browser: str = ""             # links: borrow sign-in cookies from this browser ("" = none)
    cookies_file: str = ""                # links: or from an exported cookies.txt
    max_line_chars: int = 42
    vram_saver: bool = True               # unload the ASR model before translating and the LLM after (12 GB cards)

    # --- live translator
    live_source: str = "loopback"         # loopback | mic
    live_device: str = ""                 # device name; "" = system default
    live_asr_model: str = "large-v3"
    live_compute_type: str = "int8_float16"
    live_translator: str = "whisper"      # whisper | ollama | openai | none
    live_llm_model: str = "qwen3:4b"
    live_max_utterance: float = 7.0
    live_silence_ms: int = 500
    live_beam_size: int = 2
    overlay_font_size: int = 26
    overlay_lines: int = 2
    overlay_bg_opacity: int = 55          # percent
    overlay_show_japanese: bool = False
    overlay_click_through: bool = False
    overlay_geometry: list[int] = field(default_factory=list)   # x, y, w, h

    # --- window
    window_geometry: list[int] = field(default_factory=list)
    first_run_done: bool = False

    @property
    def effective_asr_model(self) -> str:
        return self.asr_custom_model.strip() or self.asr_model

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or cls.path()
        s = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        names = {f.name for f in fields(cls)}
        for k, v in data.items():
            if k in names:
                setattr(s, k, v)
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def copy(self) -> "Settings":
        return Settings(**json.loads(json.dumps(asdict(self))))
