import {
  fetchKeptClips, fetchPresets, startEncoding, fetchEncodeStatus, cancelEncoding,
  fetchYouTubeStatus, fetchYouTubePlaylists, startYouTubeAuth, revokeYouTubeAuth,
  startUpload, fetchUploadStatus, cancelUpload, createPlaylist, deleteKeptClip,
  openKeptFolder, fetchStorageSummary, deleteEncodedClip, fetchSources, deleteSource,
} from '../api';
import type { KeptClipInfo, Playlist, StorageSummary, SourceVideo } from '../api';
import { escapeHtml, fmtTime, formatClipTitle } from '../utils';
import { tasks } from '../tasks';
import { renderCompilationList, loadPastCompilations } from './compile';

export let keptClips: KeptClipInfo[] = [];
let encodingPresets: Array<{ name: string; display_name: string; extension: string }> = [];
let defaultPreset = 'h264_hq';
let encodingFpsOptions: Array<number | null> = [null, 24, 30, 60];
let ytAuthenticated = false;
let ytChannelName = '';
let ytPlaylists: Playlist[] = [];

let storageSummary: StorageSummary | null = null;
let sourcesData: SourceVideo[] = [];

type Subtab = 'clips' | 'compilation' | 'youtube';
let activeSubtab: Subtab = 'clips';
let sourcesExpanded = false;
let exportInitialized = false;

export function initExportTab(): void {
  if (exportInitialized) return;
  exportInitialized = true;
  // Sub-tab nav
  document.querySelectorAll<HTMLButtonElement>('.cc-subtabs > .cc-subtab').forEach(btn => {
    btn.addEventListener('click', () => {
      const s = btn.dataset.subtab as Subtab | undefined;
      if (s) setSubtab(s);
    });
  });
  // Sources accordion + GIF preset visibility delegated through #exportContent
  const body = document.getElementById('exportContent');
  if (body) {
    body.addEventListener('click', (e) => {
      const target = (e.target as HTMLElement).closest<HTMLElement>('[data-action]');
      if (!target) return;
      const action = target.dataset.action;
      if (action === 'toggle-sources') {
        sourcesExpanded = !sourcesExpanded;
        renderActiveSubtab();
      }
    });
  }
}

function setSubtab(s: Subtab): void {
  activeSubtab = s;
  renderSubtabsState();
  renderActiveSubtab();
}

export async function loadExportTab(): Promise<void> {
  initExportTab();
  try {
    const [keptData, presetsData, ytStatus, summaryData, sourcesResp] = await Promise.all([
      fetchKeptClips(),
      fetchPresets(),
      fetchYouTubeStatus(),
      fetchStorageSummary().catch(() => null),
      fetchSources().catch(() => ({ sources: [] })),
    ]);
    keptClips = keptData.clips || [];
    encodingPresets = presetsData.presets || [];
    defaultPreset = presetsData.default || 'h264_hq';
    encodingFpsOptions = presetsData.fps_options || [null, 24, 30, 60];
    ytAuthenticated = ytStatus.authenticated || false;
    ytChannelName = ytStatus.channel_name || '';
    storageSummary = summaryData;
    sourcesData = sourcesResp.sources || [];

    if (ytAuthenticated) {
      try {
        const plData = await fetchYouTubePlaylists();
        ytPlaylists = plData.playlists || [];
      } catch { ytPlaylists = []; }
    }
  } catch (e) {
    console.error('Failed to load export tab:', e);
  }
  renderExportView();
}

export function renderExportView(): void {
  renderStorageBar();
  renderSubtabsState();
  renderActiveSubtab();
}

// ============================================================
// Storage bar (top of Export view)
// ============================================================

function renderStorageBar(): void {
  const fill = document.getElementById('storageBarFill');
  const legend = document.getElementById('storageBarLegend');
  if (!fill || !legend) return;
  if (!storageSummary) {
    fill.innerHTML = '';
    legend.innerHTML = `<span class="cc-dim">no data yet</span>`;
    return;
  }
  const segs = [
    { k: 'kept',         color: 'var(--cc-accent)', val: storageSummary.kept.size_mb,         count: storageSummary.kept.count },
    { k: 'encoded',      color: 'var(--cc-good)',   val: storageSummary.encoded.size_mb,      count: storageSummary.encoded.count },
    { k: 'compilations', color: 'var(--cc-warn)',   val: storageSummary.compilations.size_mb, count: storageSummary.compilations.count },
  ];
  const total = storageSummary.total_mb || segs.reduce((s, x) => s + x.val, 0) || 1;

  fill.innerHTML = segs.map(s =>
    `<span style="width:${(s.val / total) * 100}%;background:${s.color}"></span>`
  ).join('');

  legend.innerHTML = segs.map(s => `
    <div class="cc-storage-legend-item">
      <span class="cc-legend-swatch" style="background:${s.color};width:8px;height:8px;border-radius:2px"></span>
      <span style="text-transform:uppercase;letter-spacing:0.06em">${s.k}</span>
      <span class="cc-num">${s.count} · ${fmtMb(s.val)}</span>
    </div>
  `).join('') + `
    <div class="cc-storage-legend-item">
      <span style="text-transform:uppercase;letter-spacing:0.06em">total</span>
      <span class="cc-num cc-fg2">${fmtMb(total)}</span>
    </div>
  `;
}

