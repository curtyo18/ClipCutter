import { fetchDefaults, startProcessing, fetchProcessStatus, fetchFolderScan, deleteFolderFile, VideoEntry, FolderScanResult } from '../api';
import { escapeHtml } from '../utils';
import { tasks } from '../tasks';

let lastScanResult: FolderScanResult | null = null;
let lastScanFolder = '';

const PROCESS_BTN_LABEL = '▶ Process folder';

export async function initProcessTab(): Promise<void> {
  // Bind slider value-displays
  bindSlider('sensitivity', 'sensitivityVal', (n) => n.toFixed(1));
  bindSlider('context',     'contextVal',     (n) => `${Math.round(n)}s`);
  bindSlider('staleThreshold', 'staleThresholdVal', (n) => `${Math.round(n)}d`);
  // Stale slider also re-filters the stale list as it moves
  document.getElementById('staleThreshold')?.addEventListener('input', () => {
    if (lastScanResult) renderStaleCandidates(lastScanResult, getThreshold());
  });

  try {
    const data = await fetchDefaults();
    const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
    if (folderInput) {
      folderInput.value = data.folder;
      await scanFolder(data.folder);
    }
  } catch (e) {
    console.error('Failed to load defaults:', e);
  }
}

function bindSlider(inputId: string, valueId: string, fmt: (n: number) => string): void {
  const input = document.getElementById(inputId) as HTMLInputElement | null;
  const out = document.getElementById(valueId);
  if (!input || !out) return;
  const sync = (): void => { out.textContent = fmt(parseFloat(input.value)); };
  sync();
  input.addEventListener('input', sync);
}

export async function scanFolderHandler(): Promise<void> {
  const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
  const folder = folderInput?.value.trim() ?? '';
  if (!folder) { setPanelMessage('Enter a folder path.'); return; }
  await scanFolder(folder);
}

