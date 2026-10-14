"""Agent loop with a fake Claude client: tool use, guard retry and template fallback."""
import json
from types import SimpleNamespace as NS

import pytest

import lifeline.copilot.agent as agent


def _msg(blocks, stop):
    return NS(content=blocks, stop_reason=stop, model="fake-model",
              usage=NS(input_tokens=100, output_tokens=20))


def _text(t):
    return NS(type="text", text=t)


def _tool(i, name, inp):
    return NS(type="tool_use", id=i, name=name, input=inp)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        step = self.script.pop(0)
        return step(kw) if callable(step) else step


@pytest.fixture()
def run_dir_spend(tmp_path):
    return tmp_path / "api_spend.jsonl"


@pytest.fixture()
def fake(monkeypatch, run_dir_spend):
    holder = {}

    def install(script):
        client = FakeClient(script)
        holder["c"] = client
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: client)
        monkeypatch.setattr(agent, "_has_key", lambda: True)
        monkeypatch.setenv("BATO_SPEND_FILE", str(run_dir_spend))
        return client
    return install


def test_model_answer_with_tools(fake, run_dir, ledger):
    fid = ledger.get("access.villages.cut_off")["id"]
    c = fake([
        _msg([_tool("t1", "get_fact", {"key": "access.villages.cut_off"})], "tool_use"),
        _msg([_text(f"5 settlements are likely cut off by road [{fid}].")], "end_turn"),
    ])
    r = agent.answer("How many villages are cut off?", run_dir, "en")
    assert r["model"] == "fake-model" and r["attempts"] == 1 and fid in r["citations"]
    kw = c.calls[0]
    assert kw["fallbacks"] == "default" and kw["output_config"] == {"effort": "medium"}
    assert "tool_choice" not in kw and all(t["strict"] for t in kw["tools"])
    tool_results = c.calls[1]["messages"][2]["content"]
    assert tool_results[0]["type"] == "tool_result" and json.loads(tool_results[0]["content"])[0]["id"] == fid
    assert (run_dir / "copilot_usage.jsonl").exists()


def test_fabrication_retry_then_fallback(fake, run_dir):
    c = fake([
        _msg([_tool("t1", "list_facts", {"prefix": "access."})], "tool_use"),
        _msg([_text("About 100 villages are cut off.")], "end_turn"),
        _msg([_text("Roughly 120 villages are cut off.")], "end_turn"),
    ])
    r = agent.answer("How many villages are cut off?", run_dir, "en")
    assert r["model"] == "template" and r["attempts"] == 2
    assert "100" not in r["answer"] and "5 of 10" in r["answer"]
    assert "number check" in c.calls[2]["messages"][-1]["content"]


def test_number_not_looked_up_is_rejected(fake, run_dir, ledger):
    # 40 is a real ledger value, but the model never retrieved or cited it
    fake([
        _msg([_text("40 buildings were mapped.")], "end_turn"),
        _msg([_text("40 buildings were mapped.")], "end_turn"),
    ])
    r = agent.answer("How many buildings were mapped?", run_dir, "en")
    assert r["model"] == "template"


def test_refusal_stop_reason_falls_back(fake, run_dir):
    fake([_msg([], "refusal")])
    r = agent.answer("Summarise the situation", run_dir, "en")
    assert r["model"] == "template" and "refusal" in r["fallback_reason"]


def test_casualty_never_reaches_model(fake, run_dir):
    c = fake([])
    r = agent.answer("How many people died in Haku?", run_dir, "en")
    assert r["refused"] and c.calls == []


def test_nepali_answer_must_be_nepali(fake, run_dir, ledger):
    fid = ledger.get("access.villages.cut_off")["id"]
    fake([
        _msg([_tool("t1", "get_fact", {"key": "access.villages.cut_off"})], "tool_use"),
        _msg([_text(f"5 settlements are cut off [{fid}].")], "end_turn"),
        _msg([_text(f"५ वटा बस्तीको सडक सम्पर्क टुटेको हुन सक्छ [{fid}]।")], "end_turn"),
    ])
    r = agent.answer("कति गाउँको सम्पर्क टुटेको छ?", run_dir, "ne")
    assert r["model"] == "fake-model" and r["attempts"] == 2 and "५" in r["answer"]


def test_budget_cap_blocks_calls(fake, run_dir, ledger, monkeypatch, run_dir_spend):
    from lifeline.copilot import spend
    fid = ledger.get("access.villages.cut_off")["id"]
    monkeypatch.setenv("ANTHROPIC_BUDGET_USD", "0.2")
    script = [_msg([_text(f"5 settlements are likely cut off by road [{fid}].")], "end_turn")]
    c = fake(script)
    r = agent.answer("How many villages are cut off?", run_dir, "en")   # within budget: one call made
    assert len(c.calls) == 1 and not r["budget_exhausted"]
    assert len(spend.records()) == 1 and spend.records()[0]["purpose"] == "copilot"
    spend.record("claude-opus-5-5", 0, 10_000, purpose="test")          # $0.20 more: budget reached
    r = agent.answer("How many villages are cut off?", run_dir, "en")
    assert r["budget_exhausted"] and r["model"] == "template" and len(c.calls) == 1
    assert "5 of 10" in r["answer"]
