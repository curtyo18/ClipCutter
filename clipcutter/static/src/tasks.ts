// Background-task controller.
//
// Lifecycles for the four (eventually five) long-running operations
// — process, encode, compile, upload, and later keep — are unified
// here. Each kind keeps its own status-payload shape on the wire,
// but the FE wraps them in a common Task envelope and a single
// poll loop. The chip in the tab bar, the toast stack, and the
// task modal all subscribe to two events:
//
//   - 'tasks-changed' — any state mutation (start, pct update, done)
//   - 'task-complete' — emitted once per task when it ends, used by
//                       tab loaders to refetch the data the task touched
//
// Concurrency model:
//   - process / encode / compile / upload are singleton kinds
//     (the backend status endpoints are global). start() will throw
//     if a kind is already running.
//   - keep can run in parallel (different clips) — backend changes
//     come in Phase 4; for now it's also singleton.

import { escapeHtml } from './utils';

export type TaskKind = 'process' | 'encode' | 'compile' | 'upload' | 'keep';
export type TaskState = 'running' | 'done' | 'error' | 'cancelled';

export interface Task {
  id: string;
  kind: TaskKind;
  state: TaskState;
  pct: number;            // 0–100; 0 if unknown
  label: string;          // shown in chip / modal title
  subtitle?: string;      // current item, free-form
  log: string[];          // rolling log, shown in modal mini-log
  error?: string;
  startedAt: number;
  finishedAt?: number;
  resultSummary?: string; // shown in toast when done (e.g. "5 clips · 124 MB")
}

export interface TaskRunner {
  kind: TaskKind;
  label: string;
  subtitle?: string;
  pollMs: number;
  fetchStatus(): Promise<TaskStatusUpdate>;
  cancel?: () => Promise<unknown>;
  /** Called when state transitions to 'done' or 'error'. Returned string is shown in the toast. */
  formatResult?: (task: Task) => string;
}

export interface TaskStatusUpdate {
  running: boolean;
  pct?: number;
  subtitle?: string;
  newLogLines?: string[];
  error?: string | null;
}

const KIND_LABEL: Record<TaskKind, { running: string; done: string }> = {
  process: { running: 'Processing',  done: 'Process complete' },
  encode:  { running: 'Encoding',    done: 'Encode complete'  },
  compile: { running: 'Building',    done: 'Compilation built' },
  upload:  { running: 'Uploading',   done: 'Upload complete'  },
  keep:    { running: 'Trimming',    done: 'Clip kept'        },
};

class TaskController extends EventTarget {
  private tasks = new Map<string, Task>();
  private runners = new Map<string, TaskRunner>();
  private timers = new Map<string, ReturnType<typeof setInterval>>();
  private nextId = 1;

  start(runner: TaskRunner): string {
    if (runner.kind !== 'keep' && this.findRunningByKind(runner.kind)) {
      throw new Error(`A ${runner.kind} task is already running`);
    }
    const id = `t${this.nextId++}`;
    const task: Task = {
      id,
      kind: runner.kind,
      state: 'running',
      pct: 0,
      label: runner.label,
      subtitle: runner.subtitle,
      log: [],
      startedAt: Date.now(),
    };
    this.tasks.set(id, task);
    this.runners.set(id, runner);
    this.dispatchChanged();

    const tick = async (): Promise<void> => {
      const t = this.tasks.get(id);
      if (!t || t.state !== 'running') return;
      try {
        const u = await runner.fetchStatus();
        if (u.pct != null) t.pct = u.pct;
        if (u.subtitle != null) t.subtitle = u.subtitle;
        if (u.newLogLines && u.newLogLines.length) t.log = t.log.concat(u.newLogLines);
        if (!u.running) {
          this.complete(id, u.error ?? null);
        } else {
          this.dispatchChanged();
        }
      } catch (e) {
        console.error(`[task ${id}/${runner.kind}] poll error`, e);
      }
    };
    this.timers.set(id, setInterval(tick, runner.pollMs));
    return id;
  }

