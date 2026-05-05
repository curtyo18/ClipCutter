import { fetchDefaults, startProcessing, fetchProcessStatus, fetchFolderScan, deleteFolderFile, VideoEntry, FolderScanResult } from '../api';
import { escapeHtml } from '../utils';
import { tasks } from '../tasks';

let lastScanResult: FolderScanResult | null = null;
let lastScanFolder = '';

export async function initProcessTab(): Promise<void> {
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

export async function scanFolderHandler(): Promise<void> {
  const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
  const folder = folderInput?.value.trim() ?? '';
  if (!folder) { alert('Enter a folder path.'); return; }
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
    renderVideosInFolder(result);
    const threshold = getThreshold();
    renderStaleCandidates(result, threshold);
    document.getElementById('folderScanPanel')!.style.display = 'block';
  } catch (e) {
    showScanError((e as Error).message);
    document.getElementById('folderScanPanel')!.style.display = 'none';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Scan'; }
  }
}

function getThreshold(): number {
  const input = document.getElementById('staleThreshold') as HTMLInputElement | null;
  const val = parseInt(input?.value ?? '30', 10);
  return isNaN(val) || val < 1 ? 30 : val;
}

function renderVideosInFolder(result: FolderScanResult): void {
  const section = document.getElementById('videosInFolderSection')!;
  if (result.videos.length === 0) {
    section.innerHTML = `
      <div class="scan-panel-header">
        <span class="scan-panel-title">Videos in Folder</span>
        <span class="scan-panel-meta">No video files found</span>
      </div>`;
    return;
  }

  const totalGb = (result.total_size_mb / 1024).toFixed(2);
  const rows = result.videos.map(v => {
    const [statusClass, statusText] = statusDisplay(v.status);
    return `<tr>
      <td class="filename">${escapeHtml(v.filename)}</td>
      <td>${formatSize(v.size_mb)}</td>
      <td>${v.age_days}d</td>
      <td class="${statusClass}">${statusText}</td>
    </tr>`;
  }).join('');

  section.innerHTML = `
    <div class="scan-panel-header">
      <span class="scan-panel-title">Videos in Folder</span>
      <span class="scan-panel-meta">${result.videos.length} video${result.videos.length !== 1 ? 's' : ''} &middot; ${totalGb} GB</span>
    </div>
    <table class="scan-table">
      <thead><tr><th>Filename</th><th>Size</th><th>Age</th><th>Status</th></tr></thead>
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
    return;
  }

  section.style.display = 'block';
  const totalMb = stale.reduce((sum, v) => sum + v.size_mb, 0);
  const totalGb = (totalMb / 1024).toFixed(2);

  const rows = stale.map(v => {
    const category = v.status === 'unprocessed' ? 'unprocessed' : 'processed, kept';
    const rowClass = v.status === 'unprocessed' ? 'scan-stale-unprocessed' : 'scan-stale-processed';
    const safeAttr = v.filename.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `<tr>
      <td class="filename ${rowClass}">${escapeHtml(v.filename)}</td>
      <td>${formatSize(v.size_mb)}</td>
      <td style="color:#f87171">${v.age_days}d</td>
      <td style="color:#64748b">${category}</td>
      <td><button class="btn-delete-file" data-filename="${safeAttr}" onclick="window._cc.deleteFileHandler(this)">Delete</button></td>
    </tr>`;
  }).join('');

  section.innerHTML = `
    <div class="scan-panel-header">
      <span class="scan-panel-title stale">Stale Candidates</span>
      <div class="stale-threshold-row">
        Threshold: <input id="staleThreshold" type="number" value="${thresholdDays}" min="1" oninput="window._cc.thresholdChangedHandler()" />
        days &nbsp;&middot;&nbsp; ${stale.length} file${stale.length !== 1 ? 's' : ''} &middot; ${totalGb} GB
      </div>
    </div>
    <table class="scan-table">
      <thead><tr><th>Filename</th><th>Size</th><th>Age</th><th>Category</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

export function thresholdChangedHandler(): void {
  if (!lastScanResult) return;
  renderStaleCandidates(lastScanResult, getThreshold());
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
    btn.insertAdjacentHTML('afterend', ` <span class="delete-error" style="color:#f87171;font-size:11px">${escapeHtml((e as Error).message)}</span>`);
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
  const ctxVal = (document.getElementById('context') as HTMLInputElement).value.trim();
  const context = ctxVal ? parseFloat(ctxVal) : null;

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
    btn.textContent = 'Process';
    return;
  }

  let loggedCount = 0;
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
          for (const line of newLines) {
            const div = document.createElement('div');
            div.className = 'log-line';
            div.textContent = line;
            box.appendChild(div);
          }
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
    formatResult: (t) => `${t.log.length} log line${t.log.length === 1 ? '' : 's'}`,
  });
}

// Subscribe once to flip the legacy Process button back when the
// process task finishes (regardless of whether the modal is open).
tasks.addEventListener('task-complete', (e) => {
  const t = (e as CustomEvent).detail.task;
  if (t.kind !== 'process') return;
  const btn = document.getElementById('btnProcess') as HTMLButtonElement | null;
  if (btn) { btn.disabled = false; btn.textContent = 'Process'; }
  const box = document.getElementById('logBox');
  if (box) {
    const div = document.createElement('div');
    div.className = t.error ? 'log-line log-error' : 'log-line log-done';
    div.textContent = t.error
      ? 'Error: ' + t.error
      : 'Done! Switch to Review tab to review clips.';
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

function statusDisplay(status: VideoEntry['status']): [string, string] {
  switch (status) {
    case 'processed':    return ['scan-status-processed', '✓ processed'];
    case 'pending_review': return ['scan-status-pending', '⏳ pending review'];
    case 'unprocessed':  return ['scan-status-unprocessed', '✗ unprocessed'];
  }
}

function formatSize(mb: number): string {
  if (mb >= 1024) return (mb / 1024).toFixed(1) + ' GB';
  return mb.toFixed(0) + ' MB';
}

function clearScanError(): void {
  const existing = document.getElementById('scanError');
  if (existing) existing.remove();
}

function showScanError(msg: string): void {
  clearScanError();
  const form = document.querySelector('.form-section')!;
  const err = document.createElement('div');
  err.id = 'scanError';
  err.className = 'scan-panel-error';
  err.style.marginTop = '8px';
  err.textContent = msg;
  form.appendChild(err);
}
