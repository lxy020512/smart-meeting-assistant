from __future__ import annotations

import json
import math
import re
from collections import Counter

from .agents import GUARD
from .schemas import AnswerOutput


def tokenize(text):
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())
    bigrams = [run[i:i+2] for run in re.findall(r"[\u4e00-\u9fff]+", text) for i in range(len(run)-1)]
    return words + bigrams


class EvidenceIndex:
    def __init__(self, store, settings=None, embedder=None):
        self.store, self.settings, self.embedder = store, settings, embedder
        self.embedding_error = None

    def _embed(self, texts):
        if not self.embedder and self.settings and self.settings.semantic_enabled:
            try:
                from fastembed import TextEmbedding
                self.embedder = TextEmbedding(self.settings.embed_model)
            except Exception as exc:
                self.embedding_error = type(exc).__name__
                return None
        if not self.embedder:
            return None
        try:
            return [v.tolist() if hasattr(v, "tolist") else list(v) for v in self.embedder.embed(texts)]
        except Exception as exc:
            self.embedding_error = type(exc).__name__
            return None

    def index(self, meeting_id, segments, source_version="", job_id=None, token=None):
        vectors = self._embed([s.text for s in segments])
        chunks = [{**s.as_dict(), "tokens": tokenize(s.text), "vector": vectors[i] if vectors else None, "source_version": source_version} for i, s in enumerate(segments)]
        self.store.replace_chunks(meeting_id, chunks, job_id, token)
        return "hybrid" if vectors else "lexical"

    def search(self, query, top_k=5, meeting_id=None):
        rows = self.store.all_chunks(meeting_id)
        parsed = [(row, json.loads(row["tokens"])) for row in rows]
        query_tokens = set(tokenize(query))
        if not query_tokens or not parsed:
            return []
        df = Counter(token for _, tokens in parsed for token in set(tokens))
        avg_length = sum(len(tokens) for _, tokens in parsed) / len(parsed)
        lexical = []
        for row, tokens in parsed:
            tf, score = Counter(tokens), 0.0
            for word in query_tokens:
                if tf[word]:
                    idf = math.log(1 + (len(parsed) - df[word] + .5) / (df[word] + .5))
                    score += idf * tf[word] * 2.2 / (tf[word] + 1.2 * (.25 + .75 * len(tokens) / max(1, avg_length)))
            if score:
                lexical.append((row["id"], score))
        lexical.sort(key=lambda pair: pair[1], reverse=True)
        semantic = []
        vectors = self._embed([query]) if any(json.loads(row["vector"] or "null") for row in rows) else None
        if vectors:
            q = vectors[0]
            for row in rows:
                v = json.loads(row["vector"] or "null")
                if not v or len(q) != len(v):
                    continue
                norm = math.sqrt(sum(x*x for x in q) * sum(x*x for x in v))
                cosine = sum(a*b for a,b in zip(q,v)) / norm if norm else 0
                if cosine > .35:
                    semantic.append((row["id"], cosine))
            semantic.sort(key=lambda pair: pair[1], reverse=True)
        scores = Counter()
        for ranking in (lexical, semantic):
            for rank, (chunk_id, _) in enumerate(ranking[:50], 1):
                scores[chunk_id] += 1 / (60 + rank)
        mapping = {row["id"]: row for row in rows}
        return [{"citation_id": f"E{chunk_id}", "meeting_id": mapping[chunk_id]["meeting_id"], "speaker": mapping[chunk_id]["speaker"], "start_ms": mapping[chunk_id]["start_ms"], "end_ms": mapping[chunk_id]["end_ms"], "text": mapping[chunk_id]["text"], "source_version": mapping[chunk_id]["source_version"], "score": round(score, 6), "retrieval_mode": "hybrid" if semantic else "lexical"} for chunk_id, score in scores.most_common(top_k)]


def index_segments(store, meeting_id, segments, source_version="", job_id=None, token=None):
    return EvidenceIndex(store).index(meeting_id, segments, source_version, job_id, token)


def search(store, query, top_k=5):
    return EvidenceIndex(store).search(query, top_k)


def answer_question(index, client, query, meeting_id=None):
    evidence = index.search(query, 5, meeting_id)
    if not evidence:
        return {"answer": "未找到支持回答的会议证据。", "citations": [], "mode": "abstain"}
    raw, trace = client.ask_json("qa", GUARD + "只使用所给证据回答。citations 必须引用证据 citation_id，无法回答时返回空列表。", json.dumps({"question": query, "evidence": evidence}, ensure_ascii=False), schema=AnswerOutput)
    allowed = {e["citation_id"] for e in evidence}
    if raw and raw["citations"] and set(raw["citations"]) <= allowed:
        cited = [e for e in evidence if e["citation_id"] in raw["citations"]]
        if all(e["source_version"] == index.store.source_version(e["meeting_id"]) for e in cited):
            return {**raw, "evidence": cited, "mode": "model", "trace": trace.__dict__}
    return {"answer": "检索到相关原文；生成答案不可用或引用未通过校验，请查看证据。", "citations": [], "evidence": evidence, "mode": "evidence_only", "trace": trace.__dict__}
