from jpensubmaker.translate import LLMClient, LLMConfig, parse_numbered


def test_parse_variants():
    assert parse_numbered('{"1": "a", "2": "b"}', 2) == {0: "a", 1: "b"}
    assert parse_numbered('<think>x</think>\n```json\n{"translations": {"1": "a"}}\n```', 1) == {0: "a"}
    assert parse_numbered("1. Hello\n2: World", 2) == {0: "Hello", 1: "World"}
    assert parse_numbered('["x", "y"]', 2) == {0: "x", 1: "y"}
    assert parse_numbered('{"1": {"en": "z"}}', 1) == {0: "z"}
    assert parse_numbered("Just text", 1) == {0: "Just text"}
    assert parse_numbered('{"5": "out of range"}', 2) == {}


def test_ollama_batches_with_context_and_retry(fake_llm):
    url, srv = fake_llm
    srv.drop_line = 2
    c = LLMClient(LLMConfig(backend="ollama", url=url, model="fake", batch=3, glossary="先生 = Teacher"))
    lines = [f"行{i}" for i in range(7)]
    out = c.translate_lines(lines)
    assert out == [f"EN(行{i})" for i in range(7)]
    paths = [p for p, _ in srv.requests]
    assert paths.count("/api/chat") > 3            # the dropped lines were retried singly
    first = srv.requests[0][1]
    assert first["think"] is False and first["format"] == "json"
    assert "Glossary" in first["messages"][0]["content"]
    later = [b for p, b in srv.requests if "Context" in b["messages"][-1]["content"]]
    assert later, "later batches carry previous lines as context"
    assert c.list_models() == ["fake:latest"]
    c.unload()
    assert srv.requests[-1][0] == "/api/generate" and srv.requests[-1][1]["keep_alive"] == 0


def test_openai_backend(fake_llm):
    url, srv = fake_llm
    c = LLMClient(LLMConfig(backend="openai", url=url + "/v1", model="m", api_key="k"))
    assert c.translate_lines(["こんにちは"]) == ["EN(こんにちは)"]
    assert srv.requests[0][0] == "/v1/chat/completions"
    assert c.list_models() == ["fake-model"]
