let opinions = [];
let choices = [];
let currentRound = 0;

const tierColors = ['text-teal-400', 'text-emerald-300', 'text-amber-400', 'text-orange-300', 'text-red-400'];

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.add('hidden'));
  document.getElementById(id).classList.remove('hidden');
  window.scrollTo(0, 0);
}

// Chart (results only)
function drawHistogram(canvasId, scores, highlightScore) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const parent = canvas.parentElement;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = parent.clientWidth - 24; // account for padding
  const h = parent.clientHeight - 24;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  canvas.style.width = w + 'px';
  canvas.style.height = h + 'px';
  ctx.scale(dpr, dpr);

  const bins = new Array(11).fill(0);
  scores.forEach(s => bins[Math.min(10, Math.max(0, Math.round(s)))]++);
  const maxCount = Math.max(...bins, 1);

  const gap = 4;
  const barW = (w - gap * 12) / 11;
  const bottom = h - 18;
  const top = 4;
  const chartH = bottom - top;

  ctx.clearRect(0, 0, w, h);
  bins.forEach((count, i) => {
    const x = gap + i * (barW + gap);
    const barH = (count / maxCount) * chartH;
    const y = bottom - barH;
    const isHl = highlightScore !== undefined && Math.round(highlightScore) === i;

    ctx.shadowBlur = 0;
    ctx.fillStyle = isHl ? '#6c5ce7' : (count > 0 ? '#374151' : '#1f2937');
    if (isHl) { ctx.shadowColor = 'rgba(108,92,231,0.5)'; ctx.shadowBlur = 8; }

    ctx.fillRect(x, bottom - Math.max(barH, 2), barW, Math.max(barH, 2));
    ctx.shadowBlur = 0;

    ctx.fillStyle = isHl ? '#e5e7eb' : '#6b7280';
    ctx.font = '10px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(i.toString(), x + barW / 2, h - 2);
  });
}

// Landing
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
    if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = "Couldn't load scores"; }
  }
}

function startQuiz() {
  showScreen('opinions');
  buildOpinionInputs();
}

// Opinions
function buildOpinionInputs() {
  const container = document.getElementById('opinion-inputs');
  container.innerHTML = '';
  for (let i = 0; i < 5; i++) {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-2';
    row.innerHTML = `
      <span class="text-xs font-bold text-brand shrink-0" style="width:1.25rem; text-align:center">${i + 1}</span>
      <input type="text" placeholder="Type an idea…" maxlength="300" data-idx="${i}"
        style="flex:1; min-width:0; font-size:16px; padding:0.625rem 0.75rem; background:#111827; border:1px solid #1f2937; border-radius:0.5rem; color:#f3f4f6; outline:none" />
    `;
    container.appendChild(row);
  }
  container.querySelectorAll('input').forEach(inp => inp.addEventListener('input', checkAllFilled));
  container.querySelector('input').focus();
}

function checkAllFilled() {
  const inputs = document.querySelectorAll('#opinion-inputs input');
  document.getElementById('begin-btn').disabled = ![...inputs].every(i => i.value.trim().length > 0);
}

function beginQuiz() {
  opinions = [...document.querySelectorAll('#opinion-inputs input')].map(i => i.value.trim());
  choices = [];
  currentRound = 0;
  showScreen('quiz');
  loadRound();
}

// Quiz
async function loadRound() {
  const optionsEl = document.getElementById('response-options');
  const spinnerEl = document.getElementById('loading-spinner');
  optionsEl.innerHTML = '';
  spinnerEl.classList.remove('hidden');

  document.getElementById('round-label').textContent = `Idea ${currentRound + 1} of 5`;
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
    if (data.error) { optionsEl.innerHTML = `<p class="text-red-400 text-sm text-center">Error: ${data.error}</p>`; return; }

    data.responses.forEach((resp, idx) => {
      const btn = document.createElement('button');
      btn.style.cssText = 'display:block; width:100%; text-align:left; padding:0.75rem; background:#111827; border:1px solid #1f2937; border-radius:0.5rem; color:#e5e7eb; font-size:0.875rem; line-height:1.4; cursor:pointer; word-break:break-word';
      btn.innerHTML = `<span class="block text-[10px] font-bold uppercase tracking-wider mb-0.5 ${tierColors[idx]}">${resp.label}</span>${resp.text}`;
      btn.onclick = () => selectOption(idx);
      optionsEl.appendChild(btn);
    });
  } catch {
    spinnerEl.classList.add('hidden');
    optionsEl.innerHTML = `<p class="text-red-400 text-sm text-center">Something went wrong.</p>`;
  }
}

function selectOption(idx) {
  choices.push(idx);
  currentRound++;
  currentRound < 5 ? loadRound() : submitScore();
}

// Results
async function submitScore() {
  showScreen('results');
  try {
    const res = await fetch('/api/submit-score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ choices }),
    });
    const data = await res.json();
    animateNumber(document.getElementById('score-number'), data.score);
    document.getElementById('score-description').textContent = getScoreDescription(data.score);
    setTimeout(() => drawHistogram('results-canvas', data.all_scores, data.score), 100);
    document.getElementById('scoring-method').textContent = data.scoring_method;
  } catch {
    document.getElementById('score-description').textContent = 'Error submitting score.';
  }
}

function animateNumber(el, target) {
  const start = performance.now();
  (function tick(now) {
    const p = Math.min((now - start) / 1200, 1);
    el.textContent = (((1 - Math.pow(1 - p, 3)) * target)).toFixed(1);
    if (p < 1) requestAnimationFrame(tick);
  })(start);
}

function getScoreDescription(s) {
  if (s >= 9) return 'You strongly prefer encouragement — you want to hear your ideas are great.';
  if (s >= 7) return 'You lean toward encouragement but can handle some honest feedback.';
  if (s >= 5) return "You're balanced — you appreciate both support and honest pushback.";
  if (s >= 3) return 'You prefer honest feedback over encouragement, even if it stings.';
  return 'You strongly prefer blunt honesty — tell it like it is, no sugarcoating.';
}

function resetQuiz() {
  opinions = []; choices = []; currentRound = 0;
  showScreen('landing');
  loadScores();
}

loadScores();
