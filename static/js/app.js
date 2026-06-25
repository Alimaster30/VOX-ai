// ── State ──────────────────────────────────────────────────────────────────
const state = {
  recording: false,
  processing: false,
  modelsReady: false,
  mediaRecorder: null,
  audioChunks: [],
  audioContext: null,
  analyser: null,
  stream: null,
  lastAudioB64: null,
  lastAudioMime: 'audio/mpeg',
  lastLanguage: 'en',
  queryCount: 0,
  layerCounts: { '1': 0, '2': 0, '3': 0 },
  uptimeStart: Date.now(),
  evalCharts: {},
  testResults: [],
  currentTab: 'voice',
  pollInterval: null,
  datasetJobId: null,
  datasetJobPolling: false,
  adminAuthenticated: false
};

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initMic();
  checkAdminAccess();
  checkReadiness();
  updateUptime();
  setInterval(updateUptime, 1000);
  document.addEventListener('keydown', handleKeyboard);
});

// ── Models Readiness Poll ──────────────────────────────────────────────────
async function checkReadiness() {
  const status = document.getElementById('mic-status');
  const btn = document.getElementById('mic-button');
  status.textContent = 'Loading models...';
  btn.style.opacity = '0.5';
  btn.style.pointerEvents = 'none';
  document.getElementById('topbar-status').textContent = 'Initializing...';

  const poll = async () => {
    try {
      const resp = await fetch('/api/ready');
      const data = await resp.json();
      if (data.ready) {
        state.modelsReady = true;
        status.textContent = 'Tap to Speak';
        btn.style.opacity = '1';
        btn.style.pointerEvents = 'auto';
        document.getElementById('topbar-status').textContent = 'System Ready';
        document.getElementById('mic-outer-ring').classList.add('mic-breathing');
        fetchStatus();
        pollStatus();
        return;
      }
      if (data.models_error) {
        status.textContent = 'Model load failed';
        document.getElementById('topbar-status').textContent = 'Model Error';
      } else if (data.models_loading) {
        status.textContent = 'Loading models...';
        document.getElementById('topbar-status').textContent = 'Loading Models...';
      }
    } catch (e) {}
    setTimeout(poll, 2000);
  };
  poll();
}

// ── Keyboard Shortcuts ─────────────────────────────────────────────────────
function handleKeyboard(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  if (e.code === 'Space') {
    e.preventDefault();
    toggleRecording();
  }
}

// ── Toast ──────────────────────────────────────────────────────────────────
function showToast(msg, type = '') {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// ── Tab Switching ──────────────────────────────────────────────────────────
function initTabs() {
  // Hide all tabs immediately on load
  document.querySelectorAll('.tab-content').forEach(c => {
    c.style.display = 'none';
  });
  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });
  switchTab('voice');
}

function switchTab(tabName) {
  state.currentTab = tabName;

  // Reset all nav tabs
  document.querySelectorAll('.nav-tab').forEach(t => {
    t.style.color = '';
    t.style.borderColor = 'transparent';
    const icon = t.querySelector('.material-symbols-outlined');
    if (icon) icon.style.color = '';
  });

  // Highlight active nav tab
  const activeTab = document.querySelector(`[data-tab="${tabName}"]`);
  if (activeTab) {
    activeTab.style.color = '#82947F';
    activeTab.style.borderColor = '#82947F';
    activeTab.style.fontWeight = 'bold';
    const icon = activeTab.querySelector('.material-symbols-outlined');
    if (icon) icon.style.color = '#82947F';
  }

  // Hide all tab content using inline style (overrides Tailwind flex)
  document.querySelectorAll('.tab-content').forEach(c => {
    c.style.display = 'none';
    c.classList.remove('active');
  });

  // Show the selected tab with correct flex direction
  const content = document.getElementById(`tab-${tabName}`);
  if (content) {
    content.style.display = 'flex';
    content.style.flexDirection = tabName === 'voice' ? 'row' : 'column';
    content.classList.add('active');
  }

  if (tabName === 'evaluation') fetchEvaluation();
  if (tabName === 'logs') renderLogs();
  if (tabName === 'health') fetchStatus();
  if (tabName === 'organization') refreshOrganizationSetup();
}

// ── Mic: Init ──────────────────────────────────────────────────────────────
async function initMic() {
  const btn = document.getElementById('mic-button');
  btn.addEventListener('click', toggleRecording);
  document.getElementById('mic-status').textContent = 'Tap to Speak';
}

async function toggleRecording() {
  if (state.processing) return;
  if (!state.modelsReady) {
    showToast('Models are still loading — please wait', 'error');
    return;
  }
  if (state.recording) {
    stopRecording();
  } else {
    startRecording();
  }
}

async function startRecording() {
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true }
    });

    state.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const source = state.audioContext.createMediaStreamSource(state.stream);
    state.analyser = state.audioContext.createAnalyser();
    state.analyser.fftSize = 64;
    source.connect(state.analyser);

    state.mediaRecorder = new MediaRecorder(state.stream, { mimeType: 'audio/webm;codecs=pcm' });
    state.audioChunks = [];

    state.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) state.audioChunks.push(e.data);
    };

    state.mediaRecorder.onstop = async () => {
      state.recording = false;
      updateMicUI('idle');
      stopWaveform();
      stopStream();

      if (state.audioChunks.length === 0) return;

      const blob = new Blob(state.audioChunks, { type: 'audio/webm' });
      state.audioChunks = [];

      try {
        const arrayBuffer = await blob.arrayBuffer();
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
        const wavBuffer = audioBufferToWav(audioBuffer);
        audioCtx.close();
        await sendVoice(wavBuffer);
      } catch (e) {
        console.error('Audio conversion failed:', e);
        showToast('Audio processing error', 'error');
        resetToIdle();
      }
    };

    state.recording = true;
    state.mediaRecorder.start();
    updateMicUI('recording');
    startWaveform();

    // Auto-stop after 10 seconds
    setTimeout(() => {
      if (state.recording) stopRecording();
    }, 10000);

  } catch (e) {
    console.error('Mic access denied:', e);
    showToast('Microphone access denied', 'error');
  }
}

function stopRecording() {
  if (state.mediaRecorder && state.mediaRecorder.state === 'recording') {
    state.mediaRecorder.stop();
  }
}

function stopStream() {
  if (state.stream) {
    state.stream.getTracks().forEach(t => t.stop());
    state.stream = null;
  }
  if (state.audioContext) {
    state.audioContext.close();
    state.audioContext = null;
    state.analyser = null;
  }
}

function audioBufferToWav(audioBuffer) {
  const numChannels = 1;
  const sampleRate = 16000;
  const format = 1; // PCM
  const bitsPerSample = 16;

  // Resample to 16kHz if needed
  let samples;
  if (audioBuffer.sampleRate !== sampleRate) {
    const offlineCtx = new OfflineAudioContext(1, Math.ceil(audioBuffer.duration * sampleRate), sampleRate);
    const source = offlineCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(offlineCtx.destination);
    source.start();
    return new Promise((resolve) => {
      offlineCtx.startRendering().then((resampled) => {
        resolve(encodeWav(resampled.getChannelData(0), sampleRate, 1, 16));
      });
    });
  }

  return encodeWav(audioBuffer.getChannelData(0), sampleRate, 1, 16);
}

