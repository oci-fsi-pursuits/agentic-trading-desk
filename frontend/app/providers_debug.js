function byId(id) {
  return document.getElementById(id);
}

function pretty(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function setStatus(message, isError = false) {
  const node = byId("status");
  node.textContent = message;
  node.style.color = isError ? "#b23333" : "#5f6d84";
}

function renderGrid(gridId, entries) {
  const grid = byId(gridId);
  if (!grid) return;
  const items = entries || [];
  if (!items.length) {
    grid.innerHTML = '<article class="card"><div class="title">No data</div><pre>Empty</pre></article>';
    return;
  }
  grid.innerHTML = items
    .map((item) => {
      const title = String(item.title || "Untitled");
      const content = pretty(item.content || {});
      return `<article class="card"><div class="title">${title}</div><pre>${escapeHtml(content)}</pre></article>`;
    })
    .join("");
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function loadSnapshot() {
  const ticker = String(byId("ticker")?.value || "").trim().toUpperCase() || "NVDA";
  const scenarioId = String(byId("scenarioId")?.value || "").trim() || "single_name_earnings";
  const maxWords = Number(byId("maxWords")?.value || 220);
  const includeStatements = Boolean(byId("includeStatements")?.checked);
  const params = new URLSearchParams({
    ticker,
    scenario_id: scenarioId,
    max_words: String(Number.isFinite(maxWords) ? maxWords : 220),
    include_statements: includeStatements ? "1" : "0",
  });

  setStatus(`Loading ${ticker} ...`);
  try {
    const response = await fetch(`/api/debug/providers?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.message || payload?.error || "request_failed");
    }

    byId("providerChains").textContent = pretty(payload.provider_chains || {});
    byId("liveContext").textContent = pretty(payload.live_context_summary || {});

    const providerEntries = Object.entries(payload.providers || {}).map(([key, value]) => ({
      title: key,
      content: value,
    }));
    renderGrid("providersGrid", providerEntries);

    const toolEntries = Object.entries(payload.tool_outputs || {}).map(([key, value]) => ({
      title: key,
      content: value,
    }));
    renderGrid("toolsGrid", toolEntries);

    const promptEntries = Object.entries(payload.prompt_preview || {}).map(([roleId, value]) => ({
      title: roleId,
      content: value,
    }));
    renderGrid("promptsGrid", promptEntries);

    setStatus(`Loaded ${ticker} at ${payload.generated_at || "unknown time"}`);
  } catch (error) {
    setStatus(`Failed: ${error}`, true);
  }
}

function init() {
  const button = byId("loadBtn");
  if (button) {
    button.addEventListener("click", () => {
      loadSnapshot();
    });
  }
  loadSnapshot();
}

init();
