'use client';

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  Lock,
  Unlock,
  KeyRound,
  ShieldAlert,
  ShieldCheck,
  Search,
  ArrowUpDown,
  Filter,
  Play,
  Pause,
  RefreshCw,
  X,
  Compass,
  Layers,
  Sparkles,
  Flame,
  Activity,
  Copy,
  Check,
  Eye,
  LogOut,
  Radio,
  Clock,
  Wifi,
  WifiOff,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  TrendingUp,
} from 'lucide-react';

import {
  NormalizedEvent,
  DashboardStats,
  EmbeddingsMapData,
  SituationSummaryData,
  SummaryCitationEvent,
  SummaryClusterBreakdown,
  Theme,
  AlertItem,
} from '@/lib/types';
import {
  loginPublicViewer,
  verifyViewerToken,
  getPublicSummary,
  getPublicEvents,
  getPublicMap,
  getPublicStats,
  getPublicTheme,
} from '@/lib/publicApi';
import { getViewerSocket } from '@/lib/socket';
import { EventCard } from '@/components/timeline/EventCard';
import { EventDetailsModal } from '@/components/timeline/EventDetailsModal';
import { AlertWheel } from '@/components/timeline/AlertWheel';
import { RelevanceMap } from '@/components/map/RelevanceMap';
import { AIModelBadge } from '@/components/common/AIModelBadge';
import { AppIcon } from '@/components/AppIcon';
import { InstallPwaButton } from '@/components/pwa/InstallPwaButton';
import { cn, sortEvents, SortOption, formatRelativeTime, formatDateTime, parseModelProvider } from '@/lib/utils';

const STORAGE_KEY = 'osint_viewer_token';

function getTrajectoryStyles(trajectory?: string) {
  switch (trajectory) {
    case 'Escalating':
      return {
        badge: 'bg-rose-500/15 text-rose-400 border-rose-500/30',
        dot: 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.6)]',
        accent: 'text-rose-400',
        border: 'border-rose-500/25',
      };
    case 'Volatile':
      return {
        badge: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
        dot: 'bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.6)]',
        accent: 'text-orange-400',
        border: 'border-orange-500/25',
      };
    case 'Elevated':
      return {
        badge: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
        dot: 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]',
        accent: 'text-amber-400',
        border: 'border-amber-500/25',
      };
    case 'De-escalating':
      return {
        badge: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
        dot: 'bg-sky-400 shadow-[0_0_8px_rgba(56,189,248,0.6)]',
        accent: 'text-sky-400',
        border: 'border-sky-500/25',
      };
    case 'Stable':
    default:
      return {
        badge: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
        dot: 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]',
        accent: 'text-emerald-400',
        border: 'border-emerald-500/25',
      };
  }
}

function getTrustBadgeStyles(label?: string) {
  switch (label) {
    case 'Highly Corroborated':
      return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
    case 'Developing / Verified':
      return 'bg-sky-500/15 text-sky-400 border-sky-500/30';
    case 'Single-Source Unverified':
      return 'bg-amber-500/15 text-amber-400 border-amber-500/30';
    case 'Isolated Outlier':
    default:
      return 'bg-rose-500/15 text-rose-400 border-rose-500/30';
  }
}

function getThreatLevelBadge(level?: string) {
  switch (level) {
    case 'critical':
      return 'bg-rose-500/15 text-rose-400 border-rose-500/30';
    case 'high':
      return 'bg-amber-500/15 text-amber-400 border-amber-500/30';
    case 'medium':
      return 'bg-sky-500/15 text-sky-400 border-sky-500/30';
    case 'low':
    default:
      return 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30';
  }
}

function citationToNormalizedEvent(cit: SummaryCitationEvent, themeId: string): NormalizedEvent {
  return {
    id: cit.id,
    theme_id: themeId,
    source_type: cit.source_type,
    source_name: cit.source_name,
    source_url: cit.source_url,
    external_id: cit.id,
    title: cit.title,
    raw_text: cit.raw_text || cit.summary,
    translated_text: cit.translated_text,
    importance_score: cit.importance_score,
    score_rationale: cit.score_rationale || `Trust: ${cit.trustworthiness_score}% (${cit.trustworthiness_label})`,
    summary: cit.summary,
    headline: cit.headline || cit.title,
    entities_json: cit.entities_json || [],
    location_json: cit.location_json || null,
    is_analyzed: true,
    published_at: cit.published_at,
    ingested_at: cit.ingested_at,
  };
}

