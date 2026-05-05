// Waveform — DOM bars + region overlays + single trim rectangle + playhead.
// Replaced the canvas implementation in Phase 3b so the visual treatment
// matches the design tokens exactly (region tags, animated playhead, IN/OUT
// pseudo-labels on the trim rect via .cc-wave-trim CSS).

import type { HighlightRegion, WaveformData } from './api';
import { fetchWaveform } from './api';

export const REGION_COLORS: Record<string, string> = {
  volume_spike: '#6aa0ff',
  laughter:     '#fbbf24',
  shouting:     '#ef4444',
  sudden_noise: '#a78bfa',
  fallback:     '#888888',
};

export const REGION_LABELS: Record<string, string> = {
  volume_spike: 'volume',
  laughter:     'laughter',
  shouting:     'shouting',
  sudden_noise: 'noise',
  fallback:     'fallback',
};

let waveformData: WaveformData | null = null;
let animFrame: number | null = null;

export async function loadWaveform(
  videoStem: string,
  filename: string,
  fallbackRegions: HighlightRegion[],
): Promise<void> {
  waveformData = null;
  stopWaveformSync();
  try {
    const data = await fetchWaveform(videoStem, filename);
    waveformData = data;
    if (!waveformData.highlight_regions || waveformData.highlight_regions.length === 0) {
      waveformData.highlight_regions = fallbackRegions;
    }
    renderWaveform();
    startWaveformSync();
  } catch (e) {
    console.error('Waveform load failed:', e);
  }
}

export function renderWaveform(): void {
  const wave = document.getElementById('waveform');
  if (!wave || !waveformData) return;
  const { waveform: bars, duration, highlight_regions = [] } = waveformData;

  const barsHtml = bars.map(v => {
    const h = Math.max(2, v * 80);
    return `<span class="cc-wave-bar" style="height:${h}px"></span>`;
  }).join('');

  const regionsHtml = (highlight_regions || []).map(r => {
    const left = (r.offset / duration) * 100;
    const width = (r.duration / duration) * 100;
    const color = REGION_COLORS[r.type] || REGION_COLORS.fallback;
    const label = REGION_LABELS[r.type] || r.type.replace('_', ' ');
    return `<div class="cc-wave-region" style="left:${left}%;width:${width}%;color:${color}">
      <div class="cc-wave-region-tag">${label}</div>
    </div>`;
  }).join('');

  wave.innerHTML = `
    <div class="cc-wave-bars" id="waveformBars">${barsHtml}</div>
    ${regionsHtml}
    <div class="cc-wave-trim" id="waveformTrim" style="display:none;left:0;width:0"></div>
    <div class="cc-wave-playhead" id="waveformPlayhead" style="left:0"></div>
  `;
}

export function startWaveformSync(): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;
  const tick = (): void => {
    const ph = document.getElementById('waveformPlayhead');
    if (ph && player.duration) {
      ph.style.left = (player.currentTime / player.duration) * 100 + '%';
    }
    animFrame = requestAnimationFrame(tick);
  };
  animFrame = requestAnimationFrame(tick);
}

export function stopWaveformSync(): void {
  if (animFrame !== null) {
    cancelAnimationFrame(animFrame);
    animFrame = null;
  }
}

export function updateWaveformTrimMarkers(inPct: number, outPct: number, hasTrim: boolean): void {
  const trim = document.getElementById('waveformTrim');
  if (!trim) return;
  if (hasTrim) {
    trim.style.display = 'block';
    trim.style.left = inPct + '%';
    trim.style.width = Math.max(0, outPct - inPct) + '%';
  } else {
    trim.style.display = 'none';
  }
}

export function getWaveformDuration(): number {
  return waveformData?.duration ?? 0;
}
