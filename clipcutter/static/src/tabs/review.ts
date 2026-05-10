import { fetchClips, keepClip, fetchKeepStatus, discardClip, fetchSources, deleteSource } from '../api';
import type { ClipInfo } from '../api';
import { fmtTime, fmtTimePrecise, escapeHtml, attachVolumePreference } from '../utils';
import { loadWaveform, stopWaveformSync, updateWaveformTrimMarkers, REGION_COLORS, REGION_LABELS } from '../waveform';
import { tasks } from '../tasks';

interface SegmentEntry {
  start: number;
  end: number;
}

let clips: ClipInfo[] = [];
let currentIndex = 0;
let results: Array<'kept' | 'discarded' | 'skipped' | null> = [];
let segments: SegmentEntry[] = [];
let activeSegmentIndex = 0;
let queueOpen = true;

let reviewListenerAttached = false;

export function getActiveSegmentIndex(): number {
  return activeSegmentIndex;
}

function attachReviewListener(): void {
  if (reviewListenerAttached) return;
  const container = document.getElementById('reviewContent');
  if (!container) return;
  reviewListenerAttached = true;
  container.addEventListener('click', (e: MouseEvent) => {
    const clicked = e.target as HTMLElement;
    if (clicked.tagName === 'INPUT' || clicked.tagName === 'SELECT' || clicked.tagName === 'TEXTAREA') return;
    const target = clicked.closest('[data-action]') as HTMLElement | null;
    if (!target) return;
    const action = target.dataset.action!;
    const seg = target.dataset.seg !== undefined ? parseInt(target.dataset.seg, 10) : activeSegmentIndex;
    const which = (target.dataset.which ?? 'in') as 'in' | 'out';
    const stem = target.dataset.stem ?? '';
    const idx = target.dataset.idx !== undefined ? parseInt(target.dataset.idx, 10) : -1;

    switch (action) {
      case 'keep':           clipAction('keep'); break;
      case 'skip':           clipAction('skip'); break;
      case 'discard':        clipAction('discard'); break;
      case 'add-segment':    addSegment(); break;
      case 'remove-segment': removeSegment(seg); break;
      case 'focus-segment':  focusSegment(seg); break;
      case 'set-point':      setSegmentPoint(seg, which); break;
      case 'seek-to':        seekToSegment(seg, which); break;
      case 'delete-source':  deleteSourceHandler(stem, target as HTMLButtonElement); break;
      case 'jump-to-clip':   if (idx >= 0) jumpToClip(idx); break;
      case 'toggle-queue':   toggleQueue(); break;
    }
  });
}

export async function loadClips(): Promise<void> {
  attachReviewListener();
  const data = await fetchClips();
  clips = data.clips;
  results = new Array(clips.length).fill(null);
  currentIndex = 0;
  clips.length === 0 ? showEmpty() : showClip();
}

function showEmpty(): void {
  const hdr = document.getElementById('headerRight');
  if (hdr) hdr.textContent = '';
  document.getElementById('reviewContent')!.innerHTML =
    '<div class="empty-state">No pending clips. Process some videos first.</div>';
}

function jumpToClip(idx: number): void {
  if (idx < 0 || idx >= clips.length) return;
  currentIndex = idx;
  showClip();
}

function toggleQueue(): void {
  queueOpen = !queueOpen;
  const root = document.querySelector<HTMLElement>('.cc-review');
  if (root) root.dataset.queue = queueOpen ? 'shown' : 'hidden';
  const btn = document.getElementById('toggleQueueBtn');
  if (btn) btn.textContent = queueOpen ? '⤍ Hide queue' : '⤎ Show queue';
}

