// Shared helpers for Gauntlet's pages. Server values are always set as text, never as HTML,
// because agent replies are untrusted.

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

const KIND_LABELS = {
  passed: "passed",
  failed: "failed",
  errored: "errored",
  needs_review: "needs review",
  reviewed: "reviewed",
};

function kindChip(outcome) {
  let style = outcome.kind;
  if (outcome.kind === "reviewed") style = outcome.passed ? "passed" : "failed";
  return el("span", { class: `chip ${style}` }, KIND_LABELS[outcome.kind]);
}

function detailOf(outcome) {
  if (outcome.kind === "passed") return "";
  if (outcome.kind === "errored") return outcome.message;
  if (outcome.kind === "reviewed") {
    return `${outcome.passed ? "passed" : "failed"} by a person: ${outcome.note}`;
  }
  return outcome.reason;
}

function totals(view) {
  const scored = view.cases.filter((c) => c.score);
  return {
    finished: scored.length,
    total: view.cases.length,
    passesAll: scored.filter((c) => c.score.passes_all).length,
    passesSome: scored.filter((c) => c.score.passes_some).length,
  };
}