// ============================================================
// Sub-tab nav (chrome state, count badges, YouTube status)
// ============================================================

function renderSubtabsState(): void {
  document.querySelectorAll<HTMLButtonElement>('.cc-subtabs > .cc-subtab').forEach(btn => {
    btn.dataset.active = btn.dataset.subtab === activeSubtab ? 'true' : 'false';
  });
  const clipsCount = document.getElementById('clipsCount');
  if (clipsCount) clipsCount.textContent = String(keptClips.length);
  const compCount = document.getElementById('compCount');
  if (compCount) compCount.textContent = '0'; // populated by compile.ts when on its sub-tab
  const ytStatusEl = document.getElementById('ytSubtabStatus');
  if (ytStatusEl) {
    ytStatusEl.innerHTML = ytAuthenticated
      ? `<span class="cc-dot" style="background:var(--cc-good)"></span><span>youtube · ${escapeHtml(ytChannelName || 'connected')}</span>`
      : `<span class="cc-dot" style="background:var(--cc-fg-dim)"></span><span>youtube · not connected</span>`;
  }
}

// ============================================================
// Sub-tab dispatch
// ============================================================

function renderActiveSubtab(): void {
  const body = document.getElementById('exportContent');
  if (!body) return;
  switch (activeSubtab) {
    case 'clips':       renderClipsSubtab(body); break;
    case 'compilation': renderCompilationSubtab(body); break;
    case 'youtube':     renderYouTubeSubtab(body); break;
  }
}

// ============================================================
// Clips sub-tab — kept clips table + encode controls + sources
// ============================================================

function renderClipsSubtab(body: HTMLElement): void {
  body.innerHTML = `
    <div class="cc-export-grid">
      <div class="cc-panel">
        <div class="cc-panel-head">
          <span id="clipsPanelTitle">Kept clips · ${keptClips.length}</span>
          <span style="flex:1"></span>
          <button class="cc-btn" data-variant="ghost" data-size="sm" onclick="window._cc.toggleAllClips('encode')">Select all</button>
        </div>
        <div class="cc-panel-body">${renderClipsTable()}</div>
      </div>
      ${renderEncodePanel()}
    </div>
    ${renderSourcesAccordion()}
  `;

  // Bind encode preset / GIF slowdown visibility
  const presetSelect = document.getElementById('encodePreset') as HTMLSelectElement | null;
  if (presetSelect) {
    const updateSlowdown = (): void => {
      const sg = document.getElementById('slowdownGroup');
      if (sg) sg.style.display = presetSelect.value === 'gif' ? 'flex' : 'none';
    };
    updateSlowdown();
    presetSelect.addEventListener('change', updateSlowdown);
  }
  // Selection count + Encode button label live-update
  body.querySelectorAll<HTMLInputElement>('.encode-cb').forEach(cb => {
    cb.addEventListener('change', updateEncodeSelectionCount);
  });
  updateEncodeSelectionCount();
}

