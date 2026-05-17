/** Format seconds as MM:SS */
export function fmtTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

/** Format seconds as M:SS.d (tenths precision) */
export function fmtTimePrecise(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.round((seconds % 1) * 10);
  return `${m}:${String(s).padStart(2, '0')}.${ms}`;
}

/** Parse "M:SS.d" or plain seconds string into a number */
export function parseTrimTime(str: string): number {
  const parts = str.trim().split(':');
  if (parts.length === 2) {
    return parseInt(parts[0]) * 60 + parseFloat(parts[1]);
  }
  return parseFloat(str) || 0;
}

/** Escape HTML special characters — safe for both text and attribute contexts.
 *  The textContent/innerHTML round-trip only escapes & < > and silently leaves
 *  " and ' alone, which breaks out of attribute boundaries. Explicit replace
 *  chain covers the full 5-char set. */
export function escapeHtml(text: string): string {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Convert display name to safe filename stem */
export function sanitizeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9 _-]/g, '').trim().replace(/ /g, '_');
}

/** Format a raw filename into a title (remove extension, replace _ and - with spaces) */
export function formatClipTitle(filename: string): string {
  let title = filename.replace(/\.[^.]+$/, '');
  title = title.replace(/[_-]/g, ' ');
  return title.charAt(0).toUpperCase() + title.slice(1);
}

/** Apply the user's saved volume / muted preference to a <video>, and write
 *  back any subsequent change. Storage is best-effort — Safari private mode and
 *  similar contexts throw on `localStorage`, so the read/write is wrapped. */
export function attachVolumePreference(video: HTMLVideoElement): void {
  const apply = (): void => {
    try {
      const rawVol = localStorage.getItem('cc.playerVolume');
      const rawMuted = localStorage.getItem('cc.playerMuted');
      let v = rawVol == null ? 0.5 : parseFloat(rawVol);
      if (!isFinite(v)) v = 0.5;
      v = Math.max(0, Math.min(1, v));
      video.volume = v;
      if (rawMuted != null) video.muted = rawMuted === 'true';
    } catch { /* ignore */ }
  };

  if (video.readyState >= 1) apply();
  video.addEventListener('loadeddata', apply);

  video.addEventListener('volumechange', () => {
    try {
      localStorage.setItem('cc.playerVolume', String(video.volume));
      localStorage.setItem('cc.playerMuted', String(video.muted));
    } catch { /* ignore */ }
  });
}

/** Show a fullscreen modal that plays the given video URL. Esc / backdrop click close. */
export function openPreviewModal(url: string, _title?: string): void {
  document.getElementById('clipPreviewModal')?.remove();

  const modal = document.createElement('div');
  modal.id = 'clipPreviewModal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:1000;display:flex;align-items:center;justify-content:center';

  const video = document.createElement('video');
  video.src = url;
  video.controls = true;
  video.autoplay = true;
  video.style.cssText = 'max-width:90vw;max-height:85vh';
  attachVolumePreference(video);

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