async function scanFolder(folder: string): Promise<void> {
  const btn = document.getElementById('btnScan') as HTMLButtonElement | null;
  if (btn) { btn.disabled = true; btn.textContent = 'Scanning...'; }
  clearScanError();

  try {
    const result = await fetchFolderScan(folder);
    lastScanResult = result;
    lastScanFolder = folder;
    renderFolderStats(result);
    renderVideosInFolder(result);
    renderStaleCandidates(result, getThreshold());
    updatePanelTitle(folder, result);
  } catch (e) {
    showScanError((e as Error).message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Scan'; }
  }
}

function getThreshold(): number {
  const input = document.getElementById('staleThreshold') as HTMLInputElement | null;
  const val = parseInt(input?.value ?? '30', 10);
  return isNaN(val) || val < 1 ? 30 : val;
}

function updatePanelTitle(folder: string, result: FolderScanResult): void {
  const el = document.getElementById('folderPanelTitle');
  if (!el) return;
  const name = folder.split(/[\\/]/).filter(Boolean).pop() ?? folder;
  el.textContent = `${name} · ${result.videos.length} video${result.videos.length === 1 ? '' : 's'}`;
}

function renderFolderStats(result: FolderScanResult): void {
  const grid = document.getElementById('folderStats');
  if (!grid) return;
  const counts = { unprocessed: 0, pending_review: 0, processed: 0 };
  for (const v of result.videos) counts[v.status]++;
  setText('statUnprocessed', String(counts.unprocessed));
  setText('statPending',     String(counts.pending_review));
  setText('statProcessed',   String(counts.processed));
  grid.style.display = result.videos.length > 0 ? 'grid' : 'none';
}

function setText(id: string, text: string): void {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function renderVideosInFolder(result: FolderScanResult): void {
  const section = document.getElementById('videosInFolderSection')!;
  if (result.videos.length === 0) {
    section.innerHTML = `
      <div style="padding:24px;text-align:center;color:var(--cc-fg-dim);font-size:var(--cc-fs-sm)">
        No video files found in this folder.
      </div>`;
    return;
  }

  const rows = result.videos.map(v => {
    const pill = pillFor(v.status);
    const safeAttr = htmlAttr(v.filename);
    return `<tr>
      <td class="cc-mono" style="width:46%">${escapeHtml(v.filename)}</td>
      <td class="cc-num cc-fg2" style="width:80px">${formatSize(v.size_mb)}</td>
      <td class="cc-num cc-dim" style="width:70px">${v.age_days}d</td>
      <td style="width:140px"><span class="cc-pill" data-status="${pill.status}">${pill.text}</span></td>
      <td style="width:90px;text-align:right">
        <button class="cc-btn" data-variant="danger" data-size="sm"
                data-filename="${safeAttr}" onclick="event.stopPropagation();window._cc.deleteFileHandler(this)">Delete</button>
      </td>
    </tr>`;
  }).join('');

  section.innerHTML = `
    <table class="cc-table">
      <thead>
        <tr>
          <th>Filename</th>
          <th>Size</th>
          <th>Age</th>
          <th>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderStaleCandidates(result: FolderScanResult, thresholdDays: number): void {
  const section = document.getElementById('staleCandidatesSection')!;
  const stale = result.videos.filter(v =>
    v.age_days >= thresholdDays && v.status !== 'pending_review'
  );

  if (stale.length === 0) {
    section.style.display = 'none';
    section.innerHTML = '';
    return;
  }

  section.style.display = 'block';
  section.style.borderTop = '1px solid var(--cc-line)';

  const rows = stale.map(v => {
    const safeAttr = htmlAttr(v.filename);
    const reviewedNote = v.status === 'unprocessed' ? 'never reviewed' : 'reviewed';
    return `<tr>
      <td class="cc-mono" style="width:46%">${escapeHtml(v.filename)}</td>
      <td class="cc-num cc-fg2" style="width:80px">${formatSize(v.size_mb)}</td>
      <td class="cc-num cc-dim" style="width:70px">${v.age_days}d</td>
      <td style="width:140px"><span class="cc-pill" data-status="stale">${escapeHtml(reviewedNote)}</span></td>
      <td style="width:90px;text-align:right">
        <button class="cc-btn" data-variant="danger" data-size="sm"
                data-filename="${safeAttr}" onclick="window._cc.deleteFileHandler(this)">Delete</button>
      </td>
    </tr>`;
  }).join('');

  section.innerHTML = `
    <div class="cc-panel-head" style="border-top:0">
      <span style="color:var(--cc-warn)">⚠ Stale (${stale.length})</span>
      <span class="cc-dim" style="font-weight:400;text-transform:none;letter-spacing:0;margin-left:6px">· reviewed &gt; ${thresholdDays}d ago</span>
      <span class="cc-spacer" style="flex:1"></span>
    </div>
    <div class="cc-panel-body">
      <table class="cc-table"><tbody>${rows}</tbody></table>
    </div>`;
}

export async function deleteFileHandler(btn: HTMLButtonElement): Promise<void> {
  const filename = btn.dataset.filename ?? '';
  if (!filename || !lastScanFolder) return;
  btn.disabled = true;
  btn.textContent = '...';

  try {
    await deleteFolderFile(lastScanFolder, filename);
    await scanFolder(lastScanFolder);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Delete';
    btn.parentElement?.querySelectorAll('.delete-error').forEach(el => el.remove());
    btn.insertAdjacentHTML('afterend', ` <span class="delete-error" style="color:var(--cc-bad);font-size:var(--cc-fs-xs);margin-left:6px">${escapeHtml((e as Error).message)}</span>`);
  }
}

export async function startProcessingHandler(): Promise<void> {
  const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
  const folder = folderInput?.value.trim() ?? '';
  if (!folder) { alert('Enter a folder path.'); return; }

  if (tasks.isRunning('process')) {
    alert('A processing task is already running.');
    return;
  }

  const sensitivity = parseFloat((document.getElementById('sensitivity') as HTMLInputElement).value) || 1.0;
  const context = parseFloat((document.getElementById('context') as HTMLInputElement).value) || 20;

  const btn = document.getElementById('btnProcess') as HTMLButtonElement;
  btn.disabled = true;
  btn.textContent = 'Processing...';
  const logBox = document.getElementById('logBox')!;
  logBox.innerHTML = '';

  try {
    await startProcessing({ folder, sensitivity, context });
  } catch (e) {
    alert('Error: ' + (e as Error).message);
    btn.disabled = false;
    btn.textContent = PROCESS_BTN_LABEL;
    return;
  }

  let loggedCount = 0;
  const startedAt = Date.now();
  startElapsedTicker();

  tasks.start({
    kind: 'process',
    label: 'Processing folder',
    subtitle: folder,
    pollMs: 800,
    fetchStatus: async () => {
      const s = await fetchProcessStatus();
      const newLines = s.log.slice(loggedCount);
      loggedCount = s.log.length;
      // Mirror the log into the legacy logBox so the Process tab's
      // behaviour is preserved when the modal is dismissed.
      if (newLines.length) {
        const box = document.getElementById('logBox');
        if (box) {
          for (const line of newLines) appendLogLine(box, line);
          box.scrollTop = box.scrollHeight;
        }
      }
      return {
        running: s.running,
        newLogLines: newLines,
        subtitle: s.log[s.log.length - 1] ?? folder,
        error: s.error,
      };
    },
    formatResult: (t) => {
      const dur = t.finishedAt && t.startedAt ? Math.round((t.finishedAt - t.startedAt) / 1000) : Math.round((Date.now() - startedAt) / 1000);
      return `${t.log.length} log line${t.log.length === 1 ? '' : 's'} in ${dur}s`;
    },
  });
}

// Subscribe once to flip the legacy Process button back when the
// process task finishes (regardless of whether the modal is open).
tasks.addEventListener('task-complete', (e) => {
  const t = (e as CustomEvent).detail.task;
  if (t.kind !== 'process') return;
  stopElapsedTicker();
  const btn = document.getElementById('btnProcess') as HTMLButtonElement | null;
  if (btn) { btn.disabled = false; btn.textContent = PROCESS_BTN_LABEL; }
  const box = document.getElementById('logBox');
  if (box) {
    const div = document.createElement('div');
    div.className = 'cc-log-line';
    div.innerHTML = t.error
      ? `<span class="cc-log-tag" data-kind="warn">[error]</span><span style="color:var(--cc-bad)">${escapeHtml(t.error)}</span>`
      : `<span class="cc-log-tag" data-kind="hit">[done]</span><span>Switch to Review tab to review clips.</span>`;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }
});

/** Re-scan whatever folder was last scanned. Used by the task-complete hook. */
export function scanCurrentFolder(): void {
  if (lastScanFolder) {
    void scanFolder(lastScanFolder);
  }
}

// ---- Helpers ----

function pillFor(status: VideoEntry['status']): { status: string; text: string } {
  switch (status) {
    case 'processed':      return { status: 'processed',   text: 'processed' };
    case 'pending_review': return { status: 'pending',     text: 'pending review' };
    case 'unprocessed':    return { status: 'unprocessed', text: 'unprocessed' };
  }
}

function formatSize(mb: number): string {
  if (mb >= 1024) return (mb / 1024).toFixed(1) + ' GB';
  return mb.toFixed(0) + ' MB';
}

function htmlAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function setPanelMessage(msg: string): void {
  const section = document.getElementById('videosInFolderSection');
  if (!section) return;
  section.innerHTML = `<div style="padding:24px;text-align:center;color:var(--cc-bad);font-size:var(--cc-fs-sm)">${escapeHtml(msg)}</div>`;
}

function clearScanError(): void {
  const existing = document.getElementById('scanError');
  if (existing) existing.remove();
}

function showScanError(msg: string): void {
  setPanelMessage(msg);
}

function appendLogLine(box: HTMLElement, line: string): void {
  // Try to parse "[HH:MM:SS] message" out of pipeline log strings; fall back to plain.
  const m = /^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/.exec(line);
  const div = document.createElement('div');
  div.className = 'cc-log-line';
  if (m) {
    const time = document.createElement('span');
    time.className = 'cc-log-time';
    time.textContent = m[1];
    const tag = document.createElement('span');
    tag.className = 'cc-log-tag';
    const lower = m[2].toLowerCase();
    tag.dataset.kind = lower.startsWith('error') ? 'warn' : (lower.includes('found') || lower.includes('clip')) ? 'hit' : 'info';
    tag.textContent = '[info]';
    const msg = document.createElement('span');
    msg.textContent = m[2];
    div.append(time, tag, msg);
  } else {
    div.textContent = line;
  }
  box.appendChild(div);
}

let elapsedTimer: ReturnType<typeof setInterval> | null = null;
function startElapsedTicker(): void {
  stopElapsedTicker();
  const start = Date.now();
  const el = document.getElementById('logElapsed');
  if (!el) return;
  const tick = (): void => {
    const s = Math.round((Date.now() - start) / 1000);
    const m = Math.floor(s / 60);
    const rs = s % 60;
    el.textContent = `${String(m).padStart(2, '0')}:${String(rs).padStart(2, '0')} elapsed`;
  };
  tick();
  elapsedTimer = setInterval(tick, 1000);
}
function stopElapsedTicker(): void {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
}
