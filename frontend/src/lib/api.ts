import type {
  NormalizedEvent,
  Theme,
  Source,
  DashboardStats,
  SystemSettings,
  AIPipelineSettings,
  PizzaMeterData,
  EmbeddingsMapData,
  EmbeddingStatus,
  SituationSummaryData,
} from './types';

const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function getStoredApiKey(): string {
  if (typeof window === 'undefined') return 'osint_secret_key_2026';
  return localStorage.getItem('osint_api_key') || 'osint_secret_key_2026';
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
  const url = `${DEFAULT_API_URL}${cleanEndpoint}`;

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

// Pizza Meter
export async function getPizzaMeter(): Promise<PizzaMeterData> {
  return fetchApi<PizzaMeterData>('/api/pizza-meter');
}

export async function togglePizzaMeter(enabled: boolean): Promise<{ status: string; enabled: boolean }> {
  return fetchApi<{ status: string; enabled: boolean }>('/api/pizza-meter/toggle', {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  });
}

export async function simulatePizzaMeter(params: Partial<{
  level: number;
  status: string;
  score: number;
  trend: string;
  reset: boolean;
}>): Promise<PizzaMeterData> {
  return fetchApi<PizzaMeterData>('/api/pizza-meter/simulate', {
    method: 'POST',
    body: JSON.stringify(params),
  });
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

