import { fetchClips, keepClip, discardClip, fetchSources, deleteSource } from '../api';
import type { ClipInfo } from '../api';
import { fmtTime, fmtTimePrecise, parseTrimTime, escapeHtml } from '../utils';
import { loadWaveform, stopWaveformSync, updateWaveformTrimMarkers, getWaveformDuration } from '../waveform';

interface SegmentEntry {
  start: number;
  end: number;
}

let clips: ClipInfo[] = [];
let currentIndex = 0;
let results: Array<string | null> = [];
let segments: SegmentEntry[] = [];
let activeSegmentIndex = 0;
export let savedVolume = 0.5;

let reviewListenerAttached = false;

function attachReviewListener(): void {
  if (reviewListenerAttached) return;
  const container = document.getElementById('reviewContent');
  if (!container) return;
  reviewListenerAttached = true;
  container.addEventListener('click', (e: MouseEvent) => {
    const clicked = e.target as HTMLElement;
    if (clicked.tagName === 'INPUT') return;
    const target = clicked.closest('[data-action]') as HTMLElement | null;
    if (!target) return;
    const action = target.dataset.action!;
    const seg = target.dataset.seg !== undefined ? parseInt(target.dataset.seg, 10) : 0;
    const which = (target.dataset.which ?? 'in') as 'in' | 'out';
    const stem = target.dataset.stem ?? '';

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

function showClip(): void {
  if (currentIndex >= clips.length) { showDone(); return; }

  const clip = clips[currentIndex];
  const hdr = document.getElementById('headerRight');
  if (hdr) hdr.textContent = `Clip ${currentIndex + 1} of ${clips.length}`;

  const confPercent = Math.round(clip.confidence * 100);
  const confColor = clip.confidence > 0.7 ? '#4ade80' : clip.confidence > 0.4 ? '#fbbf24' : '#f87171';
  const tags = clip.detection_reasons.map(r =>
    `<span class="tag tag-${r}">${r.replace('_', ' ')}</span>`
  ).join('');
  const sourceName = clip.source_video.split(/[/\\]/).pop() ?? clip.source_video;

  const segmentsHtml = `
    <div class="trim-section" id="segmentList">
      <div class="segment-row" data-seg="0">
        <span class="trim-label">Seg 1</span>
        <span class="trim-label" style="margin-left:8px">In</span>
        <input type="text" class="trim-time seg-in" data-seg="0" value="0:00" />
        <button class="trim-btn" data-action="set-point" data-seg="0" data-which="in">Set</button>
        <button class="trim-btn" data-action="seek-to" data-seg="0" data-which="in">Go</button>
        <span class="trim-label" style="margin-left:8px">Out</span>
        <input type="text" class="trim-time seg-out" data-seg="0" value="${fmtTimePrecise(clip.duration)}" />
        <button class="trim-btn" data-action="set-point" data-seg="0" data-which="out">Set</button>
        <button class="trim-btn" data-action="seek-to" data-seg="0" data-which="out">Go</button>
      </div>
    </div>
    <div style="margin:6px 0">
      <button class="trim-btn" data-action="add-segment">+ Add segment</button>
      <span class="trim-indicator" id="trimIndicator"></span>
    </div>
  `;

  document.getElementById('reviewContent')!.innerHTML = `
    <div class="progress">
      ${clips.map((_, i) => {
        let cls = 'progress-dot';
        if (i < currentIndex) cls += results[i] === 'discarded' ? ' discarded' : ' done';
        else if (i === currentIndex) cls += ' current';
        return `<div class="${cls}"></div>`;
      }).join('')}
    </div>
    <div class="player-section">
      <video id="player" controls autoplay onvolumechange="window._savedVol=this.volume"
             onloadeddata="this.volume=window._savedVol||0.5">
        <source src="${clip.video_url}" type="video/mp4">
      </video>
      <div class="waveform-container" id="waveformContainer">
        <canvas class="waveform-canvas" id="waveformCanvas"></canvas>
        <div class="waveform-cursor" id="waveformCursor" style="left:0"></div>
        <div class="waveform-dimmed" id="waveformDimLeft" style="left:0;width:0"></div>
        <div class="waveform-dimmed" id="waveformDimRight" style="right:0;width:0"></div>
        <div class="waveform-trim-marker waveform-trim-in" id="waveformTrimIn" style="left:0;display:none"></div>
        <div class="waveform-trim-marker waveform-trim-out" id="waveformTrimOut" style="left:0;display:none"></div>
      </div>
      <div class="clip-info">
        <div class="clip-title">${escapeHtml(sourceName)}</div>
        <div class="clip-meta">
          <span>${fmtTime(clip.start_time)} - ${fmtTime(clip.end_time)}</span>
          <span>${Math.round(clip.duration)}s</span>
          <span>Confidence: ${confPercent}%</span>
        </div>
        <div class="tags">${tags}</div>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${confPercent}%;background:${confColor}"></div>
        </div>
      </div>
      ${segmentsHtml}
      <div class="name-section">
        <span class="trim-label">Name</span>
        <input type="text" class="clip-name-input" id="clipCustomName" placeholder="Optional custom name for export..." />
      </div>
      <div class="actions">
        <button class="btn btn-keep" data-action="keep">Keep <span class="shortcut">K</span></button>
        <button class="btn btn-skip" data-action="skip">Skip <span class="shortcut">S</span></button>
        <button class="btn btn-discard" data-action="discard">Discard <span class="shortcut">D</span></button>
      </div>
    </div>
    <div class="keyboard-hint">
      <kbd>K</kbd> keep &nbsp; <kbd>S</kbd> skip &nbsp; <kbd>D</kbd> discard &nbsp;
      <kbd>Space</kbd> play/pause &nbsp; <kbd>I</kbd> set in &nbsp; <kbd>O</kbd> set out &nbsp;
      <kbd>N</kbd> focus name
    </div>
  `;

  // Initialize segment state
  segments = [{ start: 0, end: clip.duration }];
  activeSegmentIndex = 0;

  loadWaveform(clip.video_stem, clip.filename, clip.highlight_regions || []);

  // Waveform click-to-seek
  const container = document.getElementById('waveformContainer');
  if (container) {
    container.addEventListener('click', (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!container.contains(target) || target.tagName === 'INPUT') return;
      const player = document.getElementById('player') as HTMLVideoElement | null;
      if (!player || !player.duration) return;
      const rect = container.getBoundingClientRect();
      player.currentTime = ((e.clientX - rect.left) / rect.width) * player.duration;
    });
  }
}

export function addSegment(): void {
  const clip = clips[currentIndex];
  if (!clip) return;
  const lastEnd = segments[segments.length - 1]?.end ?? clip.duration;
  const newStart = Math.min(lastEnd, clip.duration - 10);
  segments.push({ start: Math.max(0, newStart), end: clip.duration });
  renderSegments(clip.duration);
}

export function removeSegment(idx: number): void {
  if (segments.length <= 1) return;
  segments.splice(idx, 1);
  const clip = clips[currentIndex];
  if (clip) renderSegments(clip.duration);
}

function renderSegments(clipDuration: number): void {
  const list = document.getElementById('segmentList');
  if (!list) return;
  let html = '';
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    html += `<div class="segment-row" data-seg="${i}" data-action="focus-segment" style="cursor:pointer;${i === activeSegmentIndex ? 'outline:1px solid #60a5fa;' : ''}">`;
    html += `<span class="trim-label">Seg ${i + 1}</span>`;
    html += `<span class="trim-label" style="margin-left:8px">In</span>`;
    html += `<input type="text" class="trim-time seg-in" data-seg="${i}" value="${fmtTimePrecise(seg.start)}" oninput="window._cc.onSegmentInput(${i},'in',this.value)" />`;
    html += `<button class="trim-btn" data-action="set-point" data-seg="${i}" data-which="in">Set</button>`;
    html += `<button class="trim-btn" data-action="seek-to" data-seg="${i}" data-which="in">Go</button>`;
    html += `<span class="trim-label" style="margin-left:8px">Out</span>`;
    html += `<input type="text" class="trim-time seg-out" data-seg="${i}" value="${fmtTimePrecise(seg.end)}" oninput="window._cc.onSegmentInput(${i},'out',this.value)" />`;
    html += `<button class="trim-btn" data-action="set-point" data-seg="${i}" data-which="out">Set</button>`;
    html += `<button class="trim-btn" data-action="seek-to" data-seg="${i}" data-which="out">Go</button>`;
    if (segments.length > 1) {
      html += `<button class="comp-remove" data-action="remove-segment" data-seg="${i}">&times;</button>`;
    }
    html += `</div>`;
  }
  list.innerHTML = html;
  updateTrimIndicator();
}

export function focusSegment(idx: number): void {
  activeSegmentIndex = idx;
  const clip = clips[currentIndex];
  if (clip) renderSegments(clip.duration);
}

export function setSegmentPoint(segIdx: number, which: 'in' | 'out'): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  const time = player.currentTime;
  if (which === 'in') segments[segIdx].start = time;
  else segments[segIdx].end = time;
  const clip = clips[currentIndex];
  if (clip) renderSegments(clip.duration);
}

