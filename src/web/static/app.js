let opinions = [];
let choices = [];
let currentRound = 0;

const tierColors = ['text-teal-400', 'text-emerald-300', 'text-amber-400', 'text-orange-300', 'text-red-400'];

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => {
    s.classList.add('hidden');
  });
  const el = document.getElementById(id);
  el.classList.remove('hidden');
  window.scrollTo(0, 0);
}

// --- Chart ---
function drawHistogram(canvasId, scores, highlightScore) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  const w = rect.width;
  const h = rect.height;

  const bins = new Array(11).fill(0);
  scores.forEach(s => {
    const bin = Math.min(10, Math.max(0, Math.round(s)));
    bins[bin]++;
  });
  const maxCount = Math.max(...bins, 1);

  const barGap = 4;
  const totalGaps = barGap * 12;
  const barWidth = (w - totalGaps) / 11;
  const chartBottom = h - 20;
  const chartTop = 4;
  const chartHeight = chartBottom - chartTop;

  ctx.clearRect(0, 0, w, h);

  bins.forEach((count, i) => {
    const x = barGap + i * (barWidth + barGap);
    const barH = maxCount > 0 ? (count / maxCount) * chartHeight : 0;
    const y = chartBottom - barH;
    const isHighlight = highlightScore !== undefined && Math.round(highlightScore) === i;

    ctx.shadowBlur = 0;
    if (isHighlight) {
      ctx.fillStyle = '#6c5ce7';
      ctx.shadowColor = 'rgba(108,92,231,0.5)';
      ctx.shadowBlur = 8;
    } else {
      ctx.fillStyle = count > 0 ? '#374151' : '#1f2937';
    }

    const r = Math.min(3, barWidth / 2);
    if (barH > r) {
      ctx.beginPath();
      ctx.moveTo(x, chartBottom);
      ctx.lineTo(x, y + r);
      ctx.arcTo(x, y, x + r, y, r);
      ctx.arcTo(x + barWidth, y, x + barWidth, y + r, r);
      ctx.lineTo(x + barWidth, chartBottom);
      ctx.closePath();
      ctx.fill();
    } else {
      ctx.fillRect(x, chartBottom - Math.max(barH, 2), barWidth, Math.max(barH, 2));
    }

    ctx.shadowBlur = 0;
    ctx.fillStyle = isHighlight ? '#e5e7eb' : '#6b7280';
    ctx.font = '10px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(i.toString(), x + barWidth / 2, h - 4);
  });
}

// --- Landing ---
async function loadScores() {
  try {
    const res = await fetch('/api/scores');
    const data = await res.json();
    const scores = data.scores || [];
    const emptyEl = document.getElementById('landing-empty');
    if (scores.length === 0) {
      emptyEl.style.display = 'flex';
    } else {
      emptyEl.style.display = 'none';
      drawHistogram('landing-canvas', scores);
    }
  } catch {
    const emptyEl = document.getElementById('landing-empty');
    emptyEl.classList.remove('hidden');
    emptyEl.textContent = "Couldn't load scores";
  }
}

function startQuiz() {
  showScreen('opinions');
  buildOpinionInputs();
}

// --- Opinions ---
function buildOpinionInputs() {
  const container = document.getElementById('opinion-inputs');
  container.innerHTML = '';
  for (let i = 0; i < 5; i++) {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-2';
    row.innerHTML = `
      <span class="text-xs font-bold text-brand w-5 text-center shrink-0">${i + 1}</span>
      <input type="text" placeholder="Type an opinion…" maxlength="300" data-idx="${i}"
        class="flex-1 min-w-0 px-3 py-2.5 bg-gray-900 border border-gray-800 rounded-lg text-gray-100 text-sm placeholder-gray-600 focus:outline-none focus:border-brand" />
    `;
    container.appendChild(row);
  }
  container.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('input', checkAllFilled);
  });
  container.querySelector('input').focus();
}

function checkAllFilled() {
  const inputs = document.querySelectorAll('#opinion-inputs input');
  const filled = [...inputs].every(i => i.value.trim().length > 0);
  document.getElementById('begin-btn').disabled = !filled;
}

function beginQuiz() {
  const inputs = document.querySelectorAll('#opinion-inputs input');
  opinions = [...inputs].map(i => i.value.trim());
  choices = [];
  currentRound = 0;
  showScreen('quiz');
  loadRound();
}

// --- Quiz ---
async function loadRound() {
  const optionsEl = document.getElementById('response-options');
  const spinnerEl = document.getElementById('loading-spinner');
  optionsEl.innerHTML = '';
  spinnerEl.classList.remove('hidden');

  document.getElementById('round-label').textContent = `Opinion ${currentRound + 1} of 5`;
  document.getElementById('current-opinion').textContent = `"${opinions[currentRound]}"`;
  document.getElementById('progress-fill').style.width = `${(currentRound / 5) * 100}%`;

  try {
    const res = await fetch('/api/generate-responses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ opinion: opinions[currentRound] }),
    });
    const data = await res.json();
    spinnerEl.classList.add('hidden');

    if (data.error) {
      optionsEl.innerHTML = `<p class="text-red-400 text-sm text-center">Error: ${data.error}</p>`;
      return;
    }

    data.responses.forEach((resp, idx) => {
      const btn = document.createElement('button');
      btn.className = 'w-full text-left px-3 py-3 bg-gray-900 border border-gray-800 rounded-lg transition-colors hover:bg-gray-800 hover:border-brand active:bg-gray-800';
      btn.innerHTML = `<span class="block text-[10px] font-bold uppercase tracking-wider mb-0.5 ${tierColors[idx]}">${resp.label}</span><span class="text-sm text-gray-200 leading-snug">${resp.text}</span>`;
      btn.onclick = () => selectOption(idx);
      optionsEl.appendChild(btn);
    });
  } catch {
    spinnerEl.classList.add('hidden');
    optionsEl.innerHTML = `<p class="text-red-400 text-sm text-center">Something went wrong. Please try again.</p>`;
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
  showScreen('results');
  document.getElementById('progress-fill').style.width = '100%';

  try {
    const res = await fetch('/api/submit-score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ choices }),
    });
    const data = await res.json();

    animateNumber(document.getElementById('score-number'), data.score);
    document.getElementById('score-description').textContent = getScoreDescription(data.score);
    drawHistogram('results-canvas', data.all_scores, data.score);
    document.getElementById('scoring-method').textContent = data.scoring_method;
  } catch {
    document.getElementById('score-description').textContent = 'Error submitting score. Your data was not saved.';
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
  if (score >= 9) return 'You strongly prefer when others agree with you.';
  if (score >= 7) return 'You lean toward agreement but can handle some pushback.';
  if (score >= 5) return "You're balanced — you appreciate both agreement and honesty.";
  if (score >= 3) return 'You prefer people to push back and challenge your ideas.';
  return 'You strongly prefer direct, unfiltered disagreement.';
}

function resetQuiz() {
  opinions = [];
  choices = [];
  currentRound = 0;
  showScreen('landing');
  loadScores();
}

// Init
loadScores();
