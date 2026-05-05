import {
  fetchKeptClips, fetchPresets, startEncoding, fetchEncodeStatus, cancelEncoding,
  fetchYouTubeStatus, fetchYouTubePlaylists, startYouTubeAuth, revokeYouTubeAuth,
  startUpload, fetchUploadStatus, cancelUpload, createPlaylist, deleteKeptClip,
  openKeptFolder, fetchStorageSummary, deleteEncodedClip, fetchSources, deleteSource,
} from '../api';
import type { KeptClipInfo, Playlist, StorageSummary, SourceVideo } from '../api';
import { escapeHtml, fmtTime, formatClipTitle } from '../utils';
import { tasks } from '../tasks';

export let keptClips: KeptClipInfo[] = [];
let encodingPresets: Array<{ name: string; display_name: string; extension: string }> = [];
let defaultPreset = 'h264_hq';
let encodingFpsOptions: Array<number | null> = [null, 24, 30, 60];
let ytAuthenticated = false;
let ytChannelName = '';
let ytPlaylists: Playlist[] = [];

let storageSummary: StorageSummary | null = null;
let sourcesData: SourceVideo[] = [];

export async function loadExportTab(): Promise<void> {
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
  let html = '';

  // === Storage Summary Bar ===
  if (storageSummary) {
    const fmt = (mb: number) => mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb.toFixed(1) + ' MB';
    html += `<div style="font-size:12px;color:#666;margin-bottom:16px;padding:10px 14px;background:#111;border-radius:8px;display:flex;gap:24px;flex-wrap:wrap">`;
    html += `<span>Kept: <span style="color:#94a3b8">${storageSummary.kept.count} clips · ${fmt(storageSummary.kept.size_mb)}</span></span>`;
    html += `<span>Encoded: <span style="color:#94a3b8">${storageSummary.encoded.count} clips · ${fmt(storageSummary.encoded.size_mb)}</span></span>`;
    html += `<span>Compilations: <span style="color:#94a3b8">${storageSummary.compilations.count} · ${fmt(storageSummary.compilations.size_mb)}</span></span>`;
    html += `<span style="margin-left:auto">Total: <span style="color:#e2e8f0;font-weight:600">${fmt(storageSummary.total_mb)}</span></span>`;
    html += `</div>`;
  }

  // === Encode Clips ===
  html += `<div class="export-section"><h2>Encode Clips</h2>`;
  html += `<div class="export-toolbar"><div class="form-group">`;
  html += `<label>Preset</label><select class="select-styled" id="encodePreset">`;
  for (const p of encodingPresets) {
    html += `<option value="${p.name}" ${p.name === defaultPreset ? 'selected' : ''}>${escapeHtml(p.display_name)}</option>`;
  }
  html += `</select></div><div class="form-group">`;
  html += `<label>FPS</label><select class="select-styled" id="encodeFps" style="width:100px">`;
  for (const fps of encodingFpsOptions) {
    html += `<option value="${fps ?? ''}">${fps ? fps + 'fps' : 'Original'}</option>`;
  }
  html += `</select></div>`;
  html += `<div class="form-group" id="slowdownGroup" style="display:none">`;
  html += `<label>Slowdown</label><select class="select-styled" id="encodeSlowdown" style="width:100px">`;
  html += `<option value="">None</option><option value="0.5">0.5x</option><option value="0.25">0.25x</option>`;
  html += `</select></div>`;
  html += `<button class="btn-secondary" onclick="window._cc.toggleAllClips('encode')">Select All</button>`;
  html += `<button class="btn-process" id="btnEncode" onclick="window._cc.startEncodingHandler()">Encode Selected</button></div>`;
  html += `<div id="encodeProgress" style="display:none"><div style="display:flex;align-items:center;gap:12px">`;
  html += `<span class="progress-label" id="encodeProgressLabel"></span>`;
  html += `<button class="btn-cancel" onclick="window._cc.cancelEncodingHandler()">Cancel</button></div>`;
  html += `<div class="progress-bar"><div class="progress-fill" id="encodeProgressFill"></div></div></div>`;

  if (keptClips.length === 0) {
    html += `<div class="empty-state">No kept clips yet.</div>`;
  } else {
    html += `<div class="clip-list">`;
    for (let i = 0; i < keptClips.length; i++) {
      const clip = keptClips[i];
      const dur = clip.duration ? Math.round(clip.duration) + 's' : '';
      const date = clip.clipped_at ? clip.clipped_at.slice(0, 10) : '';
      const tags = (clip.detection_reasons || []).map(r =>
        `<span class="tag tag-${r}">${r.replace('_', ' ')}</span>`
      ).join('');

      // Size display
      const keptMb = clip.size_mb != null ? clip.size_mb.toFixed(1) + ' MB' : '';
      let sizeHtml = '';
      if (keptMb) {
        if (clip.encoded_exists && clip.encoded_size_mb != null) {
          sizeHtml = `<span class="clip-detail">${keptMb} → ${clip.encoded_size_mb.toFixed(1)} MB</span>`;
        } else {
          sizeHtml = `<span class="clip-detail">${keptMb}</span>`;
        }
      }

      // Encoded badge + delete-encoded button
      let encodedHtml = '';
      if (clip.encoded_exists) {
        encodedHtml = `<span class="badge badge-encoded">Encoded</span>`;
        encodedHtml += `<button class="btn-cancel" style="padding:2px 8px;font-size:11px" `
          + `data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}" `
          + `onclick="window._cc.deleteEncodedClipHandler(this)" title="Delete encoded version">✕ encoded</button>`;
      }

      html += `<div class="clip-row">`;
      html += `<input type="checkbox" class="clip-checkbox encode-cb" data-index="${i}" checked>`;
      html += `<span class="clip-name" title="${escapeHtml(clip.filename)}" style="cursor:pointer" onclick="window._cc.previewClip(${i})">${escapeHtml(clip.custom_name || clip.filename)}</span>`;
      html += `<span class="clip-detail">${escapeHtml(clip.video_stem || '')}</span>`;
      html += `<span class="clip-detail">${dur}</span>`;
      html += `<span class="clip-detail" style="color:#888">${date}</span>`;
      html += `<span class="tags" style="margin-bottom:0">${tags}</span>`;
      html += encodedHtml;
      html += sizeHtml;
      html += `<button class="btn-cancel" style="margin-left:auto;padding:2px 8px;font-size:12px" `
            + `data-stem="${escapeHtml(clip.video_stem)}" data-filename="${escapeHtml(clip.filename)}" `
            + `onclick="window._cc.deleteKeptClipHandler(this)">✕</button>`;
      html += `<button class="btn-secondary" style="padding:2px 8px;font-size:12px" `
            + `data-stem="${escapeHtml(clip.video_stem)}" `
            + `onclick="window._cc.openFolderHandler(this.dataset.stem)" title="Open folder in Explorer">📁</button>`;
      html += `</div>`;
    }
    html += `</div>`;
  }
  html += `</div>`;

  // === Build Compilation ===
  if (keptClips.length >= 2) {
    html += `<div class="export-section"><h2>Build Compilation</h2>`;
    html += `<p style="color:#888;font-size:13px;margin-bottom:16px">Select clips above, then build a highlight reel. Drag to reorder.</p>`;
    html += `<div class="comp-toolbar">`;
    html += `<button class="btn-secondary" onclick="window._cc.addSelectedToCompilation()">Add Selected</button>`;
    html += `<div class="form-group"><label>Transition</label>`;
    html += `<select class="select-styled" id="compTransition" onchange="window._cc.updateCompDuration()">`;
    html += `<option value="cut">Hard Cut</option><option value="crossfade">Crossfade</option></select></div>`;
    html += `<div class="form-group" id="compXfadeGroup" style="display:none"><label>Duration</label>`;
    html += `<input type="number" class="trim-time" id="compXfadeDur" value="0.5" min="0.1" max="3" step="0.1" style="width:70px" oninput="window._cc.updateCompDuration()">`;
    html += `<span style="color:#888;font-size:12px">s</span></div>`;
    html += `<div class="form-group"><label>Title</label>`;
    html += `<input type="text" class="clip-name-input" id="compTitle" placeholder="Optional title..." style="width:200px"></div>`;
    html += `</div>`;
    html += `<div id="compList"></div>`;
    html += `<div class="comp-footer">`;
    html += `<span class="comp-summary" id="compSummary">No clips added yet.</span>`;
    html += `<button class="btn-process" id="btnBuildComp" onclick="window._cc.startCompilationHandler()">Build</button>`;
    html += `<button class="btn-cancel" onclick="window._cc.cancelCompilationHandler()">Cancel</button>`;
    html += `</div>`;
    html += `<div id="compProgress" style="display:none">`;
    html += `<div style="display:flex;align-items:center;gap:12px">`;
    html += `<span class="progress-label" id="compProgressLabel"></span></div>`;
    html += `<div class="progress-bar"><div class="progress-fill" id="compProgressFill"></div></div></div>`;
    html += `<div id="pastCompilations"></div></div>`;
  }

  // === YouTube Upload ===
  html += `<div class="export-section"><h2>Upload to YouTube</h2>`;
  if (!ytAuthenticated) {
    html += `<div class="yt-auth-section">`;
    html += `<div class="form-group"><label>Client ID</label><input type="text" id="ytClientId" placeholder="OAuth Client ID"></div>`;
    html += `<div class="form-group"><label>Client Secret</label><input type="password" id="ytClientSecret" placeholder="OAuth Client Secret"></div>`;
    html += `<button class="btn-process" onclick="window._cc.startYouTubeAuthHandler()">Sign In</button></div>`;
    html += `<p class="yt-help">Create OAuth credentials at <a href="https://console.cloud.google.com" target="_blank" style="color:#60a5fa">console.cloud.google.com</a></p>`;
  } else {
    html += `<div class="yt-connected">`;
    html += `<span style="color:#888;font-size:13px">Connected as:</span>`;
    html += `<span class="yt-channel">${escapeHtml(ytChannelName)}</span>`;
    html += `<button class="btn-signout" onclick="window._cc.revokeYouTubeAuthHandler()">Sign Out</button></div>`;
    html += `<div class="yt-settings">`;
    html += `<div class="form-group"><label>Privacy</label><select class="select-styled" id="ytPrivacy">`;
    html += `<option value="private" selected>Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></div>`;
    html += `<div class="form-group"><label>Playlist</label><div class="playlist-row"><select class="select-styled" id="ytPlaylist">`;
    html += `<option value="">None</option>`;
    for (const pl of ytPlaylists) {
      html += `<option value="${escapeHtml(pl.id)}">${escapeHtml(pl.title)} (${pl.item_count})</option>`;
    }
    html += `<option value="__create__">+ Create New...</option></select></div></div>`;
    html += `<div class="form-group"><label>Category</label><select class="select-styled" id="ytCategory">`;
    html += `<option value="20" selected>Gaming</option><option value="24">Entertainment</option>`;
    html += `<option value="22">People &amp; Blogs</option><option value="17">Sports</option>`;
    html += `<option value="10">Music</option><option value="1">Film &amp; Animation</option><option value="23">Comedy</option>`;
    html += `</select></div>`;
    html += `<div class="form-group"><label>Tags</label><input type="text" class="title-input" id="ytTags" placeholder="tag1, tag2, tag3" style="width:100%"></div>`;
    html += `<div class="form-group full-width"><label>Description Template</label>`;
    html += `<textarea class="textarea-styled" id="ytDescription" placeholder="Enter a description template..."></textarea>`;
    html += `<div class="template-vars">Variables: <code>{source_video}</code> <code>{start_time}</code> <code>{end_time}</code> <code>{duration}</code> <code>{detection_reasons}</code></div>`;
    html += `</div></div>`;
    html += `<div class="export-toolbar">`;
    html += `<button class="btn-secondary" onclick="window._cc.toggleAllClips('upload')">Select All</button>`;
    html += `<button class="btn-process" id="btnUpload" onclick="window._cc.startUploadHandler()">Upload Selected</button></div>`;
    html += `<div id="uploadProgress" style="display:none">`;
    html += `<div style="display:flex;align-items:center;gap:12px">`;
    html += `<span class="progress-label" id="uploadProgressLabel"></span>`;
    html += `<button class="btn-cancel" onclick="window._cc.cancelUploadHandler()">Cancel</button></div>`;
    html += `<div class="progress-bar"><div class="progress-fill" id="uploadProgressFill"></div></div></div>`;
    if (keptClips.length === 0) {
      html += `<div class="empty-state" style="padding:40px 0">No kept clips to upload.</div>`;
    } else {
      html += `<div id="uploadClipList">`;
      for (let i = 0; i < keptClips.length; i++) {
        const clip = keptClips[i];
        const defaultTitle = clip.custom_name || formatClipTitle(clip.filename);
        const alreadyUploaded = !!clip.youtube_url;
        let statusHtml = `<span class="badge badge-ready">Ready</span>`;
        if (clip.youtube_upload_status === 'failed') {
          statusHtml = `<span class="badge badge-error">Failed</span>`;
        } else if (alreadyUploaded) {
          statusHtml = `<a class="upload-link" href="${escapeHtml(clip.youtube_url!)}" target="_blank">Uploaded</a>`;
        }
        html += `<div class="clip-row" id="upload-row-${i}">`;
        html += `<input type="checkbox" class="clip-checkbox upload-cb" data-index="${i}" ${alreadyUploaded ? '' : 'checked'}>`;
        html += `<span class="clip-detail" style="flex-shrink:0;width:120px" title="${escapeHtml(clip.filename)}">${escapeHtml(clip.filename)}</span>`;
        html += `<span class="clip-detail" style="margin-right:4px">Title:</span>`;
        html += `<input type="text" class="title-input upload-title" data-index="${i}" value="${escapeHtml(defaultTitle)}">`;
        html += statusHtml;
        html += `</div>`;
      }
      html += `</div>`;
    }
  }
  html += `</div>`;

  // === Source Videos ===
  const deletableSources = sourcesData.filter(s => s.exists);
  if (deletableSources.length > 0) {
    html += `<div class="export-section"><h2>Source Videos</h2>`;
    html += `<p style="color:#888;font-size:13px;margin-bottom:16px">Delete source videos to free disk space. Only sources with existing files are shown.</p>`;
    for (const src of deletableSources) {
      const name = src.source_path.split(/[/\\]/).pop() ?? src.source_path;
      const pending = src.total - src.kept - src.discarded;
      const reviewSummary = src.fully_reviewed
        ? `${src.kept} kept / ${src.discarded} discarded`
        : `${src.kept} kept / ${src.discarded} discarded / ${pending} pending`;
      const reviewColor = src.fully_reviewed ? '#4ade80' : '#fbbf24';
      html += `<div class="source-row" id="export-src-${escapeHtml(src.video_stem)}">`;
      html += `<div class="source-info">`;
      html += `<div class="source-name" title="${escapeHtml(src.source_path)}">${escapeHtml(name)}</div>`;
      html += `<div class="source-detail" style="color:${reviewColor}">${reviewSummary}</div>`;
      html += `</div>`;
      html += `<div class="source-size">${src.size_mb} MB</div>`;
      html += `<button class="btn-delete" data-stem="${escapeHtml(src.video_stem)}" `
            + `onclick="window._cc.deleteSourceFromExportHandler(this.dataset.stem, this)">Delete</button>`;
      html += `</div>`;
    }
    const totalMb = deletableSources.reduce((sum, s) => sum + s.size_mb, 0);
    html += `<div class="cleanup-total">Total reclaimable: ${totalMb.toFixed(1)} MB</div>`;
    html += `</div>`;
  }

  document.getElementById('exportContent')!.innerHTML = html;

  // Post-render: GIF slowdown visibility
  const presetSelect = document.getElementById('encodePreset') as HTMLSelectElement | null;
  if (presetSelect) {
    const updateSlowdown = () => {
      const sg = document.getElementById('slowdownGroup');
      if (sg) sg.style.display = presetSelect.value === 'gif' ? 'flex' : 'none';
    };
    updateSlowdown();
    presetSelect.addEventListener('change', updateSlowdown);
  }

  // Playlist create handler
  const plSelect = document.getElementById('ytPlaylist') as HTMLSelectElement | null;
  if (plSelect) {
    plSelect.addEventListener('change', function () {
      if (this.value === '__create__') { createPlaylistHandler(); this.value = ''; }
    });
  }

  // Initialize compilation UI (from compile.ts)
  window._cc.renderCompilationList();
  window._cc.loadPastCompilations();
}