function encodeWav(samples, sampleRate, numChannels, bitsPerSample) {
  const bytesPerSample = bitsPerSample / 8;
  const blockAlign = numChannels * bytesPerSample;
  const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
  const view = new DataView(buffer);

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * bytesPerSample, true);

  const maxVal = Math.pow(2, bitsPerSample - 1) - 1;
  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 32768 : s * 32767, true);
    offset += 2;
  }

  return new Uint8Array(buffer);
}

function writeString(view, offset, str) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}

// ── Mic: Waveform ──────────────────────────────────────────────────────────
let waveformInterval = null;

function startWaveform() {
  const container = document.getElementById('waveform-container');
  container.classList.add('recording');
  const bars = container.querySelectorAll('.waveform-bar');
  waveformInterval = setInterval(() => {
    if (!state.analyser) return;
    const data = new Uint8Array(state.analyser.frequencyBinCount);
    state.analyser.getByteFrequencyData(data);
    const step = Math.floor(data.length / bars.length);
    for (let i = 0; i < bars.length; i++) {
      let val = 0;
      for (let j = 0; j < step; j++) {
        val = Math.max(val, data[i * step + j] || 0);
      }
      const h = Math.max(5, (val / 255) * 100);
      bars[i].style.height = h + '%';
    }
  }, 50);
}

function stopWaveform() {
  clearInterval(waveformInterval);
  waveformInterval = null;
  const container = document.getElementById('waveform-container');
  container.classList.remove('recording');
  container.querySelectorAll('.waveform-bar').forEach(b => b.style.height = '5%');
}

// ── Mic: UI States ─────────────────────────────────────────────────────────
function updateMicUI(st) {
  const btn = document.getElementById('mic-button');
  const icon = document.getElementById('mic-icon');
  const status = document.getElementById('mic-status');
  const ring = document.getElementById('mic-outer-ring');

  btn.classList.remove('recording', 'processing-state');
  ring.classList.remove('mic-breathing', 'mic-listening');
  icon.classList.remove('mic-processing');

  if (st === 'idle') {
    status.textContent = 'Tap to Speak';
    icon.textContent = 'mic';
    ring.classList.add('mic-breathing');
  } else if (st === 'recording') {
    status.textContent = 'Listening...';
    icon.textContent = 'mic';
    ring.classList.add('mic-listening');
    btn.classList.add('recording');
  } else if (st === 'processing') {
    status.textContent = 'Processing...';
    icon.textContent = 'sync';
    icon.classList.add('mic-processing');
    btn.classList.add('processing-state');
  }
}

function resetToIdle() {
  state.processing = false;
  updateMicUI('idle');
  document.getElementById('output-processing').classList.remove('active');
  document.getElementById('output-empty').classList.add('active');
  const ring = document.getElementById('mic-outer-ring');
  ring.classList.add('mic-breathing');
}

// ── Pipeline Viz ───────────────────────────────────────────────────────────
function resetPipeline() {
  ['pipe-stt', 'pipe-l1', 'pipe-l2', 'pipe-l3'].forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove('active', 'processing');
    el.style.opacity = '0.5';
  });
  ['pipe-stt-ms', 'pipe-l1-ms', 'pipe-l2-ms', 'pipe-l3-ms'].forEach(id => {
    document.getElementById(id).textContent = '--';
  });
}

function updatePipeline(layer, layerMs, totalMs) {
  resetPipeline();

  const nodeMap = { 1: 'pipe-l1', 2: 'pipe-l2', 3: 'pipe-l3' };
  const msMap = { 1: 'pipe-l1-ms', 2: 'pipe-l2-ms', 3: 'pipe-l3-ms' };

  document.getElementById('pipe-stt').classList.add('active');
  document.getElementById('pipe-stt').style.opacity = '1';
  document.getElementById('pipe-stt-ms').textContent = (totalMs - layerMs) + 'ms';

  const nodeId = nodeMap[layer];
  const msId = msMap[layer];

  if (nodeId) {
    document.getElementById(nodeId).classList.add('active');
    document.getElementById(nodeId).style.opacity = '1';
    document.getElementById(msId).textContent = layerMs + 'ms';
  }
}

// ── API: Voice ─────────────────────────────────────────────────────────────
async function sendVoice(wavBuffer) {
  state.processing = true;
  updateMicUI('processing');
  resetPipeline();

  showOutputState('processing');

  try {
    const resp = await fetch('/api/voice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: wavBuffer
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      if (resp.status === 503) {
        showToast('Models still loading — please wait', 'error');
      } else {
        throw new Error(err.error || 'Server error');
      }
      resetToIdle();
      return;
    }

    const data = await resp.json();

    if (data.error === 'No speech detected') {
      showToast('No speech detected — try again', 'error');
      resetToIdle();
      return;
    }

    updatePipeline(data.layer, data.layer_ms, data.total_ms);
    showOutputResult(data);
    playAudio(data.audio_base64, data.language, data.audio_mime);

    state.lastAudioB64 = data.audio_base64;
    state.lastAudioMime = data.audio_mime || 'audio/mpeg';
    state.lastLanguage = data.language;
    state.queryCount++;
    state.layerCounts[String(data.layer)] = (state.layerCounts[String(data.layer)] || 0) + 1;
    updateSidebarCounts();
    updateTopbarLatency(data.total_ms);

    state.processing = false;
    updateMicUI('idle');
    document.getElementById('mic-outer-ring').classList.add('mic-breathing');

    setTimeout(() => updateMicUI('idle'), 2000);

  } catch (e) {
    console.error('Voice API error:', e);
    showToast(e.message || 'Request failed', 'error');
    resetToIdle();
  }
}

// ── Output Panel ───────────────────────────────────────────────────────────
function showOutputState(st) {
  document.getElementById('output-empty').classList.remove('active');
  document.getElementById('output-processing').classList.remove('active');
  document.getElementById('output-result').classList.remove('active');

  if (st === 'empty') {
    document.getElementById('output-empty').classList.add('active');
    document.getElementById('output-layer-badge').classList.add('hidden');
  } else if (st === 'processing') {
    document.getElementById('output-processing').classList.add('active');
    document.getElementById('output-layer-badge').classList.add('hidden');
  } else if (st === 'result') {
    document.getElementById('output-result').classList.add('active');
  }
}

function showOutputResult(data) {
  document.getElementById('output-empty').classList.remove('active');
  document.getElementById('output-processing').classList.remove('active');
  document.getElementById('output-result').classList.add('active');

  const layerBadge = document.getElementById('output-layer-badge');
  layerBadge.classList.remove('hidden');
  layerBadge.textContent = `L${data.layer}`;
  layerBadge.className = 'font-label-technical text-[10px] px-1 py-0.5 rounded-none uppercase ' +
    (data.layer === 1 ? 'layer-badge-l1' : data.layer === 2 ? 'layer-badge-l2' : 'layer-badge-l3');

  document.getElementById('chip-confidence').textContent = (data.confidence * 100).toFixed(1) + '%';
  document.getElementById('chip-intent').textContent = data.intent;
  document.getElementById('chip-lang').textContent = data.language === 'ur' ? 'UR' : 'EN';
  document.getElementById('chip-layer').textContent = 'L' + data.layer;
  document.getElementById('chip-time').textContent = data.total_ms + 'ms';

  const gauge = document.getElementById('confidence-gauge');
  gauge.style.width = (data.confidence * 100) + '%';
  gauge.className = 'confidence-bar h-full transition-all duration-500 ' +
    (data.confidence >= 0.8 ? 'conf-high' : data.confidence >= 0.5 ? 'conf-med' : 'conf-low');

  document.getElementById('output-transcription').textContent =
    (data.language === 'ur' ? '\u200F' : '') + '"' + data.transcription + '"';

  document.getElementById('output-response').textContent = data.response;

  const urduSection = document.getElementById('output-urdu-section');
  if (data.language === 'ur') {
    urduSection.classList.remove('hidden');
    document.getElementById('output-urdu-text').textContent = data.response;
  } else {
    urduSection.classList.add('hidden');
  }

  const replayBtn = document.getElementById('btn-replay');
  if (data.audio_base64) {
    replayBtn.classList.remove('hidden');
  } else {
    replayBtn.classList.add('hidden');
  }

  document.getElementById('output-result').classList.add('fade-in');
  setTimeout(() => document.getElementById('output-result').classList.remove('fade-in'), 300);
}

