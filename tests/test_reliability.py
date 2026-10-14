from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest

from app.llm import DeepSeekClient
from app.pipeline import MeetingPipeline
from app.schemas import ReviewOutput
from app.storage import LeaseLost, Store


@pytest.mark.parametrize("status,expected", [(401, 1), (403, 1), (400, 1), (429, 2), (503, 2)])
def test_provider_retry_budget(config, status, expected):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status)
    client = DeepSeekClient(replace(config, model_api_key="fake-test-key"), httpx.Client(transport=httpx.MockTransport(respond)), sleeper=lambda _: None)
    value, trace = client.ask_json("critic", "", "", schema=ReviewOutput)
    assert value is None and trace.fallback and trace.attempts == expected
    assert len(calls) == expected


def test_timeout_is_bounded(config):
    def respond(request):
        raise httpx.ReadTimeout("test", request=request)
    client = DeepSeekClient(replace(config, model_api_key="fake-test-key"), httpx.Client(transport=httpx.MockTransport(respond)), sleeper=lambda _: None)
    value, trace = client.ask_json("critic", "", "", schema=ReviewOutput)
    assert value is None and trace.error == "timeout" and trace.attempts == 2


def test_invalid_schema_repair_then_success(config):
    calls = []
    def respond(request):
        calls.append(1)
        content = '{"approved":true,"issues":42}' if len(calls) == 1 else '{"approved":true,"issues":[]}'
        return httpx.Response(200, json={"choices":[{"message":{"content":content}}],"usage":{"prompt_tokens":4,"completion_tokens":2}})
    client = DeepSeekClient(replace(config, model_api_key="fake-test-key"), httpx.Client(transport=httpx.MockTransport(respond)), sleeper=lambda _: None)
    value, trace = client.ask_json("critic", "", "", schema=ReviewOutput)
    assert value["approved"] and not trace.fallback and trace.attempts == 2
    assert trace.prompt_tokens == 8 and trace.completion_tokens == 4


def test_wrong_type_never_reaches_agents(config):
    def respond(request):
        return httpx.Response(200,json={"choices":[{"message":{"content":'{"approved":true,"issues":42}'}}]})
    client = DeepSeekClient(replace(config, model_api_key="fake-test-key"), httpx.Client(transport=httpx.MockTransport(respond)), sleeper=lambda _: None)
    value, trace = client.ask_json("critic", "", "", schema=ReviewOutput)
    assert value is None and trace.error == "invalid_structured_output"


def test_atomic_claim_across_connections(store):
    _, job_id = store.create_meeting("竞争领取", "demo.txt", "txt")
    def claim(_):
        return Store(store.path).claim_job(30)
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(claim, range(6)))
    assert sum(c is not None for c in claims) == 1
    assert store.job(job_id)["attempts"] == 1


def test_idempotent_submission(store):
    first = store.create_meeting("会议", "demo.txt", "txt", "same-key")
    assert store.create_meeting("会议", "demo.txt", "txt", "same-key") == first
    assert len(store.meetings()) == 1


def test_stale_worker_cannot_publish(store):
    meeting_id, job_id = store.create_meeting("租约回收", "demo.txt", "txt")
    old = store.claim_job(30)
    with store._db() as db:
        db.execute("UPDATE jobs SET lease_until='2000-01-01' WHERE id=?", (job_id,))
    new = store.claim_job(30)
    assert old["lease_token"] != new["lease_token"]
    with pytest.raises(LeaseLost):
        store.save_report(meeting_id, {"wrong":True}, job_id, old["lease_token"])
    store.save_report(meeting_id, {"correct":True}, job_id, new["lease_token"])
    assert store.report(meeting_id)["correct"]


def test_heartbeat_extends_lease(store):
    _, job_id = store.create_meeting("心跳", "demo.txt", "txt")
    job = store.claim_job(30)
    store.heartbeat(job_id, job["lease_token"], 120)
    assert store.job(job_id)["lease_until"] > job["lease_until"]


def test_checkpoint_reuses_model_and_transcript(config, store, tmp_path, monkeypatch):
    source = tmp_path / "meeting.txt"
    source.write_text("成员甲负责整理预算，周五前完成。\n会议决定先灰度发布。", encoding="utf-8")
    mid, jid = store.create_meeting("复用", str(source), "txt")
    pipeline = MeetingPipeline(store, DeepSeekClient(config), config)
    pipeline.execute(mid, jid)
    def must_not_call(*args, **kwargs):
        raise AssertionError("checkpoint should reuse the result")
    monkeypatch.setattr(pipeline.agents, "summary_agent", must_not_call)
    monkeypatch.setattr(pipeline.agents, "action_agent", must_not_call)
    monkeypatch.setattr(pipeline.agents, "critic_agent", must_not_call)
    monkeypatch.setattr("app.pipeline.choose_transcriber", must_not_call)
    pipeline.execute(mid, jid)
    assert sum(e["state"] == "reused" for e in store.events(jid)) == 5


def test_crash_budget_exhaustion(store):
    _, jid = store.create_meeting("崩溃预算", "demo.txt", "txt")
    for _ in range(3):
        assert store.claim_job(30)
        with store._db() as db:
            db.execute("UPDATE jobs SET lease_until='2000-01-01' WHERE id=?", (jid,))
    assert store.claim_job(30) is None
    assert store.job(jid)["state"] == "dead"