export function toggleAllClips(section: 'encode' | 'upload'): void {
  const selector = section === 'encode' ? '.encode-cb' : '.upload-cb';
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>(selector));
  if (!checkboxes.length) return;
  const allChecked = checkboxes.every(cb => cb.checked);
  checkboxes.forEach(cb => { cb.checked = !allChecked; });
}

export async function startEncodingHandler(): Promise<void> {
  const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>('.encode-cb:checked'));
  if (!checkboxes.length) { alert('Select at least one clip to encode.'); return; }
  if (tasks.isRunning('encode')) { alert('An encode task is already running.'); return; }

  const preset = (document.getElementById('encodePreset') as HTMLSelectElement).value;
  const fpsVal = (document.getElementById('encodeFps') as HTMLSelectElement).value;
  const fps = fpsVal ? parseInt(fpsVal) : null;
  const slowdownVal = (document.getElementById('encodeSlowdown') as HTMLSelectElement).value;
  const slowdown = slowdownVal ? parseFloat(slowdownVal) : null;

  const clipsToEncode = checkboxes.map(cb => {
    const idx = parseInt(cb.dataset.index!);
    const clip = keptClips[idx];
    return { video_stem: clip.video_stem, filename: clip.filename };
  });

  const btn = document.getElementById('btnEncode') as HTMLButtonElement;
  btn.disabled = true;
  btn.textContent = 'Encoding...';

  try {
    await startEncoding({ clips: clipsToEncode, preset, target_fps: fps, slowdown_factor: slowdown });
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Encode Selected';
    return;
  }

  document.getElementById('encodeProgress')!.style.display = 'block';

  tasks.start({
    kind: 'encode',
    label: 'Encoding clips',
    subtitle: `${clipsToEncode.length} clip${clipsToEncode.length === 1 ? '' : 's'} · ${preset}`,
    pollMs: 800,
    cancel: cancelEncoding,
    fetchStatus: async () => {
      const data = await fetchEncodeStatus();
      const label = document.getElementById('encodeProgressLabel');
      const fill = document.getElementById('encodeProgressFill');
      const completedCount = (data.completed || []).length;
      const pct = data.total > 0 ? Math.round((completedCount / data.total) * 100) : 0;
      const subtitle = data.current_file
        ? `clip ${data.current_index || 0} of ${data.total}: ${data.current_file}`
        : `${completedCount} / ${data.total}`;
      if (label) label.textContent = `Encoding clip ${data.current_index || 0} of ${data.total}: ${data.current_file || ''}`;
      if (fill) fill.style.width = pct + '%';
      const error = !data.running && data.errors?.length
        ? `${data.errors.length} error(s) — ${data.errors[0].error}`
        : null;
      return { running: data.running, pct, subtitle, error };
    },
    formatResult: (t) => t.subtitle ?? '',
  });
}

