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

