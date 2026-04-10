// ---- Shared types ----

export interface HighlightRegion {
  offset: number;
  duration: number;
  type: string;
}

export interface ClipInfo {
  filename: string;
  source_video: string;
  video_stem: string;
  start_time: number;
  end_time: number;
  duration: number;
  detection_reasons: string[];
  confidence: number;
  video_url: string;
  highlight_regions: HighlightRegion[];
}

export interface KeptClipInfo {
  filename: string;
  source_video: string;
  video_stem: string;
  start_time: number;
  end_time: number;
  duration: number;
  detection_reasons: string[];
  confidence: number;
  video_url: string;
  custom_name: string | null;
  encoded_filename: string | null;
  encoding_preset: string | null;
  encoded_exists: boolean;
  encoded_video_url?: string;
  youtube_video_id: string | null;
  youtube_url: string | null;
  youtube_upload_status: string | null;
  clipped_at?: string;
}

export interface WaveformData {
  waveform: number[];
  duration: number;
  sample_count: number;
  highlight_regions: HighlightRegion[];
}

export interface Segment {
  start: number;
  end: number;
}

export interface KeepRequest {
  segments: Segment[];
  custom_name: string | null;
}

export interface ProcessStatus {
  running: boolean;
  log: string[];
  error: string | null;
}

export interface EncodeStatus {
  running: boolean;
  current_file: string | null;
  current_index: number;
  total: number;
  completed: string[];
  errors: Array<{ filename: string; error: string }>;
  cancelled: boolean;
}

export interface UploadStatus {
  running: boolean;
  current_file: string | null;
  current_index: number;
  total: number;
  bytes_sent: number;
  bytes_total: number;
  completed: Array<{ filename: string; video_id: string; url: string }>;
  errors: Array<{ filename: string; error: string }>;
  cancelled: boolean;
}

export interface CompilationStatus {
  running: boolean;
  current_step: string;
  progress_pct: number;
  completed: boolean;
  error: string | null;
  output_filename: string | null;
  cancelled: boolean;
}

export interface CompilationInfo {
  compilation_id: string;
  filename: string;
  title: string | null;
  clips: Array<{ video_stem: string; filename: string; custom_name?: string }>;
  total_duration?: number;
  clip_count: number;
  file_exists: boolean;
}

export interface PresetInfo {
  name: string;
  display_name: string;
  extension: string;
}

export interface PresetsData {
  presets: PresetInfo[];
  default: string;
  fps_options: Array<number | null>;
}

export interface SourceVideo {
  video_stem: string;
  source_path: string;
  exists: boolean;
  size_mb: number;
  fully_reviewed: boolean;
  kept: number;
  discarded: number;
  total: number;
}

export interface VideoEntry {
  filename: string;
  size_mb: number;
  age_days: number;
  status: 'processed' | 'pending_review' | 'unprocessed';
}

export interface FolderScanResult {
  videos: VideoEntry[];
  total_size_mb: number;
}

export interface YouTubeStatus {
  authenticated: boolean;
  channel_name?: string;
  error?: string;
}

export interface Playlist {
  id: string;
  title: string;
  item_count: number;
}

export interface EncodeClipRef {
  video_stem: string;
  filename: string;
}

export interface CompilationClipRef {
  video_stem: string;
  filename: string;
}

export interface YouTubeUploadClip {
  video_stem: string;
  filename: string;
  use_encoded: boolean;
  title: string;
  description: string;
  tags: string[];
  category_id: string;
  privacy: string;
  playlist_id: string | null;
}

// ---- API helpers ----

async function apiPost<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json() as Promise<T>;
}

async function apiGet<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json() as Promise<T>;
}

