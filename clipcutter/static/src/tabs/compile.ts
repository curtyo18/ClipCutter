import { startCompilation, fetchCompilationStatus, cancelCompilation, fetchCompilations, deleteCompilation, deleteCompilationSources } from '../api';
import type { KeptClipInfo } from '../api';
import { escapeHtml } from '../utils';

interface CompilationClip {
  video_stem: string;
  filename: string;
  custom_name: string;
  duration: number;
}

let compilationClips: CompilationClip[] = [];
let compilationPoll: ReturnType<typeof setInterval> | null = null;

export function addSelectedToCompilation(keptClips: KeptClipInfo[]): void {
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>('.encode-cb:checked'));
  if (!checkboxes.length) { alert('Check some clips first.'); return; }
  for (const cb of checkboxes) {
    const idx = parseInt(cb.dataset.index!);
    const clip = keptClips[idx];
    if (!clip) continue;
    if (compilationClips.some(c => c.video_stem === clip.video_stem && c.filename === clip.filename)) continue;
    compilationClips.push({
      video_stem: clip.video_stem,
      filename: clip.filename,
      custom_name: clip.custom_name || clip.filename,
      duration: clip.duration || 0,
    });
  }
  renderCompilationList();
}

export function renderCompilationList(): void {
  const list = document.getElementById('compList');
  if (!list) return;
  if (compilationClips.length === 0) {
    list.innerHTML = '<div style="padding:16px;text-align:center;color:#555;font-size:13px">Add clips using the checkboxes above</div>';
    updateCompDuration();
    return;
  }
  let html = '';
  for (let i = 0; i < compilationClips.length; i++) {
    const c = compilationClips[i];
    const dur = c.duration ? Math.round(c.duration) + 's' : '';
    html += `<div class="comp-item" draggable="true" data-idx="${i}">`;
    html += `<span class="drag-handle">&#x2630;</span>`;
    html += `<span class="comp-clip-name" title="${escapeHtml(c.filename)}">${escapeHtml(c.custom_name)}</span>`;
    html += `<span class="comp-clip-dur">${dur}</span>`;
    html += `<button class="comp-remove" onclick="window._cc.removeCompClip(${i})">&times;</button>`;
    html += `</div>`;
  }
  list.innerHTML = html;
  initCompDragDrop();
  updateCompDuration();
}

export function removeCompClip(idx: number): void {
  compilationClips.splice(idx, 1);
  renderCompilationList();
}