function renderClipsTable(): string {
  if (keptClips.length === 0) {
    return `<div class="empty-state" style="padding:40px 24px">No kept clips yet. Decide some in the Review tab.</div>`;
  }
  const rows = keptClips.map((clip, i) => {
    const customName = clip.custom_name ? `<span class="cc-dim" style="margin-left:6px">· ${escapeHtml(clip.custom_name)}</span>` : '';
    const sourceMb = clip.size_mb != null ? `${clip.size_mb.toFixed(0)} MB` : '—';
    const encodedMb = clip.encoded_exists && clip.encoded_size_mb != null
      ? `${clip.encoded_size_mb.toFixed(0)} MB`
      : '<span class="cc-dim">—</span>';
    const preset = clip.encoding_preset
      ? escapeHtml(clip.encoding_preset)
      : '<span class="cc-dim">—</span>';
    let ytCell = '<span class="cc-dim">—</span>';
    if (clip.youtube_url) {
      ytCell = `<a class="cc-pill" data-status="processed" href="${escapeHtml(clip.youtube_url)}" target="_blank" style="text-decoration:none">uploaded</a>`;
    } else if (clip.youtube_upload_status === 'failed') {
      ytCell = `<span class="cc-pill" data-status="stale">failed</span>`;
    } else if (clip.youtube_upload_status === 'queued' || clip.youtube_upload_status === 'uploading') {
      ytCell = `<span class="cc-pill" data-status="pending">${escapeHtml(clip.youtube_upload_status)}</span>`;
    }

    return `
      <tr>
        <td style="width:24px">
          <input type="checkbox" class="encode-cb" data-index="${i}" checked
                 style="accent-color:var(--cc-accent)"
                 onclick="event.stopPropagation()">
        </td>
        <td class="cc-mono">${escapeHtml(clip.filename)}${customName}</td>
        <td class="cc-num cc-fg2" style="width:80px">${sourceMb}</td>
        <td class="cc-num" style="width:80px">${encodedMb}</td>
        <td class="cc-fg2" style="width:80px">${preset}</td>
        <td style="width:100px">${ytCell}</td>
        <td style="width:120px;text-align:right">
          <div style="display:inline-flex;gap:4px;justify-content:flex-end">
            <button class="cc-btn" data-variant="ghost" data-size="sm" title="Preview"
                    onclick="event.stopPropagation();window._cc.previewClip(${i})">▶</button>
            <button class="cc-btn" data-variant="ghost" data-size="sm" title="Open folder"
                    data-stem="${escapeHtml(clip.video_stem)}"
                    onclick="event.stopPropagation();window._cc.openFolderHandler(this.dataset.stem)">📂</button>
            ${clip.encoded_exists
              ? `<button class="cc-btn" data-variant="danger" data-size="sm" title="Delete encoded"
                         data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}"
                         onclick="event.stopPropagation();window._cc.deleteEncodedClipHandler(this)">×enc</button>`
              : ''}
            <button class="cc-btn" data-variant="danger" data-size="sm" title="Delete clip"
                    data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}"
                    onclick="event.stopPropagation();window._cc.deleteKeptClipHandler(this)">×</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  return `
    <table class="cc-table">
      <thead>
        <tr>
          <th></th>
          <th>Clip</th>
          <th>Source</th>
          <th>Encoded</th>
          <th>Preset</th>
          <th>YouTube</th>
          <th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderEncodePanel(): string {
  const presetButtons = encodingPresets.map(p => {
    const isDefault = p.name === defaultPreset;
    return `<option value="${p.name}" ${isDefault ? 'selected' : ''}>${escapeHtml(p.display_name)}</option>`;
  }).join('');
  const fpsOptions = encodingFpsOptions.map(fps =>
    `<option value="${fps ?? ''}">${fps ? fps + ' fps' : 'Original'}</option>`
  ).join('');
  return `
    <div class="cc-panel">
      <div class="cc-panel-head"><span>Encode</span></div>
      <div class="cc-panel-body" style="padding:var(--cc-pad-2);display:flex;flex-direction:column;gap:var(--cc-gap-2)">
        <div class="cc-col" style="gap:6px">
          <span class="cc-label">Preset</span>
          <select class="cc-select" id="encodePreset">${presetButtons}</select>
        </div>
        <div class="cc-col" style="gap:6px">
          <span class="cc-label">Target FPS</span>
          <select class="cc-select" id="encodeFps">${fpsOptions}</select>
        </div>
        <div class="cc-col" id="slowdownGroup" style="gap:6px;display:none">
          <span class="cc-label">Slowdown (GIF only)</span>
          <select class="cc-select" id="encodeSlowdown">
            <option value="">None (1.0×)</option>
            <option value="0.5">0.5×</option>
            <option value="0.25">0.25×</option>
          </select>
        </div>
        <div style="height:1px;background:var(--cc-line);margin:4px 0"></div>
        <div class="cc-col" style="gap:6px;font-size:var(--cc-fs-xs);color:var(--cc-fg-2)">
          <div style="display:flex;justify-content:space-between">
            <span>Selected</span>
            <span class="cc-num" id="encodeSelCount">0 clips</span>
          </div>
        </div>
        <button class="cc-btn" data-variant="primary" id="btnEncode"
                onclick="window._cc.startEncodingHandler()">Encode selected</button>
      </div>
    </div>
  `;
}

function updateEncodeSelectionCount(): void {
  const count = document.querySelectorAll<HTMLInputElement>('.encode-cb:checked').length;
  const el = document.getElementById('encodeSelCount');
  if (el) el.textContent = `${count} clip${count === 1 ? '' : 's'}`;
  const btn = document.getElementById('btnEncode') as HTMLButtonElement | null;
  if (btn && !btn.disabled) {
    btn.textContent = count > 0 ? `Encode ${count} clip${count === 1 ? '' : 's'}` : 'Encode selected';
  }
}

function renderSourcesAccordion(): string {
  const deletable = sourcesData.filter(s => s.exists);
  if (deletable.length === 0) return '';
  const totalMb = deletable.reduce((sum, s) => sum + s.size_mb, 0);
  const rows = deletable.map(src => {
    const name = src.source_path.split(/[/\\]/).pop() ?? src.source_path;
    const pending = src.total - src.kept - src.discarded;
    const reviewSummary = src.fully_reviewed
      ? `${src.kept} kept · ${src.discarded} discarded`
      : `${src.kept} kept · ${src.discarded} discarded · ${pending} pending`;
    const reviewColor = src.fully_reviewed ? 'var(--cc-good)' : 'var(--cc-warn)';
    return `
      <div id="export-src-${escapeHtml(src.video_stem)}"
           style="display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--cc-line-soft)">
        <div style="flex:1;min-width:0">
          <div class="cc-mono" style="color:var(--cc-fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
               title="${escapeHtml(src.source_path)}">${escapeHtml(name)}</div>
          <div style="font-size:var(--cc-fs-xs);color:${reviewColor};margin-top:2px">${reviewSummary}</div>
        </div>
        <div class="cc-num cc-fg2">${src.size_mb} MB</div>
        <button class="cc-btn" data-variant="danger" data-size="sm"
                data-stem="${escapeHtml(src.video_stem)}"
                onclick="window._cc.deleteSourceFromExportHandler(this.dataset.stem, this)">Delete</button>
      </div>
    `;
  }).join('');

  return `
    <div class="cc-panel" style="margin-top:var(--cc-gap-2)">
      <button class="cc-panel-head" data-action="toggle-sources"
              style="cursor:pointer;text-align:left;background:transparent;border:0;border-bottom:1px solid var(--cc-line);width:100%;font-family:inherit;color:inherit;letter-spacing:inherit;text-transform:inherit;font-size:inherit;font-weight:inherit;padding:var(--cc-pad) var(--cc-pad-2)">
        <span>${sourcesExpanded ? '▾' : '▸'} Source videos</span>
        <span style="flex:1"></span>
        <span class="cc-num cc-dim">${deletable.length} files · ${totalMb.toFixed(1)} MB reclaimable</span>
      </button>
      <div class="cc-panel-body" style="display:${sourcesExpanded ? 'block' : 'none'};padding:0">${rows}</div>
    </div>
  `;
}

// ============================================================
// Compilation sub-tab — sequence + build controls
// ============================================================

function renderCompilationSubtab(body: HTMLElement): void {
  body.innerHTML = `
    <div class="cc-comp">
      <div class="cc-panel">
        <div class="cc-panel-head">
          <span id="compSummary">No clips added yet.</span>
          <span style="flex:1"></span>
          <button class="cc-btn" data-variant="ghost" data-size="sm"
                  onclick="window._cc.addSelectedToCompilation()">+ Add selected</button>
        </div>
        <div class="cc-panel-body">
          <div class="cc-comp-list" id="compList"></div>
          <div id="pastCompilations"></div>
        </div>
      </div>
      <div class="cc-panel">
        <div class="cc-panel-head"><span>Build</span></div>
        <div class="cc-panel-body" style="padding:var(--cc-pad-2);display:flex;flex-direction:column;gap:var(--cc-gap-2)">
          <div class="cc-col" style="gap:6px">
            <span class="cc-label">Title</span>
            <input class="cc-input" id="compTitle" placeholder="Optional title…">
          </div>
          <div class="cc-col" style="gap:6px">
            <span class="cc-label">Transition</span>
            <select class="cc-select" id="compTransition" onchange="window._cc.updateCompDuration()">
              <option value="cut">Hard cut</option>
              <option value="crossfade">Crossfade</option>
            </select>
          </div>
          <div class="cc-col" id="compXfadeGroup" style="gap:6px;display:none">
            <span class="cc-label">Crossfade duration</span>
            <input type="number" class="cc-input" id="compXfadeDur" value="0.5" min="0.1" max="3" step="0.1"
                   oninput="window._cc.updateCompDuration()">
          </div>
          <div style="height:1px;background:var(--cc-line);margin:4px 0"></div>
          <button class="cc-btn" data-variant="primary" id="btnBuildComp"
                  onclick="window._cc.startCompilationHandler()">Build compilation</button>
        </div>
      </div>
    </div>
  `;
  renderCompilationList();
  void loadPastCompilations();
}

// ============================================================
// YouTube sub-tab — auth + upload form + clip list
// ============================================================

function renderYouTubeSubtab(body: HTMLElement): void {
  if (!ytAuthenticated) {
    body.innerHTML = `
      <div class="cc-upload">
        <div class="cc-h">YouTube not connected</div>
        <p class="cc-dim" style="font-size:var(--cc-fs-sm);max-width:560px">
          Sign in with an OAuth client to upload clips. Create credentials at
          <a href="https://console.cloud.google.com" target="_blank" style="color:var(--cc-accent)">console.cloud.google.com</a>.
        </p>
        <div class="cc-upload-grid">
          <span class="cc-label">Client ID</span>
          <input class="cc-input" type="text" id="ytClientId" placeholder="OAuth Client ID">
          <span class="cc-label">Client Secret</span>
          <input class="cc-input" type="password" id="ytClientSecret" placeholder="OAuth Client Secret">
        </div>
        <div style="display:flex;justify-content:flex-end">
          <button class="cc-btn" data-variant="primary"
                  onclick="window._cc.startYouTubeAuthHandler()">Sign in</button>
        </div>
      </div>
    `;
    return;
  }

  const playlistOptions = ['<option value="">— None —</option>']
    .concat(ytPlaylists.map(pl =>
      `<option value="${escapeHtml(pl.id)}">${escapeHtml(pl.title)} (${pl.item_count})</option>`
    ))
    .concat(['<option value="__create__">+ Create new…</option>'])
    .join('');

  const clipRows = keptClips.map((clip, i) => {
    const defaultTitle = clip.custom_name || formatClipTitle(clip.filename);
    const alreadyUploaded = !!clip.youtube_url;
    let statusHtml = `<span class="cc-pill" data-status="unprocessed">ready</span>`;
    if (alreadyUploaded) {
      statusHtml = `<a class="cc-pill" data-status="processed" href="${escapeHtml(clip.youtube_url!)}" target="_blank" style="text-decoration:none">uploaded</a>`;
    } else if (clip.youtube_upload_status === 'failed') {
      statusHtml = `<span class="cc-pill" data-status="stale">failed</span>`;
    }
    return `
      <tr id="upload-row-${i}">
        <td style="width:24px">
          <input type="checkbox" class="upload-cb" data-index="${i}" ${alreadyUploaded ? '' : 'checked'}
                 style="accent-color:var(--cc-accent)">
        </td>
        <td class="cc-mono">${escapeHtml(clip.filename)}</td>
        <td>
          <input type="text" class="cc-input upload-title" data-index="${i}"
                 value="${escapeHtml(defaultTitle)}" style="width:100%">
        </td>
        <td style="width:100px">${statusHtml}</td>
      </tr>
    `;
  }).join('');

  body.innerHTML = `
    <div class="cc-upload">
      <div style="display:flex;align-items:center;gap:var(--cc-gap-2)">
        <div class="cc-h" style="margin:0">Connected as</div>
        <span class="cc-mono cc-fg2">${escapeHtml(ytChannelName)}</span>
        <span style="flex:1"></span>
        <button class="cc-btn" data-variant="ghost" data-size="sm"
                onclick="window._cc.revokeYouTubeAuthHandler()">Sign out</button>
      </div>
      <div class="cc-upload-grid">
        <span class="cc-label">Privacy</span>
        <select class="cc-select" id="ytPrivacy">
          <option value="private" selected>Private</option>
          <option value="unlisted">Unlisted</option>
          <option value="public">Public</option>
        </select>
        <span class="cc-label">Category</span>
        <select class="cc-select" id="ytCategory">
          <option value="20" selected>Gaming</option>
          <option value="24">Entertainment</option>
          <option value="22">People &amp; Blogs</option>
          <option value="17">Sports</option>
          <option value="10">Music</option>
          <option value="1">Film &amp; Animation</option>
          <option value="23">Comedy</option>
        </select>
        <span class="cc-label">Playlist</span>
        <select class="cc-select" id="ytPlaylist">${playlistOptions}</select>
        <span class="cc-label">Tags</span>
        <input class="cc-input" type="text" id="ytTags" placeholder="tag1, tag2, tag3">
        <span class="cc-label">Description</span>
        <textarea id="ytDescription" placeholder="Description template — supports {source_video} {start_time} {end_time} {duration} {detection_reasons}"></textarea>
      </div>
      <div class="cc-panel">
        <div class="cc-panel-head">
          <span>Clips · ${keptClips.length}</span>
          <span style="flex:1"></span>
          <button class="cc-btn" data-variant="ghost" data-size="sm"
                  onclick="window._cc.toggleAllClips('upload')">Toggle all</button>
        </div>
        <div class="cc-panel-body">
          ${keptClips.length === 0
            ? `<div class="empty-state" style="padding:40px 24px">No kept clips to upload.</div>`
            : `<table class="cc-table"><tbody>${clipRows}</tbody></table>`}
        </div>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:var(--cc-gap)">
        <button class="cc-btn" data-variant="primary" id="btnUpload"
                onclick="window._cc.startUploadHandler()">Upload selected</button>
      </div>
    </div>
  `;

  const plSelect = document.getElementById('ytPlaylist') as HTMLSelectElement | null;
  if (plSelect) {
    plSelect.addEventListener('change', function () {
      if (this.value === '__create__') { void createPlaylistHandler(); this.value = ''; }
    });
  }
}

// ============================================================
// Existing handlers (unchanged behaviour, modal/chip drives progress)
// ============================================================

export function toggleAllClips(section: 'encode' | 'upload'): void {
  const selector = section === 'encode' ? '.encode-cb' : '.upload-cb';
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>(selector));
  if (!checkboxes.length) return;
  const allChecked = checkboxes.every(cb => cb.checked);
  checkboxes.forEach(cb => { cb.checked = !allChecked; });
  if (section === 'encode') updateEncodeSelectionCount();
}

export async function startEncodingHandler(): Promise<void> {
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>('.encode-cb:checked'));
  if (!checkboxes.length) { alert('Select at least one clip to encode.'); return; }
  if (tasks.isRunning('encode')) { alert('An encode task is already running.'); return; }

  const preset = (document.getElementById('encodePreset') as HTMLSelectElement).value;
  const fpsVal = (document.getElementById('encodeFps') as HTMLSelectElement).value;
  const fps = fpsVal ? parseInt(fpsVal) : null;
  const slowdownVal = (document.getElementById('encodeSlowdown') as HTMLSelectElement)?.value ?? '';
  const slowdown = slowdownVal ? parseFloat(slowdownVal) : null;

  const clipsToEncode = checkboxes.map(cb => {
    const idx = parseInt(cb.dataset.index!);
    const clip = keptClips[idx];
    return { video_stem: clip.video_stem, filename: clip.filename };
  });

  const btn = document.getElementById('btnEncode') as HTMLButtonElement;
  btn.disabled = true;
  btn.textContent = 'Encoding…';

  try {
    await startEncoding({ clips: clipsToEncode, preset, target_fps: fps, slowdown_factor: slowdown });
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Encode selected';
    return;
  }

  tasks.start({
    kind: 'encode',
    label: 'Encoding clips',
    subtitle: `${clipsToEncode.length} clip${clipsToEncode.length === 1 ? '' : 's'} · ${preset}`,
    pollMs: 800,
    cancel: cancelEncoding,
    fetchStatus: async () => {
      const data = await fetchEncodeStatus();
      const completedCount = (data.completed || []).length;
      const pct = data.total > 0 ? Math.round((completedCount / data.total) * 100) : 0;
      const subtitle = data.current_file
        ? `clip ${data.current_index || 0} of ${data.total}: ${data.current_file}`
        : `${completedCount} / ${data.total}`;
      const error = !data.running && data.errors?.length
        ? `${data.errors.length} error(s) — ${data.errors[0].error}`
        : null;
      return { running: data.running, pct, subtitle, error };
    },
    formatResult: (t) => t.subtitle ?? '',
  });
}