function showClip(): void {
  if (currentIndex >= clips.length) { showDone(); return; }

  const clip = clips[currentIndex];
  const hdr = document.getElementById('headerRight');
  if (hdr) hdr.textContent = '';

  // Compose head meta
  const dur = clip.duration ? fmtTimePrecise(clip.duration) : '';
  const conf = Math.round(clip.confidence * 100);
  const reasonLabels = (clip.detection_reasons || []).map(r =>
    REGION_LABELS[r] ?? r.replace('_', ' ')
  );

  // Initial single segment over the whole clip
  segments = [{ start: 0, end: clip.duration }];
  activeSegmentIndex = 0;

  document.getElementById('reviewContent')!.innerHTML = `
    <div class="cc-review" data-queue="${queueOpen ? 'shown' : 'hidden'}">
      <div class="cc-review-head">
        <span class="cc-clip-name">${escapeHtml(clip.filename)}</span>
        <span class="cc-clip-meta">· ${dur}${dur ? ' · ' : ''}${conf}% confidence · ${escapeHtml(reasonLabels.join(', '))}</span>
        <span style="flex:1"></span>
        <span class="cc-review-pos">
          <span>${currentIndex + 1}</span><span class="cc-dim">/ ${clips.length}</span>
        </span>
        <button class="cc-btn" data-variant="ghost" data-size="sm" id="toggleQueueBtn" data-action="toggle-queue">${queueOpen ? '⤍ Hide queue' : '⤎ Show queue'}</button>
      </div>

      <div class="cc-review-main">
        <div class="cc-video">
          <video id="player" controls autoplay
                 style="width:100%;height:100%;object-fit:contain;background:#000">
            <source src="${clip.video_url}" type="video/mp4">
          </video>
        </div>

        <div class="cc-wave-wrap">
          <div class="cc-wave-meta">
            <span style="text-transform:uppercase;letter-spacing:0.1em">Waveform</span>
            <span style="flex:1"></span>
            <div class="cc-wave-legend">
              ${Object.entries(REGION_COLORS).filter(([k]) => k !== 'fallback').map(([k, c]) => `
                <span class="cc-wave-legend-item">
                  <span class="cc-legend-swatch" style="background:${c}"></span>
                  <span class="cc-dim" style="text-transform:uppercase;font-size:10px;letter-spacing:0.1em">${REGION_LABELS[k] ?? k}</span>
                </span>
              `).join('')}
            </div>
          </div>
          <div class="cc-wave" id="waveform"></div>
        </div>

        <div class="cc-trim-bar">
          <button class="cc-btn" data-size="sm" data-action="set-point" data-which="in" id="btnSetIn">⟸ Set IN <span class="cc-kbd">I</span></button>
          <button class="cc-btn" data-size="sm" data-action="set-point" data-which="out" id="btnSetOut">Set OUT ⟹ <span class="cc-kbd">O</span></button>
          <button class="cc-btn" data-variant="ghost" data-size="sm" data-action="add-segment">+ Add segment</button>
          <span id="trimIndicator" class="cc-mono cc-dim" style="margin-left:8px;font-size:var(--cc-fs-xs)"></span>
          <span style="flex:1"></span>
          <span class="cc-label">Quality</span>
          <select class="cc-select" id="trimQuality" style="width:140px">
            <option value="copy">Fast (copy)</option>
            <option value="precise" selected>Precise (CRF 16)</option>
            <option value="ultra">Ultra (lossless)</option>
          </select>
          <span class="cc-label">Name</span>
          <input class="cc-input" id="clipCustomName" placeholder="optional" style="width:180px">
        </div>

        <div class="cc-segments" id="segmentList"></div>
      </div>

      <div class="cc-queue">
        <div class="cc-queue-head">
          <span>Queue · ${clips.length}</span>
          <span style="flex:1"></span>
          <span class="cc-num cc-dim" style="font-size:10px">${currentIndex + 1} of ${clips.length}</span>
        </div>
        <div class="cc-queue-list" id="queueList"></div>
      </div>

      <div class="cc-review-actions">
        <button class="cc-btn cc-action-keep" data-action="keep">✓ Keep <span class="cc-kbd">K</span></button>
        <button class="cc-btn cc-action-skip" data-variant="ghost" data-action="skip">↷ Skip <span class="cc-kbd">S</span></button>
        <button class="cc-btn cc-action-discard" data-variant="danger" data-action="discard">✕ Discard <span class="cc-kbd">D</span></button>
        <span style="flex:1"></span>
        <span class="cc-dim cc-mono" style="font-size:10px">
          ${(clip.detection_reasons || []).map(r => `<span style="color:${REGION_COLORS[r] ?? '#888'};margin-right:8px">● ${REGION_LABELS[r] ?? r}</span>`).join('')}
        </span>
      </div>
    </div>
  `;

  const playerEl = document.getElementById('player') as HTMLVideoElement | null;
  if (playerEl) attachVolumePreference(playerEl);

  loadWaveform(clip.video_stem, clip.filename, clip.highlight_regions || []);
  renderSegments();
  renderQueue();

  // Click-to-seek on waveform
  const wave = document.getElementById('waveform');
  if (wave) {
    wave.addEventListener('click', (e: MouseEvent) => {
      const player = document.getElementById('player') as HTMLVideoElement | null;
      if (!player || !player.duration) return;
      const rect = wave.getBoundingClientRect();
      player.currentTime = ((e.clientX - rect.left) / rect.width) * player.duration;
    });
  }
}