export function seekToSegment(segIdx: number, which: 'in' | 'out'): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  player.currentTime = which === 'in' ? segments[segIdx].start : segments[segIdx].end;
}

export function onSegmentInput(segIdx: number, which: 'in' | 'out', val: string): void {
  const t = parseTrimTime(val);
  if (which === 'in') segments[segIdx].start = t;
  else segments[segIdx].end = t;
  updateTrimIndicator();
}

export function updateTrimIndicator(): void {
  const clip = clips[currentIndex];
  if (!clip) return;
  const indicator = document.getElementById('trimIndicator');
  if (!indicator) return;
  const isFullClip = segments.length === 1 && segments[0].start <= 0.1 && (clip.duration - segments[0].end) <= 0.1;
  if (!isFullClip) {
    const totalKept = segments.reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0);
    indicator.textContent = segments.length > 1
      ? `${segments.length} segments \u2014 ${totalKept.toFixed(1)}s kept`
      : `Trimmed: ${totalKept.toFixed(1)}s`;
    indicator.className = 'trim-indicator active';
  } else {
    indicator.textContent = '';
    indicator.className = 'trim-indicator';
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
    const isFullClip = segments.length === 1 && segments[0].start <= 0.1 && (clip.duration - segments[0].end) <= 0.1;
    showOverlay(isFullClip ? 'Saving clip...' : segments.length > 1 ? 'Cutting segments...' : 'Trimming clip...');
    try {
      await keepClip(clip.video_stem, clip.filename, {
        segments: segments.map(s => ({ start: s.start, end: s.end })),
        custom_name: customName,
      });
    } catch (e) {
      hideOverlay();
      alert((e as Error).message || 'Keep failed');
      return;
    }
    hideOverlay();
    results[currentIndex] = 'kept';
  } else if (type === 'discard') {
    await discardClip(clip.video_stem, clip.filename).catch(() => {});
    results[currentIndex] = 'discarded';
  } else {
    results[currentIndex] = 'skipped';
  }

  currentIndex++;
  showClip();
}