  private complete(id: string, error: string | null): void {
    const t = this.tasks.get(id);
    if (!t) return;
    const timer = this.timers.get(id);
    if (timer) { clearInterval(timer); this.timers.delete(id); }
    t.state = error ? 'error' : 'done';
    t.error = error ?? undefined;
    t.pct = 100;
    t.finishedAt = Date.now();
    const runner = this.runners.get(id);
    if (runner?.formatResult) {
      try { t.resultSummary = runner.formatResult(t); } catch { /* ignore */ }
    }
    this.dispatchChanged();
    this.dispatchEvent(new CustomEvent('task-complete', { detail: { task: { ...t } } }));
    // Auto-clear from chip after a beat so it doesn't sit forever.
    setTimeout(() => {
      const cur = this.tasks.get(id);
      if (cur && (cur.state === 'done' || cur.state === 'error')) {
        this.tasks.delete(id);
        this.runners.delete(id);
        this.dispatchChanged();
      }
    }, 7000);
  }

  async cancel(id: string): Promise<void> {
    const runner = this.runners.get(id);
    const t = this.tasks.get(id);
    if (!t) return;
    if (runner?.cancel) {
      try { await runner.cancel(); } catch (e) { console.error('Cancel error:', e); }
    }
    const timer = this.timers.get(id);
    if (timer) { clearInterval(timer); this.timers.delete(id); }
    t.state = 'cancelled';
    t.finishedAt = Date.now();
    this.dispatchChanged();
    setTimeout(() => {
      this.tasks.delete(id);
      this.runners.delete(id);
      this.dispatchChanged();
    }, 3000);
  }

  isRunning(kind: TaskKind): boolean {
    return !!this.findRunningByKind(kind);
  }

  getAll(): Task[] {
    return Array.from(this.tasks.values()).sort((a, b) => a.startedAt - b.startedAt);
  }

  get(id: string): Task | undefined {
    return this.tasks.get(id);
  }

  clearDone(): void {
    let removed = false;
    for (const [id, t] of this.tasks) {
      if (t.state === 'done' || t.state === 'error' || t.state === 'cancelled') {
        this.tasks.delete(id);
        this.runners.delete(id);
        removed = true;
      }
    }
    if (removed) this.dispatchChanged();
  }

  private findRunningByKind(kind: TaskKind): Task | undefined {
    for (const t of this.tasks.values()) {
      if (t.kind === kind && t.state === 'running') return t;
    }
    return undefined;
  }

  private dispatchChanged(): void {
    this.dispatchEvent(new CustomEvent('tasks-changed'));
  }
}

export const tasks = new TaskController();

// ============================================================
// Renderers — chip in titlebar, toast stack, task modal
// ============================================================

let openTaskId: string | null = null;
let popoverOpen = false;
let toastTimers = new Map<string, ReturnType<typeof setTimeout>>();

export function initTaskUI(): void {
  tasks.addEventListener('tasks-changed', () => { renderChip(); renderModal(); });
  tasks.addEventListener('task-complete', (e) => {
    const t = (e as CustomEvent).detail.task as Task;
    pushToast(t);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && (openTaskId || popoverOpen)) {
      openTaskId = null;
      popoverOpen = false;
      renderModal();
      renderChip();
    }
  });
  // Click outside the popover closes it
  document.addEventListener('click', (e) => {
    if (!popoverOpen) return;
    const target = e.target as HTMLElement;
    if (!target.closest('.cc-task-chip-group')) {
      popoverOpen = false;
      renderChip();
    }
  });
  renderChip();
}