function initCompDragDrop(): void {
  const list = document.getElementById('compList');
  if (!list) return;
  let dragIdx: number | null = null;

  list.querySelectorAll<HTMLElement>('.comp-item').forEach(item => {
    item.addEventListener('dragstart', (e: DragEvent) => {
      dragIdx = parseInt(item.dataset.idx!);
      item.style.opacity = '0.4';
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
    });
    item.addEventListener('dragend', () => { item.style.opacity = '1'; });
    item.addEventListener('dragover', (e: DragEvent) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'; });
    item.addEventListener('drop', (e: DragEvent) => {
      e.preventDefault();
      const dropIdx = parseInt(item.dataset.idx!);
      if (dragIdx !== null && dragIdx !== dropIdx) {
        const [moved] = compilationClips.splice(dragIdx, 1);
        compilationClips.splice(dropIdx, 0, moved);
        renderCompilationList();
      }
      dragIdx = null;
    });
  });
}

export function updateCompDuration(): void {
  const summary = document.getElementById('compSummary');
  const xfadeGroup = document.getElementById('compXfadeGroup');
  const transition = document.getElementById('compTransition') as HTMLSelectElement | null;

  if (xfadeGroup && transition) {
    xfadeGroup.style.display = transition.value === 'crossfade' ? 'flex' : 'none';
  }

  if (!summary) return;
  if (compilationClips.length === 0) { summary.textContent = 'No clips added yet.'; return; }

  let total = compilationClips.reduce((sum, c) => sum + (c.duration || 0), 0);
  if (transition?.value === 'crossfade' && compilationClips.length > 1) {
    const xfadeDur = parseFloat((document.getElementById('compXfadeDur') as HTMLInputElement)?.value || '0.5');
    total -= (compilationClips.length - 1) * xfadeDur;
  }
  total = Math.max(0, total);
  const m = Math.floor(total / 60);
  const s = Math.round(total % 60);
  summary.textContent = `${compilationClips.length} clips \u2014 Total: ${m}:${String(s).padStart(2, '0')}`;
}

export async function startCompilationHandler(): Promise<void> {
  if (compilationClips.length < 2) { alert('Add at least 2 clips.'); return; }

  const transition = (document.getElementById('compTransition') as HTMLSelectElement)?.value || 'cut';
  const xfadeDur = parseFloat((document.getElementById('compXfadeDur') as HTMLInputElement)?.value || '0.5');
  const title = (document.getElementById('compTitle') as HTMLInputElement)?.value?.trim() || '';

  const btn = document.getElementById('btnBuildComp') as HTMLButtonElement;
  btn.disabled = true; btn.textContent = 'Building...';

  try {
    await startCompilation({
      clips: compilationClips.map(c => ({ video_stem: c.video_stem, filename: c.filename })),
      transition, crossfade_duration: xfadeDur, title,
    });
    document.getElementById('compProgress')!.style.display = 'block';
    compilationPoll = setInterval(pollCompilationStatus, 1000);
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false; btn.textContent = 'Build';
  }
}

async function pollCompilationStatus(): Promise<void> {
  try {
    const data = await fetchCompilationStatus();
    const label = document.getElementById('compProgressLabel');
    const fill = document.getElementById('compProgressFill');
    if (label) label.textContent = data.current_step || 'Building...';
    if (fill) fill.style.width = data.progress_pct + '%';

    if (!data.running) {
      if (compilationPoll) { clearInterval(compilationPoll); compilationPoll = null; }
      const btn = document.getElementById('btnBuildComp') as HTMLButtonElement | null;
      if (btn) { btn.disabled = false; btn.textContent = 'Build'; }
      if (fill) fill.style.width = '100%';
      if (data.error) {
        if (label) label.textContent = 'Error: ' + data.error;
      } else {
        if (label) label.textContent = 'Compilation complete! ' + (data.output_filename || '');
        compilationClips = [];
        renderCompilationList();
      }
      loadPastCompilations();
    }
  } catch (e) { console.error('Compilation poll error:', e); }
}

export async function cancelCompilationHandler(): Promise<void> {
  await cancelCompilation().catch(() => {});
}

export async function deleteCompilationSourcesHandler(compId: string, clipCount: number): Promise<void> {
  if (!confirm(`Delete the ${clipCount} source clip(s) used in this compilation? This cannot be undone.`)) return;
  try {
    const data = await deleteCompilationSources(compId) as { deleted_count: number };
    alert(`Deleted ${data.deleted_count} clip file(s).`);
    loadPastCompilations();
  } catch (e) {
    alert((e as Error).message);
  }
}

export async function loadPastCompilations(): Promise<void> {
  const container = document.getElementById('pastCompilations');
  if (!container) return;
  try {
    const data = await fetchCompilations();
    const comps = (data.compilations || []).filter(c => c.file_exists);
    if (comps.length === 0) { container.innerHTML = ''; return; }
    let html = '<h3 style="font-size:14px;color:#fff;margin:16px 0 8px">Past Compilations</h3>';
    for (const comp of comps) {
      const dur = comp.total_duration ? Math.round(comp.total_duration) + 's' : '';
      html += `<div class="comp-past-item">`;
      html += `<span class="clip-name" style="flex:1">${escapeHtml(comp.filename)}</span>`;
      html += `<span class="clip-detail">${comp.clip_count} clips</span>`;
      html += `<span class="clip-detail">${dur}</span>`;
      html += `<a href="/video/compilation/${encodeURIComponent(comp.filename)}" target="_blank" class="upload-link">Play</a>`;
      html += `<button class="btn-secondary" style="font-size:12px;padding:4px 8px" onclick="window._cc.deleteCompilationSourcesHandler('${escapeHtml(comp.compilation_id)}', ${comp.clip_count})">Clean up clips</button>`;
      html += `<button class="comp-remove" onclick="window._cc.deleteCompilationHandler('${escapeHtml(comp.compilation_id)}')">&times;</button>`;
      html += `</div>`;
    }
    container.innerHTML = html;
  } catch (e) { console.error('Failed to load compilations:', e); }
}

export async function deleteCompilationHandler(compId: string): Promise<void> {
  if (!confirm('Delete this compilation?')) return;
  try {
    await deleteCompilation(compId);
    loadPastCompilations();
  } catch (e) { alert((e as Error).message); }
}
