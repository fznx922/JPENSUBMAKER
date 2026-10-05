from jpensubmaker.asr import Segment, Word, join_words, repair_timestamps, _restore_punctuation
from jpensubmaker.cues import Cue, build_cues, collapse_repeats, is_hallucination, tidy_timing
from jpensubmaker import subtitles


def seg(words):
    ws = [Word(t, s, e) for t, s, e in words]
    return Segment(ws, join_words(ws, "ja"), ws[0].start, ws[-1].end)


def test_sentence_and_pause_splits():
    s = seg([("今日", 0.0, 0.4), ("は", 0.4, 0.5), ("いい", 0.5, 0.8), ("天気", 0.8, 1.2), ("ですね。", 1.2, 1.6),
             ("そう", 1.7, 2.0), ("だね", 2.0, 2.3),
             ("行こう", 4.0, 4.5), ("か", 4.5, 4.7)])
    cues = build_cues([s], "ja")
    assert [c.ja for c in cues] == ["今日はいい天気ですね。", "そうだね", "行こうか"]
    assert all(cues[i].end <= cues[i + 1].start for i in range(len(cues) - 1))


def test_long_run_is_cut_at_soft_break():
    a, b, c = "昨日の夜に駅前の本屋で、", "友達と偶然会ったんだけど", "その後みんなで晩ご飯を食べに行った"
    cues = build_cues([seg([(a, 0.0, 2.0), (b, 2.0, 4.0), (c, 4.0, 6.0)])], "ja", max_chars=32)
    assert cues[0].ja == a
    assert "".join(x.ja for x in cues) == a + b + c


def test_hallucinations_dropped():
    assert is_hallucination("ご視聴ありがとうございました")
    assert is_hallucination("♪～")
    assert not is_hallucination("ありがとうございました、先生")
    s = seg([("ご視聴ありがとうございました", 0, 2)])
    assert build_cues([s], "ja") == []


def test_collapse_repeats():
    assert collapse_repeats("ああああああああああ") == "あああ"
    assert collapse_repeats("そうそうそうそうそう") == "そうそうそう"
    assert collapse_repeats("普通の文") == "普通の文"


def test_english_cues_join_with_spaces():
    ws = [Word(" Hello", 0, 0.5), Word(" there.", 0.5, 1.0), Word(" How", 1.2, 1.4), Word(" are", 1.4, 1.6),
          Word(" you?", 1.6, 2.0)]
    cues = build_cues([Segment(ws, "", 0, 2)], "en")
    assert [c.en for c in cues] == ["Hello there.", "How are you?"]


def test_tidy_timing_min_duration_and_no_overlap():
    cues = tidy_timing([Cue(0, 0.1, ja="あ"), Cue(0.5, 3, ja="いい"), Cue(2.9, 4, ja="うう")])
    assert cues[0].end <= cues[1].start
    assert cues[1].end <= cues[2].start


def test_repair_collapsed_timestamps():
    ws = [Word("a", 0, 0.5), Word("b", 0.5, 0.5), Word("c", 0.5, 0.5), Word("d", 0.5, 0.5), Word("e", 3.0, 3.2)]
    out = repair_timestamps(ws, 0, 4)
    starts = [w.start for w in out]
    assert starts == sorted(starts) and len(set(starts)) == 5


def test_restore_punctuation():
    ws = [Word("今日", 0, 1), Word("は", 1, 2), Word("晴れ", 2, 3), Word("です", 3, 4)]
    out = _restore_punctuation(ws, "今日は、晴れです。")
    assert "".join(w.text for w in out) == "今日は、晴れです。"


def test_writers_and_roundtrip(tmp_path):
    cues = [Cue(1.0, 2.5, ja="こんにちは", en="Hello."), Cue(3.0, 5.0, ja="元気？", en="")]
    srt = subtitles.to_srt(cues, "en")
    assert "00:00:01,000 --> 00:00:02,500\nHello." in srt
    assert "元気？" in srt                      # untranslated falls back to Japanese
    bi = subtitles.to_srt(cues, "bilingual")
    assert "Hello.\nこんにちは" in bi
    back = subtitles.parse_srt(bi)
    assert back[0].en == "Hello." and back[0].ja == "こんにちは" and abs(back[0].end - 2.5) < 1e-6
    assert subtitles.to_vtt(cues).startswith("WEBVTT")
    ass = subtitles.to_ass(cues, "bilingual")
    assert "Style: Japanese" in ass and ",Japanese,,0,0,0,,こんにちは" in ass
    files = subtitles.write(cues, tmp_path / "Ep 1", ["srt", "ass", "vtt"], "en")
    assert sorted(f.name for f in files) == ["Ep 1.en.ass", "Ep 1.en.srt", "Ep 1.en.vtt"]
    assert files[0].read_bytes().startswith(b"\xef\xbb\xbf")


def test_wrap_en_balanced():
    lines = subtitles.wrap_en("I told you already, we are not going to the festival tonight.", 42)
    assert len(lines) == 2 and all(len(l) <= 42 for l in lines)
