const source = new EventSource(`/calls/${window.callId}/events`);

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
