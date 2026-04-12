// State
let messages = []; // {role, content} for API
let turnNumber = 0;
let turnData = []; // {gap_surfaced, user_was_thoughtful}
let chatActive = false;

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => {
    s.classList.add('hidden');
    if (s.style.display) s.style.display = 'none';
  });
  const el = document.getElementById(id);
  el.classList.remove('hidden');
  if (id === 'chat') {
    el.style.display = 'flex';
  } else {
    el.style.display = '';
  }
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
  const gap = 4, barW = (w - gap * 12) / 11, bottom = h - 18, chartH = bottom - 4;

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
    const el = document.getElementById('landing-empty');
    if (el) el.style.display = 'flex';
  }
}

// Chat
function startChat() {
  messages = [];
  turnNumber = 0;
  turnData = [];
  chatActive = true;
  document.getElementById('chat-messages').innerHTML = '';
  showScreen('chat');

  const input = document.getElementById('chat-input');
  input.value = '';
  input.disabled = true;
  document.getElementById('send-btn').disabled = true;

  // First turn: AI starts the conversation
  sendToAI();
}

function addMessage(role, text) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = role === 'assistant'
    ? 'mb-3 mr-12'
    : 'mb-3 ml-12';

  const bubble = document.createElement('div');
  bubble.style.cssText = role === 'assistant'
    ? 'padding:0.75rem 1rem; background:#1f2937; border-radius:0.75rem 0.75rem 0.75rem 0.25rem; font-size:0.875rem; line-height:1.5; color:#e5e7eb;'
    : 'padding:0.75rem 1rem; background:#6c5ce7; border-radius:0.75rem 0.75rem 0.25rem 0.75rem; font-size:0.875rem; line-height:1.5; color:white;';
  bubble.textContent = text;
  div.appendChild(bubble);
  container.appendChild(div);
  scrollChatToBottom();
  return div;
}

function showTyping() {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.id = 'typing-indicator';
  div.className = 'mb-3 mr-12';
  div.innerHTML = '<div style="padding:0.75rem 1rem; background:#1f2937; border-radius:0.75rem 0.75rem 0.75rem 0.25rem; display:inline-flex; gap:4px"><span class="typing-dot" style="width:6px;height:6px;background:#6b7280;border-radius:50%;display:block"></span><span class="typing-dot" style="width:6px;height:6px;background:#6b7280;border-radius:50%;display:block"></span><span class="typing-dot" style="width:6px;height:6px;background:#6b7280;border-radius:50%;display:block"></span></div>';
  container.appendChild(div);
  scrollChatToBottom();
}

function hideTyping() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

async function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text || !chatActive) return;

  input.value = '';
  input.style.height = 'auto';
  input.disabled = true;
  document.getElementById('send-btn').disabled = true;

  addMessage('user', text);
  messages.push({ role: 'user', content: text });

  await sendToAI();
}

async function sendToAI() {
  turnNumber++;
  showTyping();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages,
        turn_number: turnNumber,
      }),
    });
    const data = await res.json();
    hideTyping();

    if (data.error) {
      addMessage('assistant', 'Something went wrong. Try refreshing.');
      return;
    }

    // Show AI message
    addMessage('assistant', data.message);
    messages.push({ role: 'assistant', content: data.message });

    if (data.is_final) {
      chatActive = false;
      document.getElementById('chat-input').disabled = true;
      document.getElementById('send-btn').disabled = true;

      // Show "see results" button after a pause
      setTimeout(() => {
        const container = document.getElementById('chat-messages');
        const div = document.createElement('div');
        div.className = 'mt-4 mb-20';
        div.innerHTML = '<button onclick="showResults()" class="w-full py-3 bg-brand hover:bg-brand-light text-white font-semibold rounded-xl">See your score</button>';
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
      }, 1500);
    } else {
      // Enable input
      const input = document.getElementById('chat-input');
      input.disabled = false;
      document.getElementById('send-btn').disabled = false;
      input.focus();
    }
  } catch {
    hideTyping();
    addMessage('assistant', 'Connection error. Try again.');
    document.getElementById('chat-input').disabled = false;
    document.getElementById('send-btn').disabled = false;
  }
}

// Enable send button when input has text
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('chat-input');
  if (input) {
    input.addEventListener('input', () => {
      document.getElementById('send-btn').disabled = !input.value.trim() || !chatActive;
    });
  }
});

// Results
async function showResults() {
  showScreen('results');
  document.getElementById('results-loading').style.display = 'block';
  document.getElementById('results-content').style.display = 'none';

  try {
    const res = await fetch('/api/submit-score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript: messages }),
    });
    const data = await res.json();

    document.getElementById('results-loading').style.display = 'none';
    document.getElementById('results-content').style.display = 'block';
    animateNumber(document.getElementById('score-number'), data.score);
    document.getElementById('score-description').textContent = data.reasoning || getScoreDescription(data.score);
    setTimeout(() => drawHistogram('results-canvas', data.all_scores, data.score), 100);
    document.getElementById('scoring-method').textContent = data.scoring_method;
  } catch {
    document.getElementById('results-loading').style.display = 'none';
    document.getElementById('results-content').style.display = 'block';
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
  if (s >= 8) return 'You engaged like you would with a real friend — openly, honestly, even when it got uncomfortable.';
  if (s >= 6) return 'You were mostly open to the hard questions. More real friend than sycophantic computer.';
  if (s >= 4) return 'A mix — you engaged with some of the harder moments but moved past others. Most people land here.';
  if (s >= 2) return 'You tended toward comfortable territory. A sycophantic computer would have kept you there.';
  return 'You stayed on the surface. A real friend would have pushed harder — and you might have let them.';
}

function resetAll() {
  messages = [];
  turnNumber = 0;
  turnData = [];
  chatActive = false;
  showScreen('landing');
  loadScores();
}

// Scroll chat to bottom helper
function scrollChatToBottom() {
  const container = document.getElementById('chat-messages');
  if (container) setTimeout(() => { container.scrollTop = container.scrollHeight; }, 50);
}

// Resize chat container when mobile keyboard appears/disappears
if (window.visualViewport) {
  const resizeChat = () => {
    const chat = document.getElementById('chat');
    if (chat && chat.style.display === 'flex') {
      chat.style.height = window.visualViewport.height + 'px';
      chat.style.bottom = 'auto';
      scrollChatToBottom();
    }
  };
  window.visualViewport.addEventListener('resize', resizeChat);
  window.visualViewport.addEventListener('scroll', resizeChat);
}

// Scroll to input on focus
document.addEventListener('focusin', (e) => {
  if (e.target && e.target.id === 'chat-input') {
    setTimeout(() => {
      e.target.scrollIntoView({ block: 'end', behavior: 'smooth' });
      scrollChatToBottom();
    }, 300);
  }
});

loadScores();