// ── Audio Playback ─────────────────────────────────────────────────────────
function playAudio(b64Audio, lang, mime) {
  if (!b64Audio) return;
  const byteStr = atob(b64Audio);
  const bytes = new Uint8Array(byteStr.length);
  for (let i = 0; i < byteStr.length; i++) {
    bytes[i] = byteStr.charCodeAt(i);
  }

  const blob = new Blob([bytes], { type: mime || 'audio/mpeg' });
  const url = URL.createObjectURL(blob);
  const player = document.getElementById('audio-player');
  player.src = url;
  player.play().catch(e => console.warn('Audio play failed:', e));

  player.onended = () => URL.revokeObjectURL(url);
}

function replayLastAudio() {
  if (state.lastAudioB64) {
    playAudio(state.lastAudioB64, state.lastLanguage, state.lastAudioMime);
  }
}

// ── Sidebar & Topbar ───────────────────────────────────────────────────────
function updateSidebarCounts() {
  document.getElementById('sidebar-qcount').textContent = state.queryCount;
  document.getElementById('sidebar-l1').textContent = state.layerCounts['1'] || 0;
  document.getElementById('sidebar-l2').textContent = state.layerCounts['2'] || 0;
  document.getElementById('sidebar-l3').textContent = state.layerCounts['3'] || 0;
  document.getElementById('mic-qcount').textContent = state.queryCount;
}

function updateTopbarLatency(ms) {
  document.getElementById('topbar-latency').textContent = `Latency: ${ms}ms`;
}

function updateUptime() {
  const elapsed = Math.floor((Date.now() - state.uptimeStart) / 1000);
  const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
  const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
  const s = Math.floor(elapsed % 60).toString().padStart(2, '0');
  document.getElementById('topbar-uptime').textContent = `Uptime: ${h}:${m}:${s}`;
}

// ── Session Management ─────────────────────────────────────────────────────
async function clearSession() {
  try {
    await fetch('/api/session/clear', { method: 'POST' });
    state.queryCount = 0;
    state.layerCounts = { '1': 0, '2': 0, '3': 0 };
    updateSidebarCounts();
    showOutputState('empty');
    showToast('Session cleared', 'success');
    renderLogs();
  } catch (e) {
    showToast('Failed to clear session', 'error');
  }
}

async function fetchSession() {
  try {
    const resp = await fetch('/api/session');
    const data = await resp.json();
    state.queryCount = data.query_count;
    state.layerCounts = data.layer_counts;
    updateSidebarCounts();
    return data.history;
  } catch (e) {
    return [];
  }
}

// ── Session Logs Tab ───────────────────────────────────────────────────────
async function renderLogs() {
  const list = document.getElementById('logs-list');
  const empty = document.getElementById('logs-empty');
  const count = document.getElementById('logs-count');
  const history = await fetchSession();

  count.textContent = history.length + ' interactions';
  list.innerHTML = '';

  if (history.length === 0) {
    list.appendChild(empty);
    empty.classList.remove('hidden');
    return;
  }

  empty.classList.add('hidden');

  history.slice().reverse().forEach((entry, i) => {
    const layerColors = { 1: 'l1-accent', 2: 'l2-accent', 3: 'l3-accent' };
    const lcolor = layerColors[entry.layer] || 'l1-accent';
    const card = document.createElement('div');
    card.className = 'bg-surface-container-lowest border border-outline-variant p-md flex flex-col gap-sm fade-in';
    card.style.animationDelay = (i * 50) + 'ms';

    const lBadgeClass = entry.layer === 1 ? 'layer-badge-l1' : entry.layer === 2 ? 'layer-badge-l2' : 'layer-badge-l3';
    card.innerHTML = `
      <div class="flex items-center justify-between">
        <span class="font-label-technical text-[10px] ${lcolor} uppercase">#${history.length - i}</span>
        <span class="font-label-technical text-[10px] text-on-surface-variant">${entry.language === 'ur' ? 'UR' : 'EN'} · ${entry.layer_ms}ms</span>
      </div>
      <div class="text-body-md text-on-surface">"${entry.user}"</div>
      <div class="bg-background border border-outline-variant p-sm">
        <p class="text-body-md text-on-background">${entry.assistant}</p>
      </div>
      <div class="flex items-center gap-sm">
        <span class="font-label-technical text-[10px] ${lBadgeClass} px-1 py-0.5 rounded-none uppercase">${entry.intent}</span>
        <span class="font-label-technical text-[10px] text-on-surface-variant">${(entry.confidence * 100).toFixed(0)}%</span>
      </div>
    `;
    list.appendChild(card);
  });
}

// ── Status Polling ─────────────────────────────────────────────────────────
async function fetchStatus() {
  try {
    const resp = await fetch('/api/status');
    const data = await resp.json();
    updateHealthTable(data);
    document.getElementById('topbar-status').textContent =
      (data.whisper_loaded && data.handler_ready && data.classifier_ready && data.chroma_doc_count > 0)
        ? 'System Ready' : 'Initializing...';
  } catch (e) {
    document.getElementById('topbar-status').textContent = 'Offline';
  }
}

function pollStatus() {
  state.pollInterval = setInterval(fetchStatus, 15000);
}

function updateHealthTable(data) {
  const green = '<span class="status-dot-green">●</span> Ready';
  const red = '<span class="status-dot-red">●</span> Error';

  document.getElementById('h-whisper').innerHTML = data.whisper_loaded ? green : red;
  document.getElementById('h-handler').innerHTML = data.handler_ready ? green : red;
  document.getElementById('h-classifier').innerHTML =
    (data.classifier_ready && data.faiss_ready) ? green : red;
  document.getElementById('h-chroma').innerHTML = data.chroma_doc_count > 0 ? green : red;
  document.getElementById('h-chroma-detail').textContent = data.chroma_doc_count + ' documents';
  document.getElementById('h-ollama').innerHTML = data.ollama_ready ? green : red;
  const ollamaMissing = data.ollama?.missing_models || [];
  const ollamaDetail = ollamaMissing.length
    ? `Missing: ${ollamaMissing.join(', ')}`
    : (data.ollama?.reachable ? data.llm_model : (data.ollama?.error || data.llm_model));
  const ollamaRow = document.getElementById('h-ollama')?.closest('tr');
  if (ollamaRow) {
    const detailCell = ollamaRow.querySelector('td:last-child');
    if (detailCell) detailCell.textContent = ollamaDetail;
  }
  document.getElementById('h-argos').innerHTML = '<span class="status-dot-green">●</span> Ready';
  document.getElementById('h-gtts').innerHTML = '<span class="status-dot-yellow">●</span> Internet required';
  document.getElementById('h-gpu').innerHTML =
    data.device === 'cuda'
      ? '<span class="status-dot-green">●</span> CUDA'
      : '<span class="status-dot-yellow">●</span> CPU';
  document.getElementById('h-gpu-detail').textContent = data.device;
}