tasks.addEventListener('task-complete', (e) => {
  const t = (e as CustomEvent).detail.task;
  if (t.kind !== 'encode') return;
  const btn = document.getElementById('btnEncode') as HTMLButtonElement | null;
  if (btn) { btn.disabled = false; updateEncodeSelectionCount(); }
});

export async function cancelEncodingHandler(): Promise<void> {
  const running = tasks.getAll().find(x => x.kind === 'encode' && x.state === 'running');
  if (running) await tasks.cancel(running.id);
  else await cancelEncoding().catch(console.error);
}

export async function startYouTubeAuthHandler(): Promise<void> {
  const clientId = (document.getElementById('ytClientId') as HTMLInputElement).value.trim();
  const clientSecret = (document.getElementById('ytClientSecret') as HTMLInputElement).value.trim();
  if (!clientId || !clientSecret) { alert('Enter both Client ID and Client Secret.'); return; }
  try {
    const data = await startYouTubeAuth(clientId, clientSecret);
    window.open(data.auth_url, 'youtube-auth', 'width=600,height=700');
    const listener = async (event: MessageEvent): Promise<void> => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type === 'youtube-auth-success') {
        window.removeEventListener('message', listener);
        await loadExportTab();
      }
    };
    window.addEventListener('message', listener);
  } catch (e) { alert((e as Error).message); }
}

