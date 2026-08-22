const source = new EventSource(`/calls/${window.callId}/events`);

function addReportList(parent, heading, rows, value) {
  const title = document.createElement("h3");
  const list = document.createElement("ul");
  title.textContent = heading;
  (rows.length ? rows : ["None"]).forEach((row) => {
    const item = document.createElement("li");
    item.textContent = value(row);
    list.append(item);
  });
  parent.append(title, list);
}

function renderSummary(call) {
  const report = document.querySelector("#summary-report");
  if (call.summary) {
    report.replaceChildren();
    const objectiveTitle = document.createElement("strong");
    const objective = document.createElement("p");
    const resultTitle = document.createElement("strong");
    const result = document.createElement("p");
    objectiveTitle.textContent = "Objective";
    objective.textContent = call.objective;
    resultTitle.textContent = "Result";
    result.textContent = call.summary.summary;
    report.append(objectiveTitle, objective, resultTitle, result);
    addReportList(report, "Information obtained", call.summary.information_obtained,
      (row) => `${row.text} · ${row.certainty}`);
    addReportList(report, "Actions taken", call.summary.actions_taken, (row) => row);
    addReportList(report, "Follow-up", call.summary.follow_up,
      (row) => `${row.text} · ${row.certainty}`);
  } else if (call.summary_status === "failed") {
    report.replaceChildren();
    const message = document.createElement("p");
    const retry = document.createElement("button");
    message.className = "error";
    message.textContent = call.summary_error;
    retry.id = "retry-summary";
    retry.className = "button";
    retry.textContent = "Retry report";
    report.append(message, retry);
  } else if (call.status === "completed") {
    report.textContent = "Generating report…";
  }
}

function renderRecording(call) {
  const container = document.querySelector("#recording");
  if (!call.recording) return;
  container.replaceChildren();
  if (call.recording.status === "completed") {
    const audio = document.createElement("audio");
    const details = document.createElement("p");
    audio.controls = true;
    audio.preload = "none";
    audio.src = call.recording.url;
    details.className = "muted";
    details.textContent = `${call.recording.duration ?? "—"}s · ${call.recording.channels ?? "—"} channel(s)`;
    container.append(audio, details);
  } else {
    container.textContent = `Recording status: ${call.recording.status}`;
  }
}

function replaceList(id, rows, render, emptyText) {
  const list = document.querySelector(id);
  list.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.className = "empty-row";
    empty.textContent = emptyText;
    list.append(empty);
    return;
  }
  rows.forEach((row) => list.append(render(row)));
}

source.addEventListener("call", (event) => {
  const call = JSON.parse(event.data);
  const status = document.querySelector("#call-status");
  status.textContent = call.status_label;
  status.className = `status status-${call.status}`;
  document.querySelector("#objective-status").textContent = call.objective_status.toUpperCase();
  document.querySelector("#duration").textContent = call.duration === null ? "—" : `${call.duration}s`;
  document.querySelector("#ended-at").textContent = call.ended_at || "—";
  document.querySelector("#end-call").hidden = ["completed", "failed", "busy", "no-answer", "canceled"].includes(call.status);
  renderSummary(call);
  renderRecording(call);
  replaceList("#transcript", call.transcripts, (row) => {
    const li = document.createElement("li");
    const speaker = document.createElement("span");
    const text = document.createElement("p");
    speaker.textContent = row.speaker;
    text.textContent = row.text;
    li.append(speaker, text);
    return li;
  }, "Waiting for final transcript entries…");
  replaceList("#facts", call.facts, (row) => {
    const li = document.createElement("li");
    const meta = document.createElement("span");
    const text = document.createElement("p");
    meta.textContent = `${row.category} · ${row.confidence}`;
    text.textContent = row.fact;
    li.append(meta, text);
    return li;
  }, "No facts captured yet.");
  replaceList("#events", call.events, (row) => {
    const li = document.createElement("li");
    const type = document.createElement("strong");
    const time = document.createElement("time");
    type.textContent = row.type;
    time.textContent = row.created_at;
    li.append(type, time);
    return li;
  }, "No events yet.");
});

document.querySelector("#end-call").addEventListener("click", async () => {
  const button = document.querySelector("#end-call");
  button.disabled = true;
  const response = await fetch(`/calls/${window.callId}/end`, {method: "POST"});
  if (!response.ok) button.disabled = false;
});

document.querySelector("#summary-card").addEventListener("click", async (event) => {
  if (event.target.id !== "retry-summary") return;
  event.target.disabled = true;
  await fetch(`/calls/${window.callId}/summary/retry`, {method: "POST"});
});