function renderSegments(): void {
  const list = document.getElementById('segmentList');
  if (!list) return;
  const html = segments.map((seg, i) => {
    const isActive = i === activeSegmentIndex;
    const dur = Math.max(0, seg.end - seg.start);
    const isOnly = segments.length === 1;
    return `
      <div class="cc-segment" data-active="${isActive}" data-action="focus-segment" data-seg="${i}" style="cursor:pointer">
        <span class="cc-segment-num">${String(i + 1).padStart(2, '0')}</span>
        <span class="cc-segment-times">${fmtTimePrecise(seg.start)}<span class="cc-dim"> → </span>${fmtTimePrecise(seg.end)}</span>
        <span class="cc-segment-dur">${fmtTimePrecise(dur)}</span>
        <button class="cc-btn" data-variant="ghost" data-size="sm" data-action="seek-to" data-seg="${i}" data-which="in" title="Seek to in">⤺</button>
        <button class="cc-btn" data-variant="ghost" data-size="sm" data-action="seek-to" data-seg="${i}" data-which="out" title="Seek to out">⤻</button>
        ${isOnly ? '' : `<button class="cc-btn" data-variant="danger" data-size="sm" data-action="remove-segment" data-seg="${i}" title="Remove">×</button>`}
      </div>
    `;
  }).join('');
  list.innerHTML = html;
  updateTrimIndicator();
}