function renderChip(): void {
  const slot = document.getElementById('taskChip') ?? document.querySelector<HTMLElement>('.cc-task-chip-group');
  if (!slot) return;
  const all = tasks.getAll();

  if (all.length === 0) {
    slot.outerHTML = `<span class="cc-task-chip" data-state="idle" id="taskChip"><span class="cc-dot"></span><span>idle</span></span>`;
    return;
  }

  const running = all.filter(t => t.state === 'running');
  const done = all.filter(t => t.state === 'done');
  const errored = all.filter(t => t.state === 'error');

  // Single task, running — compact chip
  if (all.length === 1 && running.length === 1) {
    const t = running[0];
    slot.outerHTML = `
      <button class="cc-task-chip" data-state="running" id="taskChip" title="View progress" data-task-id="${t.id}">
        <span class="cc-task-chip-spin"></span>
        <span>${escapeHtml(KIND_LABEL[t.kind].running)}</span>
        <span class="cc-task-chip-pct">${Math.round(t.pct)}%</span>
      </button>`;
    document.getElementById('taskChip')?.addEventListener('click', () => {
      openTaskId = t.id;
      renderModal();
    });
    return;
  }

  // Single task, done/error — transient
  if (all.length === 1 && (done.length === 1 || errored.length === 1)) {
    const t = done[0] ?? errored[0];
    const state = t.state === 'error' ? 'error' : 'done';
    const icon = t.state === 'error' ? '!' : '✓';
    slot.outerHTML = `
      <button class="cc-task-chip" data-state="${state}" id="taskChip" title="Dismiss">
        <span aria-hidden="true">${icon}</span>
        <span>${escapeHtml(KIND_LABEL[t.kind].done)}</span>
        <span class="cc-task-chip-x">×</span>
      </button>`;
    document.getElementById('taskChip')?.addEventListener('click', () => {
      tasks.clearDone();
    });
    return;
  }

  // Multi — group + popover
  const hasRunning = running.length > 0;
  const headLabel = hasRunning
    ? `${running.length} running`
    : `${done.length + errored.length} done`;
  const headState = hasRunning ? 'running' : (errored.length ? 'error' : 'done');
  const popoverHtml = popoverOpen ? renderPopover(all) : '';
  slot.outerHTML = `
    <span class="cc-task-chip-group" id="taskChip">
      <button class="cc-task-chip" data-state="${headState}" id="taskChipBtn" title="${all.length} task(s)">
        ${hasRunning ? '<span class="cc-task-chip-spin"></span>' : '<span aria-hidden="true">✓</span>'}
        <span>${escapeHtml(headLabel)}</span>
        <span class="cc-task-chip-pct">▾</span>
      </button>
      ${popoverHtml}
    </span>`;
  document.getElementById('taskChipBtn')?.addEventListener('click', (e) => {
    e.stopPropagation();
    popoverOpen = !popoverOpen;
    renderChip();
  });
  if (popoverOpen) {
    document.querySelectorAll<HTMLButtonElement>('.cc-task-pop-row[data-task-id]').forEach(btn => {
      btn.addEventListener('click', () => {
        openTaskId = btn.dataset.taskId ?? null;
        popoverOpen = false;
        renderChip();
        renderModal();
      });
    });
    document.getElementById('taskChipClearBtn')?.addEventListener('click', () => {
      tasks.clearDone();
    });
  }
}

function renderPopover(all: Task[]): string {
  const rows = all.map(t => {
    const isRunning = t.state === 'running';
    const isDone = t.state === 'done';
    const icon = isRunning
      ? '<span class="cc-task-chip-spin"></span>'
      : isDone ? '✓' : (t.state === 'error' ? '!' : '×');
    const pct = isRunning ? `${Math.round(t.pct)}%` : (isDone ? 'done' : t.state);
    const label = isRunning ? KIND_LABEL[t.kind].running : KIND_LABEL[t.kind].done;
    return `
      <button class="cc-task-pop-row" data-state="${t.state}" data-task-id="${t.id}">
        <span class="cc-task-pop-icon">${icon}</span>
        <span class="cc-task-pop-label">${escapeHtml(label)}</span>
        <span class="cc-task-pop-pct">${escapeHtml(pct)}</span>
      </button>`;
  }).join('');
  const hasDone = all.some(t => t.state !== 'running');
  const clearBtn = hasDone
    ? `<button class="cc-task-pop-clear" id="taskChipClearBtn">Clear completed</button>`
    : '';
  return `
    <div class="cc-task-pop">
      <div class="cc-task-pop-head">tasks</div>
      ${rows}
      ${clearBtn}
    </div>`;
}