export async function revokeYouTubeAuthHandler(): Promise<void> {
  if (!confirm('Sign out from YouTube?')) return;
  await revokeYouTubeAuth().catch(console.error);
  ytAuthenticated = false;
  ytChannelName = '';
  ytPlaylists = [];
  renderExportView();
}

export async function startUploadHandler(): Promise<void> {
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>('.upload-cb:checked'));
  if (!checkboxes.length) { alert('Select at least one clip to upload.'); return; }
  if (tasks.isRunning('upload')) { alert('An upload task is already running.'); return; }

  const privacy = (document.getElementById('ytPrivacy') as HTMLSelectElement).value;
  const playlistId = (document.getElementById('ytPlaylist') as HTMLSelectElement).value;
  const categoryId = (document.getElementById('ytCategory') as HTMLSelectElement).value;
  const tagsRaw = (document.getElementById('ytTags') as HTMLInputElement).value.trim();
  const descTemplate = (document.getElementById('ytDescription') as HTMLTextAreaElement).value;

  const clipsToUpload = checkboxes.map(cb => {
    const idx = parseInt(cb.dataset.index!);
    const clip = keptClips[idx];
    const titleInput = document.querySelector<HTMLInputElement>(`.upload-title[data-index="${idx}"]`);
    const title = titleInput?.value.trim() || (clip.custom_name || formatClipTitle(clip.filename));
    const description = descTemplate
      .replace(/\{source_video\}/g, clip.source_video || '')
      .replace(/\{start_time\}/g, clip.start_time ? fmtTime(clip.start_time) : '')
      .replace(/\{end_time\}/g, clip.end_time ? fmtTime(clip.end_time) : '')
      .replace(/\{duration\}/g, clip.duration ? Math.round(clip.duration) + 's' : '')
      .replace(/\{detection_reasons\}/g, (clip.detection_reasons || []).join(', '));
    return {
      video_stem: clip.video_stem, filename: clip.filename,
      use_encoded: !!clip.encoded_exists, title, description,
      tags: tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(t => t) : [],
      category_id: categoryId, privacy, playlist_id: playlistId || null,
    };
  });

  const btn = document.getElementById('btnUpload') as HTMLButtonElement;
  btn.disabled = true; btn.textContent = 'Uploading…';
  try {
    await startUpload({ clips: clipsToUpload });
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Upload selected';
    return;
  }

  let lastCompletedCount = 0;

  tasks.start({
    kind: 'upload',
    label: 'Uploading to YouTube',
    subtitle: `${clipsToUpload.length} clip${clipsToUpload.length === 1 ? '' : 's'}`,
    pollMs: 1000,
    cancel: cancelUpload,
    fetchStatus: async () => {
      const data = await fetchUploadStatus();
      const filePct = data.bytes_total > 0 ? Math.round((data.bytes_sent / data.bytes_total) * 100) : 0;
      const completedClips = (data.completed || []).length;
      const overallPct = data.total > 0
        ? Math.round(((completedClips + filePct / 100) / data.total) * 100)
        : 0;

      // Splice newly-completed uploads into the per-clip list as links
      const newLines: string[] = [];
      const completed = data.completed || [];
      for (let i = lastCompletedCount; i < completed.length; i++) {
        const c = completed[i];
        const idx = keptClips.findIndex(k => k.filename === c.filename);
        if (idx >= 0) {
          const row = document.getElementById('upload-row-' + idx);
          const status = row?.querySelector('.cc-pill');
          if (status) status.outerHTML = `<a class="cc-pill" data-status="processed" href="${escapeHtml(c.url)}" target="_blank" style="text-decoration:none">uploaded</a>`;
        }
        newLines.push(`✓ ${c.filename}`);
      }
      lastCompletedCount = completed.length;

      const error = !data.running && data.errors?.length
        ? `${data.errors.length} error(s) — ${data.errors[0].error}`
        : null;

      return {
        running: data.running,
        pct: overallPct,
        subtitle: data.current_file
          ? `${data.current_index || 0} / ${data.total} · ${data.current_file} (${filePct}%)`
          : `${completedClips} / ${data.total}`,
        newLogLines: newLines,
        error,
      };
    },
    formatResult: (t) => t.subtitle ?? '',
  });
}