function renderQueue(): void {
  const list = document.getElementById('queueList');
  if (!list) return;
  list.innerHTML = clips.map((c, i) => {
    const r = results[i];
    const tag = c.detection_reasons?.[0] ?? 'fallback';
    const tagColor = REGION_COLORS[tag] ?? '#888';
    const dur = c.duration ? fmtTime(c.duration) : '';
    const stateNote = r === 'kept' ? '✓' : r === 'discarded' ? '✕' : r === 'skipped' ? '↷' : '';
    const stateColor = r === 'kept' ? 'var(--cc-good)' : r === 'discarded' ? 'var(--cc-bad)' : r === 'skipped' ? 'var(--cc-warn)' : 'transparent';
    const fakeBars = Array.from({ length: 14 }, (_, j) => {
      const h = 20 + (Math.sin(j + i) * 30 + 30);
      return `<span style="height:${h}%"></span>`;
    }).join('');
    return `
      <div class="cc-queue-item" data-active="${i === currentIndex}" data-action="jump-to-clip" data-idx="${i}">
        <div class="cc-queue-thumb">
          <div class="cc-queue-thumb-bars">${fakeBars}</div>
        </div>
        <div class="cc-queue-info">
          <div class="cc-queue-name">${escapeHtml(c.filename)}</div>
          <div class="cc-queue-meta">
            <span class="cc-queue-tag" style="background:${tagColor}"></span>
            <span>${dur}</span>
            ${stateNote ? `<span style="color:${stateColor};margin-left:auto;font-weight:600">${stateNote}</span>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

export function addSegment(): void {
  const clip = clips[currentIndex];
  if (!clip) return;
  const lastEnd = segments[segments.length - 1]?.end ?? clip.duration;
  const newStart = Math.min(lastEnd, clip.duration - 10);
  segments.push({ start: Math.max(0, newStart), end: clip.duration });
  activeSegmentIndex = segments.length - 1;
  renderSegments();
}

export function removeSegment(idx: number): void {
  if (segments.length <= 1) return;
  segments.splice(idx, 1);
  if (activeSegmentIndex >= segments.length) activeSegmentIndex = segments.length - 1;
  renderSegments();
}

export function focusSegment(idx: number): void {
  if (idx < 0 || idx >= segments.length) return;
  activeSegmentIndex = idx;
  renderSegments();
}

export function setSegmentPoint(segIdx: number, which: 'in' | 'out'): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  const time = player.currentTime;
  if (segIdx < 0 || segIdx >= segments.length) return;
  if (which === 'in') segments[segIdx].start = time;
  else segments[segIdx].end = time;
  renderSegments();
}

export function seekToSegment(segIdx: number, which: 'in' | 'out'): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  if (segIdx < 0 || segIdx >= segments.length) return;
  player.currentTime = which === 'in' ? segments[segIdx].start : segments[segIdx].end;
}

function updateTrimIndicator(): void {
  const clip = clips[currentIndex];
  if (!clip) return;
  const indicator = document.getElementById('trimIndicator');
  const isFullClip = segments.length === 1
    && segments[0].start <= 0.1
    && (clip.duration - segments[0].end) <= 0.1;

  if (indicator) {
    if (!isFullClip) {
      const totalKept = segments.reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0);
      indicator.textContent = segments.length > 1
        ? `${segments.length} segments · ${totalKept.toFixed(1)}s kept`
        : `Trimmed: ${totalKept.toFixed(1)}s`;
      (indicator as HTMLElement).style.color = 'var(--cc-warn)';
    } else {
      indicator.textContent = '';
      (indicator as HTMLElement).style.color = '';
    }
  }

  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (player && player.duration && segments.length === 1) {
    const dur = player.duration;
    updateWaveformTrimMarkers(
      (segments[0].start / dur) * 100,
      (segments[0].end / dur) * 100,
      !isFullClip,
    );
  }
}

export async function clipAction(type: 'keep' | 'skip' | 'discard'): Promise<void> {
  const clip = clips[currentIndex];
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (player) player.pause();

  if (type === 'keep') {
    const customName = (document.getElementById('clipCustomName') as HTMLInputElement)?.value?.trim() || null;
    const qualitySelect = document.getElementById('trimQuality') as HTMLSelectElement | null;
    const quality = qualitySelect?.value ?? 'precise';
    const segs = segments.map(s => ({ start: s.start, end: s.end }));

    let resp: { task_id: string };
    try {
      resp = await keepClip(clip.video_stem, clip.filename, {
        segments: segs,
        custom_name: customName,
        quality,
      });
    } catch (e) {
      alert((e as Error).message || 'Keep failed');
      return;
    }

    const taskId = resp.task_id;
    const subtitle = `${customName || clip.filename} · ${segs.length} seg${segs.length === 1 ? '' : 's'} · ${quality}`;

    tasks.start({
      kind: 'keep',
      label: 'Trimming clip',
      subtitle,
      pollMs: 500,
      fetchStatus: async () => {
        const data = await fetchKeepStatus();
        const t = data.tasks.find(x => x.task_id === taskId);
        if (!t) {
          // Task GC'd — backend treats it as finished. Assume done.
          return { running: false };
        }
        return {
          running: t.status === 'running',
          subtitle: t.progress_step,
          error: t.status === 'error' ? (t.error ?? 'Keep failed') : null,
        };
      },
      formatResult: () => subtitle,
    });

    // Optimistically advance — the queue refresh on task-complete will
    // reconcile if this clip ends up failing.
    results[currentIndex] = 'kept';
  } else if (type === 'discard') {
    await discardClip(clip.video_stem, clip.filename).catch(() => {});
    results[currentIndex] = 'discarded';
  } else {
    results[currentIndex] = 'skipped';
  }

  // Advance to next un-decided clip; if none, show done.
  let next = currentIndex + 1;
  while (next < clips.length && results[next] !== null) next++;
  currentIndex = next;
  showClip();
}

