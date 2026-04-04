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

/** Escape HTML special characters */
export function escapeHtml(text: string): string {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
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