tasks.addEventListener('task-complete', (e) => {
  const t = (e as CustomEvent).detail.task;
  if (t.kind !== 'upload') return;
  const btn = document.getElementById('btnUpload') as HTMLButtonElement | null;
  if (btn) { btn.disabled = false; btn.textContent = 'Upload selected'; }
});

export async function cancelUploadHandler(): Promise<void> {
  const running = tasks.getAll().find(x => x.kind === 'upload' && x.state === 'running');
  if (running) await tasks.cancel(running.id);
  else await cancelUpload().catch(console.error);
}

async function createPlaylistHandler(): Promise<void> {
  const title = prompt('New playlist title:');
  if (!title?.trim()) return;
  try {
    const newPl = await createPlaylist(title.trim(), 'private');
    ytPlaylists.push(newPl);
    const plSelect = document.getElementById('ytPlaylist') as HTMLSelectElement | null;
    if (plSelect) {
      const createOpt = plSelect.querySelector('option[value="__create__"]');
      const newOpt = document.createElement('option');
      newOpt.value = newPl.id;
      newOpt.textContent = `${newPl.title} (0)`;
      if (createOpt) plSelect.insertBefore(newOpt, createOpt);
      plSelect.value = newPl.id;
    }
  } catch (e) { alert((e as Error).message); }
}

