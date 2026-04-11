let opinions = [];
let choices = [];
let currentRound = 0;

// --- Screens ---
function showScreen(id) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
  window.scrollTo(0, 0);
}

// --- Chart drawing (simple canvas histogram) ---
function drawHistogram(canvasId, scores, highlightScore) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width;
  const h = rect.height;

  // Bucket into 11 bins: 0, 1, 2, ... 10
  const bins = new Array(11).fill(0);
  scores.forEach((s) => {
    const bin = Math.min(10, Math.max(0, Math.round(s)));
    bins[bin]++;
  });
  const maxCount = Math.max(...bins, 1);

  const barGap = 6;
  const barWidth = (w - barGap * 12) / 11;
  const chartBottom = h - 24;
  const chartTop = 8;
  const chartHeight = chartBottom - chartTop;

  ctx.clearRect(0, 0, w, h);

  bins.forEach((count, i) => {
    const x = barGap + i * (barWidth + barGap);
    const barH = maxCount > 0 ? (count / maxCount) * chartHeight : 0;
    const y = chartBottom - barH;

    const isHighlight =
      highlightScore !== undefined &&
      Math.round(highlightScore) === i;

    if (isHighlight) {
      ctx.fillStyle = "#6c5ce7";
      ctx.shadowColor = "rgba(108, 92, 231, 0.5)";
      ctx.shadowBlur = 12;
    } else {
      ctx.fillStyle = count > 0 ? "#2a2a3d" : "#1a1a28";
      ctx.shadowBlur = 0;
    }

    // Round-top bars
    const r = Math.min(4, barWidth / 2);
    if (barH > r) {
      ctx.beginPath();
      ctx.moveTo(x, chartBottom);
      ctx.lineTo(x, y + r);
      ctx.arcTo(x, y, x + r, y, r);
      ctx.arcTo(x + barWidth, y, x + barWidth, y + r, r);
      ctx.lineTo(x + barWidth, chartBottom);
      ctx.closePath();
      ctx.fill();
    } else if (barH > 0) {
      ctx.fillRect(x, y, barWidth, barH);
    } else {
      // Empty bin — draw a tiny placeholder
      ctx.fillRect(x, chartBottom - 2, barWidth, 2);
    }

    ctx.shadowBlur = 0;

    // Label
    ctx.fillStyle = isHighlight ? "#e8e8f0" : "#6a6a80";
    ctx.font = "11px -apple-system, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(i.toString(), x + barWidth / 2, h - 4);
  });
}

// --- Landing ---
async function loadScores() {
  try {
    const res = await fetch("/api/scores");
    const data = await res.json();
    const scores = data.scores || [];
    if (scores.length === 0) {
      document.getElementById("landing-empty").style.display = "block";
    } else {
      document.getElementById("landing-empty").style.display = "none";
      drawHistogram("landing-canvas", scores);
    }
  } catch (e) {
    document.getElementById("landing-empty").style.display = "block";
    document.getElementById("landing-empty").textContent =
      "Couldn't load scores";
  }
}

function startQuiz() {
  showScreen("opinions");
  buildOpinionInputs();
}

// --- Opinion inputs ---
function buildOpinionInputs() {
  const container = document.getElementById("opinion-inputs");
  container.innerHTML = "";
  for (let i = 0; i < 5; i++) {
    const row = document.createElement("div");
    row.className = "opinion-row";
    row.innerHTML = `
      <span class="opinion-num">${i + 1}</span>
      <input type="text" placeholder="Type an opinion…" maxlength="300"
             data-idx="${i}" />
    `;
    container.appendChild(row);
  }
  container.querySelectorAll("input").forEach((inp) => {
    inp.addEventListener("input", checkAllFilled);
  });
  container.querySelector("input").focus();
}

function checkAllFilled() {
  const inputs = document.querySelectorAll("#opinion-inputs input");
  const filled = [...inputs].every((i) => i.value.trim().length > 0);
  document.getElementById("begin-btn").disabled = !filled;
}

function beginQuiz() {
  const inputs = document.querySelectorAll("#opinion-inputs input");
  opinions = [...inputs].map((i) => i.value.trim());
  choices = [];
  currentRound = 0;
  showScreen("quiz");
  loadRound();
}

// --- Quiz rounds ---
async function loadRound() {
  const optionsEl = document.getElementById("response-options");
  const spinnerEl = document.getElementById("loading-spinner");
  optionsEl.innerHTML = "";
  spinnerEl.style.display = "block";

  document.getElementById("round-label").textContent = `Opinion ${currentRound + 1} of 5`;
  document.getElementById("current-opinion").textContent = `"${opinions[currentRound]}"`;
  document.getElementById("progress-fill").style.width = `${(currentRound / 5) * 100}%`;

  try {
    const res = await fetch("/api/generate-responses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ opinion: opinions[currentRound] }),
    });
    const data = await res.json();
    spinnerEl.style.display = "none";

    if (data.error) {
      optionsEl.innerHTML = `<p style="color:var(--red)">Error: ${data.error}</p>`;
      return;
    }

    data.responses.forEach((resp, idx) => {
      const btn = document.createElement("button");
      btn.className = "option-btn";
      btn.dataset.tier = idx;
      btn.innerHTML = `<span class="option-label">${resp.label}</span>${resp.text}`;
      btn.onclick = () => selectOption(idx);
      optionsEl.appendChild(btn);
    });
  } catch (e) {
    spinnerEl.style.display = "none";
    optionsEl.innerHTML = `<p style="color:var(--red)">Something went wrong. Please try again.</p>`;
  }
}

function selectOption(tierIndex) {
  choices.push(tierIndex);
  currentRound++;

  if (currentRound < 5) {
    loadRound();
  } else {
    submitScore();
  }
}

// --- Results ---
async function submitScore() {
  showScreen("results");
  document.getElementById("progress-fill").style.width = "100%";

  try {
    const res = await fetch("/api/submit-score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ choices }),
    });
    const data = await res.json();

    // Animate score
    const scoreEl = document.getElementById("score-number");
    animateNumber(scoreEl, data.score);

    // Description
    const desc = getScoreDescription(data.score);
    document.getElementById("score-description").textContent = desc;

    // Chart
    drawHistogram("results-canvas", data.all_scores, data.score);

    // Scoring method
    document.getElementById("scoring-method").textContent = data.scoring_method;
  } catch (e) {
    document.getElementById("score-description").textContent =
      "Error submitting score. Your data was not saved.";
  }
}

function animateNumber(el, target) {
  const duration = 1200;
  const start = performance.now();
  function tick(now) {
    const elapsed = now - start;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = (eased * target).toFixed(1);
    if (progress < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function getScoreDescription(score) {
  if (score >= 9) return "You strongly prefer when others agree with you.";
  if (score >= 7) return "You lean toward agreement but can handle some pushback.";
  if (score >= 5) return "You're balanced — you appreciate both agreement and honesty.";
  if (score >= 3) return "You prefer people to push back and challenge your ideas.";
  return "You strongly prefer direct, unfiltered disagreement.";
}

function resetQuiz() {
  opinions = [];
  choices = [];
  currentRound = 0;
  showScreen("landing");
  loadScores();
}

// --- Init ---
loadScores();
