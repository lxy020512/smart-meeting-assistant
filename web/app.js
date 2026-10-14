let activeJob = null;
const $ = (selector) => document.querySelector(selector);

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function renderReport(report) {
  if (!report) { $("#report-body").className = "empty"; $("#report-body").textContent = "报告将在任务完成后显示。"; return; }
  const summary = report.summary || {};
  const actions = (report.actions || []).map((item) => `<li><strong>${item.owner || "待确认"}</strong>：${item.task}${item.due ? `（${item.due}）` : ""}</li>`).join("");
  const decisions = (summary.decisions || []).map((item) => `<li>${item}</li>`).join("");
  $("#report-body").className = "report-body";
  $("#report-body").innerHTML = `<h3>摘要</h3><p>${summary.summary || "无"}</p><h3>决议</h3><ul>${decisions || "<li>未识别到明确决议</li>"}</ul><h3>行动项</h3><ul>${actions || "<li>未识别到行动项</li>"}</ul><p class="hint">处理 ${report.metrics?.processing_ms ?? "-"} ms；降级 Agent ${report.metrics?.fallback_agents ?? 0} 个；证据审核 ${report.review?.approved ? "通过" : "需要复核"}。</p>`;
}

function renderEvents(events) {
  $("#events").innerHTML = events.map((event) => `<li class="${event.state === "succeeded" ? "done" : ""}"><strong>${event.step}</strong> · ${event.state}</li>`).join("");
}

async function poll() {
  if (!activeJob) return;
  try {
    const data = await request(`/api/jobs/${activeJob}`);
    renderEvents(data.events);
    if (["succeeded", "dead"].includes(data.job.state)) {
      const meeting = await request(`/api/meetings/${data.job.meeting_id}`);
      renderReport(meeting.report);
      return;
    }
    setTimeout(poll, 800);
  } catch (error) { $("#events").innerHTML += `<li>${error.message}</li>`; }
}

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button"); button.disabled = true;
  try {
    const body = new FormData(event.target);
    const result = await request("/api/meetings", { method: "POST", body });
    activeJob = result.job_id; $("#job-id").textContent = result.job_id; $("#events").innerHTML = "<li>任务已入队，等待 Worker 领取。</li>"; poll();
  } catch (error) { alert(error.message); } finally { button.disabled = false; }
});

$("#refresh").addEventListener("click", poll);
$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const q = new FormData(event.target).get("q");
  try { const data = await request(`/api/search?q=${encodeURIComponent(q)}`); $("#search-results").innerHTML = data.results.map((item) => `<div class="result">${item.text}<br><small>${item.meeting_id} · ${item.speaker} · ${item.start_ms}ms · score ${item.score}</small></div>`).join("") || "<p class='empty'>没有命中。</p>"; } catch (error) { alert(error.message); }
});

request("/api/health").then((data) => { $("#health").textContent = data.model_configured === "true" ? "模型已配置" : "规则降级模式"; }).catch(() => { $("#health").textContent = "服务不可用"; });