export async function deleteKeptClipHandler(btn: HTMLButtonElement): Promise<void> {
  const stem = btn.dataset.stem!;
  const filename = btn.dataset.filename!;
  if (!confirm(`Delete "${filename}"?`)) return;
  try {
    await deleteKeptClip(stem, filename);
    const idx = keptClips.findIndex(c => c.video_stem === stem && c.filename === filename);
    if (idx >= 0) keptClips.splice(idx, 1);
    renderExportView();
  } catch (e) { alert((e as Error).message); }
}

export async function openFolderHandler(video_stem: string | undefined): Promise<void> {
  if (!video_stem) return;
  try { await openKeptFolder(video_stem); }
  catch (e) { alert((e as Error).message); }
}

export async function deleteEncodedClipHandler(btn: HTMLButtonElement): Promise<void> {
  const stem = btn.dataset.stem!;
  const filename = btn.dataset.filename!;
  if (!confirm(`Delete encoded version of "${filename}"? The original kept clip will remain.`)) return;
  btn.disabled = true;
  try {
    await deleteEncodedClip(stem, filename);
    const idx = keptClips.findIndex(c => c.video_stem === stem && c.filename === filename);
    if (idx >= 0) {
      keptClips[idx].encoded_exists = false;
      keptClips[idx].encoded_filename = null;
      keptClips[idx].encoded_size_mb = undefined;
    }
    await loadExportTab();
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
  }
}

