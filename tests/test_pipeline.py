import pytest

from jpensubmaker import pipeline
from jpensubmaker.asr import Segment, Word
from jpensubmaker.settings import Settings


class FakeEngine:
    can_translate = True

    def __init__(self):
        self.calls = []
        self.closed = 0

    def transcribe(self, audio, task="transcribe", progress=None, cancelled=None, **kw):
        self.calls.append(task)
        if task == "translate":
            ws = [Word(" Good", 0.5, 0.9), Word(" morning.", 0.9, 1.5), Word(" Let's", 2.5, 2.8), Word(" go.", 2.8, 3.2)]
        else:
            ws = [Word("おはよう", 0.5, 1.2), Word("ございます。", 1.2, 1.5), Word("行こう", 2.5, 3.0), Word("か", 3.0, 3.2)]
        if progress:
            progress(1.0, "x")
        return [Segment(ws, "", ws[0].start, ws[-1].end)]

    def close(self):
        self.closed += 1


@pytest.fixture
def engine(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(pipeline.models, "ensure_whisper", lambda m, *a, **k: m)
    monkeypatch.setattr(pipeline.ENGINES, "get", lambda *a, **k: eng)
    monkeypatch.setattr(pipeline.ENGINES, "release", lambda: eng.close())
    return eng


def run(src, s):
    logs = []
    job = pipeline.run_job(pipeline.Job(str(src)), s, log=logs.append, progress=lambda f, m: None,
                           cancelled=lambda: False)
    assert job.status == "done", "\n".join(logs)
    return job


def test_llm_bilingual(video, engine, fake_llm, tmp_path):
    url, _ = fake_llm
    s = Settings(translator="ollama", llm_url=url, llm_model="fake", sub_mode="bilingual", formats=["srt", "ass"])
    job = run(video, s)
    assert engine.calls == ["transcribe"] and engine.closed == 1       # ASR released before the LLM
    srt = (tmp_path / "clip.en-ja.srt").read_text(encoding="utf-8-sig")
    assert "EN(おはようございます。)\nおはようございます。" in srt
    assert (tmp_path / "clip.en-ja.ass").exists()
    assert len(job.cues) == 2


def test_whisper_translate_bilingual(video, engine, tmp_path):
    s = Settings(translator="whisper", sub_mode="bilingual")
    run(video, s)
    assert engine.calls == ["transcribe", "translate"]
    srt = (tmp_path / "clip.en-ja.srt").read_text(encoding="utf-8-sig")
    assert "Good morning.\nおはようございます。" in srt


def test_japanese_only_to_output_dir(video, engine, tmp_path):
    out = tmp_path / "out"
    s = Settings(translator="none", sub_mode="ja", save_next_to_video=False, output_dir=str(out), formats=["vtt"])
    run(video, s)
    assert "行こうか" in (out / "clip.ja.vtt").read_text(encoding="utf-8")


def test_missing_file_fails_cleanly(tmp_path):
    job = pipeline.run_job(pipeline.Job(str(tmp_path / "nope.mkv")), Settings(), log=lambda m: None,
                           progress=lambda f, m: None, cancelled=lambda: False)
    assert job.status == "failed" and "not found" in job.error


def test_unknown_ollama_model_fails_before_transcribing(video, engine, fake_llm):
    url, _ = fake_llm
    s = Settings(translator="ollama", llm_url=url, llm_model="missing:7b")
    job = pipeline.run_job(pipeline.Job(str(video)), s, log=lambda m: None, progress=lambda f, m: None,
                           cancelled=lambda: False)
    assert job.status == "failed" and "does not exist" in job.error
    assert engine.calls == []


def test_model_not_yet_pulled_is_downloaded(video, engine, fake_llm, tmp_path):
    url, srv = fake_llm
    s = Settings(translator="ollama", llm_url=url, llm_model="new-model:12b")
    msgs = []
    job = pipeline.run_job(pipeline.Job(str(video)), s, log=lambda m: None, progress=lambda f, m: msgs.append(m),
                           cancelled=lambda: False)
    assert job.status == "done", job.error
    assert any(p == "/api/pull" for p, _ in srv.requests)
    assert any("Downloading new-model:12b" in m for m in msgs)


def test_translation_failure_keeps_japanese(video, engine, fake_llm, monkeypatch, tmp_path):
    url, _ = fake_llm
    from jpensubmaker.translate import LLMClient, TranslatorError

    def boom(*a, **k):
        raise TranslatorError("server went away")
    monkeypatch.setattr(LLMClient, "translate_lines", boom)
    s = Settings(translator="ollama", llm_url=url, llm_model="fake")
    job = pipeline.run_job(pipeline.Job(str(video)), s, log=lambda m: None, progress=lambda f, m: None,
                           cancelled=lambda: False)
    assert job.status == "failed" and "clip.ja.srt" in job.error
    assert "おはようございます。" in (tmp_path / "clip.ja.srt").read_text(encoding="utf-8-sig")