async function showDone(): Promise<void> {
  const kept = results.filter(r => r === 'kept').length;
  const discarded = results.filter(r => r === 'discarded').length;
  const skipped = results.filter(r => r === 'skipped').length;

  const hdr = document.getElementById('headerRight');
  if (hdr) hdr.textContent = '';

  let html = `
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;padding:48px 24px;text-align:center">
      <div style="font-size:var(--cc-fs-xl);font-weight:600;color:var(--cc-fg);text-transform:uppercase;letter-spacing:0.1em">Review Complete</div>
      <div class="cc-mono cc-dim" style="margin-top:12px;font-size:var(--cc-fs-sm)">
        ${kept} kept · ${discarded} discarded · ${skipped} skipped
      </div>
    </div>
  `;

  try {
    const data = await fetchSources();
    const deletable = data.sources.filter(s => s.fully_reviewed && s.exists);
    if (deletable.length > 0) {
      const totalMb = deletable.reduce((sum, s) => sum + s.size_mb, 0);
      const rows = deletable.map(src => {
        const name = src.source_path.split(/[/\\]/).pop() ?? src.source_path;
        return `
          <div class="source-row" id="src-${escapeHtml(src.video_stem)}" style="display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--cc-line-soft)">
            <div style="flex:1;min-width:0">
              <div class="cc-mono" style="color:var(--cc-fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escapeHtml(src.source_path)}">${escapeHtml(name)}</div>
              <div class="cc-dim" style="font-size:var(--cc-fs-xs);margin-top:2px">${src.kept} kept · ${src.discarded} discarded</div>
            </div>
            <div class="cc-num" style="color:var(--cc-bad);font-weight:600">${src.size_mb} MB</div>
            <button class="cc-btn" data-variant="danger" data-size="sm" data-action="delete-source" data-stem="${escapeHtml(src.video_stem)}">Delete</button>
          </div>
        `;
      }).join('');
      html += `
        <div class="cc-panel" style="margin:0 24px 24px;max-width:720px;align-self:center;width:100%">
          <div class="cc-panel-head">
            <span>Delete original videos</span>
            <span style="flex:1"></span>
            <span class="cc-num cc-dim">${totalMb.toFixed(1)} MB reclaimable</span>
          </div>
          <div class="cc-panel-body">${rows}</div>
        </div>
      `;
    }
  } catch (e) {
    console.error('Failed to load sources:', e);
  }

  document.getElementById('reviewContent')!.innerHTML = html;
}

export async function deleteSourceHandler(videoStem: string, btn: HTMLButtonElement): Promise<void> {
  if (!confirm('Permanently delete this source video? This cannot be undone.')) return;
  btn.disabled = true;
  btn.textContent = 'Deleting...';
  try {
    const data = await deleteSource(videoStem);
    const row = document.getElementById('src-' + videoStem);
    if (row) {
      const action = row.querySelector('button');
      if (action) {
        action.outerHTML = '<span class="cc-pill" data-status="processed">Deleted</span>';
      }
      const sizeEl = row.querySelector('.cc-num') as HTMLElement | null;
      if (sizeEl) {
        let label = `${data.freed_mb} MB freed`;
        if (data.leftover > 0) label += ` (${data.leftover} clip(s) locked)`;
        sizeEl.textContent = label;
        sizeEl.style.color = data.leftover > 0 ? 'var(--cc-warn)' : 'var(--cc-good)';
      }
    }
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Delete';
  }
}

export { stopWaveformSync };
