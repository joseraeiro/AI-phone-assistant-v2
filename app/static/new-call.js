const form = document.querySelector("#new-call-form");
const error = document.querySelector("#form-error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  const data = Object.fromEntries(new FormData(form));
  for (const key of ["realtime_model", "voice"]) {
    if (!data[key]) delete data[key];
  }
  const response = await fetch("/calls", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const body = await response.json();
    error.textContent = body.detail instanceof Array
      ? body.detail.map((item) => item.msg).join("; ")
      : body.detail || "The call could not be created.";
    error.hidden = false;
    return;
  }
  const call = await response.json();
  window.location.assign(`/calls/${call.internal_call_id}`);
});