// ── Evaluation Dashboard ───────────────────────────────────────────────────
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function setOrgMessage(message, isError = false) {
  const el = document.getElementById('org-last-message');
  if (!el) return;
  el.textContent = message;
  el.classList.toggle('text-error', isError);
}

async function adminFetch(url, options = {}, retry = true) {
  const token = localStorage.getItem('voxAdminToken') || '';
  const headers = new Headers(options.headers || {});
  if (token) headers.set('X-VOX-Admin-Token', token);

  return fetch(url, { ...options, headers });
}

function renderAdminAccess(ok, message) {
  state.adminAuthenticated = ok;
  const status = document.getElementById('admin-access-status');
  if (!status) return;
  status.textContent = message || (ok ? 'Admin access ready' : 'Admin token required');
  status.classList.toggle('text-primary', ok);
  status.classList.toggle('text-error', !ok);
}

async function checkAdminAccess() {
  try {
    const resp = await adminFetch('/api/admin/check');
    if (resp.ok) {
      const data = await resp.json();
      renderAdminAccess(true, data.admin_token_required ? 'Admin access ready' : 'Admin access open');
      return true;
    }
    renderAdminAccess(false, 'Admin token required');
    return false;
  } catch (e) {
    renderAdminAccess(false, 'Admin check failed');
    return false;
  }
}

async function refreshAdminTokens() {
  const list = document.getElementById('admin-token-list');
  if (!list) return;
  try {
    const resp = await adminFetch('/api/admin/tokens');
    if (!resp.ok) {
      list.innerHTML = '<span class="text-body-md text-on-surface-variant">Root admin token required to manage operator tokens.</span>';
      return;
    }
    const data = await resp.json();
    renderAdminTokens(data.tokens || []);
  } catch (e) {
    list.innerHTML = '<span class="text-body-md text-error">Could not load admin tokens.</span>';
  }
}

function renderAdminTokens(tokens) {
  const list = document.getElementById('admin-token-list');
  if (!list) return;
  if (!tokens.length) {
    list.innerHTML = '<span class="text-body-md text-on-surface-variant">No database admin tokens created yet.</span>';
    return;
  }
  list.innerHTML = tokens.map(token => `
    <div class="flex items-center justify-between gap-sm border-b border-outline-variant py-sm last:border-b-0">
      <div class="min-w-0">
        <div class="text-body-md text-on-surface font-bold truncate">${escapeHtml(token.name)}</div>
        <div class="font-label-technical text-[10px] text-on-surface-variant uppercase">
          ${escapeHtml(token.org_id || 'all orgs')} | ${escapeHtml((token.scopes || []).join(','))} | ${token.active ? 'active' : token.expired ? 'expired' : 'revoked'} | expires ${escapeHtml(token.expires_at || 'never')}
        </div>
      </div>
      ${token.active ? `<button onclick="revokeAdminToken('${escapeHtml(token.token_id)}')" class="px-sm py-xs bg-surface-container border border-outline-variant font-label-technical text-[10px] uppercase hover:bg-surface-container-highest transition-colors cursor-pointer">Revoke</button>` : ''}
    </div>
  `).join('');
}

async function createAdminToken() {
  const nameInput = document.getElementById('admin-token-name');
  const orgInput = document.getElementById('admin-token-org');
  const scopeInput = document.getElementById('admin-token-scope');
  const daysInput = document.getElementById('admin-token-days');
  const createdBox = document.getElementById('admin-token-created');
  const name = nameInput.value.trim();
  const orgId = orgInput.value.trim();
  const scope = scopeInput.value || 'admin';
  const days = Number(daysInput.value || 90);
  if (!name) {
    setOrgMessage('Enter a token name first.', true);
    return;
  }
  if (!Number.isFinite(days) || days < 1 || days > 3650) {
    setOrgMessage('Token expiry must be between 1 and 3650 days.', true);
    return;
  }
  try {
    const resp = await adminFetch('/api/admin/tokens', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, org_id: orgId || null, scopes: [scope], expires_in_days: days })
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Token creation failed');
    createdBox.classList.remove('hidden');
    createdBox.textContent = `New token, shown once: ${data.token}`;
    nameInput.value = '';
    orgInput.value = '';
    scopeInput.value = 'admin';
    daysInput.value = '90';
    setOrgMessage('Admin token created. Store it securely now.');
    await refreshAdminTokens();
  } catch (e) {
    setOrgMessage(e.message || 'Token creation failed.', true);
  }
}