function renderModal(): void {
  const body = document.querySelector<HTMLElement>('.cc-body');
  if (!body) return;
  const existing = document.getElementById('cc-modal-back');
  const t = openTaskId ? tasks.get(openTaskId) : undefined;
  if (!t || t.state !== 'running') {
    if (existing) existing.remove();
    if (!t) openTaskId = null;
    return;
  }

  const html = `
    <div class="cc-modal-back" id="cc-modal-back">
      <div class="cc-modal" role="dialog" aria-modal="true">
        <div class="cc-modal-head">
          <span class="cc-modal-spinner"></span>
          <div style="flex:1">
            <div class="cc-modal-title">${escapeHtml(KIND_LABEL[t.kind].running)}</div>
            <div class="cc-clip-meta" style="margin-top:2px">${escapeHtml(t.subtitle ?? '')}</div>
          </div>
          <button class="cc-btn" data-variant="ghost" data-size="icon" id="cc-modal-x" title="Run in background">×</button>
        </div>
        <div class="cc-modal-body">
          <div class="cc-progress"><span style="width:${Math.round(t.pct)}%"></span></div>
          <div class="cc-progress-meta">
            <span>${escapeHtml(t.subtitle ?? '')}</span>
            <span>${Math.round(t.pct)}%</span>
          </div>
          ${t.log.length ? `<div class="cc-mini-log" id="cc-modal-log">${t.log.map(l => `<div>${escapeHtml(l)}</div>`).join('')}</div>` : ''}
        </div>
        <div class="cc-modal-foot">
          <button class="cc-btn" id="cc-modal-bg">Run in background</button>
          <span style="flex:1"></span>
          <button class="cc-btn" data-variant="danger" id="cc-modal-cancel">Cancel</button>
        </div>
      </div>
    </div>`;

  if (existing) {
    existing.outerHTML = html;
  } else {
    body.insertAdjacentHTML('beforeend', html);
  }

  // Auto-scroll mini-log to bottom
  const logEl = document.getElementById('cc-modal-log');
  if (logEl) logEl.scrollTop = logEl.scrollHeight;

  const close = (): void => { openTaskId = null; renderModal(); };
  document.getElementById('cc-modal-x')?.addEventListener('click', close);
  document.getElementById('cc-modal-bg')?.addEventListener('click', close);
  document.getElementById('cc-modal-cancel')?.addEventListener('click', async () => {
    if (openTaskId) await tasks.cancel(openTaskId);
    close();
  });
  document.getElementById('cc-modal-back')?.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) close();
  });
}

function pushToast(t: Task): void {
  const wrap = document.getElementById('toastWrap');
  if (!wrap) return;
  const id = `toast-${t.id}`;
  if (toastTimers.has(id)) return; // already shown

  const isError = t.state === 'error';
  const summary = t.resultSummary
    ?? (isError ? (t.error ?? 'Failed') : KIND_LABEL[t.kind].done);
  const elapsed = t.finishedAt && t.startedAt ? fmtDuration(t.finishedAt - t.startedAt) : '';

  const el = document.createElement('div');
  el.className = 'cc-toast';
  el.id = id;
  if (isError) el.dataset.kind = 'error';
  el.innerHTML = `
    <span class="cc-toast-icon">${isError ? '!' : '✓'}</span>
    <div style="flex:1">
      <div class="cc-toast-title">${escapeHtml(KIND_LABEL[t.kind].done)}</div>
      <div class="cc-toast-meta">${escapeHtml(summary)}${elapsed ? ' · ' + elapsed : ''}</div>
    </div>
    <button class="cc-toast-close" type="button">×</button>`;
  wrap.appendChild(el);

  const dismiss = (): void => {
    el.remove();
    const timer = toastTimers.get(id);
    if (timer) clearTimeout(timer);
    toastTimers.delete(id);
  };
  el.querySelector('.cc-toast-close')?.addEventListener('click', dismiss);
  toastTimers.set(id, setTimeout(dismiss, 5000));
}

function fmtDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  return `${m}m${rs ? ` ${rs}s` : ''}`;
}
