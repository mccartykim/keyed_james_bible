"use strict";

const form = document.getElementById("ask");
const input = document.getElementById("q");
const out = document.getElementById("out");
const status = document.getElementById("status");
const reference = document.getElementById("reference");
const versesEl = document.getElementById("verses");
const fullEl = document.getElementById("full");
const levelsEl = document.getElementById("levels");
const metaEl = document.getElementById("meta");

let inFlight = null;
let lastReading = null;

function showStatus(message, isError) {
  status.hidden = false;
  status.textContent = message;
  status.classList.toggle("error", Boolean(isError));
}

function clearStatus() {
  status.hidden = true;
  status.textContent = "";
  status.classList.remove("error");
}

/* ------------------------------------------------------------------ ask */

async function ask(question) {
  if (!question) return;

  if (inFlight) inFlight.abort();
  const controller = new AbortController();
  inFlight = controller;

  form.querySelector("button").disabled = true;
  showStatus("keying…", false);
  out.hidden = true;

  try {
    const response = await fetch(`/api/ask?q=${encodeURIComponent(question)}`, {
      signal: controller.signal,
    });
    const payload = await response.json();

    if (!response.ok) {
      showStatus(payload.hint ? `${payload.error} — ${payload.hint}` : payload.error, true);
      return;
    }
    clearStatus();
    render(payload);
  } catch (error) {
    if (error.name !== "AbortError") {
      showStatus(`could not reach the service: ${error.message}`, true);
    }
  } finally {
    form.querySelector("button").disabled = false;
    inFlight = null;
  }
}

/* --------------------------------------------------------------- render */

function render(payload) {
  lastReading = payload;
  reference.textContent = payload.reference;

  versesEl.replaceChildren();
  const first = payload.verses[0];
  for (const verse of payload.verses) {
    const p = document.createElement("p");
    // Within one chapter the book name is noise; show just the verse number.
    const label =
      verse.book === first.book && verse.chapter === first.chapter
        ? String(verse.verse)
        : `${verse.chapter}:${verse.verse}`;
    const span = document.createElement("span");
    span.className = "v";
    span.textContent = label;
    p.append(span, document.createTextNode(verse.text));
    versesEl.append(p);
  }

  renderCopyButton(payload);
  renderLevels(payload);
  renderMeta(payload);
  out.hidden = false;
}

function renderCopyButton(payload) {
  fullEl.replaceChildren();
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "copy passage";
  button.addEventListener("click", async () => {
    const text = `${payload.verses.map((v) => v.text).join(" ")}\n\n— ${payload.reference} (KJV)`;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "copied";
    } catch {
      button.textContent = "copy failed";
    }
    setTimeout(() => (button.textContent = "copy passage"), 1400);
  });
  fullEl.append(button);
}

function renderLevels(payload) {
  levelsEl.replaceChildren();

  const head = document.createElement("tr");
  for (const label of ["step", "chose", "confidence", "options", "runners-up"]) {
    const th = document.createElement("th");
    th.textContent = label;
    head.append(th);
  }
  levelsEl.append(head);

  for (const trace of payload.selection) {
    const row = document.createElement("tr");

    const level = document.createElement("td");
    level.className = "level";
    level.textContent = trace.level;

    const choice = document.createElement("td");
    choice.className = "choice";
    choice.textContent = trace.choice;

    const conf = document.createElement("td");
    conf.className = "conf";
    const bar = document.createElement("span");
    bar.className = "bar";
    const fill = document.createElement("i");
    fill.style.width = `${Math.round(trace.confidence * 100)}%`;
    bar.append(fill);
    conf.append(bar, document.createTextNode(trace.confidence.toFixed(3)));

    const options = document.createElement("td");
    options.className = "level";
    options.textContent = trace.options;

    const alt = document.createElement("td");
    alt.className = "alt";
    alt.textContent = trace.alternatives
      .slice(1, 3)
      .map((a) => `${a.option} ${a.probability.toFixed(2)}`)
      .join(", ");

    row.append(level, choice, conf, options, alt);
    levelsEl.append(row);
  }
}

function renderMeta(payload) {
  const parts = [
    payload.cached ? "cache hit" : `live · ${payload.latency_ms} ms`,
    `${payload.confidence.toFixed(4)} chain confidence`,
    payload.cached ? "no new charge" : `$${payload.cost_usd.toFixed(6)}`,
    payload.model,
  ];
  if (payload.span_override) parts.push(`span forced to ${payload.span_override}`);
  metaEl.textContent = parts.join("  ·  ");
}

/* ---------------------------------------------------------------- wiring */

form.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(input.value.trim());
});

for (const button of document.querySelectorAll(".ex")) {
  button.addEventListener("click", () => {
    input.value = button.textContent;
    ask(button.textContent);
  });
}

fetch("/api/health")
  .then((r) => r.json())
  .then((health) => {
    if (!health.ok) return;
    showStatus(
      `corpus: ${health.verses.toLocaleString()} verses across ${health.chapters} chapters ` +
        `· ${health.cached_questions} questions cached`,
      false,
    );
  })
  .catch(() => showStatus("service unavailable", true));
