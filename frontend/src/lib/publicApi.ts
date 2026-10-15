import type {
  NormalizedEvent,
  Theme,
  DashboardStats,
  EmbeddingsMapData,
  SituationSummaryData,
} from './types';
import { getApiBaseUrl } from './api';

export async function fetchPublicApi<T>(
  endpoint: string,
  token?: string | null,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers || {});

  if (token) {
    headers.set('X-Viewer-Token', token);
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
    let retryAfter: number | undefined;
    try {
      const parsed = JSON.parse(errorText);
      detail = parsed.detail || errorText;
    } catch {
      // Keep errorText
    }

    const retryHeader = response.headers.get('Retry-After');
    if (retryHeader) {
      retryAfter = parseInt(retryHeader, 10);
    }

    const err = new Error(detail) as Error & { status: number; retryAfter?: number };
    err.status = response.status;
    err.retryAfter = retryAfter;
    throw err;
  }

  return response.json() as Promise<T>;
}

export async function loginPublicViewer(password: string): Promise<{ token: string; expires_in: number }> {
  return fetchPublicApi<{ token: string; expires_in: number }>('/api/public/auth', null, {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
}

export async function verifyViewerToken(token: string): Promise<boolean> {
  try {
    const res = await fetchPublicApi<{ authenticated: boolean }>('/api/public/verify', token);
    return !!res.authenticated;
  } catch {
    return false;
  }
}

export async function getPublicSummary(
  token: string,
  params?: { theme_id?: string; time_range?: string }
): Promise<SituationSummaryData> {
  const query = new URLSearchParams();
  if (params?.theme_id && params.theme_id !== 'active') query.set('theme_id', params.theme_id);
  if (params?.time_range && params.time_range !== 'all') query.set('time_range', params.time_range);
  const qs = query.toString();
  return fetchPublicApi<SituationSummaryData>(`/api/public/summary${qs ? `?${qs}` : ''}`, token);
}

export async function getPublicEvents(
  token: string,
  params?: {
    theme_id?: string;
    source_type?: string;
    min_score?: number;
    search?: string;
    sort_by?: string;
    limit?: number;
  }
): Promise<NormalizedEvent[]> {
  const query = new URLSearchParams();
  if (params?.theme_id) query.set('theme_id', params.theme_id);
  if (params?.source_type && params.source_type !== 'all') query.set('source_type', params.source_type);
  if (params?.min_score && params.min_score > 0) query.set('min_score', params.min_score.toString());
  if (params?.search) query.set('search', params.search);
  if (params?.sort_by) query.set('sort_by', params.sort_by);
  if (params?.limit) query.set('limit', params.limit.toString());
  const qs = query.toString();
  return fetchPublicApi<NormalizedEvent[]>(`/api/public/events${qs ? `?${qs}` : ''}`, token);
}

export async function getPublicMap(
  token: string,
  params?: {
    theme_id?: string;
    time_range?: string;
    min_score?: number;
    limit?: number;
  }
): Promise<EmbeddingsMapData> {
  const query = new URLSearchParams();
  if (params?.theme_id && params.theme_id !== 'all') query.set('theme_id', params.theme_id);
  if (params?.time_range && params.time_range !== 'all') query.set('time_range', params.time_range);
  if (params?.min_score && params.min_score > 0) query.set('min_score', params.min_score.toString());
  if (params?.limit) query.set('limit', params.limit.toString());
  const qs = query.toString();
  return fetchPublicApi<EmbeddingsMapData>(`/api/public/map${qs ? `?${qs}` : ''}`, token);
}

export async function getPublicStats(token: string): Promise<DashboardStats> {
  return fetchPublicApi<DashboardStats>('/api/public/stats', token);
}

export async function getPublicTheme(token: string): Promise<Theme> {
  return fetchPublicApi<Theme>('/api/public/theme', token);
}