// Restore the legacy Encode-tab UI when the encode task finishes.
tasks.addEventListener('task-complete', (e) => {
  const t = (e as CustomEvent).detail.task;
  if (t.kind !== 'encode') return;
  const btn = document.getElementById('btnEncode') as HTMLButtonElement | null;
  if (btn) { btn.disabled = false; btn.textContent = 'Encode Selected'; }
  const fill = document.getElementById('encodeProgressFill');
  if (fill) fill.style.width = '100%';
});

export async function cancelEncodingHandler(): Promise<void> {
  const running = tasks.getAll().find(x => x.kind === 'encode' && x.state === 'running');
  if (running) {
    await tasks.cancel(running.id);
  } else {
    await cancelEncoding().catch(console.error);
  }
}

export async function startYouTubeAuthHandler(): Promise<void> {
  const clientId = (document.getElementById('ytClientId') as HTMLInputElement).value.trim();
  const clientSecret = (document.getElementById('ytClientSecret') as HTMLInputElement).value.trim();
  if (!clientId || !clientSecret) { alert('Enter both Client ID and Client Secret.'); return; }
  try {
    const data = await startYouTubeAuth(clientId, clientSecret);
    window.open(data.auth_url, 'youtube-auth', 'width=600,height=700');
    const listener = async (event: MessageEvent) => {
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
  btn.disabled = true; btn.textContent = 'Uploading...';
  try {
    await startUpload({ clips: clipsToUpload });
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Upload Selected';
    return;
  }

  document.getElementById('uploadProgress')!.style.display = 'block';
  let lastCompletedCount = 0;

  tasks.start({
    kind: 'upload',
    label: 'Uploading to YouTube',
    subtitle: `${clipsToUpload.length} clip${clipsToUpload.length === 1 ? '' : 's'}`,
    pollMs: 1000,
    cancel: cancelUpload,
    fetchStatus: async () => {
      const data = await fetchUploadStatus();
      const label = document.getElementById('uploadProgressLabel');
      const fill = document.getElementById('uploadProgressFill');

      const filePct = data.bytes_total > 0 ? Math.round((data.bytes_sent / data.bytes_total) * 100) : 0;
      const completedClips = (data.completed || []).length;
      const overallPct = data.total > 0
        ? Math.round(((completedClips + filePct / 100) / data.total) * 100)
        : 0;

      if (label) label.textContent = `Uploading clip ${data.current_index || 0} of ${data.total}: ${data.current_file || ''} (${filePct}%)`;
      if (fill) fill.style.width = overallPct + '%';

      // Splice newly-completed uploads into the per-clip list as links
      const newLines: string[] = [];
      const completed = data.completed || [];
      for (let i = lastCompletedCount; i < completed.length; i++) {
        const c = completed[i];
        const idx = keptClips.findIndex(k => k.filename === c.filename);
        if (idx >= 0) {
          const row = document.getElementById('upload-row-' + idx);
          const badge = row?.querySelector('.badge, .upload-link');
          if (badge) badge.outerHTML = `<a class="upload-link" href="${escapeHtml(c.url)}" target="_blank">Uploaded</a>`;
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
  if (btn) { btn.disabled = false; btn.textContent = 'Upload Selected'; }
  const fill = document.getElementById('uploadProgressFill');
  if (fill) fill.style.width = '100%';
});

export async function cancelUploadHandler(): Promise<void> {
  const running = tasks.getAll().find(x => x.kind === 'upload' && x.state === 'running');
  if (running) {
    await tasks.cancel(running.id);
  } else {
    await cancelUpload().catch(console.error);
  }
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
    const row = btn.closest('.clip-row') as HTMLElement | null;
    if (row) row.remove();
    const idx = keptClips.findIndex(c => c.video_stem === stem && c.filename === filename);
    if (idx >= 0) keptClips.splice(idx, 1);
  } catch (e) {
    alert((e as Error).message);
  }
}

export async function openFolderHandler(video_stem: string | undefined): Promise<void> {
  if (!video_stem) return;
  try {
    await openKeptFolder(video_stem);
  } catch (e) {
    alert((e as Error).message);
  }
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
  btn.textContent = 'Deleting...';
  try {
    const data = await deleteSource(videoStem);
    const row = document.getElementById('export-src-' + videoStem);
    if (row) {
      row.querySelector('.btn-delete')!.outerHTML = '<button class="btn-deleted">Deleted</button>';
      const sizeEl = row.querySelector('.source-size') as HTMLElement;
      let label = data.freed_mb + ' MB freed';
      if (data.leftover > 0) label += ` (${data.leftover} clip(s) locked)`;
      sizeEl.textContent = label;
      sizeEl.style.color = data.leftover > 0 ? '#fbbf24' : '#4ade80';
    }
  } catch (e) {
    alert((e as Error).message);
    btn.disabled = false;
    btn.textContent = 'Delete';
  }
}

export function previewClip(index: number): void {
  document.getElementById('clipPreviewModal')?.remove();  // close any existing modal
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
