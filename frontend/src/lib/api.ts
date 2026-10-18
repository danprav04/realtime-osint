import type {
  NormalizedEvent,
  Theme,
  Source,
  DashboardStats,
  SystemSettings,
  AIPipelineSettings,
  EmbeddingsMapData,
  EmbeddingStatus,
  SituationSummaryData,
  HistoricalRollup,
  SourceCredibilityItem,
  UsageTelemetryResponse,
  ServerLogsResponse,
  ServerLogEntry,
  ServerLogStats,
  WorldTheaterContext,
} from './types';

const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const DEFAULT_API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'osint_secret_key_2026';

export function getApiBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const custom = localStorage.getItem('osint_api_url');
    if (custom) return custom;
    // When served over standard ports or behind a reverse proxy (e.g. ir-osint.danprav.me),
    // always use current origin without appending a custom port.
    if (!window.location.port || window.location.port === '80' || window.location.port === '443') {
      return window.location.origin;
    }
    // Direct access to host ports (e.g. Tailscale http://production:26816)
    if (window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      const port = process.env.NEXT_PUBLIC_API_PORT || '24176';
      return `${window.location.protocol}//${window.location.hostname}:${port}`;
    }
  }
  return DEFAULT_API_URL;
}

export function getStoredApiKey(): string {
  if (typeof window === 'undefined') return DEFAULT_API_KEY;
  return localStorage.getItem('osint_api_key') || DEFAULT_API_KEY;
}

export function setStoredApiKey(key: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem('osint_api_key', key);
  }
}

export async function fetchApi<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const apiKey = getStoredApiKey();
  const headers = new Headers(options.headers || {});

  if (!headers.has('X-API-Key')) {
    headers.set('X-API-Key', apiKey);
  }
  if (!headers.has('Content-Type') && options.method && options.method !== 'GET') {
    headers.set('Content-Type', 'application/json');
  }

  const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  const baseUrl = getApiBaseUrl();
  const url = `${baseUrl}${cleanEndpoint}`;

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorText = await response.text();
    let detail = errorText;
    try {
      const parsed = JSON.parse(errorText);
      detail = parsed.detail || errorText;
    } catch {
      // Keep errorText
    }
    throw new Error(`API Error [${response.status}] ${detail}`);
  }

  return response.json() as Promise<T>;
}

