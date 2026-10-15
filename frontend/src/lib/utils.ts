import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatRelativeTime(dateString?: string | null): string {
  if (!dateString) return 'Never';
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return dateString;

  const now = new Date();
  const diffInSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (diffInSeconds < 0) return 'Just now';
  if (diffInSeconds < 60) return `${diffInSeconds}s ago`;
  const diffInMinutes = Math.floor(diffInSeconds / 60);
  if (diffInMinutes < 60) return `${diffInMinutes}m ago`;
  const diffInHours = Math.floor(diffInMinutes / 60);
  if (diffInHours < 24) return `${diffInHours}h ago`;
  const diffInDays = Math.floor(diffInHours / 24);
  return `${diffInDays}d ago`;
}

export function formatDateTime(dateString?: string | null): string {
  if (!dateString) return '—';
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return dateString;
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function getScoreCategory(score?: number | null): 'routine' | 'advisory' | 'warning' | 'critical' {
  const val = Number(score) || 0;
  if (val >= 9) return 'critical';
  if (val >= 7) return 'warning';
  if (val >= 4) return 'advisory';
  return 'routine';
}

export function getScoreColors(score?: number | null): {
  bg: string;
  text: string;
  border: string;
  glow: string;
  label: string;
} {
  const category = getScoreCategory(score);
  switch (category) {
    case 'critical':
      return {
        bg: 'bg-rose-500/20',
        text: 'text-rose-300',
        border: 'border-rose-500/40',
        glow: 'shadow-[0_0_12px_rgba(239,68,68,0.4)]',
        label: 'CRITICAL',
      };
    case 'warning':
      return {
        bg: 'bg-amber-500/15',
        text: 'text-amber-300',
        border: 'border-amber-500/30',
        glow: 'shadow-[0_0_8px_rgba(245,158,11,0.25)]',
        label: 'HIGH',
      };
    case 'advisory':
      return {
        bg: 'bg-cyan-500/15',
        text: 'text-cyan-300',
        border: 'border-cyan-500/30',
        glow: '',
        label: 'MED',
      };
    case 'routine':
    default:
      return {
        bg: 'bg-slate-500/15',
        text: 'text-slate-300',
        border: 'border-slate-500/30',
        glow: '',
        label: 'LOW',
      };
  }
}

export function truncateText(text?: string | null, maxLength = 160): string {
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}...`;
}

export function formatAlertTime(dateString?: string | null): {
  timeStr: string;
  relativeStr: string;
  fullStr: string;
} {
  if (!dateString) {
    const now = new Date();
    const timeStr = `${String(now.getUTCHours()).padStart(2, '0')}:${String(now.getUTCMinutes()).padStart(2, '0')}:${String(now.getUTCSeconds()).padStart(2, '0')} UTC`;
    return {
      timeStr,
      relativeStr: 'Just now',
      fullStr: now.toISOString(),
    };
  }

  const date = new Date(dateString);
  if (isNaN(date.getTime())) {
    return {
      timeStr: String(dateString),
      relativeStr: '',
      fullStr: String(dateString),
    };
  }

  const hours = String(date.getUTCHours()).padStart(2, '0');
  const minutes = String(date.getUTCMinutes()).padStart(2, '0');
  const seconds = String(date.getUTCSeconds()).padStart(2, '0');
  const timeStr = `${hours}:${minutes}:${seconds} UTC`;
  const relativeStr = formatRelativeTime(dateString);
  const fullStr = `${date.toUTCString()} (${formatDateTime(dateString)})`;

  return { timeStr, relativeStr, fullStr };
}

export type SortOption = 'newest' | 'score_desc' | 'score_asc' | 'ingested_desc' | 'oldest';

export function getEventTimestamp(
  ev: { published_at?: string | null; ingested_at?: string | null },
  prefer: 'published' | 'ingested' = 'published'
): number {
  const primaryStr = prefer === 'published' ? ev.published_at : ev.ingested_at;
  const secondaryStr = prefer === 'published' ? ev.ingested_at : ev.published_at;

  if (primaryStr) {
    const t = new Date(primaryStr).getTime();
    if (!isNaN(t)) return t;
  }
  if (secondaryStr) {
    const t = new Date(secondaryStr).getTime();
    if (!isNaN(t)) return t;
  }
  return 0;
}

export function sortEvents<
  T extends {
    published_at?: string | null;
    ingested_at?: string | null;
    importance_score?: number | null;
  }
>(list: T[], sortBy: SortOption = 'newest'): T[] {
  return [...list].sort((a, b) => {
    if (sortBy === 'score_desc') {
      const diff = (b.importance_score ?? -1) - (a.importance_score ?? -1);
      if (diff !== 0) return diff;
      const timeA = getEventTimestamp(a, 'published');
      const timeB = getEventTimestamp(b, 'published');
      return timeB - timeA;
    }
    if (sortBy === 'score_asc') {
      const diff = (a.importance_score ?? 99) - (b.importance_score ?? 99);
      if (diff !== 0) return diff;
      const timeA = getEventTimestamp(a, 'published');
      const timeB = getEventTimestamp(b, 'published');
      return timeB - timeA;
    }
    if (sortBy === 'oldest') {
      const timeA = getEventTimestamp(a, 'published');
      const timeB = getEventTimestamp(b, 'published');
      if (timeA !== timeB) return timeA - timeB;
      return (b.importance_score ?? 0) - (a.importance_score ?? 0);
    }
    if (sortBy === 'ingested_desc') {
      const timeA = getEventTimestamp(a, 'ingested');
      const timeB = getEventTimestamp(b, 'ingested');
      if (timeB !== timeA) return timeB - timeA;
      return (b.importance_score ?? 0) - (a.importance_score ?? 0);
    }
    // Default: 'newest' (published_at DESC, tiebreakers: ingested_at DESC, importance_score DESC)
    const timeA = getEventTimestamp(a, 'published');
    const timeB = getEventTimestamp(b, 'published');
    if (timeB !== timeA) return timeB - timeA;
    const ingA = getEventTimestamp(a, 'ingested');
    const ingB = getEventTimestamp(b, 'ingested');
    if (ingB !== ingA) return ingB - ingA;
    return (b.importance_score ?? 0) - (a.importance_score ?? 0);
  });
}

export interface ProviderRateLimitInfo {
  id: 'gemini' | 'groq' | 'aihubmix' | 'telegram' | 'newsapi' | 'reddit' | 'huggingface';
  name: string;
  shortName: string;
  category: 'ai' | 'ingestion' | 'alert';
  rateLimitUrl: string;
  usageUrl: string;
  docsUrl: string;
  quotaSummary: string;
  keyFields: string[];
  sourceTypes?: string[];
  description: string;
}

export interface ProviderRateLimitStatus extends ProviderRateLimitInfo {
  isConnected: boolean;
  hasApiKey: boolean;
  connectionType: 'api_key' | 'pipeline' | 'source' | 'disconnected';
  connectionLabel: string;
}

export const PROVIDER_RATE_LIMITS: ProviderRateLimitInfo[] = [
  {
    id: 'gemini',
    name: 'Google Gemini',
    shortName: 'Gemini',
    category: 'ai',
    rateLimitUrl: 'https://aistudio.google.com/rate-limit',
    usageUrl: 'https://aistudio.google.com/usage',
    docsUrl: 'https://ai.google.dev/gemini-api/docs/rate-limits',
    quotaSummary: '15 RPM • 500–14.4K RPD',
    keyFields: ['gemini_api_key'],
    description: 'Google AI Studio active RPM, TPM, and daily RPD model quotas',
  },
  {
    id: 'groq',
    name: 'Groq Cloud',
    shortName: 'Groq',
    category: 'ai',
    rateLimitUrl: 'https://console.groq.com/settings/limits',
    usageUrl: 'https://console.groq.com/dashboard/usage',
    docsUrl: 'https://console.groq.com/docs/rate-limits',
    quotaSummary: '30 RPM • 1K–14.4K RPD',
    keyFields: ['groq_api_key'],
    description: 'Groq Console organization RPM, RPD, TPM, and TPD model limits',
  },
  {
    id: 'aihubmix',
    name: 'AI Hub Mix',
    shortName: 'AIHubMix',
    category: 'ai',
    rateLimitUrl: 'https://aihubmix.com/models/free',
    usageUrl: 'https://aihubmix.com/token',
    docsUrl: 'https://doc.aihubmix.com',
    quotaSummary: '10 RPM • 100 RPD • 1M TPD (Free Flagships)',
    keyFields: ['aihubmix_api_key'],
    description: 'AI Hub Mix free tier flagship models (coding-glm-5.3-free, coding-minimax-m3-free, nemotron-3-super-120b)',
  },
  {
    id: 'telegram',
    name: 'Telegram API',
    shortName: 'Telegram',
    category: 'alert',
    rateLimitUrl: 'https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this',
    usageUrl: 'https://my.telegram.org/apps',
    docsUrl: 'https://core.telegram.org/api/obtaining_api_id',
    quotaSummary: '30 msg/s • 20 msg/min group',
    keyFields: ['telegram_bot_token', 'telegram_chat_id', 'telegram_api_id', 'telegram_api_hash'],
    sourceTypes: ['telegram'],
    description: 'Telegram Bot API dispatch limits and MTProto application console',
  },
  {
    id: 'newsapi',
    name: 'NewsAPI / Currents',
    shortName: 'NewsAPI',
    category: 'ingestion',
    rateLimitUrl: 'https://newsapi.org/account',
    usageUrl: 'https://currentsapi.services/en/profile',
    docsUrl: 'https://newsapi.org/pricing',
    quotaSummary: '100 req/day • GDELT: Free',
    keyFields: ['newsapi_key'],
    sourceTypes: ['newsapi'],
    description: 'NewsAPI developer account request quota and Currents API usage',
  },
  {
    id: 'reddit',
    name: 'Reddit Data API',
    shortName: 'Reddit',
    category: 'ingestion',
    rateLimitUrl: 'https://www.reddit.com/prefs/apps',
    usageUrl: 'https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki',
    docsUrl: 'https://www.reddit.com/dev/api/',
    quotaSummary: '100 QPM (OAuth2)',
    keyFields: ['reddit_client_id', 'reddit_client_secret'],
    sourceTypes: ['reddit'],
    description: 'Reddit OAuth2 application console and 100 queries/min rate limit tier',
  },
  {
    id: 'huggingface',
    name: 'Hugging Face TEI',
    shortName: 'HF TEI',
    category: 'ai',
    rateLimitUrl: 'https://huggingface.co/spaces',
    usageUrl: 'https://huggingface.co/settings/tokens',
    docsUrl: 'https://huggingface.co/docs/hub/rate-limits',
    quotaSummary: 'Unlimited • 384d MiniLM',
    keyFields: ['hf_api_key', 'hf_embedding_url'],
    description: 'Self-hosted Hugging Face Space TEI embeddings server',
  },
];

export function getProviderRateLimitInfo(providerId: string): ProviderRateLimitInfo | undefined {
  const norm = providerId.toLowerCase().trim();
  return PROVIDER_RATE_LIMITS.find((p) => p.id === norm || p.keyFields.includes(norm));
}

export function getProviderRateLimitStatuses(options?: {
  apiKeys?: Record<string, string> | null;
  aiSettings?: {
    extraction?: { provider?: string; fallbacks?: Array<{ provider?: string }> };
    scoring?: { provider?: string; fallbacks?: Array<{ provider?: string }> };
    summarization?: { provider?: string; fallbacks?: Array<{ provider?: string }> };
    visuals?: { provider?: string; fallbacks?: Array<{ provider?: string }> };
  } | null;
  sources?: Array<{ source_type?: string; is_enabled?: boolean; enabled?: boolean }> | null;
  hfSpaceConfigured?: boolean;
}): ProviderRateLimitStatus[] {
  const apiKeys = options?.apiKeys || {};
  const aiSettings = options?.aiSettings;
  const sources = options?.sources || [];

  const stages = aiSettings
    ? [aiSettings.extraction, aiSettings.scoring, aiSettings.summarization, aiSettings.visuals].filter(Boolean)
    : [];

  return PROVIDER_RATE_LIMITS.map((baseProvider) => {
    const customHfUrl =
      baseProvider.id === 'huggingface' && apiKeys.hf_embedding_url
        ? String(apiKeys.hf_embedding_url).trim()
        : '';
    const provider = customHfUrl
      ? {
          ...baseProvider,
          rateLimitUrl: customHfUrl,
          usageUrl: customHfUrl,
        }
      : baseProvider;

    const hasApiKey = provider.keyFields.some((k) => {
      const val = apiKeys[k];
      return Boolean(val && String(val).trim().length > 0);
    });

    const activeInAiPipeline =
      (provider.id === 'gemini' || provider.id === 'groq' || provider.id === 'aihubmix') &&
      stages.some(
        (stage) =>
          stage?.provider === provider.id ||
          (Array.isArray(stage?.fallbacks) && stage!.fallbacks.some((fb) => fb?.provider === provider.id))
      );

    const activeInSources =
      Array.isArray(provider.sourceTypes) &&
      provider.sourceTypes.length > 0 &&
      sources.some(
        (s) =>
          (s.is_enabled ?? s.enabled ?? true) &&
          provider.sourceTypes!.includes(String(s.source_type || '').toLowerCase())
      );

    const hfActive = provider.id === 'huggingface' && Boolean(options?.hfSpaceConfigured);

    if (hasApiKey) {
      return {
        ...provider,
        isConnected: true,
        hasApiKey: true,
        connectionType: 'api_key',
        connectionLabel: 'API Key Connected',
      };
    }

    if (hfActive) {
      return {
        ...provider,
        isConnected: true,
        hasApiKey: false,
        connectionType: 'pipeline',
        connectionLabel: 'HF Space Connected',
      };
    }

    if (activeInAiPipeline) {
      return {
        ...provider,
        isConnected: true,
        hasApiKey: false,
        connectionType: 'pipeline',
        connectionLabel: 'AI Pipeline Active',
      };
    }

    if (activeInSources) {
      return {
        ...provider,
        isConnected: true,
        hasApiKey: false,
        connectionType: 'source',
        connectionLabel: 'Active Source Feed',
      };
    }

    return {
      ...provider,
      isConnected: false,
      hasApiKey: false,
      connectionType: 'disconnected',
      connectionLabel: 'Not Connected',
    };
  });
}


