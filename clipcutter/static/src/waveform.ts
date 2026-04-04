import type { HighlightRegion, WaveformData } from './api';
import { fetchWaveform } from './api';

const REGION_COLORS: Record<string, string> = {
  volume_spike: '#f87171',
  laughter: '#4ade80',
  shouting: '#fbbf24',
  sudden_noise: '#60a5fa',
  fallback: '#888',
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
  const canvas = document.getElementById('waveformCanvas') as HTMLCanvasElement | null;
  if (!canvas || !waveformData) return;
  const ctx = canvas.getContext('2d')!;

  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
  const w = rect.width;
  const h = rect.height;
  ctx.clearRect(0, 0, w, h);

  const bars = waveformData.waveform;
  const dur = waveformData.duration;
  const regions = waveformData.highlight_regions || [];

  for (const region of regions) {
    const x1 = (region.offset / dur) * w;
    const x2 = ((region.offset + region.duration) / dur) * w;
    ctx.fillStyle = REGION_COLORS[region.type] || '#fbbf24';
    ctx.globalAlpha = 0.12;
    ctx.fillRect(x1, 0, Math.max(x2 - x1, 2), h);
  }
  ctx.globalAlpha = 1.0;

  const barWidth = w / bars.length;
  const gap = Math.max(0.5, barWidth * 0.15);
  for (let i = 0; i < bars.length; i++) {
    const val = bars[i];
    const barH = Math.max(1, val * h * 0.9);
    const x = i * barWidth;
    const y = (h - barH) / 2;
    const barTime = (i / bars.length) * dur;
    let inRegion = false;
    for (const region of regions) {
      if (barTime >= region.offset && barTime <= region.offset + region.duration) {
        ctx.fillStyle = REGION_COLORS[region.type] || '#fbbf24';
        inRegion = true;
        break;
      }
    }
    if (!inRegion) ctx.fillStyle = '#3b82f6';
    ctx.globalAlpha = 0.85;
    ctx.fillRect(x + gap / 2, y, barWidth - gap, barH);
  }
  ctx.globalAlpha = 1.0;
}

export function startWaveformSync(): void {
  const player = document.getElementById('player') as HTMLVideoElement | null;
  if (!player) return;

  function tick() {
    const cursor = document.getElementById('waveformCursor');
    if (cursor && player!.duration) {
      cursor.style.left = (player!.currentTime / player!.duration) * 100 + '%';
    }
    animFrame = requestAnimationFrame(tick);
  }
  animFrame = requestAnimationFrame(tick);
}

export function stopWaveformSync(): void {
  if (animFrame !== null) {
    cancelAnimationFrame(animFrame);
    animFrame = null;
  }
}

export function updateWaveformTrimMarkers(inPct: number, outPct: number, hasTrim: boolean): void {
  const trimIn = document.getElementById('waveformTrimIn');
  const trimOut = document.getElementById('waveformTrimOut');
  if (!trimIn || !trimOut) return;
  if (hasTrim) {
    trimIn.style.display = 'block';
    trimIn.style.left = inPct + '%';
    trimOut.style.display = 'block';
    trimOut.style.left = outPct + '%';
  } else {
    trimIn.style.display = 'none';
    trimOut.style.display = 'none';
  }
}

export function getWaveformDuration(): number {
  return waveformData?.duration ?? 0;
}