export default function PublicDisplayPage() {
  // Authentication State
  const [token, setToken] = useState<string | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState<boolean>(true);
  const [passwordInput, setPasswordInput] = useState<string>('');
  const [authError, setAuthError] = useState<string | null>(null);
  const [isSubmittingAuth, setIsSubmittingAuth] = useState<boolean>(false);
  const [rateLimitTimer, setRateLimitTimer] = useState<number | null>(null);

  // Intelligence Data State
  const [summary, setSummary] = useState<SituationSummaryData | null>(null);
  const [events, setEvents] = useState<NormalizedEvent[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [activeTheme, setActiveTheme] = useState<Theme | null>(null);
  const [mapData, setMapData] = useState<EmbeddingsMapData>({
    nodes: [],
    edges: [],
    clusters: [],
    stats: { total_nodes: 0, total_edges: 0, total_clusters: 0, active_model: 'gemini-embedding-2' },
  });

  const [loading, setLoading] = useState<boolean>(true);
  const [isPaused, setIsPaused] = useState<boolean>(false);
  const [selectedEvent, setSelectedEvent] = useState<NormalizedEvent | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [copiedMarkdown, setCopiedMarkdown] = useState<boolean>(false);
  const [expandedClusters, setExpandedClusters] = useState<Record<number, boolean>>({});
  const [wsConnected, setWsConnected] = useState<boolean>(false);

  // Mobile Display Tab Navigation ('sitrep' | 'feed' | 'radar')
  const [mobileTab, setMobileTab] = useState<'sitrep' | 'feed' | 'radar'>('sitrep');
  const [mounted, setMounted] = useState<boolean>(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSource, setSelectedSource] = useState<string>('all');
  const [minScore, setMinScore] = useState<number>(0);
  const [sortBy, setSortBy] = useState<SortOption>('newest');
  const sortByRef = useRef<SortOption>(sortBy);

  useEffect(() => {
    sortByRef.current = sortBy;
  }, [sortBy]);

  // Handle rate limit countdown
  useEffect(() => {
    if (rateLimitTimer === null || rateLimitTimer <= 0) return;
    const interval = setInterval(() => {
      setRateLimitTimer((prev) => (prev && prev > 1 ? prev - 1 : null));
    }, 1000);
    return () => clearInterval(interval);
  }, [rateLimitTimer]);

  // 1. Initial auth check on mount
  useEffect(() => {
    const stored =
      typeof window !== 'undefined'
        ? sessionStorage.getItem(STORAGE_KEY) || localStorage.getItem(STORAGE_KEY)
        : null;

    if (!stored) {
      setIsCheckingAuth(false);
      return;
    }

    verifyViewerToken(stored).then((valid) => {
      if (valid) {
        setToken(stored);
      } else {
        sessionStorage.removeItem(STORAGE_KEY);
        localStorage.removeItem(STORAGE_KEY);
      }
      setIsCheckingAuth(false);
    });
  }, []);

  // Handle Login
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!passwordInput.trim() || isSubmittingAuth || (rateLimitTimer && rateLimitTimer > 0)) return;

    setIsSubmittingAuth(true);
    setAuthError(null);

    try {
      const res = await loginPublicViewer(passwordInput.trim());
      sessionStorage.setItem(STORAGE_KEY, res.token);
      localStorage.setItem(STORAGE_KEY, res.token);
      setToken(res.token);
      setPasswordInput('');
      window.dispatchEvent(new Event('osint_viewer_login'));
    } catch (err: any) {
      if (err.status === 429) {
        const secs = err.retryAfter || 300;
        setRateLimitTimer(secs);
        setAuthError(`Too many failed attempts. Please wait ${secs} seconds.`);
      } else {
        setAuthError(err.message || 'Incorrect password. Access denied.');
      }
    } finally {
      setIsSubmittingAuth(false);
    }
  };

  const handleLogout = () => {
    sessionStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(STORAGE_KEY);
    setToken(null);
    setSummary(null);
    setEvents([]);
    setMapData({
      nodes: [],
      edges: [],
      clusters: [],
      stats: { total_nodes: 0, total_edges: 0, total_clusters: 0, active_model: 'gemini-embedding-2' },
    });
    window.dispatchEvent(new Event('osint_viewer_logout'));
  };

  useEffect(() => {
    const handleRemoteLogout = () => {
      setToken(null);
      setSummary(null);
      setEvents([]);
    };
    window.addEventListener('osint_viewer_logout', handleRemoteLogout);
    return () => window.removeEventListener('osint_viewer_logout', handleRemoteLogout);
  }, []);

  // 2. Fetch intelligence data when authenticated
  const loadIntelligenceData = useCallback(async () => {
    if (!token) return;
    setLoading(true);

    try {
      const [themeRes, summaryRes, eventsRes, mapRes, statsRes] = await Promise.all([
        getPublicTheme(token).catch(() => null),
        getPublicSummary(token).catch(() => null),
        getPublicEvents(token, { limit: 100 }),
        getPublicMap(token),
        getPublicStats(token).catch(() => null),
      ]);

      if (themeRes) setActiveTheme(themeRes);
      if (summaryRes) setSummary(summaryRes);
      if (eventsRes) {
        const sorted = sortEvents(eventsRes, sortByRef.current);
        setEvents(sorted);

        // Seed Alert Wheel
        const threshold = themeRes?.alert_threshold ?? 8;
        const initialAlerts: AlertItem[] = eventsRes
          .filter((ev) => (ev.importance_score ?? 0) >= threshold)
          .slice(0, 10)
          .map((ev) => ({
            id: ev.id,
            event_id: ev.id,
            title: ev.headline || ev.title,
            headline: ev.headline,
            summary: ev.summary || ev.title,
            importance_score: ev.importance_score ?? 8,
            source_name: ev.source_name,
            source_url: ev.source_url,
            ai_provenance: ev.ai_provenance || ev.metadata_json?.ai_provenance,
            metadata_json: ev.metadata_json,
            published_at: ev.published_at,
            timestamp: ev.published_at,
            created_at: ev.ingested_at,
          }));

        if (initialAlerts.length > 0) {
          setAlerts(initialAlerts);
        }
      }
      if (mapRes) setMapData(mapRes);
      if (statsRes) setStats(statsRes);
    } catch (err: any) {
      console.error('Failed to load public intelligence data:', err);
      if (err.status === 401) {
        handleLogout();
      }
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (token) {
      loadIntelligenceData();
    }
  }, [token, loadIntelligenceData]);

  // 3. Setup Socket.IO connection for live streaming
  useEffect(() => {
    if (!token) return;

    const socket = getViewerSocket(token);
    if (!socket) return;

    const onConnect = () => setWsConnected(true);
    const onDisconnect = () => setWsConnected(false);

    const onNewEvent = (newEvent: NormalizedEvent) => {
      if (isPaused) return;
      let isExisting = false;
      setEvents((prev) => {
        const index = prev.findIndex(
          (e) => e.id === newEvent.id || (e.external_id && e.external_id === newEvent.external_id)
        );
        let nextList: NormalizedEvent[];
        if (index >= 0) {
          isExisting = true;
          const updated = [...prev];
          const existing = updated[index];
          const mergedEntities =
            (newEvent as any).entities || newEvent.entities_json || existing.entities_json || [];
          updated[index] = {
            ...existing,
            ...newEvent,
            entities_json: mergedEntities,
          };
          nextList = updated;
        } else {
          const initialEntities = (newEvent as any).entities || newEvent.entities_json || [];
          nextList = [{ ...newEvent, entities_json: initialEntities }, ...prev];
        }
        return sortEvents(nextList, sortByRef.current).slice(0, 150);
      });

      setStats((prev) => {
        if (!prev) return null;
        const isHigh = (newEvent.importance_score ?? 0) >= 7;
        const heartbeatTime = newEvent.ingested_at || new Date().toISOString();
        return {
          ...prev,
          total_events: isExisting ? prev.total_events : prev.total_events + 1,
          high_priority_events: isHigh && !isExisting ? prev.high_priority_events + 1 : prev.high_priority_events,
          last_updated: heartbeatTime,
          last_update_timestamp: heartbeatTime,
        };
      });

      // Alert Wheel item
      const threshold = activeTheme?.alert_threshold ?? 8;
      if ((newEvent.importance_score ?? 0) >= threshold) {
        const item: AlertItem = {
          id: newEvent.id,
          event_id: newEvent.id,
          title: newEvent.headline || newEvent.title,
          headline: newEvent.headline,
          summary: newEvent.summary || newEvent.raw_text,
          importance_score: newEvent.importance_score ?? 8,
          source_name: newEvent.source_name,
          source_url: newEvent.source_url,
          ai_provenance: newEvent.ai_provenance || (newEvent as any).metadata_json?.ai_provenance || (newEvent as any).metadata?.ai_provenance,
          metadata_json: (newEvent as any).metadata_json || (newEvent as any).metadata,
          published_at: newEvent.published_at,
          timestamp: newEvent.published_at,
          created_at: newEvent.ingested_at,
          received_at: new Date().toISOString(),
        };
        setAlerts((prev) => {
          if (prev.some((a) => a.id === item.id || a.event_id === item.event_id)) return prev;
          return [item, ...prev].slice(0, 20);
        });
      }
    };

    const onCriticalAlert = (alertData: any) => {
      const item: AlertItem = {
        id: alertData.id || alertData.event_id || `alt-${Date.now()}`,
        event_id: alertData.event_id || alertData.id,
        title: alertData.headline || alertData.title || 'Critical Flash Alert',
        headline: alertData.headline || alertData.title,
        summary: alertData.summary || alertData.raw_text,
        importance_score: alertData.importance_score ?? 9,
        source_name: alertData.source_name || 'REAL-TIME OSINT',
        source_url: alertData.source_url,
        ai_provenance: alertData.ai_provenance || alertData.metadata_json?.ai_provenance || alertData.metadata?.ai_provenance,
        metadata_json: alertData.metadata_json || alertData.metadata,
        published_at: alertData.published_at || new Date().toISOString(),
        timestamp: alertData.timestamp || new Date().toISOString(),
        created_at: alertData.created_at || new Date().toISOString(),
        received_at: new Date().toISOString(),
      };
      setAlerts((prev) => {
        const filtered = prev.filter((a) => a.id !== item.id && a.event_id !== item.event_id);
        return [item, ...filtered].slice(0, 20);
      });
    };

    const onSummaryUpdate = () => {
      if (token) {
        getPublicSummary(token).then((res) => {
          if (res) setSummary(res);
        }).catch(() => {});
      }
    };

    const onMapUpdate = () => {
      if (token) {
        getPublicMap(token).then((res) => {
          if (res) setMapData(res);
        }).catch(() => {});
      }
    };

    const onStatsUpdate = (updatedStats: DashboardStats) => {
      setStats(updatedStats);
    };

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('event:new', onNewEvent);
    socket.on('alert:critical', onCriticalAlert);
    socket.on('summary:update', onSummaryUpdate);
    socket.on('map:update', onMapUpdate);
    socket.on('stats:update', onStatsUpdate);

    if (socket.connected) {
      setWsConnected(true);
    }

    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
      socket.off('event:new', onNewEvent);
      socket.off('alert:critical', onCriticalAlert);
      socket.off('summary:update', onSummaryUpdate);
      socket.off('map:update', onMapUpdate);
      socket.off('stats:update', onStatsUpdate);
    };
  }, [token, isPaused, activeTheme]);

  // Citations lookup map for modal inspection
  const citationMap = useMemo(() => {
    const map: Record<string, SummaryCitationEvent> = {};
    if (!summary) return map;
    for (const c of summary.clusters || []) {
      for (const ev of c.events || []) {
        map[ev.id] = ev;
      }
    }
    for (const ev of summary.unverified_outliers?.events || []) {
      map[ev.id] = ev;
    }
    return map;
  }, [summary]);

  const handleInspectEventId = (eventId?: string | null) => {
    if (!eventId || !summary) return;
    const found = citationMap[eventId];
    if (found) {
      setSelectedEvent(citationToNormalizedEvent(found, summary.theme_id));
    }
  };

  const handleCopyMarkdown = () => {
    if (!summary) return;
    const lines: string[] = [
      `# OSINT SITUATIONAL BRIEFING (SITREP) — ${summary.theme_name.toUpperCase()}`,
      `- **Generated:** ${summary.generated_at}`,
      `- **Threat Trajectory:** ${summary.threat_trajectory} (Level ${summary.threat_level_score}/10)`,
      `- **Reports Analyzed:** ${summary.telemetry.total_events} (${summary.telemetry.corroboration_rate_pct}% Multi-Source Corroborated)`,
      `- **Synthesis Engine:** ${summary.active_ai_model}`,
      '',
      `## Executive BLUF`,
      summary.executive_bluf,
      '',
      `## Strategic Assessment`,
      summary.strategic_assessment,
      '',
      `## Key Verified Developments`,
      ...summary.key_developments.map((d) => `- ${d.bullet}`),
      '',
      `## Semantic Story Clusters (${summary.clusters.length})`,
      ...summary.clusters.flatMap((c) => [
        `### [Cluster #${c.cluster_id}] ${c.title} (${c.threat_level.toUpperCase()} | ${c.report_count} reports | Trust ${c.avg_trustworthiness_score}%)`,
        c.executive_summary,
        `- **Operational Implication:** ${c.operational_implication}`,
        `- **Sources:** ${c.independent_sources.join(', ')}`,
        '',
      ]),
      `## 12-24h Tactical Watchlist`,
      ...summary.watchlist_indicators.map((w, i) => `${i + 1}. ${w}`),
    ];

    navigator.clipboard.writeText(lines.join('\n'));
    setCopiedMarkdown(true);
    setTimeout(() => setCopiedMarkdown(false), 2500);
  };

  const toggleClusterExpand = (clusterId: number) => {
    setExpandedClusters((prev) => ({
      ...prev,
      [clusterId]: !prev[clusterId],
    }));
  };

  // Filtered timeline stream
  const filteredEvents = useMemo(() => {
    const list = events.filter((ev) => {
      if (selectedSource !== 'all') {
        const type = (ev.source_type || '').toLowerCase();
        if (selectedSource === 'ais' && !type.includes('ais')) return false;
        if (selectedSource === 'oref' && !['oref', 'pikud_haoref', 'homefront', 'hfc'].includes(type)) return false;
        if (selectedSource !== 'ais' && selectedSource !== 'oref' && type !== selectedSource) return false;
      }
      const score = ev.importance_score ?? 0;
      if (minScore > 0 && score < minScore) return false;

      if (searchQuery.trim()) {
        const query = searchQuery.toLowerCase();
        const titleMatch = ev.title?.toLowerCase().includes(query);
        const headlineMatch = ev.headline?.toLowerCase().includes(query);
        const summaryMatch = ev.summary?.toLowerCase().includes(query);
        const entityMatch = ev.entities_json?.some((e) => e.toLowerCase().includes(query));
        const rawMatch = ev.raw_text?.toLowerCase().includes(query);
        if (!titleMatch && !headlineMatch && !summaryMatch && !entityMatch && !rawMatch) {
          return false;
        }
      }
      return true;
    });

    return sortEvents(list, sortBy);
  }, [events, selectedSource, minScore, searchQuery, sortBy]);

  const sourceFilters = [
    { id: 'all', label: 'All Sources' },
    { id: 'oref', label: 'OREF Alerts' },
    { id: 'telegram', label: 'Telegram' },
    { id: 'rss', label: 'RSS' },
    { id: 'newsapi', label: 'News APIs' },
    { id: 'reddit', label: 'Reddit' },
    { id: 'ais', label: 'Maritime AIS' },
  ];

  // --------------------------------------------------------------------------
  // RENDER: Checking Auth State
  // --------------------------------------------------------------------------
  if (isCheckingAuth) {
    return (
      <div className="min-h-[calc(100vh-12rem)] flex items-center justify-center">
        <div className="flex flex-col items-center space-y-3">
          <AppIcon size={48} animated />
          <span className="text-xs font-mono text-zinc-500 animate-pulse">
            Verifying intelligence portal access...
          </span>
        </div>
      </div>
    );
  }

  // --------------------------------------------------------------------------
  // RENDER: Lock Screen (Zero Heavy Data Loaded)
  // --------------------------------------------------------------------------
  if (!token) {
    return (
      <div className="min-h-[calc(100vh-12rem)] flex items-center justify-center p-4">
        <div className="w-full max-w-md card-slick rounded-2xl p-8 border border-white/10 shadow-2xl space-y-6">
          <div className="flex flex-col items-center text-center space-y-3">
            <div className="relative p-3 rounded-2xl bg-surface-raised border border-white/10 shadow-inner">
              <AppIcon size={52} withBeacon />
            </div>
            <div>
              <div className="flex items-center justify-center space-x-2">
                <h1 className="text-lg font-bold tracking-tight text-zinc-100 uppercase">
                  OSINT <span className="text-accent-cyan">REALTIME</span>
                </h1>
                <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-500/10 text-amber-400 border border-amber-500/30">
                  READ-ONLY
                </span>
              </div>
              <p className="text-xs text-zinc-400 mt-1">
                Restricted Geopolitical Intelligence &amp; Live Fusion Display
              </p>
            </div>
          </div>

          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-1.5">
              <label className="block text-[11px] font-mono text-zinc-400 uppercase tracking-wider">
                Display Access Password
              </label>
              <div className="relative">
                <input
                  type="password"
                  value={passwordInput}
                  onChange={(e) => setPasswordInput(e.target.value)}
                  placeholder="Enter password..."
                  disabled={isSubmittingAuth || (rateLimitTimer !== null && rateLimitTimer > 0)}
                  className="w-full rounded-xl bg-canvas-recessed pl-10 pr-4 py-2.5 text-base sm:text-xs text-zinc-100 placeholder-zinc-500 border border-white/10 focus:outline-none focus:border-accent-cyan transition-colors disabled:opacity-50"
                  autoFocus
                />
                <Lock className="absolute left-3.5 top-3 w-4 h-4 text-zinc-500" />
              </div>
            </div>

            {authError && (
              <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300 flex items-center space-x-2 animate-fade-in">
                <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0" />
                <span>{authError}</span>
              </div>
            )}

            {rateLimitTimer !== null && rateLimitTimer > 0 && (
              <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs text-amber-300 flex items-center space-x-2">
                <Clock className="w-4 h-4 text-amber-400 shrink-0 animate-pulse" />
                <span>Rate limited. Cooldown active: {rateLimitTimer}s remaining.</span>
              </div>
            )}

            <button
              type="submit"
              disabled={
                !passwordInput.trim() ||
                isSubmittingAuth ||
                (rateLimitTimer !== null && rateLimitTimer > 0)
              }
              className="w-full flex items-center justify-center space-x-2 py-2.5 rounded-xl bg-accent-cyan/15 hover:bg-accent-cyan/25 border border-accent-cyan/40 text-xs font-semibold text-accent-cyan transition-all disabled:opacity-50 disabled:cursor-not-allowed pressable"
            >
              {isSubmittingAuth ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  <span>Verifying Credentials...</span>
                </>
              ) : (
                <>
                  <Unlock className="w-4 h-4" />
                  <span>Unlock Intelligence Portal</span>
                </>
              )}
            </button>
          </form>

          <div className="pt-2 border-t border-white/5 space-y-3 text-center">
            <InstallPwaButton
              label="Install Display App (PWA)"
              variant="prominent"
              appName="OSINT Public Display"
            />
            <span className="text-[11px] font-mono text-zinc-500 block">
              Live Feed • AI SitRep • 2D Consensus Radar
            </span>
          </div>
        </div>
      </div>
    );
  }

  // --------------------------------------------------------------------------
  // RENDER: Authenticated Read-Only Display
  // --------------------------------------------------------------------------
  const trajStyle = getTrajectoryStyles(summary?.threat_trajectory);

  const synthesizerProv =
    summary?.ai_provenance?.synthesizer ||
    (summary?.active_ai_model ? parseModelProvider(summary.active_ai_model, 'situation_summary') : undefined);
  const embeddingProv =
    summary?.ai_provenance?.embedding ||
    (summary?.active_embedding_model ? parseModelProvider(summary.active_embedding_model, 'embedding') : undefined);

  return (
    <div className="space-y-6 pb-24 lg:pb-12">

      {/* Critical Flash Alert Wheel */}
      {alerts.length > 0 && (
        <AlertWheel
          alerts={alerts}
          events={events}
          onDismissAlert={(id) => setAlerts((prev) => prev.filter((a) => a.id !== id && a.event_id !== id))}
          onDismissAll={() => setAlerts([])}
          onOpenDetails={(alert) => {
            const match = events.find((e) => e.id === alert.id || e.id === alert.event_id);
            if (match) {
              setSelectedEvent(match);
            } else {
              setSelectedEvent({
                id: alert.id || alert.event_id || 'preview-alert',
                theme_id: activeTheme?.id || 'default',
                source_type: 'alert',
                source_name: alert.source_name || 'REAL-TIME OSINT',
                source_url: alert.source_url,
                external_id: alert.event_id || alert.id,
                title: alert.title,
                headline: alert.headline || alert.title,
                summary: alert.summary,
                raw_text: alert.summary || alert.title,
                importance_score: alert.importance_score ?? 9,
                ai_provenance: alert.ai_provenance || alert.metadata_json?.ai_provenance,
                metadata_json: alert.metadata_json,
                published_at: alert.published_at || new Date().toISOString(),
                ingested_at: alert.created_at || new Date().toISOString(),
              });
            }
          }}
        />
      )}

      {/* -------------------------------------------------------------------- */}
      {/* MODULAR COMPONENT RENDERERS                                          */}
      {/* -------------------------------------------------------------------- */}

      {/* Helper: KPI Cards (2x2 on mobile, 4-col on desktop) */}
      {(() => {
        const renderKpiCards = (isMobile = false) => {
          if (!summary) return null;
          return (
            <div
              className={cn(
                isMobile
                  ? 'grid grid-cols-2 gap-2.5'
                  : 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4'
              )}
            >
              {/* 1. Threat Trajectory */}
              <div
                className={cn(
                  'card-slick rounded-xl flex flex-col justify-between border',
                  isMobile ? 'p-3 space-y-2' : 'p-4',
                  trajStyle.border
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] sm:text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                    Trajectory
                  </span>
                  <span
                    className={cn(
                      'inline-flex items-center space-x-1 sm:space-x-1.5 px-2 py-0.5 rounded-full text-[10px] sm:text-[11px] font-semibold border',
                      trajStyle.badge
                    )}
                  >
                    <span className={cn('w-1.5 h-1.5 rounded-full', trajStyle.dot)} />
                    <span className="truncate max-w-[85px] sm:max-w-none">{summary.threat_trajectory}</span>
                  </span>
                </div>
                <div className="flex items-baseline justify-between mt-1 sm:mt-3">
                  <div>
                    <span
                      className={cn(
                        'text-xl sm:text-2xl font-semibold tracking-tight font-mono tabular',
                        trajStyle.accent
                      )}
                    >
                      {summary.threat_level_score}
                    </span>
                    <span className="text-[10px] sm:text-xs text-zinc-500 font-mono"> / 10</span>
                  </div>
                  <span className="text-[10px] sm:text-[11px] text-zinc-400 font-mono tabular">
                    Peak {summary.telemetry.max_importance_score}/10
                  </span>
                </div>
                <div className="text-[10px] sm:text-[11px] text-zinc-400 truncate pt-1 border-t border-white/5">
                  Theme: <span className="text-zinc-200 font-medium">{summary.theme_name}</span>
                </div>
              </div>

              {/* 2. Embedding Corroboration */}
              <div
                className={cn(
                  'card-slick rounded-xl flex flex-col justify-between',
                  isMobile ? 'p-3 space-y-2' : 'p-4'
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] sm:text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                    Corroboration
                  </span>
                  <ShieldCheck className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-emerald-400" />
                </div>
                <div className="flex items-baseline justify-between mt-1 sm:mt-3">
                  <div className="flex items-baseline space-x-1 sm:space-x-1.5">
                    <span className="text-xl sm:text-2xl font-semibold tracking-tight text-emerald-400 font-mono tabular">
                      {summary.telemetry.corroboration_rate_pct}%
                    </span>
                    <span className="text-[10px] sm:text-[11px] text-zinc-400">verified</span>
                  </div>
                  <span className="text-[10px] sm:text-[11px] font-mono text-zinc-400 tabular truncate max-w-[90px] sm:max-w-none">
                    {summary.telemetry.cross_source_edges_count} links
                  </span>
                </div>
                <div className="flex items-center justify-between text-[10px] sm:text-[11px] text-zinc-400 font-mono tabular pt-1 border-t border-white/5">
                  <span>{summary.telemetry.corroborated_reports_count}/{summary.telemetry.total_events} confirmed</span>
                  <span className="text-emerald-400/90 hidden sm:inline">{summary.telemetry.embedding_coverage_pct}% embedded</span>
                </div>
              </div>

              {/* 3. Story Clusters & Alerts */}
              <div
                className={cn(
                  'card-slick rounded-xl flex flex-col justify-between',
                  isMobile ? 'p-3 space-y-2' : 'p-4'
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] sm:text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                    Clusters &amp; Alerts
                  </span>
                  <Compass className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-accent-cyan" />
                </div>
                <div className="flex items-baseline justify-between mt-1 sm:mt-3">
                  <div className="flex items-baseline space-x-1 sm:space-x-1.5">
                    <span className="text-xl sm:text-2xl font-semibold tracking-tight text-accent-cyan font-mono tabular">
                      {summary.telemetry.total_clusters}
                    </span>
                    <span className="text-[10px] sm:text-[11px] text-zinc-400">clusters</span>
                  </div>
                  <span className="inline-flex items-center space-x-1 px-1.5 sm:px-2 py-0.5 rounded bg-rose-500/10 border border-rose-500/20 text-rose-400 text-[10px] sm:text-[11px] font-mono tabular">
                    <Flame className="w-2.5 h-2.5 sm:w-3 sm:h-3" />
                    <span>{summary.telemetry.critical_alerts_count} crit</span>
                  </span>
                </div>
                <div className="flex items-center justify-between text-[10px] sm:text-[11px] text-zinc-400 font-mono tabular pt-1 border-t border-white/5">
                  <span>{summary.telemetry.total_events} reports</span>
                  <span>{summary.telemetry.isolated_outliers_count} outliers</span>
                </div>
              </div>

              {/* 4. Operational Tempo */}
              <div
                className={cn(
                  'card-slick rounded-xl flex flex-col justify-between',
                  isMobile ? 'p-3 space-y-2' : 'p-4'
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] sm:text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                    Operational Tempo
                  </span>
                  <Activity className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-accent-amber" />
                </div>
                <div className="flex items-baseline justify-between mt-1 sm:mt-3">
                  <span className="text-lg sm:text-xl font-semibold tracking-tight text-zinc-100 truncate max-w-[110px] sm:max-w-none">
                    {summary.telemetry.velocity_status}
                  </span>
                  <span className="text-[10px] sm:text-[11px] font-mono text-zinc-400 tabular">
                    {summary.telemetry.events_last_6h} in 6h
                  </span>
                </div>
                <div className="flex items-center justify-between text-[10px] sm:text-[11px] font-mono tabular pt-1 border-t border-white/5">
                  <span className="text-zinc-400">Avg Score:</span>
                  <span className="text-zinc-200">{summary.telemetry.avg_importance_score} / 10</span>
                </div>
              </div>
            </div>
          );
        };

        const renderSitRepContent = () => {
          if (!summary) {
            return (
              <div className="card-slick rounded-xl p-8 text-center space-y-3">
                <RefreshCw className="w-6 h-6 text-accent-cyan animate-spin mx-auto" />
                <p className="text-sm font-medium text-zinc-200">Synthesizing Situational Briefing...</p>
                <p className="text-xs text-zinc-400">Automatic AI synthesis generates every 30 minutes.</p>
              </div>
            );
          }

          return (
            <div className="card-slick rounded-xl p-4 sm:p-6 space-y-5">
              {/* Header: Title, Engine Badges, Copy SitRep */}
              <div className="flex flex-wrap items-center justify-between gap-2 pb-3 hairline-b">
                <div className="flex items-center space-x-2.5 min-w-0">
                  <div className="p-2 rounded-lg bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/25 shrink-0">
                    <Sparkles className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-xs sm:text-sm font-semibold text-zinc-100 tracking-tight uppercase font-mono">
                        Executive Situational Briefing (BLUF)
                      </h3>
                      <span className="text-[9px] sm:text-[10px] font-mono text-accent-cyan bg-accent-cyan/10 px-2 py-0.5 rounded border border-accent-cyan/25 whitespace-nowrap">
                        30-MIN AUTO FUSION
                      </span>
                      <AIModelBadge provenance={synthesizerProv} size="xs" linkToSettings={false} />
                    </div>
                    <p className="text-[10px] sm:text-[11px] text-zinc-400 mt-0.5">
                      Synthesized across {summary.telemetry.total_events} reports, {summary.telemetry.total_clusters} clusters, and {summary.telemetry.cross_source_edges_count} corroboration links
                    </p>
                    {/* Active AI Engine Badges */}
                    <div className="flex flex-wrap items-center gap-2 pt-1.5 text-[10px] sm:text-[11px] font-mono text-zinc-400 tabular">
                      <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded bg-surface-raised border border-white/5">
                        <Sparkles className="w-3 h-3 text-accent-cyan" />
                        <span>Synthesizer:</span>
                        <AIModelBadge provenance={synthesizerProv} size="xs" linkToSettings={false} />
                      </div>
                      <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded bg-surface-raised border border-white/5">
                        <Compass className="w-3 h-3 text-emerald-400" />
                        <span>Embeddings:</span>
                        <AIModelBadge provenance={embeddingProv} size="xs" linkToSettings={false} />
                      </div>
                    </div>
                  </div>
                </div>

                <div className="flex items-center space-x-2 sm:space-x-3 ml-auto sm:ml-0">
                  <span className="text-[10px] sm:text-[11px] font-mono text-zinc-400 tabular">
                    Updated {formatRelativeTime(summary.generated_at)}
                  </span>
                  <button
                    onClick={handleCopyMarkdown}
                    className="flex items-center space-x-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised hairline-border text-xs font-medium text-zinc-200 transition-colors pressable"
                  >
                    {copiedMarkdown ? (
                      <>
                        <Check className="w-3.5 h-3.5 text-emerald-400" />
                        <span className="text-emerald-400">Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5 text-zinc-400" />
                        <span className="hidden sm:inline">Export SitRep</span>
                        <span className="sm:hidden">Export</span>
                      </>
                    )}
                  </button>
                </div>
              </div>

              {/* BLUF Text */}
              <div className="space-y-3 text-sm text-zinc-200 leading-relaxed">
                {summary.executive_bluf.split('\n\n').map((para, idx) => (
                  <p key={idx} className="text-xs sm:text-[13.5px] leading-relaxed text-zinc-200">
                    {para}
                  </p>
                ))}
              </div>

              {/* Strategic Assessment Callout */}
              {summary.strategic_assessment && (
                <div className="p-3.5 sm:p-4 rounded-xl bg-surface-raised/70 border border-white/10 space-y-1.5">
                  <div className="flex items-center justify-between gap-2 text-xs font-mono uppercase tracking-wider text-accent-cyan">
                    <div className="flex items-center space-x-1.5">
                      <TrendingUp className="w-3.5 h-3.5" />
                      <span>Strategic Telemetry Assessment</span>
                    </div>
                    <AIModelBadge provenance={synthesizerProv} size="xs" linkToSettings={false} />
                  </div>
                  <p className="text-xs text-zinc-300 leading-relaxed">{summary.strategic_assessment}</p>
                </div>
              )}

              {/* Key Verified Developments */}
              {summary.key_developments && summary.key_developments.length > 0 && (
                <div className="pt-2 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-mono uppercase tracking-wider text-zinc-400 flex items-center space-x-1.5">
                      <Flame className="w-3.5 h-3.5 text-rose-400" />
                      <span>Key High-Impact Developments</span>
                    </h4>
                    <AIModelBadge provenance={synthesizerProv} size="xs" linkToSettings={false} />
                  </div>
                  <div className="space-y-2">
                    {summary.key_developments.map((dev, idx) => (
                      <div
                        key={idx}
                        className="flex items-start justify-between gap-2.5 sm:gap-3 p-3 rounded-lg bg-surface-card/90 hairline-border"
                      >
                        <div className="flex items-start space-x-2 sm:space-x-2.5 min-w-0">
                          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded bg-accent-cyan/10 border border-accent-cyan/25 text-[11px] font-mono text-accent-cyan tabular">
                            {idx + 1}
                          </span>
                          <div className="space-y-1 min-w-0">
                            <p className="text-xs text-zinc-200 leading-relaxed">{dev.bullet}</p>
                            <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
                              {dev.source_name && (
                                <span className="text-[10px] font-mono text-zinc-400 bg-zinc-800/80 px-1.5 py-0.5 rounded">
                                  {dev.source_name}
                                </span>
                              )}
                              {dev.importance_score && (
                                <span className="text-[10px] font-mono text-amber-400 bg-amber-500/10 border border-amber-500/20 px-1.5 py-0.5 rounded tabular">
                                  Score {dev.importance_score}/10
                                </span>
                              )}
                              {dev.trustworthiness_label && (
                                <span className={cn('text-[10px] font-mono px-1.5 py-0.5 rounded border', getTrustBadgeStyles(dev.trustworthiness_label))}>
                                  {dev.trustworthiness_label}
                                </span>
                              )}
                              {dev.event_id && citationMap[dev.event_id]?.ai_provenance && (
                                <AIModelBadge
                                  provenance={citationMap[dev.event_id]?.ai_provenance?.scoring}
                                  size="xs"
                                  linkToSettings={false}
                                />
                              )}
                            </div>
                          </div>
                        </div>

                        {dev.event_id && citationMap[dev.event_id] && (
                          <button
                            onClick={() => handleInspectEventId(dev.event_id)}
                            className="shrink-0 flex items-center space-x-1 px-2 sm:px-2.5 py-1 rounded bg-surface-raised hover:bg-surface-overlay hairline-border text-[11px] font-medium text-accent-cyan transition-colors pressable"
                          >
                            <Eye className="w-3 h-3" />
                            <span className="hidden sm:inline">Inspect</span>
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Semantic Story Clusters Accordion */}
              {summary.clusters && summary.clusters.length > 0 && (
                <div className="pt-2 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-mono uppercase tracking-wider text-zinc-400 flex items-center space-x-1.5">
                      <Layers className="w-3.5 h-3.5 text-accent-cyan" />
                      <span>Semantic Storyline Clusters ({summary.clusters.length})</span>
                    </h4>
                    <div className="flex items-center space-x-2">
                      <AIModelBadge provenance={synthesizerProv} size="xs" linkToSettings={false} />
                      <AIModelBadge provenance={embeddingProv} size="xs" linkToSettings={false} />
                    </div>
                  </div>
                  <div className="space-y-2.5">
                    {summary.clusters.map((c: SummaryClusterBreakdown) => {
                      const isExpanded = !!expandedClusters[c.cluster_id];
                      return (
                        <div key={c.cluster_id} className="p-3.5 sm:p-4 rounded-xl bg-surface-card/60 hairline-border space-y-2">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex items-center space-x-2">
                              <span className="px-2 py-0.5 rounded bg-accent-cyan/10 border border-accent-cyan/25 text-accent-cyan font-mono text-[10px] sm:text-[11px] font-semibold">
                                CLUSTER #{c.cluster_id}
                              </span>
                              <span className={cn('px-2 py-0.5 rounded border text-[10px] font-mono uppercase font-semibold', getThreatLevelBadge(c.threat_level))}>
                                {c.threat_level}
                              </span>
                              <span className="text-xs font-semibold text-zinc-100">{c.title}</span>
                            </div>
                            <div className="flex items-center space-x-2">
                              <AIModelBadge provenance={synthesizerProv} size="xs" linkToSettings={false} />
                              <span className="text-[10px] sm:text-[11px] font-mono text-zinc-400">
                                {c.report_count} reports • Trust {c.avg_trustworthiness_score}%
                              </span>
                            </div>
                          </div>
                          <p className="text-xs text-zinc-300 leading-relaxed">{c.executive_summary}</p>
                          <div className="flex items-center justify-between pt-1 text-[11px] text-zinc-400">
                            <span><strong>Implication:</strong> {c.operational_implication}</span>
                            <button
                              onClick={() => toggleClusterExpand(c.cluster_id)}
                              className="text-accent-cyan hover:underline inline-flex items-center space-x-1"
                            >
                              <span>{isExpanded ? 'Hide reports' : `View ${c.events.length} reports`}</span>
                              {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                            </button>
                          </div>
                          {isExpanded && (
                            <div className="pt-2 space-y-1.5 border-t border-white/5">
                              {c.events.map((ev) => (
                                <div
                                  key={ev.id}
                                  className="flex items-center justify-between p-2 rounded bg-surface-raised/60 text-xs"
                                >
                                  <div className="min-w-0 pr-2">
                                    <span className="font-medium text-zinc-200 block truncate">{ev.headline || ev.title}</span>
                                    <span className="text-[10px] font-mono text-zinc-500">{ev.source_name} • Score {ev.importance_score}/10</span>
                                  </div>
                                  <button
                                    onClick={() => setSelectedEvent(citationToNormalizedEvent(ev, summary.theme_id))}
                                    className="shrink-0 text-accent-cyan hover:underline text-[11px]"
                                  >
                                    Inspect
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* 12-24h Tactical Watchlist Indicators */}
              {summary.watchlist_indicators && summary.watchlist_indicators.length > 0 && (
                <div className="pt-2 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-mono uppercase tracking-wider text-zinc-400 flex items-center space-x-1.5">
                      <Clock className="w-3.5 h-3.5 text-accent-amber" />
                      <span>12–24h Tactical Watchlist</span>
                    </h4>
                    <AIModelBadge provenance={synthesizerProv} size="xs" linkToSettings={false} />
                  </div>
                  <ul className="space-y-2">
                    {summary.watchlist_indicators.map((indicator, idx) => (
                      <li
                        key={idx}
                        className="flex items-start space-x-2.5 p-2.5 rounded-lg bg-surface-card/90 hairline-border text-xs text-zinc-200 leading-relaxed"
                      >
                        <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent-cyan/20 text-accent-cyan font-mono text-[10px] font-semibold tabular">
                          {idx + 1}
                        </span>
                        <span>{indicator}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Quick Jump actions for mobile at the bottom of the SitRep card */}
              <div className="lg:hidden grid grid-cols-2 gap-2 pt-3 border-t border-white/5">
                <button
                  onClick={() => {
                    setMobileTab('feed');
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                  }}
                  className="flex items-center justify-center space-x-1.5 p-2.5 rounded-xl bg-surface-raised hairline-border text-xs font-medium text-zinc-200 hover:text-accent-cyan transition-colors pressable"
                >
                  <Radio className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Live Stream &rarr;</span>
                </button>
                <button
                  onClick={() => {
                    setMobileTab('radar');
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                  }}
                  className="flex items-center justify-center space-x-1.5 p-2.5 rounded-xl bg-surface-raised hairline-border text-xs font-medium text-zinc-200 hover:text-accent-cyan transition-colors pressable"
                >
                  <Compass className="w-3.5 h-3.5 text-accent-cyan" />
                  <span>Radar Map &rarr;</span>
                </button>
              </div>
            </div>
          );
        };

        const renderFeedContent = (isMobile = false) => {
          return (
            <div className="space-y-4">
              {/* Feed Filter & Control Toolbar */}
              <div className="card-slick rounded-xl p-3.5 sm:p-4 space-y-3">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 sm:gap-3">
                  {/* Search Input */}
                  <div className="relative flex-1">
                    <input
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Search intelligence reports..."
                      className="w-full rounded-lg bg-canvas-recessed pl-9 pr-3.5 py-2 sm:py-1.5 text-base sm:text-xs text-zinc-100 placeholder-zinc-500 hairline-border focus:outline-none focus:border-accent-cyan"
                    />
                    <Search className="absolute left-3 top-2.5 sm:top-2 w-3.5 h-3.5 text-zinc-500" />
                    {searchQuery && (
                      <button onClick={() => setSearchQuery('')} className="absolute right-3 top-2.5 sm:top-2 text-zinc-500 hover:text-zinc-300">
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>

                  {/* Controls: Score Slider, Pause Stream, Refresh */}
                  <div className="flex items-center justify-between sm:justify-end space-x-2">
                    {/* Score Slider */}
                    <div className="flex items-center space-x-1.5 bg-canvas-recessed px-2.5 py-1 rounded-lg hairline-border text-xs tabular flex-1 sm:flex-initial justify-center sm:justify-start">
                      <span className="text-zinc-400 text-[11px]">Min:</span>
                      <input
                        type="range"
                        min="0"
                        max="10"
                        value={minScore}
                        onChange={(e) => setMinScore(Number(e.target.value))}
                        className="w-16 accent-accent-cyan cursor-pointer"
                      />
                      <span className="font-mono font-bold text-accent-cyan text-xs">
                        {minScore > 0 ? `≥${minScore}` : 'All'}
                      </span>
                    </div>

                    {/* Pause Stream */}
                    <button
                      onClick={() => setIsPaused(!isPaused)}
                      className={cn(
                        'p-2 sm:p-1.5 rounded-lg hairline-border text-xs transition-colors pressable',
                        isPaused ? 'bg-amber-500/15 text-amber-300 border-amber-500/30' : 'bg-surface-raised text-zinc-300'
                      )}
                      title={isPaused ? 'Resume stream' : 'Pause stream'}
                    >
                      {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
                    </button>

                    {/* Refresh Feed */}
                    <button
                      onClick={() => loadIntelligenceData()}
                      className="p-2 sm:p-1.5 rounded-lg bg-surface-raised hairline-border text-zinc-400 hover:text-zinc-200 transition-colors pressable"
                      title="Refresh intelligence data"
                    >
                      <RefreshCw className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Source Pills row with smooth horizontal scroll on mobile */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 pt-2 border-t border-white/5">
                  <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-none py-1 -mx-1 px-1 sm:mx-0 sm:px-0 flex-nowrap sm:flex-wrap">
                    <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider shrink-0 mr-1">Filter:</span>
                    {sourceFilters.map((sf) => (
                      <button
                        key={sf.id}
                        onClick={() => setSelectedSource(sf.id)}
                        className={cn(
                          'px-2.5 py-1 sm:py-0.5 rounded-md sm:rounded text-[11px] font-medium transition-all hairline-border shrink-0 pressable whitespace-nowrap',
                          selectedSource === sf.id
                            ? 'bg-surface-raised text-accent-cyan border-accent-cyan/40 shadow-sm'
                            : 'bg-canvas-recessed text-zinc-400 hover:text-zinc-200'
                        )}
                      >
                        {sf.label}
                      </button>
                    ))}
                  </div>
                  <div className="text-[10px] sm:text-[11px] font-mono text-zinc-500 tabular shrink-0 pt-0.5 sm:pt-0">
                    Showing {filteredEvents.length} of {events.length}
                  </div>
                </div>
              </div>

              {/* Timeline Cards Stream */}
              <div className="space-y-3">
                {loading ? (
                  <div className="space-y-3">
                    {[1, 2, 3].map((i) => (
                      <div key={i} className="card-slick rounded-xl p-4 space-y-2 animate-pulse">
                        <div className="h-3 bg-zinc-800 rounded w-1/4" />
                        <div className="h-5 bg-zinc-800 rounded w-3/4" />
                        <div className="h-3 bg-zinc-800 rounded w-full" />
                      </div>
                    ))}
                  </div>
                ) : filteredEvents.length === 0 ? (
                  <div className="card-slick rounded-xl p-8 text-center space-y-2">
                    <p className="text-sm text-zinc-300 font-medium">No intelligence events match filters</p>
                    <p className="text-xs text-zinc-500">Try relaxing your search query or minimum score filter.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {filteredEvents.map((event) => (
                      <EventCard
                        key={event.id}
                        event={event}
                        onOpenDetails={(ev) => setSelectedEvent(ev)}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        };

        const renderRadarContent = (isMobile = false) => {
          return (
            <div className="space-y-4">
              <div className="card-slick rounded-xl p-3.5 sm:p-4 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2 pb-2 hairline-b">
                  <div className="flex items-center space-x-2">
                    <Compass className="w-4 h-4 text-accent-cyan" />
                    <h3 className="text-xs font-semibold text-zinc-100 uppercase tracking-wider font-mono">
                      Consensus &amp; Relevance Radar
                    </h3>
                  </div>
                  <div className="flex items-center space-x-2">
                    <AIModelBadge provenance={embeddingProv} size="xs" linkToSettings={false} />
                    <span className="text-[10px] font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                      CORE-TO-PERIPHERY
                    </span>
                  </div>
                </div>
                <p className="text-[11px] text-zinc-400 leading-relaxed">
                  Multi-source consensus radar. Confirmed high-agreement clusters anchor the core, while isolated single-source dispatches orbit the outer perimeter.
                </p>
                {/* Touch hint for mobile users */}
                {isMobile && (
                  <div className="flex items-center justify-between text-[10px] font-mono text-zinc-400 bg-surface-raised/70 px-2.5 py-1.5 rounded-lg hairline-border">
                    <span>💡 Touch: Pinch to zoom • Drag to pan</span>
                    <span className="text-accent-cyan">Tap node to inspect</span>
                  </div>
                )}
              </div>

              {/* Interactive Relevance Map in Read-Only Mode */}
              <div
                className={cn(
                  'rounded-xl overflow-hidden hairline-border bg-surface-card',
                  isMobile ? 'h-[calc(100vh-270px)] min-h-[460px] max-h-[720px]' : ''
                )}
              >
                <RelevanceMap
                  data={mapData}
                  isLoading={loading}
                  onRefresh={() => loadIntelligenceData()}
                  readOnly={true}
                />
              </div>
            </div>
          );
        };

        return (
          <>
            {/* ================================================================ */}
            {/* MOBILE LAYOUT (< 1024px: lg:hidden)                              */}
            {/* Dedicated Tabs: SitRep Briefing | Live Stream | Consensus Radar  */}
            {/* ================================================================ */}
            <div className="lg:hidden space-y-4">
              {/* Mobile View Active Tab Display */}
              {mobileTab === 'sitrep' && (
                <div className="space-y-4 animate-fade-in">
                  {renderKpiCards(true)}
                  {renderSitRepContent()}
                </div>
              )}

              {mobileTab === 'feed' && (
                <div className="space-y-4 animate-fade-in">
                  {renderFeedContent(true)}
                </div>
              )}

              {mobileTab === 'radar' && (
                <div className="space-y-4 animate-fade-in">
                  {renderRadarContent(true)}
                </div>
              )}
            </div>

            {/* ================================================================ */}
            {/* DESKTOP LAYOUT (>= 1024px: hidden lg:block)                      */}
            {/* Unified Command Center: Top 4-Col KPIs + SitRep + 7/5 Col Split  */}
            {/* ================================================================ */}
            <div className="hidden lg:block space-y-6">
              {summary && (
                <div className="space-y-4">
                  {renderKpiCards(false)}
                  {renderSitRepContent()}
                </div>
              )}

              <div className="grid grid-cols-12 gap-6 items-start">
                <div className="col-span-7 space-y-4">
                  {renderFeedContent(false)}
                </div>
                <div className="col-span-5 space-y-4">
                  {renderRadarContent(false)}
                </div>
              </div>
            </div>

            {/* ================================================================ */}
            {/* FIXED MOBILE BOTTOM NAVIGATION BAR (PORTAL TO DOCUMENT.BODY)      */}
            {/* Directly anchored to body, isolated from layout/overflow contexts */}
            {/* ================================================================ */}
            {mounted && typeof document !== 'undefined'
              ? createPortal(
                  <nav
                    aria-label="Mobile Navigation"
                    className="lg:hidden fixed inset-x-0 bottom-0 z-40 bg-[#0d0e12]/95 backdrop-blur-md border-t border-white/10 px-2 py-1.5 pb-[calc(0.6rem+env(safe-area-inset-bottom,0px))] shadow-[0_-4px_24px_rgba(0,0,0,0.8)]"
                  >
                    <div className="flex items-center justify-around max-w-md mx-auto">
                      <button
                        onClick={() => {
                          setMobileTab('sitrep');
                          window.scrollTo({ top: 0, behavior: 'smooth' });
                        }}
                        className={cn(
                          'flex flex-col items-center justify-center flex-1 py-1 px-2 rounded-xl transition-all pressable',
                          mobileTab === 'sitrep' ? 'text-accent-cyan font-semibold' : 'text-zinc-400 hover:text-zinc-200'
                        )}
                      >
                        <div
                          className={cn(
                            'p-1 rounded-lg transition-colors',
                            mobileTab === 'sitrep' ? 'bg-accent-cyan/15 text-accent-cyan' : 'text-zinc-400'
                          )}
                        >
                          <Sparkles className="w-4 h-4" />
                        </div>
                        <span className="text-[10px] mt-0.5 tracking-tight font-medium">SitRep</span>
                      </button>

                      <button
                        onClick={() => {
                          setMobileTab('feed');
                          window.scrollTo({ top: 0, behavior: 'smooth' });
                        }}
                        className={cn(
                          'flex flex-col items-center justify-center flex-1 py-1 px-2 rounded-xl transition-all pressable relative',
                          mobileTab === 'feed' ? 'text-accent-cyan font-semibold' : 'text-zinc-400 hover:text-zinc-200'
                        )}
                      >
                        <div
                          className={cn(
                            'p-1 rounded-lg transition-colors relative',
                            mobileTab === 'feed' ? 'bg-accent-cyan/15 text-accent-cyan' : 'text-zinc-400'
                          )}
                        >
                          <Radio className="w-4 h-4" />
                          {events.length > 0 && (
                            <span className="absolute -top-1 -right-2 px-1 py-0.2 bg-emerald-500 text-zinc-950 font-mono text-[9px] font-bold rounded-full min-w-[16px] text-center">
                              {events.length > 99 ? '99+' : events.length}
                            </span>
                          )}
                        </div>
                        <span className="text-[10px] mt-0.5 tracking-tight font-medium">Live Stream</span>
                      </button>

                      <button
                        onClick={() => {
                          setMobileTab('radar');
                          window.scrollTo({ top: 0, behavior: 'smooth' });
                        }}
                        className={cn(
                          'flex flex-col items-center justify-center flex-1 py-1 px-2 rounded-xl transition-all pressable',
                          mobileTab === 'radar' ? 'text-accent-cyan font-semibold' : 'text-zinc-400 hover:text-zinc-200'
                        )}
                      >
                        <div
                          className={cn(
                            'p-1 rounded-lg transition-colors',
                            mobileTab === 'radar' ? 'bg-accent-cyan/15 text-accent-cyan' : 'text-zinc-400'
                          )}
                        >
                          <Compass className="w-4 h-4" />
                        </div>
                        <span className="text-[10px] mt-0.5 tracking-tight font-medium">Radar Map</span>
                      </button>
                    </div>
                  </nav>,
                  document.body
                )
              : null}
          </>
        );
      })()}

      {/* Event Details Inspection Modal */}
      <EventDetailsModal
        event={selectedEvent}
        isOpen={!!selectedEvent}
        onClose={() => setSelectedEvent(null)}
      />
    </div>
  );
}