function showOverlay(text: string): void {
  const el = document.getElementById('overlayText');
  const overlay = document.getElementById('overlay');
  if (el) el.textContent = text;
  if (overlay) overlay.classList.add('active');
}

function hideOverlay(): void {
  document.getElementById('overlay')?.classList.remove('active');
}

async function showDone(): Promise<void> {
  const kept = results.filter(r => r === 'kept').length;
  const discarded = results.filter(r => r === 'discarded').length;
  const skipped = results.filter(r => r === 'skipped').length;

  const hdr = document.getElementById('headerRight');
  if (hdr) hdr.textContent = 'Done';

  let html = `
    <div class="done-state">
      <h2>Review Complete</h2>
      <p>${kept} kept &middot; ${discarded} discarded &middot; ${skipped} skipped</p>
    </div>
  `;

  try {
    const data = await fetchSources();
    const deletable = data.sources.filter(s => s.fully_reviewed && s.exists);
    if (deletable.length > 0) {
      const totalMb = deletable.reduce((sum, s) => sum + s.size_mb, 0);
      html += `<div class="cleanup-section"><h3>Delete Original Videos</h3>`;
      html += `<p style="color:#888;font-size:13px;margin-bottom:16px;">Fully reviewed. Delete to free disk space.</p>`;
      for (const src of deletable) {
        const name = src.source_path.split(/[/\\]/).pop() ?? src.source_path;
        html += `
          <div class="source-row" id="src-${src.video_stem}">
            <div class="source-info">
              <div class="source-name" title="${escapeHtml(src.source_path)}">${escapeHtml(name)}</div>
              <div class="source-detail">${src.kept} kept, ${src.discarded} discarded</div>
            </div>
            <div class="source-size">${src.size_mb} MB</div>
            <button class="btn-delete" data-action="delete-source" data-stem="${src.video_stem}">Delete</button>
          </div>`;
      }
      html += `<div class="cleanup-total">Total reclaimable: ${totalMb.toFixed(1)} MB</div></div>`;
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
    const row = document.getElementById('src-' + videoStem)!;
    row.querySelector('.btn-delete')!.outerHTML = '<button class="btn-deleted">Deleted</button>';
    const sizeEl = row.querySelector('.source-size') as HTMLElement;
    let label = data.freed_mb + ' MB freed';
    if (data.leftover > 0) label += ` (${data.leftover} clip(s) locked, will clean on restart)`;
    sizeEl.textContent = label;
    sizeEl.style.color = data.leftover > 0 ? '#fbbf24' : '#4ade80';
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Delete';
  }
}

export { stopWaveformSync };