export async function deleteSourceFromExportHandler(videoStem: string, btn: HTMLButtonElement): Promise<void> {
  if (!confirm('Permanently delete this source video? This cannot be undone.')) return;
  btn.disabled = true;
  btn.textContent = 'Deleting…';
  try {
    const data = await deleteSource(videoStem);
    const row = document.getElementById('export-src-' + videoStem);
    if (row) {
      const action = row.querySelector('button');
      if (action) action.outerHTML = `<span class="cc-pill" data-status="processed">Deleted</span>`;
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

export function previewClip(index: number): void {
  document.getElementById('clipPreviewModal')?.remove();
  const clip = keptClips[index];
  const url = clip.encoded_video_url || clip.video_url;

  const modal = document.createElement('div');
  modal.id = 'clipPreviewModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:1000;display:flex;align-items:center;justify-content:center';

  const video = document.createElement('video');
  video.src = url;
  video.controls = true;
  video.autoplay = true;
  video.style.cssText = 'max-width:90vw;max-height:85vh';

  modal.appendChild(video);
  document.body.appendChild(modal);

  const close = (): void => {
    video.pause();
    modal.remove();
    document.removeEventListener('keydown', onKey);
  };

  const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
}

// ---- Helpers ----

function fmtMb(mb: number): string {
  if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
  return mb.toFixed(0) + ' MB';
}