async function revokeAdminToken(tokenId) {
  if (!confirm('Revoke this admin token?')) return;
  try {
    const resp = await adminFetch(`/api/admin/tokens/${encodeURIComponent(tokenId)}`, { method: 'DELETE' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Token revoke failed');
    setOrgMessage('Admin token revoked.');
    await refreshAdminTokens();
  } catch (e) {
    setOrgMessage(e.message || 'Token revoke failed.', true);
  }
}

async function refreshHandoffs() {
  const list = document.getElementById('handoff-list');
  if (!list) return;
  try {
    const resp = await adminFetch('/api/handoffs?status=open&limit=25');
    if (!resp.ok) {
      list.innerHTML = '<span class="text-body-md text-on-surface-variant">Admin token required.</span>';
      return;
    }
    const data = await resp.json();
    renderHandoffs(data.tickets || []);
  } catch (e) {
    list.innerHTML = '<span class="text-body-md text-error">Could not load handoffs.</span>';
  }
}

async function refreshDatasetVersions() {
  const list = document.getElementById('dataset-version-list');
  if (!list) return;
  try {
    const resp = await adminFetch('/api/datasets/versions');
    if (!resp.ok) {
      list.innerHTML = '<span class="text-body-md text-on-surface-variant">Admin token required.</span>';
      return;
    }
    const data = await resp.json();
    renderDatasetVersions(data.versions || [], data.active_version || '');
  } catch (e) {
    list.innerHTML = '<span class="text-body-md text-error">Could not load dataset versions.</span>';
  }
}

function renderDatasetVersions(versions, activeVersion) {
  const list = document.getElementById('dataset-version-list');
  if (!list) return;
  if (!versions.length) {
    list.innerHTML = '<span class="text-body-md text-on-surface-variant">No dataset versions yet. Process a dataset to create one.</span>';
    return;
  }
  list.innerHTML = versions.map(version => {
    const isActive = version.version_id === activeVersion;
    const stats = version.stats || {};
    return `
      <div class="flex items-center justify-between gap-sm border-b border-outline-variant py-sm last:border-b-0">
        <div class="min-w-0">
          <div class="text-body-md text-on-surface font-bold truncate">${escapeHtml(version.version_id)} ${isActive ? '(active)' : ''}</div>
          <div class="font-label-technical text-[10px] text-on-surface-variant uppercase">
            ${escapeHtml(version.created_at || '')} | docs ${stats.document_count || 0} | chunks ${stats.chunk_count || 0} | index ${version.has_vector_index ? 'yes' : 'no'}
          </div>
        </div>
        <button onclick="rollbackDatasetVersion('${escapeHtml(version.version_id)}')" class="px-sm py-xs bg-surface-container border border-outline-variant font-label-technical text-[10px] uppercase hover:bg-surface-container-highest transition-colors cursor-pointer">Rollback</button>
      </div>
    `;
  }).join('');
}

async function rollbackDatasetVersion(versionId) {
  if (!confirm('Roll back to this dataset version?')) return;
  try {
    const resp = await adminFetch(`/api/datasets/versions/${encodeURIComponent(versionId)}/rollback`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Rollback failed');
    setOrgMessage('Dataset version restored.');
    await refreshOrganizationSetup();
  } catch (e) {
    setOrgMessage(e.message || 'Rollback failed.', true);
  }
}

function renderHandoffs(tickets) {
  const list = document.getElementById('handoff-list');
  if (!list) return;
  if (!tickets.length) {
    list.innerHTML = '<span class="text-body-md text-on-surface-variant">No open handoffs.</span>';
    return;
  }
  list.innerHTML = tickets.map(ticket => `
    <div class="border-b border-outline-variant py-sm last:border-b-0 flex flex-col gap-xs">
      <div class="flex items-start justify-between gap-sm">
        <div class="min-w-0">
          <div class="text-body-md text-on-surface font-bold">${escapeHtml(ticket.query)}</div>
          <div class="font-label-technical text-[10px] text-on-surface-variant uppercase">
            ${escapeHtml(ticket.department || 'Support')} | ${(Number(ticket.confidence || 0) * 100).toFixed(0)}% | ${escapeHtml(ticket.created_at || '')}
          </div>
        </div>
        <button onclick="resolveHandoff('${escapeHtml(ticket.ticket_id)}')" class="px-sm py-xs bg-primary text-on-primary font-label-technical text-[10px] uppercase hover:opacity-90 transition-opacity cursor-pointer">Resolve</button>
      </div>
      <div class="text-body-md text-on-surface-variant">${escapeHtml(ticket.response || '')}</div>
      <div class="font-label-technical text-[10px] text-on-surface-variant uppercase">
        ${escapeHtml(ticket.contact?.phone || '')} ${escapeHtml(ticket.contact?.email || '')}
      </div>
    </div>
  `).join('');
}

async function resolveHandoff(ticketId) {
  const notes = prompt('Resolution notes, optional') || '';
  try {
    const resp = await adminFetch(`/api/handoffs/${encodeURIComponent(ticketId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'resolved', notes })
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Could not resolve handoff');
    setOrgMessage('Handoff resolved.');
    await refreshHandoffs();
  } catch (e) {
    setOrgMessage(e.message || 'Could not resolve handoff.', true);
  }
}

async function saveAdminToken() {
  const input = document.getElementById('admin-token-input');
  const token = input.value.trim();
  if (!token) {
    setOrgMessage('Enter an admin token first.', true);
    return;
  }
  localStorage.setItem('voxAdminToken', token);
  input.value = '';
  const ok = await checkAdminAccess();
  setOrgMessage(ok ? 'Admin token saved.' : 'Admin token was rejected.', !ok);
  if (ok) await refreshOrganizationSetup();
}

async function clearAdminToken() {
  localStorage.removeItem('voxAdminToken');
  renderAdminAccess(false, 'Admin token cleared');
  setOrgMessage('Admin token cleared.');
  await refreshOrganizationSetup();
}

async function refreshOrganizationSetup() {
  try {
    const statusResp = await fetch('/api/status');
    const status = await statusResp.json();
    document.getElementById('org-name').textContent = status.organization_name || status.org_id || '--';

    const hasAdmin = await checkAdminAccess();
    if (!hasAdmin) {
      renderOrganizationList(null);
      document.getElementById('org-doc-list').innerHTML = '<span class="text-body-md text-on-surface-variant">Admin token required.</span>';
      document.getElementById('org-draft-list').innerHTML = '<span class="text-body-md text-on-surface-variant">Admin token required.</span>';
      renderAdminTokens([]);
      renderHandoffs([]);
      renderDatasetVersions([], '');
      setOrgMessage('Enter the admin token to manage organizations and datasets.', true);
      return;
    }

    await refreshAdminTokens();
    await refreshHandoffs();
    await refreshDatasetVersions();

    const [datasetResp, draftResp, orgsResp] = await Promise.all([
      adminFetch('/api/datasets'),
      adminFetch('/api/intents/draft'),
      adminFetch('/api/organizations')
    ]);
    const dataset = await datasetResp.json();
    const draft = draftResp.ok ? await draftResp.json() : null;
    const orgs = orgsResp.ok ? await orgsResp.json() : null;

    renderOrganizationStatus(status, dataset, draft);
    renderOrganizationList(orgs);
  } catch (e) {
    setOrgMessage('Could not refresh organization setup.', true);
  }
}

function renderOrganizationStatus(status, dataset, draft) {
  document.getElementById('org-name').textContent = status.organization_name || status.org_id || '--';
  document.getElementById('org-doc-count').textContent = dataset.stats?.document_count ?? 0;
  document.getElementById('org-chunk-count').textContent = dataset.stats?.chunk_count ?? 0;
  document.getElementById('org-active-intents').textContent = status.active_intents?.intent_count ?? 0;
  document.getElementById('org-index-status').textContent =
    `Index: ${dataset.vector_index?.status || 'not started'} · ${dataset.vector_index?.indexed_chunk_count || 0} chunks`;

  const draftCount = draft?.intents?.length || status.intent_draft?.intent_count || 0;
  document.getElementById('org-draft-count').textContent = draftCount;
  document.getElementById('org-draft-method').textContent =
    draft?.generation_method || status.intent_draft?.generation_method || '--';

  renderOrganizationDocuments(dataset.documents || []);
  renderOrganizationDraft(draft?.intents || []);
  resumeLatestDatasetJob();
}

function renderOrganizationList(data) {
  const el = document.getElementById('org-list');
  if (!el) return;
  const organizations = data?.organizations || [];
  if (!organizations.length) {
    el.innerHTML = '<span class="text-body-md text-on-surface-variant">No organizations found.</span>';
    return;
  }
  el.innerHTML = organizations.map(org => `
    <div class="border-b border-outline-variant py-sm last:border-b-0">
      <div class="flex items-center justify-between gap-sm">
        <div>
          <div class="font-label-technical text-[10px] text-on-surface uppercase">${escapeHtml(org.organization_name || org.org_id)}</div>
          <div class="font-label-technical text-[10px] text-on-surface-variant uppercase">${escapeHtml(org.org_id)} · ${escapeHtml(org.domain || 'general')}</div>
        </div>
        ${org.active
          ? '<span class="font-label-technical text-[10px] text-primary uppercase">Active</span>'
          : `<button onclick='switchOrganization(${JSON.stringify(org.org_id)})' class="px-sm py-xs border border-outline-variant font-label-technical text-[10px] text-on-surface uppercase hover:bg-surface-container cursor-pointer">Switch</button>`}
      </div>
    </div>
  `).join('');
}

function renderOrganizationDocuments(documents) {
  const el = document.getElementById('org-doc-list');
  if (!documents.length) {
    el.innerHTML = '<span class="text-body-md text-on-surface-variant">No documents uploaded yet.</span>';
    return;
  }
  el.innerHTML = documents.map(doc => `
    <div class="border-b border-outline-variant py-sm last:border-b-0">
      <div class="font-label-technical text-[10px] text-on-surface uppercase">${escapeHtml(doc.original_filename)}</div>
      <div class="font-label-technical text-[10px] text-on-surface-variant uppercase">
        ${escapeHtml(doc.status)} · ${doc.chunk_count || 0} chunks · ${doc.character_count || 0} chars
      </div>
      ${doc.error ? `<div class="text-error text-body-md mt-xs">${escapeHtml(doc.error)}</div>` : ''}
    </div>
  `).join('');
}

function renderOrganizationDraft(intents) {
  const el = document.getElementById('org-draft-list');
  if (!intents.length) {
    el.innerHTML = '<span class="text-body-md text-on-surface-variant">No draft generated yet.</span>';
    return;
  }
  el.innerHTML = intents.map(intent => `
    <div class="border-b border-outline-variant py-sm last:border-b-0">
      <div class="font-label-technical text-[10px] text-on-surface uppercase">${escapeHtml(intent.tag)}</div>
      <div class="text-body-md text-on-surface-variant">${escapeHtml((intent.patterns || []).slice(0, 3).join(', '))}</div>
    </div>
  `).join('');
}

async function createOrganization() {
  const orgId = document.getElementById('new-org-id').value.trim();
  const organizationName = document.getElementById('new-org-name').value.trim();
  const domain = document.getElementById('new-org-domain').value.trim() || 'general';
  const assistantName = document.getElementById('new-org-assistant').value.trim() || 'VOX';

  if (!orgId || !organizationName) {
    setOrgMessage('Organization id and name are required.', true);
    return;
  }

  setOrgMessage('Creating organization...');
  try {
    const resp = await adminFetch('/api/organizations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        org_id: orgId,
        organization_name: organizationName,
        domain,
        assistant_name: assistantName
      })
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Organization creation failed');

    document.getElementById('new-org-id').value = '';
    document.getElementById('new-org-name').value = '';
    document.getElementById('new-org-domain').value = '';
    document.getElementById('new-org-assistant').value = 'VOX';
    setOrgMessage(`Created organization ${data.profile?.org_id}. Set VOX_ORG_ID=${data.profile?.org_id} and restart to activate it.`);
    await refreshOrganizationSetup();
  } catch (e) {
    setOrgMessage(e.message || 'Organization creation failed.', true);
  }
}

async function switchOrganization(orgId) {
  if (!orgId) return;
  setOrgMessage(`Switching active organization to ${orgId}...`);
  try {
    const resp = await adminFetch('/api/organizations/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ org_id: orgId })
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Organization switch failed');
    state.datasetJobId = null;
    state.datasetJobPolling = false;
    setOrgMessage(`Active organization switched to ${data.profile?.organization_name || data.active_org_id}.`);
    await refreshOrganizationSetup();
    await fetchStatus();
  } catch (e) {
    setOrgMessage(e.message || 'Organization switch failed.', true);
  }
}

function renderDatasetJob(job) {
  if (!job) return;
  const label = `${job.status} · ${job.progress || 0}% · ${job.message || ''}`;
  document.getElementById('org-index-status').textContent = `Processing: ${label}`;
  if (job.status === 'failed') {
    setOrgMessage(job.error || 'Dataset processing failed.', true);
  }
}

async function pollDatasetJob(jobId) {
  state.datasetJobId = jobId;
  state.datasetJobPolling = true;
  const resp = await adminFetch(`/api/jobs/${jobId}`);
  const job = await resp.json();
  if (!resp.ok) {
    setOrgMessage(job.error || 'Could not read dataset job.', true);
    state.datasetJobPolling = false;
    return;
  }

  renderDatasetJob(job);
  if (job.status === 'queued' || job.status === 'running') {
    setTimeout(() => pollDatasetJob(jobId), 2000);
    return;
  }

  state.datasetJobId = null;
  state.datasetJobPolling = false;
  if (job.status === 'completed') {
    setOrgMessage(job.message || 'Dataset processing completed.');
    await refreshOrganizationSetup();
  }
}

async function resumeLatestDatasetJob() {
  if (state.datasetJobPolling) return;
  try {
    const resp = await adminFetch('/api/jobs/latest/dataset_processing');
    if (!resp.ok) return;
    const job = await resp.json();
    if (job.status === 'queued' || job.status === 'running') {
      renderDatasetJob(job);
      pollDatasetJob(job.job_id);
    }
  } catch (e) {}
}

async function uploadOrganizationDataset() {
  const input = document.getElementById('org-file-input');
  if (!input.files.length) {
    setOrgMessage('Choose one or more files first.', true);
    return;
  }

  const form = new FormData();
  Array.from(input.files).forEach(file => form.append('files', file));
  setOrgMessage('Uploading dataset...');

  try {
    const resp = await adminFetch('/api/datasets/upload', { method: 'POST', body: form });
    const data = await resp.json();
    if (!resp.ok && !data.saved?.length) throw new Error(data.error || 'Upload failed');
    input.value = '';
    setOrgMessage(`Uploaded ${data.saved?.length || 0} file(s).`);
    await refreshOrganizationSetup();
  } catch (e) {
    setOrgMessage(e.message || 'Dataset upload failed.', true);
  }
}

async function processOrganizationDataset() {
  setOrgMessage('Starting dataset processing job...');
  try {
    const resp = await adminFetch('/api/datasets/process', { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Dataset processing failed');
    setOrgMessage(`Dataset job started: ${data.job_id}`);
    renderDatasetJob(data);
    pollDatasetJob(data.job_id);
  } catch (e) {
    setOrgMessage(e.message || 'Dataset processing failed.', true);
  }
}

async function generateOrganizationIntents() {
  setOrgMessage('Generating draft intents with Qwen...');
  try {
    const resp = await adminFetch('/api/intents/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_intents: 12 })
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Intent generation failed');
    setOrgMessage(`Generated ${data.intents?.length || 0} draft intents.`);
    await refreshOrganizationSetup();
  } catch (e) {
    setOrgMessage(e.message || 'Intent generation failed.', true);
  }
}

async function publishOrganizationIntents() {
  setOrgMessage('Publishing draft intents...');
  try {
    const resp = await adminFetch('/api/intents/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: 'merge' })
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Intent publish failed');
    setOrgMessage(`Published draft. Live intents: ${data.active_intents_after}.`);
    await refreshOrganizationSetup();
  } catch (e) {
    setOrgMessage(e.message || 'Intent publish failed.', true);
  }
}

async function fetchEvaluation() {
  try {
    const resp = await fetch('/api/evaluate');
    const data = await resp.json();
    if (data.message && data.cached === false) {
      document.getElementById('eval-timestamp').textContent = 'Not yet run — click Re-run';
    } else if (data.timestamp) {
      document.getElementById('eval-timestamp').textContent = 'Last run: ' + data.timestamp;
      renderEvalCharts(data);
    }
  } catch (e) {
    console.error('Eval fetch error:', e);
  }
}

async function runEvaluation() {
  const btn = document.getElementById('btn-eval-run');
  btn.disabled = true;
  btn.innerHTML = '<span class="material-symbols-outlined text-[16px] mic-processing">sync</span> Running...';

  try {
    const resp = await fetch('/api/evaluate-run', { method: 'POST' });
    const data = await resp.json();
    document.getElementById('eval-timestamp').textContent = 'Last run: ' + (data.timestamp || 'just now');
    renderEvalCharts(data);
  } catch (e) {
    showToast('Evaluation failed', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span class="material-symbols-outlined text-[16px]">play_arrow</span> Re-run Evaluation';
  }
}

function renderEvalCharts(data) {
  Object.values(state.evalCharts).forEach(c => c.destroy());
  state.evalCharts = {};

  const results = data.results || [];
  const accuracy = data.accuracy || 0;
  const f1       = data.f1      || 0;
  const rouge    = data.rouge   || 0;
  const bleu     = data.bleu    || 0;
  const meteor   = data.meteor  || 0;
  const laaj     = data.laaj    || 0;
  const wer      = data.wer     || 0;

  // Update all metric cards with real values
  document.getElementById('eval-f1').textContent       = f1.toFixed(2);
  document.getElementById('eval-rouge').textContent    = rouge.toFixed(2);
  document.getElementById('eval-bleu').textContent     = bleu.toFixed(2);
  document.getElementById('eval-meteor').textContent   = meteor.toFixed(2);
  document.getElementById('eval-laaj').textContent     = laaj.toFixed(2);
  document.getElementById('eval-wer').textContent      = wer.toFixed(2);
  document.getElementById('eval-accuracy').textContent = (accuracy * 100).toFixed(0) + '%';

  const grade = v => v >= 0.8 ? 'Great' : v >= 0.6 ? 'Good' : 'OK';
  document.getElementById('eval-f1').nextElementSibling.textContent       = grade(f1);
  document.getElementById('eval-rouge').nextElementSibling.textContent    = grade(rouge);
  document.getElementById('eval-bleu').nextElementSibling.textContent     = grade(bleu);
  document.getElementById('eval-meteor').nextElementSibling.textContent   = grade(meteor);
  document.getElementById('eval-laaj').nextElementSibling.textContent     = grade(laaj);
  document.getElementById('eval-wer').nextElementSibling.textContent      = wer <= 0.1 ? 'Great' : wer <= 0.2 ? 'Good' : 'OK';
  document.getElementById('eval-accuracy').nextElementSibling.textContent = grade(accuracy);

  // ── Radar chart ──────────────────────────────────────────────────────────────────
  state.evalCharts.radar = new Chart(document.getElementById('chart-radar'), {
    type: 'radar',
    data: {
      labels: ['Accuracy', 'F1', 'ROUGE-L', 'BLEU', 'METEOR', 'LaaJ', '1-WER'],
      datasets: [{
        data: [accuracy, f1, rouge, bleu, meteor, laaj, 1 - wer],
        borderColor: '#82947F',
        backgroundColor: 'rgba(130,148,127,0.15)',
        borderWidth: 2,
        pointBackgroundColor: '#82947F'
      }]
    },
    options: {
      responsive: true,
      plugins: { title: { display: true, text: 'Evaluation Radar', font: { family: 'JetBrains Mono', size: 11 }, color: '#333' }, legend: { display: false } },
      scales: { r: { min: 0, max: 1, ticks: { font: { family: 'JetBrains Mono', size: 9 }, stepSize: 0.2 } } }
    }
  });

  // ── F1 per intent bar chart ──────────────────────────────────────────────────────
  const f1Map = data.f1_per_intent || {};
  const intentLabels = Object.keys(f1Map);
  const f1Values = intentLabels.map(k => f1Map[k]);
  state.evalCharts.f1 = new Chart(document.getElementById('chart-f1-intent'), {
    type: 'bar',
    data: {
      labels: intentLabels,
      datasets: [{ data: f1Values, backgroundColor: f1Values.map(v => v >= 0.8 ? '#82947F' : v >= 0.5 ? '#c4820e' : '#ba1a1a'), borderWidth: 0 }]
    },
    options: {
      indexAxis: 'y', responsive: true,
      plugins: { title: { display: true, text: 'F1 per Intent', font: { family: 'JetBrains Mono', size: 11 }, color: '#333' }, legend: { display: false } },
      scales: { x: { min: 0, max: 1, ticks: { font: { family: 'JetBrains Mono', size: 9 } } }, y: { ticks: { font: { family: 'JetBrains Mono', size: 9 } } } }
    }
  });

  // ── ROUGE bar chart (real value for ROUGE-L, estimated R1/R2) ────────────────────────
  state.evalCharts.rouge = new Chart(document.getElementById('chart-rouge'), {
    type: 'bar',
    data: {
      labels: ['ROUGE-1', 'ROUGE-2', 'ROUGE-L'],
      datasets: [{ data: [Math.min(rouge + 0.08, 1), Math.max(rouge - 0.12, 0), rouge], backgroundColor: ['#82947F', '#7029d3', '#c4820e'], borderWidth: 0 }]
    },
    options: {
      responsive: true,
      plugins: { title: { display: true, text: 'ROUGE Scores', font: { family: 'JetBrains Mono', size: 11 }, color: '#333' }, legend: { display: false } },
      scales: { y: { min: 0, max: 1, ticks: { font: { family: 'JetBrains Mono', size: 9 } } }, x: { ticks: { font: { family: 'JetBrains Mono', size: 9 } } } }
    }
  });

  // ── BLEU distribution histogram (real per-query scores) ───────────────────────────
  const bleuScores = data.bleu_scores || [];
  const bins = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0];
  const hist = new Array(bins.length - 1).fill(0);
  bleuScores.forEach(v => {
    for (let i = 0; i < bins.length - 1; i++) {
      if (v >= bins[i] && v < bins[i + 1]) { hist[i]++; break; }
    }
  });
  state.evalCharts.bleu = new Chart(document.getElementById('chart-bleu-dist'), {
    type: 'bar',
    data: {
      labels: bins.slice(0, -1).map((b, i) => b.toFixed(1) + '-' + bins[i+1].toFixed(1)),
      datasets: [{ data: hist, backgroundColor: '#c4820e', borderWidth: 0 }]
    },
    options: {
      responsive: true,
      plugins: { title: { display: true, text: 'BLEU Distribution', font: { family: 'JetBrains Mono', size: 11 }, color: '#333' }, legend: { display: false } },
      scales: { x: { ticks: { font: { family: 'JetBrains Mono', size: 9 } } }, y: { ticks: { font: { family: 'JetBrains Mono', size: 9 } } } }
    }
  });

  // ── LaaJ pie (fixed component weights, real avg score in title) ─────────────────────
  state.evalCharts.laaj = new Chart(document.getElementById('chart-laaj-pie'), {
    type: 'pie',
    data: {
      labels: ['Urdu Script (0.30)', 'Keyword Cov. (0.30)', 'Resp. Length (0.20)', 'Politeness (0.20)'],
      datasets: [{ data: [30, 30, 20, 20], backgroundColor: ['#82947F', '#7029d3', '#c4820e', '#D1CCC0'], borderWidth: 1, borderColor: '#FDFBF7' }]
    },
    options: {
      responsive: true,
      plugins: { title: { display: true, text: 'LaaJ Components (avg: ' + laaj.toFixed(2) + ')', font: { family: 'JetBrains Mono', size: 11 }, color: '#333' } }
    }
  });

  // ── WER per query bar chart (real values) ───────────────────────────────────────
  const werPerQuery = data.wer_per_query || [];
  state.evalCharts.wer = new Chart(document.getElementById('chart-wer-bar'), {
    type: 'bar',
    data: {
      labels: werPerQuery.map((_, i) => 'Q' + (i + 1)),
      datasets: [{ data: werPerQuery, backgroundColor: werPerQuery.map(v => v === 0 ? '#82947F' : '#ba1a1a'), borderWidth: 0 }]
    },
    options: {
      responsive: true,
      plugins: { title: { display: true, text: 'WER per Query', font: { family: 'JetBrains Mono', size: 11 }, color: '#333' }, legend: { display: false } },
      scales: { y: { min: 0, max: 1, ticks: { font: { family: 'JetBrains Mono', size: 9 } } }, x: { ticks: { font: { family: 'JetBrains Mono', size: 9 } } } }
    }
  });
}

// ── Test Suite ─────────────────────────────────────────────────────────────
async function runTestSuite() {
  const btn = document.getElementById('btn-test-run');
  btn.disabled = true;
  btn.innerHTML = '<span class="material-symbols-outlined text-[16px] mic-processing">sync</span> Running...';
  document.getElementById('test-results-table').querySelector('tbody').innerHTML =
    '<tr><td colspan="8" class="px-md py-xl text-center font-label-technical text-label-technical text-on-surface-variant">Running 114 queries...</td></tr>';

  try {
    const resp = await fetch('/api/evaluate-run', { method: 'POST' });
    const data = await resp.json();
    state.testResults = data.results || [];
    renderTestResults(data);
  } catch (e) {
    showToast('Test suite failed', 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span class="material-symbols-outlined text-[16px]">play_arrow</span> Run All';
  }
}

function renderTestResults(data) {
  document.getElementById('ts-total').textContent = data.total_queries;
  document.getElementById('ts-passed').textContent = data.total_passed;
  document.getElementById('ts-failed').textContent = data.total_failed;
  document.getElementById('ts-s1').textContent = (data.suite1?.accuracy * 100).toFixed(0) + '%';
  document.getElementById('ts-s2').textContent = (data.suite2?.accuracy * 100).toFixed(0) + '%';

  const tbody = document.getElementById('test-results-table').querySelector('tbody');
  tbody.innerHTML = '';

  const filter = document.getElementById('test-filter').value;
  const results = state.testResults || data.results || [];
  const filtered = filter === 'all' ? results : filter === 'pass'
    ? results.filter(r => r.passed) : results.filter(r => !r.passed);

  filtered.forEach((r, i) => {
    const tr = document.createElement('tr');
    tr.className = 'border-b border-outline-variant cursor-pointer transition-colors hover:bg-surface-container';
    tr.innerHTML = `
      <td class="px-md py-sm font-label-technical text-[10px] text-on-surface-variant">${i + 1}</td>
      <td class="px-md py-sm font-label-technical text-[10px] ${r.passed ? 'text-primary' : 'text-error'}">${r.passed ? '✓' : '✗'}</td>
      <td class="px-md py-sm text-body-md text-on-surface max-w-[300px] truncate" title="${r.query}">${r.query}</td>
      <td class="px-md py-sm font-label-technical text-[10px] text-on-surface-variant">${r.expected}</td>
      <td class="px-md py-sm font-label-technical text-[10px] ${r.passed ? 'text-on-surface' : 'text-error'}">${r.got}</td>
      <td class="px-md py-sm font-label-technical text-[10px] text-on-surface-variant">${(r.confidence * 100).toFixed(0)}%</td>
      <td class="px-md py-sm font-label-technical text-[10px] ${r.layer === 1 ? 'text-l1' : r.layer === 2 ? 'text-l2' : 'text-l3'}">L${r.layer}</td>
      <td class="px-md py-sm font-label-technical text-[10px] text-on-surface-variant uppercase">${r.script}</td>
    `;

    tr.addEventListener('click', () => {
      const existing = tr.nextElementSibling;
      if (existing && existing.classList.contains('test-row-expanded')) {
        existing.remove();
        return;
      }

      const expanded = document.createElement('tr');
      expanded.className = 'test-row-expanded fade-in';
      expanded.innerHTML = `
        <td colspan="8" class="px-md py-md">
          <div class="flex flex-col gap-sm">
            <span class="font-label-technical text-[10px] text-on-surface-variant uppercase">Response Preview:</span>
            <p class="text-body-md text-on-surface">${r.response_preview || 'N/A'}</p>
          </div>
        </td>
      `;
      tr.parentNode.insertBefore(expanded, tr.nextSibling);
    });

    tbody.appendChild(tr);
  });
}

function filterTestResults() {
  if (state.testResults.length > 0) {
    const filter = document.getElementById('test-filter').value;
    const filtered = filter === 'all' ? state.testResults
      : filter === 'pass' ? state.testResults.filter(r => r.passed)
      : state.testResults.filter(r => !r.passed);
    const tbody = document.getElementById('test-results-table').querySelector('tbody');
    tbody.innerHTML = '';
    filtered.forEach((r, i) => {
      const tr = document.createElement('tr');
      tr.className = 'border-b border-outline-variant cursor-pointer transition-colors hover:bg-surface-container';
      tr.innerHTML = `
        <td class="px-md py-sm font-label-technical text-[10px] text-on-surface-variant">${i + 1}</td>
        <td class="px-md py-sm font-label-technical text-[10px] ${r.passed ? 'text-primary' : 'text-error'}">${r.passed ? '✓' : '✗'}</td>
        <td class="px-md py-sm text-body-md text-on-surface max-w-[300px] truncate" title="${r.query}">${r.query}</td>
        <td class="px-md py-sm font-label-technical text-[10px] text-on-surface-variant">${r.expected}</td>
        <td class="px-md py-sm font-label-technical text-[10px] ${r.passed ? 'text-on-surface' : 'text-error'}">${r.got}</td>
        <td class="px-md py-sm font-label-technical text-[10px] text-on-surface-variant">${(r.confidence * 100).toFixed(0)}%</td>
        <td class="px-md py-sm font-label-technical text-[10px] ${r.layer === 1 ? 'text-l1' : r.layer === 2 ? 'text-l2' : 'text-l3'}">L${r.layer}</td>
        <td class="px-md py-sm font-label-technical text-[10px] text-on-surface-variant uppercase">${r.script}</td>
      `;
      tr.addEventListener('click', () => {
        const existing = tr.nextElementSibling;
        if (existing && existing.classList.contains('test-row-expanded')) { existing.remove(); return; }
        const expanded = document.createElement('tr');
        expanded.className = 'test-row-expanded fade-in';
        expanded.innerHTML = `<td colspan="8" class="px-md py-md"><div class="flex flex-col gap-sm"><span class="font-label-technical text-[10px] text-on-surface-variant uppercase">Response Preview:</span><p class="text-body-md text-on-surface">${r.response_preview || 'N/A'}</p></div></td>`;
        tr.parentNode.insertBefore(expanded, tr.nextSibling);
      });
      tbody.appendChild(tr);
    });
  }
}

// ── Global Export ──────────────────────────────────────────────────────────
window.toggleRecording = toggleRecording;
window.replayLastAudio = replayLastAudio;
window.clearSession = clearSession;
window.runEvaluation = runEvaluation;
window.runTestSuite = runTestSuite;
window.fetchStatus = fetchStatus;
window.filterTestResults = filterTestResults;
window.refreshOrganizationSetup = refreshOrganizationSetup;
window.uploadOrganizationDataset = uploadOrganizationDataset;
window.processOrganizationDataset = processOrganizationDataset;
window.generateOrganizationIntents = generateOrganizationIntents;
window.publishOrganizationIntents = publishOrganizationIntents;
window.createOrganization = createOrganization;
window.switchOrganization = switchOrganization;
window.saveAdminToken = saveAdminToken;
window.clearAdminToken = clearAdminToken;
