import { fetchDefaults, startProcessing, fetchProcessStatus } from '../api';
import { escapeHtml } from '../utils';

let pollTimer: ReturnType<typeof setInterval> | null = null;

export async function initProcessTab(): Promise<void> {
  try {
    const data = await fetchDefaults();
    const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
    if (folderInput) folderInput.value = data.folder;
  } catch (e) {
    console.error('Failed to load defaults:', e);
  }
}

export async function startProcessingHandler(): Promise<void> {
  const folderInput = document.getElementById('folderPath') as HTMLInputElement | null;
  const folder = folderInput?.value.trim() ?? '';
  if (!folder) { alert('Enter a folder path.'); return; }

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
    pollTimer = setInterval(pollStatus, 800);
  } catch (e) {
    alert('Error: ' + (e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Process';
  }
}

async function pollStatus(): Promise<void> {
  try {
    const data = await fetchProcessStatus();
    const box = document.getElementById('logBox')!;
    box.innerHTML = data.log.map(line => `<div class="log-line">${escapeHtml(line)}</div>`).join('');
    box.scrollTop = box.scrollHeight;

    if (!data.running) {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      const btn = document.getElementById('btnProcess') as HTMLButtonElement;
      btn.disabled = false;
      btn.textContent = 'Process';

      if (data.error) {
        box.innerHTML += `<div class="log-line log-error">Error: ${escapeHtml(data.error)}</div>`;
      } else {
        box.innerHTML += `<div class="log-line log-done">Done! Switch to Review tab to review clips.</div>`;
      }
      box.scrollTop = box.scrollHeight;
    }
  } catch (e) {
    console.error('Poll error:', e);
  }
}
