async function getJSON(path) {
  const res = await fetch(path, { headers: { "accept": "application/json" } });
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = { raw: text }; }
  return { ok: res.ok, status: res.status, json };
}

async function postJSON(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "accept": "application/json", "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { json = { raw: text }; }
  return { ok: res.ok, status: res.status, json };
}

const out = document.getElementById("out");
const btn = document.getElementById("check");
const recommendBtn = document.getElementById("recommend");
const q = document.getElementById("q");
const resultsEl = document.getElementById("results");
const toggleJsonBtn = document.getElementById("toggle-json");
const statusText = document.getElementById("status-text");

let jsonExpanded = false;
let carouselItems = [];

function setJsonExpanded(expanded) {
  jsonExpanded = expanded;
  out.classList.toggle("hidden", !expanded);
  toggleJsonBtn.textContent = expanded ? "Hide JSON response" : "Show JSON response";
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function optimizeImdbImageUrl(url) {
  if (!url || typeof url !== "string") return "";
  // Convert heavy default URL variants into smaller widths for faster loading.
  // Typical IMDb pattern: "...jpg._V1_.jpg" or "..._V1_.jpg"
  return url
    .replace("._V1_.jpg", "._V1_UX360_.jpg")
    .replace("_V1_.jpg", "_V1_UX360_.jpg");
}

function renderCarousel() {
  if (!carouselItems.length) {
    resultsEl.innerHTML = '<p class="muted">No results returned.</p>';
    return;
  }
  const cards = carouselItems.map((item, idx) => {
    const genres = Array.isArray(item.genres) ? item.genres.join(", ") : "";
    const score = typeof item.score === "number" ? item.score.toFixed(4) : "n/a";
    const year = item.start_year || "";
    const title = item.primary_title || "Untitled";
    const img = optimizeImdbImageUrl(item.image_url || "");
    const plot = item.plot || "";
    return `
      <div class="rec-card">
        ${img ? `<img class="rec-image" src="${escapeHtml(img)}" alt="${escapeHtml(title)}" loading="lazy" decoding="async" fetchpriority="low" referrerpolicy="no-referrer">` : '<div class="rec-image"></div>'}
        <strong>#${idx + 1} ${escapeHtml(title)}</strong>
        <div class="rec-meta">${escapeHtml(year)} · ${escapeHtml(genres)}</div>
        <div class="rec-meta"><code>score=${escapeHtml(score)}</code> <code>id=${escapeHtml(item.imdb_id || "")}</code></div>
        <p class="rec-plot">${escapeHtml(plot)}</p>
      </div>
    `;
  }).join("");
  resultsEl.innerHTML = `
    <div class="carousel-wrap">
      <div class="carousel-nav">
        <button id="prev-rec">⬅ Left</button>
        <strong>${carouselItems.length} recommendations</strong>
        <button id="next-rec">Right ➡</button>
      </div>
      <div id="carousel-track" class="carousel-track">${cards}</div>
    </div>
  `;

  const track = document.getElementById("carousel-track");
  const prev = document.getElementById("prev-rec");
  const next = document.getElementById("next-rec");
  prev?.addEventListener("click", () => {
    if (!track) return;
    track.scrollBy({ left: -Math.max(280, track.clientWidth * 0.9), behavior: "smooth" });
  });
  next?.addEventListener("click", () => {
    if (!track) return;
    track.scrollBy({ left: Math.max(280, track.clientWidth * 0.9), behavior: "smooth" });
  });
}

setJsonExpanded(false);
toggleJsonBtn.addEventListener("click", () => setJsonExpanded(!jsonExpanded));
statusText.textContent = "Ready.";

btn.addEventListener("click", async () => {
  statusText.textContent = "Checking service readiness…";
  out.textContent = "Checking /readyz …";
  const r = await getJSON("/readyz");
  out.textContent = JSON.stringify(r, null, 2);
  statusText.textContent = r.ok ? "Service is ready." : "Service is not ready.";
});

recommendBtn.addEventListener("click", async () => {
  const query = (q.value || "").trim();
  if (!query) {
    out.textContent = "Please enter a query text first.";
    statusText.textContent = "Please enter a query first.";
    return;
  }

  statusText.textContent = "Running pipeline… this may take a few seconds on first run.";
  out.textContent = "Running recommendation pipeline…";
  carouselItems = [];
  resultsEl.innerHTML = "";

  const r = await postJSON("/api/recommend", { query, k: 5 });
  out.textContent = JSON.stringify(r, null, 2);

  if (!r.ok || !r.json || !Array.isArray(r.json.items)) {
    statusText.textContent = "Request failed. Check JSON response for details.";
    return;
  }
  carouselItems = r.json.items;
  renderCarousel();
  statusText.textContent = `Done. Received ${carouselItems.length} recommendation(s).`;
});

