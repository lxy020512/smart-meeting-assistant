from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agents import fallback_actions, replay_events
from app.pipeline import segment_chunks
from app.retrieval import EvidenceIndex, answer_question
from app.transcribe import Segment, normalize_segments, TextOrJsonTranscriber


def test_task_state_corrections_and_cancellation():
    lines = ["成员甲负责整理预算，周五前完成。", "整理预算改由成员乙负责。", "整理预算延期到下周一。", "取消整理预算。"]
    segments = [Segment("未标注",0,1,line) for line in lines]
    events = fallback_actions(segments)["events"]
    actions, history, issues = replay_events(events, segments)
    assert not issues and len(history) == 4
    assert actions[0]["owner"] == "成员乙" and actions[0]["due"] == "下周一" and actions[0]["status"] == "cancelled"


@pytest.mark.parametrize("segment_id,quote", [(8,"整理预算"),(0,"伪造证据"),(-1,"整理预算")])
def test_forged_evidence_is_rejected(segment_id, quote):
    event = {"task_id":"预算","kind":"create","task":"整理预算","owner":None,"due":None,"evidence":{"segment_id":segment_id,"quote":quote}}
    actions, history, issues = replay_events([event], [Segment("未标注",0,1,"整理预算")])
    assert not actions and not history and issues


def test_unknown_speaker_is_not_invented():
    assert all(s.speaker == "未标注" for s in normalize_segments([{"text":"第一句"},{"text":"第二句"}]))


def test_text_has_no_fabricated_audio_time(tmp_path):
    path=tmp_path/"demo.txt"
    path.write_text("测试内容",encoding="utf-8")
    assert not TextOrJsonTranscriber().transcribe(path)[0].timing_known


def test_srt_timestamps(tmp_path):
    path=tmp_path/"demo.srt"
    path.write_text("1\n00:00:01,200 --> 00:00:03,600\n测试字幕\n",encoding="utf-8")
    s=TextOrJsonTranscriber().transcribe(path)[0]
    assert s.start_ms == 1200 and s.end_ms == 3600 and s.timing_known


def test_chunking_keeps_all_segments():
    segments=[Segment("未标注",0,1,"甲"*500) for _ in range(30)]
    groups=segment_chunks(segments,1000)
    assert len(groups)==15 and sum(len(s) for _,s in groups)==30
    assert groups[-1][0] == 28


def test_oversized_segment_fails_explicitly():
    with pytest.raises(ValueError): segment_chunks([Segment("未标注",0,1,"甲"*2000)],1000)


def test_semantic_retrieval_and_scope(store):
    class Embedder:
        def embed(self, texts):
            return [[1.,0.] if any(w in t for w in ("上线","发布")) else [0.,1.] for t in texts]
    a,_=store.create_meeting("产品会","a.txt","txt")
    b,_=store.create_meeting("预算会","b.txt","txt")
    index=EvidenceIndex(store,embedder=Embedder())
    index.index(a,[Segment("未标注",0,1,"版本上线定在下周")])
    index.index(b,[Segment("未标注",0,1,"调整年度预算")])
    assert index.search("何时发布",1)[0]["meeting_id"]==a
    assert all(e["meeting_id"]==b for e in index.search("预算",5,b))


def test_qa_rejects_forged_citation(store):
    mid,_=store.create_meeting("检索","a.txt","txt")
    store.save_transcript(mid,[],"v1")
    index=EvidenceIndex(store)
    index.index(mid,[Segment("未标注",0,1,"下周发布版本")],"v1")
    class Client:
        def ask_json(self,*a,**k): return {"answer":"伪造答案","citations":["E9999"]},SimpleNamespace(__dict__={})
    assert answer_question(index,Client(),"发布")["mode"] == "evidence_only"
