import './styles/legacy.css';
import './styles/cc.css';
import { initProcessTab, startProcessingHandler, scanFolderHandler, thresholdChangedHandler, deleteFileHandler } from './tabs/process';
import { loadClips, clipAction, addSegment, removeSegment, focusSegment, setSegmentPoint, seekToSegment, onSegmentInput, updateTrimIndicator, stopWaveformSync, deleteSourceHandler } from './tabs/review';
import { loadExportTab, renderExportView, toggleAllClips, startEncodingHandler, cancelEncodingHandler, startYouTubeAuthHandler, revokeYouTubeAuthHandler, startUploadHandler, cancelUploadHandler, keptClips, deleteKeptClipHandler, openFolderHandler, previewClip, deleteEncodedClipHandler, deleteSourceFromExportHandler } from './tabs/encode';
import { addSelectedToCompilation, renderCompilationList, removeCompClip, updateCompDuration, startCompilationHandler, cancelCompilationHandler, loadPastCompilations, deleteCompilationHandler, deleteCompilationSourcesHandler } from './tabs/compile';

// Expose handlers to HTML via window._cc (avoids global namespace pollution)
declare global {
  interface Window {
    _cc: typeof handlers;
    _savedVol: number;
  }
}

const handlers = {
  // Process
  startProcessingHandler,
  scanFolderHandler,
  thresholdChangedHandler,
  deleteFileHandler,
  // Review
  clipAction,
  addSegment,
  removeSegment,
  focusSegment,
  setSegmentPoint,
  seekToSegment,
  onSegmentInput,
  updateTrimIndicator,
  deleteSourceHandler,
  // Encode
  toggleAllClips,
  startEncodingHandler,
  cancelEncodingHandler,
  startYouTubeAuthHandler,
  revokeYouTubeAuthHandler,
  startUploadHandler,
  cancelUploadHandler,
  deleteKeptClipHandler,
  openFolderHandler,
  previewClip,
  deleteEncodedClipHandler,
  deleteSourceFromExportHandler,
  addSelectedToCompilation: () => addSelectedToCompilation(keptClips),
  // Compile
  renderCompilationList,
  removeCompClip,
  updateCompDuration,
  startCompilationHandler,
  cancelCompilationHandler,
  loadPastCompilations,
  deleteCompilationHandler,
  deleteCompilationSourcesHandler,
};

window._cc = handlers;
window._savedVol = 0.5;

let activeTab = 'process';

function switchTab(tab: string): void {
  activeTab = tab;
  document.querySelectorAll<HTMLElement>('.cc-tab').forEach(t => { t.dataset.active = 'false'; });
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const target = document.querySelector<HTMLElement>(`.cc-tab[data-tab="${tab}"]`);
  if (target) target.dataset.active = 'true';
  document.getElementById('view-' + tab)?.classList.add('active');

  if (tab !== 'review') stopWaveformSync();
  if (tab === 'review') loadClips();
  if (tab === 'export') loadExportTab();
}

// Tab click handlers
(document.querySelectorAll('.cc-tab') as NodeListOf<HTMLElement>).forEach(el => {
  el.addEventListener('click', () => switchTab(el.dataset.tab!));
});

// Keyboard shortcuts (review tab only)
document.addEventListener('keydown', (e: KeyboardEvent) => {
  if (activeTab !== 'review') return;
  const target = e.target as HTMLElement;
  if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return;

  switch (e.key.toLowerCase()) {
    case 'k': e.preventDefault(); clipAction('keep'); break;
    case 'd': e.preventDefault(); clipAction('discard'); break;
    case 's': e.preventDefault(); clipAction('skip'); break;
    case 'i': e.preventDefault(); setSegmentPoint(activeSegmentIndex(), 'in'); break;
    case 'o': e.preventDefault(); setSegmentPoint(activeSegmentIndex(), 'out'); break;
    case 'n': e.preventDefault(); (document.getElementById('clipCustomName') as HTMLInputElement | null)?.focus(); break;
    case ' ':
      e.preventDefault();
      const player = document.getElementById('player') as HTMLVideoElement | null;
      if (player) player.paused ? player.play() : player.pause();
      break;
  }
});

// Trim indicator on input change
document.addEventListener('input', (e: Event) => {
  const target = e.target as HTMLElement;
  if (target.classList.contains('seg-in') || target.classList.contains('seg-out')) updateTrimIndicator();
});

// App init
initProcessTab();

// Helper: get active segment index from the focused segment row
function activeSegmentIndex(): number {
  const rows = document.querySelectorAll<HTMLElement>('.segment-row');
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].style.outline) return i;
  }
  return 0;
}
