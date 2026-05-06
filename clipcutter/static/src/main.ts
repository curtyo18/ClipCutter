import './styles/cc.css';
import { initTaskUI, tasks } from './tasks';
import type { Task } from './tasks';
import { initProcessTab, startProcessingHandler, scanFolderHandler, deleteFileHandler, scanCurrentFolder } from './tabs/process';
import { loadClips, clipAction, addSegment, removeSegment, focusSegment, setSegmentPoint, seekToSegment, stopWaveformSync, deleteSourceHandler, getActiveSegmentIndex } from './tabs/review';
import { loadExportTab, toggleAllClips, startEncodingHandler, cancelEncodingHandler, startYouTubeAuthHandler, revokeYouTubeAuthHandler, startUploadHandler, cancelUploadHandler, keptClips, deleteKeptClipHandler, openFolderHandler, previewClip, deleteEncodedClipHandler, deleteSourceFromExportHandler } from './tabs/encode';
import { addSelectedToCompilation, removeCompClip, updateCompDuration, startCompilationHandler, cancelCompilationHandler, deleteCompilationHandler, deleteCompilationSourcesHandler, previewCompilation } from './tabs/compile';

// Expose handlers to HTML via window._cc (avoids global namespace pollution)
declare global {
  interface Window {
    _cc: typeof handlers;
    _savedVol: number;
  }
}

// Handlers exposed to inline onclick / oninput attributes via `window._cc.X(...)`.
// Each entry is referenced from generated HTML somewhere in the codebase; if you
// remove an HTML reference, drop the corresponding handler here too.
const handlers = {
  // Process
  startProcessingHandler,
  scanFolderHandler,
  deleteFileHandler,
  // Review
  clipAction,
  addSegment,
  removeSegment,
  focusSegment,
  setSegmentPoint,
  seekToSegment,
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
  removeCompClip,
  updateCompDuration,
  startCompilationHandler,
  cancelCompilationHandler,
  deleteCompilationHandler,
  deleteCompilationSourcesHandler,
  previewCompilation,
};

window._cc = handlers;
window._savedVol = 0.5;

let activeTab = 'process';

function switchTab(tab: string): void {
  // Pause any playing <video> so a clip in Review (or a preview modal in
  // Export) doesn't keep playing when the user is on a different tab.
  document.querySelectorAll<HTMLVideoElement>('video').forEach(v => {
    if (!v.paused) v.pause();
  });

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
    case 'i': e.preventDefault(); setSegmentPoint(getActiveSegmentIndex(), 'in'); break;
    case 'o': e.preventDefault(); setSegmentPoint(getActiveSegmentIndex(), 'out'); break;
    case 'n': e.preventDefault(); (document.getElementById('clipCustomName') as HTMLInputElement | null)?.focus(); break;
    case ' ':
      e.preventDefault();
      const player = document.getElementById('player') as HTMLVideoElement | null;
      if (player) player.paused ? player.play() : player.pause();
      break;
  }
});

// App init
initTaskUI();
initProcessTab();

// Cross-tab refetch hooks: when a task finishes, refresh the data it touched.
// Per the design handover, each tab subscribes; tab loaders are idempotent
// so calling them while the user is on another tab is harmless.
tasks.addEventListener('task-complete', (e) => {
  const t = (e as CustomEvent).detail.task as Task;
  switch (t.kind) {
    case 'process':
      scanCurrentFolder();
      break;
    case 'encode':
    case 'compile':
    case 'upload':
      loadExportTab();
      break;
    case 'keep':
      // Refresh the export tab so kept clips show up there. The review tab
      // already advanced optimistically and uses local state, so we don't
      // reload it here — that would lose the user's place in the queue.
      void loadExportTab();
      break;
  }
});

