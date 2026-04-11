let prompts = [];
let currentRound = 0;
let choices = []; // "warm" or "honest"
let currentWarmIs = null;

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.add('hidden'));
  document.getElementById(id).classList.remove('hidden');
  window.scrollTo(0, 0);
}

// Chart
function drawHistogram(canvasId, scores, highlightScore) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const parent = canvas.parentElement;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = parent.clientWidth - 24;
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
  const chartH = bottom - 4;

  ctx.clearRect(0, 0, w, h);
  bins.forEach((count, i) => {
    const x = gap + i * (barW + gap);
    const barH = (count / maxCount) * chartH;
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
    if (emptyEl) { emptyEl.style.display = 'flex'; }
  }
}

async function startQuiz() {
  try {
    const res = await fetch('/api/prompts');
    const data = await res.json();
    prompts = data.prompts;
  } catch {
    prompts = [
      "What's a food opinion you'll defend?",
      "What's a life decision others questioned?",
      "What are you working on that matters to you?",
      "What belief do you hold that others disagree with?",
      "What about yourself are you trying to change?",
    ];
  }
  currentRound = 0;
  choices = [];
  showScreen('quiz');
  showInputPhase();
}

// Quiz flow
function showInputPhase() {
  document.getElementById('input-phase').classList.remove('hidden');
  document.getElementById('choice-phase').classList.add('hidden');
  document.getElementById('loading-spinner').classList.add('hidden');

  document.getElementById('round-label').textContent = `Round ${currentRound + 1} of 5`;
  document.getElementById('progress-fill').style.width = `${(currentRound / 5) * 100}%`;
  document.getElementById('round-prompt').textContent = prompts[currentRound];

  const textarea = document.getElementById('user-input');
  textarea.value = '';
  textarea.focus();

  const btn = document.getElementById('submit-input-btn');
  btn.disabled = true;
  textarea.oninput = () => { btn.disabled = textarea.value.trim().length === 0; };
}

async function submitInput() {
  const textarea = document.getElementById('user-input');
  const opinion = textarea.value.trim();
  if (!opinion) return;

  document.getElementById('input-phase').classList.add('hidden');
  document.getElementById('loading-spinner').classList.remove('hidden');

  try {
    const res = await fetch('/api/generate-pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ opinion, round: currentRound }),
    });
    const data = await res.json();

    if (data.error) {
      document.getElementById('loading-spinner').classList.add('hidden');
      document.getElementById('input-phase').classList.remove('hidden');
      alert('Error: ' + data.error);
      return;
    }

    currentWarmIs = data.warm_is;
    document.getElementById('option-a').textContent = data.a;
    document.getElementById('option-b').textContent = data.b;

    document.getElementById('loading-spinner').classList.add('hidden');
    document.getElementById('choice-phase').classList.remove('hidden');
  } catch {
    document.getElementById('loading-spinner').classList.add('hidden');
    document.getElementById('input-phase').classList.remove('hidden');
    alert('Something went wrong. Try again.');
  }
}

function pickChoice(letter) {
  const pickedWarm = (letter === currentWarmIs);
  choices.push(pickedWarm ? 'warm' : 'honest');

  currentRound++;
  if (currentRound < 5) {
    showInputPhase();
  } else {
    submitScore();
  }
}

// Results
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
    el.textContent = ((1 - Math.pow(1 - p, 3)) * target).toFixed(1);
    if (p < 1) requestAnimationFrame(tick);
  })(start);
}

function getScoreDescription(s) {
  if (s >= 9) return 'You consistently chose comfort, even when the stakes were high.';
  if (s >= 7) return 'You lean toward validation. The honest responses felt harder to pick as things got personal.';
  if (s >= 5) return 'You are right in the middle. You picked comfort on some rounds and truth on others.';
  if (s >= 3) return 'You lean toward honesty. You chose the harder response even when it was personal.';
  return 'You consistently chose truth over comfort, even at the highest stakes.';
}

function resetQuiz() {
  currentRound = 0;
  choices = [];
  showScreen('landing');
  loadScores();
}

loadScores();
