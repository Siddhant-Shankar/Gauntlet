// Shared helpers and components for Gauntlet's pages. Each component is a function that returns
// a DOM node. Server values are always set as text, never as HTML, because agent replies are
// untrusted.

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && body.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || response.statusText));
  }
  return body;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// "claude-sonnet-5-5" reads as "Sonnet 5.5" and "claude-haiku-4-5-20251001" as "Haiku 4.5";
// any other name is shown as it is.
function agentLabel(name) {
  const parts = name.split("-");
  if (parts[0] !== "claude" || parts.length < 3) return name;
  const version = parts.slice(2).filter((part) => part.length < 8); // drops a date like 20251001
  return `${parts[1][0].toUpperCase()}${parts[1].slice(1)} ${version.join(".")}`;
}

function timeAgo(iso) {
  const minutes = Math.round((Date.now() - new Date(iso)) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} h ago`;
  return new Date(iso).toLocaleDateString();
}

function statusChip(status) {
  let style = "failed";
  if (status === "RUNNING") style = "running";
  else if (status === "COMPLETED") style = "completed";
  return el("span", { class: `status ${style}` }, status.toLowerCase().replaceAll("_", " "));
}

const KIND_LABELS = {
  passed: "passed",
  failed: "failed",
  errored: "errored",
  needs_review: "needs review",
  reviewed: "reviewed",
};

// A person's verdict is drawn like a check's, so "reviewed" takes the colour of its decision.
function outcomeStyle(outcome) {
  if (outcome.kind === "reviewed") return outcome.passed ? "passed" : "failed";
  return outcome.kind;
}

const GLYPHS = { passed: "✓", failed: "✗", errored: "!", needs_review: "?" };

function kindChip(outcome) {
  return el("span", { class: `chip ${outcomeStyle(outcome)}` }, KIND_LABELS[outcome.kind]);
}

function detailOf(outcome) {
  if (outcome.kind === "passed") return "";
  if (outcome.kind === "errored") return outcome.message;
  if (outcome.kind === "reviewed") {
    return `${outcome.passed ? "passed" : "failed"} by a person: ${outcome.note}`;
  }
  return outcome.reason;
}

function attemptDot(attempt) {
  const style = outcomeStyle(attempt.outcome);
  const byPerson = attempt.outcome.kind === "reviewed" ? " by-person" : "";
  return el("span", {
    class: `dot ${style}${byPerson}`,
    title: `Attempt ${attempt.number}: ${KIND_LABELS[attempt.outcome.kind]}`,
  }, GLYPHS[style]);
}

// One square per attempt; dashed squares are attempts that haven't run yet.
function attemptDots(attempts, k) {
  const done = (attempts || []).map(attemptDot);
  const waiting = Array.from({ length: k - done.length }, () =>
    el("span", { class: "dot pending", title: "not run yet" }));
  return el("span", { class: "dots" }, done, waiting);
}

// How one case did: green if every attempt passed, amber if only some did, red if none did.
function scoreBadge(caseView) {
  const { attempts, score } = caseView;
  if (attempts && attempts.some((a) => a.outcome.kind === "needs_review")) {
    return el("span", { class: "score review" }, "review");
  }
  if (!score) return el("span", { class: "score waiting" }, "running");
  let style = "none";
  if (score.passes_all) style = "all";
  else if (score.passes_some) style = "some";
  return el("span", { class: `score ${style}` }, `${score.passed}/${score.k}`);
}

function meter(part, whole, style = "") {
  const percent = whole ? Math.round((100 * part) / whole) : 0;
  return el("div", { class: `meter ${style}` }, el("span", { style: `width: ${percent}%` }));
}

function statTile(label, part, whole, note, style) {
  return el("div", { class: "stat" },
    el("span", { class: "stat-label" }, label),
    el("span", { class: "stat-value" }, part, el("span", { class: "of" }, ` / ${whole}`)),
    meter(part, whole, style),
    el("span", { class: "stat-note" }, note),
  );
}

function legendItem(swatch, text) {
  return el("span", { class: "legend-item" }, swatch, text);
}

function attemptLegend() {
  const dot = (style, glyph) => el("span", { class: `dot ${style}` }, glyph);
  return el("div", { class: "legend", "aria-label": "What the squares mean" },
    legendItem(dot("passed", GLYPHS.passed), "passed"),
    legendItem(dot("failed", GLYPHS.failed), "failed"),
    legendItem(dot("errored", GLYPHS.errored), "errored: no answer came back"),
    legendItem(dot("needs_review", GLYPHS.needs_review), "needs a person"),
    legendItem(el("span", { class: "dot passed by-person" }, GLYPHS.passed), "decided by a person"),
    legendItem(dot("pending", ""), "not run yet"),
  );
}

function scoreLegend() {
  const badge = (style, text) => el("span", { class: `score ${style}` }, text);
  return el("div", { class: "legend" },
    legendItem(badge("all", "3/3"), "every attempt passed"),
    legendItem(badge("some", "2/3"), "flaky: only some passed"),
    legendItem(badge("none", "0/3"), "no attempt passed"),
    legendItem(badge("review", "review"), "waiting for a person"),
  );
}

function totals(view) {
  const scored = view.cases.filter((c) => c.score);
  const passesAll = scored.filter((c) => c.score.passes_all).length;
  const passesSome = scored.filter((c) => c.score.passes_some).length;
  const waitingForReview = view.cases
    .flatMap((c) => c.attempts || [])
    .filter((a) => a.outcome.kind === "needs_review").length;
  return {
    finished: scored.length,
    total: view.cases.length,
    passesAll,
    passesSome,
    flaky: passesSome - passesAll,
    waitingForReview,
  };
}
