let activeJob = null, activeMeeting = null, audioUrl = null, pollTimer = null;
const $ = (s) => document.querySelector(s);
function node(tag, text, cls) { const el = document.createElement(tag); el.textContent = text; if (cls) el.className = cls; return el; }
async function response(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = sessionStorage.getItem("meeting-token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const result = await fetch(url, { ...options, headers });
  if (!result.ok) throw new Error(`请求失败 ${result.status}: ${await result.text()}`);
  return result;
}
async function request(url, options) { return (await response(url, options)).json(); }
function section(body, title, items) {
  body.append(node("h3", title));
  const list = node("ul", "");
  (items.length ? items : ["暂无"]).forEach((s) => list.append(node("li", s)));
  body.append(list);
}
function renderReport(report) {
  const body = $("#report-body"); body.replaceChildren();
  if (!report) { body.textContent = "报告将在任务完成后显示。"; return; }
  body.className = "report-body";
  body.append(node("h3", "摘要"), node("p", report.summary.summary));
  section(body, "决议", report.summary.decisions.map((d) => `${d.text}；原文：${d.evidence.quote}`));
  section(body, "行动项", report.actions.map((a) => `${a.owner || "待确认"}：${a.task}（${a.due || "期限待确认"}）；原文：${a.evidence.quote}`));
  section(body, "取消安排", report.cancelled_actions.map((a) => a.task));
  section(body, "复核意见", report.review.issues);
  body.append(node("p", `处理 ${report.metrics.processing_ms} ms；降级调用 ${report.metrics.fallback_agents}；程序证据校验 ${report.review.program_evidence_valid ? "通过" : "需复核"}；模型审校 ${report.review.approved ? "通过" : "需复核"}`, "hint"));
  const details = node("details", ""); details.append(node("summary", "任务变更与 Agent 执行记录"), node("pre", JSON.stringify({ history: report.task_history, traces: report.agent_traces }, null, 2))); body.append(details);
}
async function showMeeting(id, jobId) {
  activeMeeting = id; activeJob = jobId;
  const detail = await request(`/api/meetings/${id}`);
  if (activeMeeting !== id) return;
  renderReport(detail.report); $("#transcript").replaceChildren();
  (detail.transcript || []).forEach((s, i) => {
    const row = node("div", `[${i}] ${s.speaker} · ${s.timing_known ? `${(s.start_ms/1000).toFixed(1)}s` : "无录音时间"}：${s.text}`, "result");
    if (s.timing_known) row.onclick = () => { $("#audio").currentTime = s.start_ms/1000; };
    $("#transcript").append(row);
  });
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  $("#audio").hidden = true;
  if (["wav", "mp3", "m4a", "flac"].includes(detail.meeting.source_type)) {
    const blob = await (await response(`/api/meetings/${id}/audio`)).blob();
    if (activeMeeting === id) { audioUrl = URL.createObjectURL(blob); $("#audio").src = audioUrl; $("#audio").hidden = false; }
  }
}
async function history() {
  const entries = await request("/api/meetings"); $("#history").replaceChildren();
  entries.forEach((item) => { const b = node("button", `${item.title} · ${item.state}`, "history-item"); b.onclick = async () => { clearTimeout(pollTimer); await showMeeting(item.id, item.job_id); await poll(); }; $("#history").append(b); });
}
async function poll() {
  clearTimeout(pollTimer); if (!activeJob) return; const id = activeJob;
  try {
    const data = await request(`/api/jobs/${id}`); if (activeJob !== id) return;
    $("#job-id").textContent = data.job.state;
    $("#events").replaceChildren(...data.events.map((e) => node("li", `${e.step} · ${e.state}${e.payload.elapsed_ms != null ? ` · ${e.payload.elapsed_ms} ms` : ""}`, e.state === "succeeded" ? "done" : "")));
    if (["succeeded", "dead"].includes(data.job.state)) { await showMeeting(data.job.meeting_id, id); await history(); if (data.job.error) $("#events").append(node("li", data.job.error)); return; }
    pollTimer = setTimeout(poll, 1000);
  } catch (error) { $("#events").append(node("li", error.message)); }
}
$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.target.querySelector("button"); button.disabled = true;
  try { const result = await request("/api/meetings", { method: "POST", body: new FormData(event.target) }); activeJob = result.job_id; activeMeeting = result.meeting_id; clearTimeout(pollTimer); await history(); await poll(); }
  catch (error) { alert(error.message); } finally { button.disabled = false; }
});
$("#refresh").onclick = poll;
$("#download").onclick = async () => {
  if (!activeMeeting) return;
  try { const blob = await (await response(`/api/meetings/${activeMeeting}/export`)).blob(); const url = URL.createObjectURL(blob); const a = node("a", ""); a.href = url; a.download = "会议报告.md"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  catch (error) { alert(error.message); }
};
function evidence(items) {
  $("#search-results").replaceChildren();
  items.forEach((e) => { const row = node("div", `[${e.citation_id}] ${e.text}`, "result"); row.append(node("small", `\n${e.meeting_id} · ${e.speaker} · ${(e.start_ms/1000).toFixed(1)}s · ${e.retrieval_mode}`)); const b = node("button", "打开会议", "quiet"); b.onclick = async () => { const detail = await request(`/api/meetings/${e.meeting_id}`); await showMeeting(e.meeting_id, detail.job.id); await poll(); }; row.append(b); $("#search-results").append(row); });
}
$("#search-form").addEventListener("submit", async (event) => { event.preventDefault(); try { const q = new FormData(event.target).get("q"); const data = await request(`/api/search?q=${encodeURIComponent(q)}`); evidence(data.results); $("#answer").textContent = data.results.length ? "" : "未找到相关证据"; } catch (error) { alert(error.message); } });
$("#ask").onclick = async () => { const q = new FormData($("#search-form")).get("q"); if (!q) return; $("#ask").disabled = true; try { const data = await request("/api/ask", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({question:q}) }); $("#answer").textContent = `${data.answer} ${data.citations.join(" ")}`; evidence(data.evidence || []); } catch (error) { alert(error.message); } finally { $("#ask").disabled = false; } };
async function connect() { try { const health = await request("/api/health"); $("#health").textContent = health.model_configured ? "模型已配置" : "规则降级模式"; await history(); } catch { $("#health").textContent = "连接失败"; } }
$("#token").onclick = () => { const value = prompt("输入本机服务的 API_TOKEN；不需要时留空"); if (value !== null) { sessionStorage.setItem("meeting-token", value); connect(); } };
connect();