// Events
export async function getEvents(params?: {
  theme_id?: string;
  source_type?: string;
  min_score?: number;
  max_score?: number;
  sort_by?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<NormalizedEvent[]> {
  const query = new URLSearchParams();
  if (params) {
    if (params.theme_id) query.set('theme_id', params.theme_id);
    if (params.source_type && params.source_type !== 'all') query.set('source_type', params.source_type);
    if (params.min_score !== undefined && params.min_score > 0) query.set('min_score', params.min_score.toString());
    if (params.max_score !== undefined) query.set('max_score', params.max_score.toString());
    if (params.sort_by) query.set('sort_by', params.sort_by);
    if (params.search) query.set('search', params.search);
    if (params.limit) query.set('limit', params.limit.toString());
    if (params.offset) query.set('offset', params.offset.toString());
  }
  const queryString = query.toString();
  const endpoint = `/api/events${queryString ? `?${queryString}` : ''}`;
  return fetchApi<NormalizedEvent[]>(endpoint);
}

// Themes
export async function getThemes(): Promise<Theme[]> {
  return fetchApi<Theme[]>('/api/themes');
}

export async function getActiveTheme(): Promise<Theme> {
  return fetchApi<Theme>('/api/themes/active');
}

export async function activateTheme(idOrSlug: string): Promise<{ success: boolean; active_theme: string }> {
  return fetchApi<{ success: boolean; active_theme: string }>(`/api/themes/${idOrSlug}/activate`, {
    method: 'POST',
  });
}

export async function updateTheme(idOrSlug: string, updates: Partial<Theme>): Promise<Theme> {
  return fetchApi<Theme>(`/api/themes/${idOrSlug}`, {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
}

export async function createTheme(themeData: Partial<Theme>): Promise<Theme> {
  return fetchApi<Theme>('/api/themes', {
    method: 'POST',
    body: JSON.stringify(themeData),
  });
}

// Sources
export async function getSources(themeId?: string): Promise<Source[]> {
  const endpoint = themeId ? `/api/sources?theme_id=${encodeURIComponent(themeId)}` : '/api/sources';
  return fetchApi<Source[]>(endpoint);
}

export async function updateSource(id: string, updates: Partial<Source>): Promise<Source> {
  return fetchApi<Source>(`/api/sources/${id}`, {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
}

export async function createSource(sourceData: Partial<Source>): Promise<Source> {
  return fetchApi<Source>('/api/sources', {
    method: 'POST',
    body: JSON.stringify(sourceData),
  });
}

export async function deleteSource(id: string): Promise<{ success: boolean }> {
  return fetchApi<{ success: boolean }>(`/api/sources/${id}`, {
    method: 'DELETE',
  });
}

export async function triggerSource(id: string): Promise<{ success: boolean; message: string }> {
  return fetchApi<{ success: boolean; message: string }>(`/api/sources/${id}/trigger`, {
    method: 'POST',
  });
}

// Stats
export async function getStats(themeId?: string): Promise<DashboardStats> {
  const endpoint = themeId ? `/api/stats?theme_id=${encodeURIComponent(themeId)}` : '/api/stats';
  return fetchApi<DashboardStats>(endpoint);
}

// Settings
export async function getSettings(): Promise<SystemSettings> {
  return fetchApi<SystemSettings>('/api/settings');
}

export async function updateSettings(updates: Partial<SystemSettings>): Promise<SystemSettings> {
  return fetchApi<SystemSettings>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
}

export async function getAIPipelineSettings(): Promise<AIPipelineSettings> {
  return fetchApi<AIPipelineSettings>('/api/settings/ai-pipeline');
}

export async function updateAIPipelineSettings(settings: AIPipelineSettings): Promise<AIPipelineSettings> {
  return fetchApi<AIPipelineSettings>('/api/settings/ai-pipeline', {
    method: 'PUT',
    body: JSON.stringify(settings),
  });
}

export async function resetAIPipelineSettings(): Promise<AIPipelineSettings> {
  return fetchApi<AIPipelineSettings>('/api/settings/ai-pipeline/reset', {
    method: 'POST',
  });
}

export async function getProviderUsage(): Promise<UsageTelemetryResponse> {
  return fetchApi<UsageTelemetryResponse>('/api/settings/provider-usage');
}

// Embeddings Relevance Map
export async function getEmbeddingsMap(params?: {
  theme_id?: string;
  time_range?: string;
  min_score?: number;
  limit?: number;
  force_reproject?: boolean;
}): Promise<EmbeddingsMapData> {
  const query = new URLSearchParams();
  if (params) {
    if (params.theme_id && params.theme_id !== 'all') query.set('theme_id', params.theme_id);
    if (params.time_range && params.time_range !== 'all') query.set('time_range', params.time_range);
    if (params.min_score !== undefined && params.min_score > 0) query.set('min_score', params.min_score.toString());
    if (params.limit) query.set('limit', params.limit.toString());
    if (params.force_reproject) query.set('force_reproject', 'true');
  }
  const queryString = query.toString();
  return fetchApi<EmbeddingsMapData>(`/api/embeddings/map${queryString ? `?${queryString}` : ''}`);
}

export async function triggerEmbeddingBackfill(limit: number = 100): Promise<{
  status: string;
  processed_count: number;
  message: string;
}> {
  return fetchApi<{ status: string; processed_count: number; message: string }>(
    `/api/embeddings/backfill?limit=${limit}`,
    { method: 'POST' }
  );
}

export async function reprojectEmbeddingsMap(): Promise<{
  status: string;
  total_nodes: number;
  total_clusters: number;
}> {
  return fetchApi<{ status: string; total_nodes: number; total_clusters: number }>(
    '/api/embeddings/reproject',
    { method: 'POST' }
  );
}

export async function getEmbeddingStatus(): Promise<EmbeddingStatus> {
  return fetchApi<EmbeddingStatus>('/api/embeddings/status');
}

// AI Situational Summary
export async function getSituationSummary(params?: {
  theme_id?: string;
  time_range?: string;
  force_refresh?: boolean;
  limit?: number;
}): Promise<SituationSummaryData> {
  const query = new URLSearchParams();
  if (params) {
    if (params.theme_id) query.set('theme_id', params.theme_id);
    if (params.time_range) query.set('time_range', params.time_range);
    if (params.force_refresh) query.set('force_refresh', 'true');
    if (params.limit) query.set('limit', params.limit.toString());
  }
  const queryString = query.toString();
  return fetchApi<SituationSummaryData>(`/api/summary${queryString ? `?${queryString}` : ''}`);
}

export async function generateSituationSummary(params?: {
  theme_id?: string;
  time_range?: string;
  limit?: number;
}): Promise<SituationSummaryData> {
  const query = new URLSearchParams();
  if (params) {
    if (params.theme_id) query.set('theme_id', params.theme_id);
    if (params.time_range) query.set('time_range', params.time_range);
    if (params.limit) query.set('limit', params.limit.toString());
  }
  const queryString = query.toString();
  return fetchApi<SituationSummaryData>(
    `/api/summary/generate${queryString ? `?${queryString}` : ''}`,
    { method: 'POST' }
  );
}

// Historical Memory Rollups & Compaction
export async function getHistoricalRollups(params?: {
  theme_id?: string;
  limit?: number;
}): Promise<HistoricalRollup[]> {
  const query = new URLSearchParams();
  if (params?.theme_id) query.set('theme_id', params.theme_id);
  if (params?.limit) query.set('limit', params.limit.toString());
  const queryString = query.toString();
  return fetchApi<HistoricalRollup[]>(`/api/summary/rollups${queryString ? `?${queryString}` : ''}`);
}

export async function triggerMemoryCompaction(ageDays?: number): Promise<{
  status: string;
  message: string;
  rollups_created: number;
  vectors_pruned: number;
}> {
  const query = new URLSearchParams();
  if (ageDays !== undefined) query.set('age_days', ageDays.toString());
  const queryString = query.toString();
  return fetchApi<{
    status: string;
    message: string;
    rollups_created: number;
    vectors_pruned: number;
  }>(`/api/summary/rollups/compact${queryString ? `?${queryString}` : ''}`, {
    method: 'POST',
  });
}

// Dynamic Source Credibility Leaderboard
export async function getSourceCredibility(themeId?: number | string): Promise<{
  status: string;
  count: number;
  sources: SourceCredibilityItem[];
}> {
  const query = new URLSearchParams();
  if (themeId !== undefined) query.set('theme_id', themeId.toString());
  const queryString = query.toString();
  return fetchApi<{
    status: string;
    count: number;
    sources: SourceCredibilityItem[];
  }>(`/api/stats/sources/credibility${queryString ? `?${queryString}` : ''}`);
}

// Server Logs & Diagnostics
export async function getServerLogs(params?: {
  limit?: number;
  level?: string;
  logger?: string;
  search?: string;
  since_id?: number;
}): Promise<ServerLogsResponse> {
  const query = new URLSearchParams();
  if (params) {
    if (params.limit !== undefined) query.set('limit', params.limit.toString());
    if (params.level && params.level !== 'ALL') query.set('level', params.level);
    if (params.logger && params.logger !== 'all') query.set('logger', params.logger);
    if (params.search) query.set('q', params.search);
    if (params.since_id !== undefined) query.set('since_id', params.since_id.toString());
  }
  const queryString = query.toString();
  return fetchApi<ServerLogsResponse>(`/api/settings/logs${queryString ? `?${queryString}` : ''}`);
}

export async function clearServerLogs(): Promise<{ status: string; message: string }> {
  return fetchApi<{ status: string; message: string }>('/api/settings/logs', {
    method: 'DELETE',
  });
}

export async function getServerLogStats(): Promise<ServerLogStats> {
  return fetchApi<ServerLogStats>('/api/settings/logs/stats');
}

export function getServerLogsExportUrl(format: 'text' | 'json' = 'text', limit: number = 1000): string {
  const baseUrl = getApiBaseUrl();
  const apiKey = getStoredApiKey();
  return `${baseUrl}/api/settings/logs/export?format=${format}&limit=${limit}&api_key=${encodeURIComponent(apiKey)}`;
}

export async function emitTestServerLog(
  level: string = 'INFO',
  message: string = 'Operator test log emitted from settings panel'
): Promise<{ status: string; message: string; entry: ServerLogEntry }> {
  return fetchApi<{ status: string; message: string; entry: ServerLogEntry }>('/api/settings/logs/test', {
    method: 'POST',
    body: JSON.stringify({ level, message }),
  });
}

// World Theater & Geopolitical Context
export async function getWorldTheaterContext(): Promise<WorldTheaterContext> {
  return fetchApi<WorldTheaterContext>('/api/settings/world-theater');
}

export async function updateWorldTheaterContext(
  payload: Partial<WorldTheaterContext>
): Promise<{ status: string; message: string; context: WorldTheaterContext }> {
  return fetchApi<{ status: string; message: string; context: WorldTheaterContext }>(
    '/api/settings/world-theater',
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    }
  );
}

export async function refreshWorldTheaterFromApi(): Promise<{
  status: string;
  message: string;
  live_api_status?: string;
  sync_source?: string;
  last_synced_at?: string;
  context: WorldTheaterContext;
}> {
  return fetchApi<{
    status: string;
    message: string;
    live_api_status?: string;
    sync_source?: string;
    last_synced_at?: string;
    context: WorldTheaterContext;
  }>('/api/settings/world-theater/refresh', {
    method: 'POST',
  });
}


