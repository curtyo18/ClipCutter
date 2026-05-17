import { startCompilation, fetchCompilationStatus, fetchCompilations, deleteCompilation, deleteCompilationSources } from '../api';
import type { KeptClipInfo } from '../api';
import { escapeHtml, openPreviewModal } from '../utils';
import { tasks } from '../tasks';

interface CompilationClip {
  video_stem: string;
  filename: string;
  custom_name: string;
  duration: number;
}

let compilationClips: CompilationClip[] = [];

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
  updateCompCountBadge();
  const list = document.getElementById('compList');
  if (!list) return;
  if (compilationClips.length === 0) {
    list.innerHTML = `<div style="padding:24px;text-align:center;color:var(--cc-fg-dim);font-size:var(--cc-fs-sm)">Select clips on the Clips sub-tab and click "+ Add selected".</div>`;
    updateCompDuration();
    return;
  }
  const transition = (document.getElementById('compTransition') as HTMLSelectElement | null)?.value ?? 'cut';
  const xfadeDur = parseFloat((document.getElementById('compXfadeDur') as HTMLInputElement | null)?.value ?? '0.5');
  const transLabel = transition === 'crossfade'
    ? `↔ crossfade · ${xfadeDur.toFixed(2)}s`
    : '⎯ hard cut';

  let html = '';
  for (let i = 0; i < compilationClips.length; i++) {
    const c = compilationClips[i];
    const dur = c.duration ? `${Math.round(c.duration)}s` : '';
    html += `
      <div class="cc-comp-row" draggable="true" data-idx="${i}">
        <span class="cc-comp-grip" aria-hidden="true">⋮⋮</span>
        <span class="cc-comp-num">${String(i + 1).padStart(2, '0')}</span>
        <span class="cc-comp-name" title="${escapeHtml(c.filename)}">${escapeHtml(c.custom_name)}</span>
        <span class="cc-comp-dur">${dur}</span>
        <button class="cc-btn" data-variant="danger" data-size="sm" onclick="window._cc.removeCompClip(${i})" title="Remove">×</button>
      </div>
    `;
    if (i < compilationClips.length - 1) {
      html += `<div class="cc-comp-trans">${transLabel}</div>`;
    }
  }
  list.innerHTML = html;
  initCompDragDrop();
  updateCompDuration();
}

function updateCompCountBadge(): void {
  const el = document.getElementById('compCount');
  if (el) el.textContent = String(compilationClips.length);
}

export function removeCompClip(idx: number): void {
  compilationClips.splice(idx, 1);
  renderCompilationList();
}

function initCompDragDrop(): void {
  const list = document.getElementById('compList');
  if (!list) return;
  let dragIdx: number | null = null;

  list.querySelectorAll<HTMLElement>('.cc-comp-row').forEach(item => {
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
  summary.textContent = `${compilationClips.length} clips \u00b7 ${m}:${String(s).padStart(2, '0')} total`;

  // Re-render the list so the transition labels reflect the new transition / xfade duration
  // (only if a list already exists \u2014 avoids infinite recursion since renderCompilationList
  // calls updateCompDuration).
  // Skipped for now: keep the row labels stable; updateCompDuration is called frequently
  // by oninput handlers and re-rendering each keystroke would lose drag state.
}

export async function startCompilationHandler(): Promise<void> {
  if (compilationClips.length < 2) { alert('Add at least 2 clips.'); return; }
  if (tasks.isRunning('compile')) { alert('A compilation is already being built.'); return; }

  const transition = (document.getElementById('compTransition') as HTMLSelectElement)?.value || 'cut';
  const xfadeDur = parseFloat((document.getElementById('compXfadeDur') as HTMLInputElement)?.value || '0.5');
  const title = (document.getElementById('compTitle') as HTMLInputElement)?.value?.trim() || '';

  const btn = document.getElementById('btnBuildComp') as HTMLButtonElement;
  btn.disabled = true; btn.textContent = 'Building…';

  try {
    await startCompilation({
      clips: compilationClips.map(c => ({ video_stem: c.video_stem, filename: c.filename })),
      transition, crossfade_duration: xfadeDur, title,
    });
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false; btn.textContent = 'Build compilation';
    return;
  }

  const clipsForLabel = compilationClips.length;

  tasks.start({
    kind: 'compile',
    label: 'Building compilation',
    subtitle: `${clipsForLabel} clips · ${transition}`,
    pollMs: 1000,
    fetchStatus: async () => {
      const data = await fetchCompilationStatus();
      return {
        running: data.running,
        pct: data.progress_pct,
        subtitle: data.current_step || `${clipsForLabel} clips`,
        error: data.error || null,
      };
    },
    formatResult: (t) => t.subtitle ?? `${clipsForLabel} clips`,
  });
}

tasks.addEventListener('task-complete', (e) => {
  const t = (e as CustomEvent).detail.task;
  if (t.kind !== 'compile') return;
  const btn = document.getElementById('btnBuildComp') as HTMLButtonElement | null;
  if (btn) { btn.disabled = false; btn.textContent = 'Build compilation'; }
  if (!t.error) {
    compilationClips = [];
    renderCompilationList();
  }
  void loadPastCompilations();
});

export async function deleteCompilationSourcesHandler(compId: string | undefined, clipCount: string | number | undefined): Promise<void> {
  if (!compId) return;
  const count = typeof clipCount === 'string' ? parseInt(clipCount, 10) : (clipCount ?? 0);
  if (!confirm(`Delete the ${count} source clip(s) used in this compilation? This cannot be undone.`)) return;
  try {
    const data = await deleteCompilationSources(compId);
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
    const rows = comps.map(comp => {
      const dur = comp.total_duration ? `${Math.round(comp.total_duration)}s` : '';
      return `
        <div style="display:flex;align-items:center;gap:var(--cc-gap);padding:8px 12px;border-bottom:1px solid var(--cc-line-soft)">
          <span class="cc-mono" style="flex:1;color:var(--cc-fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(comp.filename)}</span>
          <span class="cc-num cc-dim">${comp.clip_count} clips</span>
          <span class="cc-num cc-dim">${dur}</span>
          <button class="cc-btn" data-variant="ghost" data-size="sm"
                  data-filename="${escapeHtml(comp.filename)}"
                  onclick="window._cc.previewCompilation(this.dataset.filename)">▶ Play</button>
          <button class="cc-btn" data-variant="ghost" data-size="sm"
                  data-comp-id="${escapeHtml(comp.compilation_id)}" data-clip-count="${comp.clip_count}"
                  onclick="window._cc.deleteCompilationSourcesHandler(this.dataset.compId, this.dataset.clipCount)">Clean up clips</button>
          <button class="cc-btn" data-variant="danger" data-size="sm"
                  data-comp-id="${escapeHtml(comp.compilation_id)}"
                  onclick="window._cc.deleteCompilationHandler(this.dataset.compId)">×</button>
        </div>
      `;
    }).join('');
    container.innerHTML = `
      <div class="cc-h" style="margin:var(--cc-gap-2) var(--cc-pad-2) var(--cc-gap)">Past compilations</div>
      ${rows}
    `;
  } catch (e) { console.error('Failed to load compilations:', e); }
}

export function previewCompilation(filename: string | undefined): void {
  if (!filename) return;
  openPreviewModal('/video/compilation/' + encodeURIComponent(filename));
}

export async function deleteCompilationHandler(compId: string | undefined): Promise<void> {
  if (!compId) return;
  if (!confirm('Delete this compilation?')) return;
  try {
    await deleteCompilation(compId);
    loadPastCompilations();
  } catch (e) { alert((e as Error).message); }
}