async function apiDelete<T = void>(url: string): Promise<T> {
  const res = await fetch(url, { method: 'DELETE' });
  if (!res.ok) throw new Error(res.statusText);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---- Process ----

export const fetchDefaults = () => apiGet<{ folder: string }>('/api/defaults');
export const startProcessing = (body: { folder: string; sensitivity: number; context: number | null }) =>
  apiPost<{ status: string }>('/api/process', body);
export const fetchProcessStatus = () => apiGet<ProcessStatus>('/api/process/status');
export const fetchFolderScan = (folder: string) =>
  apiGet<FolderScanResult>(`/api/folder-scan?folder=${encodeURIComponent(folder)}`);
export const deleteFolderFile = (folder: string, filename: string) =>
  apiPost<{ status: string; freed_mb: number }>('/api/folder-scan/file/delete', { folder, filename });

// ---- Review ----

export const fetchClips = () => apiGet<{ clips: ClipInfo[]; total: number }>('/api/clips');
export const fetchWaveform = (stem: string, filename: string) =>
  apiGet<WaveformData>(`/api/waveform/${stem}/${filename}`);
export const keepClip = (stem: string, filename: string, req: KeepRequest) =>
  apiPost<{ status: string; trimmed: boolean }>(`/api/clips/${stem}/${filename}/keep`, req);
export const discardClip = (stem: string, filename: string) =>
  apiPost<{ status: string }>(`/api/clips/${stem}/${filename}/discard`, {});

// ---- Sources ----

export const fetchSources = () => apiGet<{ sources: SourceVideo[] }>('/api/sources');
export const deleteSource = (videoStem: string) =>
  apiPost<{ status: string; freed_mb: number; leftover: number }>(`/api/sources/${videoStem}/delete`, {});

// ---- Encode ----

export const fetchPresets = () => apiGet<PresetsData>('/api/encoding/presets');
export const fetchKeptClips = () => apiGet<{ clips: KeptClipInfo[]; total: number }>('/api/kept');
export const startEncoding = (body: { clips: EncodeClipRef[]; preset: string; target_fps: number | null; slowdown_factor: number | null }) =>
  apiPost<{ status: string }>('/api/encode', body);
export const fetchEncodeStatus = () => apiGet<EncodeStatus>('/api/encode/status');
export const cancelEncoding = () => apiPost<{ status: string }>('/api/encode/cancel', {});

// ---- Compile ----

export const startCompilation = (body: { clips: CompilationClipRef[]; transition: string; crossfade_duration: number; preset?: string; title?: string }) =>
  apiPost<{ status: string; compilation_id: string }>('/api/compilation', body);
export const fetchCompilationStatus = () => apiGet<CompilationStatus>('/api/compilation/status');
export const cancelCompilation = () => apiPost<{ status: string }>('/api/compilation/cancel', {});
export const fetchCompilations = () => apiGet<{ compilations: CompilationInfo[] }>('/api/compilations');
export const deleteCompilation = (compId: string) => apiDelete(`/api/compilation/${compId}`);
export const deleteCompilationSources = (compId: string) =>
  apiDelete(`/api/compilation/${compId}/sources`);

// ---- YouTube ----

export const fetchYouTubeStatus = () => apiGet<YouTubeStatus>('/api/youtube/status');
export const fetchYouTubePlaylists = () => apiGet<{ playlists: Playlist[] }>('/api/youtube/playlists');
export const startYouTubeAuth = (clientId: string, clientSecret: string) =>
  apiPost<{ auth_url: string }>('/api/youtube/auth/start', { client_id: clientId, client_secret: clientSecret });
export const revokeYouTubeAuth = () => apiPost<{ status: string }>('/api/youtube/auth/revoke', {});
export const startUpload = (body: { clips: YouTubeUploadClip[] }) =>
  apiPost<{ status: string }>('/api/youtube/upload', body);
export const fetchUploadStatus = () => apiGet<UploadStatus>('/api/youtube/upload/status');
export const cancelUpload = () => apiPost<{ status: string }>('/api/youtube/upload/cancel', {});
export const createPlaylist = (title: string, privacy: string) =>
  apiPost<Playlist>('/api/youtube/playlists', { title, privacy });

export const deleteKeptClip = (videoStem: string, filename: string) =>
  apiDelete(`/api/kept/${encodeURIComponent(videoStem)}/${encodeURIComponent(filename)}`);

export async function openKeptFolder(video_stem: string): Promise<void> {
  await apiGet<{ status: string }>(`/api/open-folder/kept/${encodeURIComponent(video_stem)}`);
}
