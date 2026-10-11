'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import Link from 'next/link';
import {
  Sparkles,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  RefreshCw,
  Clock,
  Layers,
  Compass,
  Flame,
  Activity,
  Copy,
  Check,
  Eye,
  Tag,
  MapPin,
  Radio,
  Server,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Crosshair,
  TrendingUp,
  Pizza,
} from 'lucide-react';
import {
  SituationSummaryData,
  SummaryCitationEvent,
  SummaryClusterBreakdown,
  NormalizedEvent,
  PizzaMeterData,
  Theme,
} from '@/lib/types';
import {
  getSituationSummary,
  generateSituationSummary,
  getPizzaMeter,
  getThemes,
} from '@/lib/api';
import { useRealtimeFeed, useSocketConnection } from '@/lib/socket';
import { EventDetailsModal } from '@/components/timeline/EventDetailsModal';
import { ImportanceBadge } from '@/components/timeline/ImportanceBadge';
import { SourceBadge } from '@/components/timeline/SourceBadge';
import { AppIcon } from '@/components/AppIcon';
import { cn, formatRelativeTime, formatDateTime } from '@/lib/utils';

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
    score_rationale: cit.score_rationale || `Embedding Trustworthiness: ${cit.trustworthiness_score}% (${cit.trustworthiness_label})`,
    summary: cit.summary,
    headline: cit.headline || cit.title,
    entities_json: cit.entities_json || [],
    location_json: cit.location_json || null,
    is_analyzed: true,
    published_at: cit.published_at,
    ingested_at: cit.ingested_at,
  };
}

export default function AISummaryPage() {
  const [summary, setSummary] = useState<SituationSummaryData | null>(null);
  const [livePizzaMeter, setLivePizzaMeter] = useState<PizzaMeterData | null | undefined>(undefined);
  const [themes, setThemes] = useState<Theme[]>([]);
  const [selectedTheme, setSelectedTheme] = useState<string>('active');
  const [timeRange, setTimeRange] = useState<string>('all');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRegenerating, setIsRegenerating] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedMarkdown, setCopiedMarkdown] = useState<boolean>(false);
  const [selectedEvent, setSelectedEvent] = useState<NormalizedEvent | null>(null);
  const [expandedClusters, setExpandedClusters] = useState<Record<number, boolean>>({});
  const [newEventsCount, setNewEventsCount] = useState<number>(0);

  const { isConnected } = useSocketConnection();

  // Load summary data
  const loadSummary = useCallback(
    async (forceRefresh: boolean = false) => {
      if (forceRefresh) {
        setIsRegenerating(true);
      } else {
        setIsLoading(true);
      }
      setError(null);

      try {
        const themeParam = selectedTheme === 'active' ? undefined : selectedTheme;
        const [summaryRes, themesRes, pizzaRes] = await Promise.all([
          forceRefresh
            ? generateSituationSummary({ theme_id: themeParam, time_range: timeRange })
            : getSituationSummary({ theme_id: themeParam, time_range: timeRange }),
          getThemes().catch(() => []),
          getPizzaMeter().catch(() => undefined),
        ]);

        setSummary(summaryRes);
        if (pizzaRes !== undefined) {
          setLivePizzaMeter(pizzaRes);
        }
        setNewEventsCount(0);
        if (themesRes && themesRes.length > 0) {
          setThemes(themesRes);
        }
      } catch (err: any) {
        console.error('Failed to load AI situation summary:', err);
        setError(err?.message || 'Unable to synthesize situational summary.');
      } finally {
        setIsLoading(false);
        setIsRegenerating(false);
      }
    },
    [selectedTheme, timeRange]
  );

  useEffect(() => {
    loadSummary(false);
  }, [loadSummary]);

  // Listen for live WebSocket events arriving after summary was generated
  useRealtimeFeed({
    onNewEvent: () => {
      setNewEventsCount((prev) => prev + 1);
    },
  });

  // Lookup map of all citation events across clusters & outliers for quick modal inspection
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

  const handleInspectCitation = (cit: SummaryCitationEvent) => {
    setSelectedEvent(citationToNormalizedEvent(cit, summary?.theme_id || '1'));
  };

  const toggleClusterExpand = (clusterId: number) => {
    setExpandedClusters((prev) => ({
      ...prev,
      [clusterId]: !prev[clusterId],
    }));
  };

  const handleCopyMarkdown = () => {
    if (!summary) return;
    const lines: string[] = [
      `# OSINT SITUATIONAL BRIEFING (SITREP) — ${summary.theme_name.toUpperCase()}`,
      `- **Generated:** ${summary.generated_at}`,
      `- **Threat Trajectory:** ${summary.threat_trajectory} (Level ${summary.threat_level_score}/10)`,
      `- **Reports Analyzed:** ${summary.telemetry.total_events} (${summary.telemetry.corroboration_rate_pct}% Multi-Source Corroborated via ${summary.active_embedding_model})`,
      `- **Synthesis Engine:** ${summary.active_ai_model}`,
      '',
      `## Executive BLUF`,
      summary.executive_bluf,
      '',
      `## Strategic & Telemetry Assessment`,
      summary.strategic_assessment,
      '',
      `## Key Verified Developments`,
      ...summary.key_developments.map((d) => `- ${d.bullet}`),
      '',
      `## Semantic Embedding Storyline Clusters (${summary.clusters.length})`,
      ...summary.clusters.flatMap((c) => [
        `### [Cluster #${c.cluster_id}] ${c.title} (${c.threat_level.toUpperCase()} | ${c.report_count} reports | Trust ${c.avg_trustworthiness_score}% ${c.trustworthiness_label})`,
        c.executive_summary,
        `- **Operational Implication:** ${c.operational_implication}`,
        `- **Sources:** ${c.independent_sources.join(', ')}`,
        '',
      ]),
      `## Unverified / Isolated Outlier Watch`,
      summary.unverified_outliers.assessment,
      '',
      `## 12-24h Tactical Watchlist Indicators`,
      ...summary.watchlist_indicators.map((w, i) => `${i + 1}. ${w}`),
    ];

    navigator.clipboard.writeText(lines.join('\n'));
    setCopiedMarkdown(true);
    setTimeout(() => setCopiedMarkdown(false), 2500);
  };

  const trajStyle = getTrajectoryStyles(summary?.threat_trajectory);
  const pizza =
    livePizzaMeter !== undefined
      ? livePizzaMeter?.enabled === false
        ? null
        : livePizzaMeter
      : summary?.telemetry?.pizza_meter;

  return (
    <div className="space-y-5">
      {/* Page Header & Action Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-3 hairline-b">
        <div className="flex items-center space-x-3">
          <AppIcon size={38} withBeacon />
          <div>
            <div className="flex items-center space-x-2.5">
              <h2 className="text-lg font-semibold text-zinc-100 tracking-tight">
                AI Situational Summary & Intelligence Fusion
              </h2>
              {isConnected && (
                <span className="flex items-center space-x-1 text-[10px] font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  <span>LIVE FUSION</span>
                </span>
              )}
              {summary && (
                <span
                  className={cn(
                    'text-[10px] font-mono px-2 py-0.5 rounded-full border',
                    summary.cached
                      ? 'bg-zinc-800/80 text-zinc-400 border-white/10'
                      : 'bg-accent-cyan/10 text-accent-cyan border-accent-cyan/30'
                  )}
                >
                  {summary.cached ? 'CACHED BRIEFING' : 'FRESH SYNTHESIS'}
                </span>
              )}
            </div>
            <p className="text-xs text-zinc-400">
              Multi-source executive SitRep synthesized from incoming report summaries, high-dimensional vector
              embeddings, semantic clusters, and live telemetry.
            </p>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center space-x-2">
          <button
            onClick={handleCopyMarkdown}
            disabled={!summary || isLoading}
            className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised hairline-border text-xs font-medium text-zinc-200 pressable transition-colors disabled:opacity-50"
            title="Copy Full SitRep as Markdown"
          >
            {copiedMarkdown ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-emerald-400">Copied SitRep</span>
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5 text-zinc-400" />
                <span>Export Markdown</span>
              </>
            )}
          </button>

          <button
            onClick={() => loadSummary(true)}
            disabled={isLoading || isRegenerating}
            className="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg bg-accent-cyan/15 hover:bg-accent-cyan/25 border border-accent-cyan/30 text-xs font-semibold text-accent-cyan pressable transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', isRegenerating && 'animate-spin')} />
            <span>{isRegenerating ? 'Synthesizing...' : 'Regenerate Briefing'}</span>
          </button>
        </div>
      </div>

      {/* Filter & Model Telemetry Ribbon */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-2.5 rounded-xl bg-surface-card/70 hairline-border text-xs">
        <div className="flex flex-wrap items-center gap-3">
          {/* Theme Selector */}
          <div className="flex items-center space-x-1.5">
            <Layers className="w-3.5 h-3.5 text-accent-amber" />
            <span className="text-zinc-400">Scope:</span>
            <select
              value={selectedTheme}
              onChange={(e) => setSelectedTheme(e.target.value)}
              className="bg-surface-raised border border-white/10 rounded-md px-2.5 py-1 text-xs text-zinc-200 focus:outline-none focus:border-accent-cyan/50"
            >
              <option value="active">Active Theme ({summary?.theme_name || 'Default'})</option>
              <option value="all">All Monitored Themes</option>
              {themes.map((t) => (
                <option key={t.id} value={t.slug || t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>

          {/* Time Window Selector */}
          <div className="flex items-center space-x-1.5">
            <Clock className="w-3.5 h-3.5 text-accent-cyan" />
            <span className="text-zinc-400">Time Window:</span>
            <select
              value={timeRange}
              onChange={(e) => setTimeRange(e.target.value)}
              className="bg-surface-raised border border-white/10 rounded-md px-2.5 py-1 text-xs text-zinc-200 focus:outline-none focus:border-accent-cyan/50"
            >
              <option value="all">All Available Intelligence</option>
              <option value="6h">Last 6 Hours (Tactical)</option>
              <option value="24h">Last 24 Hours (Daily Operational)</option>
              <option value="3d">Last 3 Days (72h Window)</option>
              <option value="7d">Last 7 Days (Weekly Strategic)</option>
            </select>
          </div>
        </div>

        {/* Active AI & Embedding Engine Badges */}
        <div className="flex flex-wrap items-center gap-3 text-[11px] font-mono text-zinc-400 tabular">
          <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded bg-surface-raised border border-white/5">
            <Sparkles className="w-3 h-3 text-accent-cyan" />
            <span>Synthesizer:</span>
            <span className="text-zinc-200 font-medium">
              {summary?.active_ai_model || 'Pipeline Summarizer'}
            </span>
          </div>
          <div className="flex items-center space-x-1.5 px-2 py-0.5 rounded bg-surface-raised border border-white/5">
            <Server className="w-3 h-3 text-emerald-400" />
            <span>Embeddings:</span>
            <span className="text-zinc-200 font-medium">
              {summary?.active_embedding_model || 'gemini-embedding-001'}
            </span>
          </div>
          {summary?.generated_at && (
            <div className="flex items-center space-x-1 text-zinc-400">
              <Clock className="w-3 h-3 text-zinc-500" />
              <span>Generated {formatRelativeTime(summary.generated_at)}</span>
            </div>
          )}
        </div>
      </div>

      {/* Live WebSocket New Intelligence Banner */}
      {newEventsCount > 0 && (
        <div className="flex items-center justify-between px-4 py-2.5 rounded-xl bg-accent-cyan/10 border border-accent-cyan/30 text-xs text-accent-cyan animate-fade-in">
          <div className="flex items-center space-x-2">
            <Radio className="w-4 h-4 animate-pulse shrink-0" />
            <span>
              <strong>{newEventsCount} new intelligence report(s)</strong> have been ingested and analyzed since this
              situational briefing was generated.
            </span>
          </div>
          <button
            onClick={() => loadSummary(true)}
            disabled={isRegenerating}
            className="px-3 py-1 rounded-md bg-accent-cyan/20 hover:bg-accent-cyan/30 border border-accent-cyan/40 text-accent-cyan font-semibold transition-colors"
          >
            Refresh Briefing Now
          </button>
        </div>
      )}

      {/* Error State */}
      {error && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={() => loadSummary(true)}
            className="px-2.5 py-1 rounded bg-rose-500/20 hover:bg-rose-500/30 text-rose-200 font-medium"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading Skeleton */}
      {isLoading && !summary ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {[1, 2, 3, 4].map((n) => (
              <div key={n} className="card-slick rounded-xl p-4 h-28 animate-pulse bg-surface-card/60" />
            ))}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-span-8 space-y-4">
              <div className="card-slick rounded-xl p-6 h-64 animate-pulse bg-surface-card/60" />
              <div className="card-slick rounded-xl p-6 h-80 animate-pulse bg-surface-card/60" />
            </div>
            <div className="lg:col-span-4 space-y-4">
              <div className="card-slick rounded-xl p-6 h-64 animate-pulse bg-surface-card/60" />
              <div className="card-slick rounded-xl p-6 h-64 animate-pulse bg-surface-card/60" />
            </div>
          </div>
        </div>
      ) : summary ? (
        <>
          {/* Top 4 Situational Posture & Embedding KPI Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* 1. Threat Trajectory & Level */}
            <div className={cn('card-slick rounded-xl p-4 flex flex-col justify-between border', trajStyle.border)}>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                  Threat Trajectory
                </span>
                <span
                  className={cn(
                    'inline-flex items-center space-x-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold border',
                    trajStyle.badge
                  )}
                >
                  <span className={cn('w-1.5 h-1.5 rounded-full', trajStyle.dot)} />
                  <span>{summary.threat_trajectory}</span>
                </span>
              </div>
              <div className="mt-3 flex items-baseline justify-between">
                <div>
                  <span className={cn('text-2xl font-semibold tracking-tight font-mono tabular', trajStyle.accent)}>
                    {summary.threat_level_score}
                  </span>
                  <span className="text-xs text-zinc-500 font-mono"> / 10</span>
                </div>
                <span className="text-[11px] text-zinc-400 font-mono tabular">
                  Peak Event: {summary.telemetry.max_importance_score}/10
                </span>
              </div>
              <div className="mt-2 text-[11px] text-zinc-400 truncate">
                Theme: <span className="text-zinc-200 font-medium">{summary.theme_name}</span>
              </div>
            </div>

            {/* 2. Embedding Corroboration Index */}
            <div className="card-slick rounded-xl p-4 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                  Embedding Corroboration
                </span>
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
              </div>
              <div className="mt-3 flex items-baseline justify-between">
                <div className="flex items-baseline space-x-1.5">
                  <span className="text-2xl font-semibold tracking-tight text-emerald-400 font-mono tabular">
                    {summary.telemetry.corroboration_rate_pct}%
                  </span>
                  <span className="text-[11px] text-zinc-400">verified</span>
                </div>
                <span className="text-[11px] font-mono text-zinc-400 tabular">
                  {summary.telemetry.cross_source_edges_count} similarity links
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-400 font-mono tabular">
                <span>
                  {summary.telemetry.corroborated_reports_count}/{summary.telemetry.total_events} corroborated
                </span>
                <span className="text-emerald-400/90">{summary.telemetry.embedding_coverage_pct}% embedded</span>
              </div>
            </div>

            {/* 3. Semantic Clusters & Critical Alerts */}
            <div className="card-slick rounded-xl p-4 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                  Story Clusters & Alerts
                </span>
                <Compass className="w-4 h-4 text-accent-cyan" />
              </div>
              <div className="mt-3 flex items-baseline justify-between">
                <div className="flex items-baseline space-x-1.5">
                  <span className="text-2xl font-semibold tracking-tight text-accent-cyan font-mono tabular">
                    {summary.telemetry.total_clusters}
                  </span>
                  <span className="text-[11px] text-zinc-400">clusters</span>
                </div>
                <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded bg-rose-500/10 border border-rose-500/20 text-rose-400 text-[11px] font-mono tabular">
                  <Flame className="w-3 h-3" />
                  <span>{summary.telemetry.critical_alerts_count} critical</span>
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px] text-zinc-400 font-mono tabular">
                <span>{summary.telemetry.total_events} reports analyzed</span>
                <span>{summary.telemetry.isolated_outliers_count} outliers</span>
              </div>
            </div>

            {/* 4. Operational Tempo & Defense Corridor */}
            <div className="card-slick rounded-xl p-4 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                  Operational Tempo
                </span>
                <Activity className="w-4 h-4 text-accent-amber" />
              </div>
              <div className="mt-3 flex items-baseline justify-between">
                <span className="text-xl font-semibold tracking-tight text-zinc-100">
                  {summary.telemetry.velocity_status}
                </span>
                <span className="text-[11px] font-mono text-zinc-400 tabular">
                  {summary.telemetry.events_last_6h} in 6h / {summary.telemetry.events_prev_18h} prev 18h
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px] font-mono tabular">
                {pizza ? (
                  <>
                    <span className="text-zinc-400 flex items-center space-x-1">
                      <Pizza className="w-3 h-3 text-amber-400" />
                      <span>Pizza Meter:</span>
                    </span>
                    <span
                      className={cn(
                        'font-semibold',
                        pizza.status === 'CRITICAL'
                          ? 'text-rose-400'
                          : pizza.status === 'HIGH' || pizza.status === 'ELEVATED'
                          ? 'text-amber-400'
                          : 'text-emerald-400'
                      )}
                    >
                      {pizza.status} ({pizza.baseline_multiplier}x)
                    </span>
                  </>
                ) : (
                  <>
                    <span className="text-zinc-400">Avg Importance:</span>
                    <span className="text-zinc-200">{summary.telemetry.avg_importance_score} / 10</span>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Main 2-Column Tactical Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {/* LEFT / MAIN COLUMN (8 cols) */}
            <div className="lg:col-span-8 space-y-6">
              {/* Executive BLUF SitRep & Strategic Assessment Card */}
              <div className="card-slick rounded-xl p-6 space-y-5 relative overflow-hidden">
                <div className="flex flex-wrap items-center justify-between gap-2 pb-3 hairline-b">
                  <div className="flex items-center space-x-2.5">
                    <div className="p-2 rounded-lg bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/25">
                      <Sparkles className="w-4 h-4" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-zinc-100 tracking-tight uppercase font-mono">
                        Executive Situational Briefing (BLUF)
                      </h3>
                      <p className="text-[11px] text-zinc-400">
                        Synthesized across {summary.telemetry.total_events} reports,{' '}
                        {summary.telemetry.total_clusters} embedding clusters, and{' '}
                        {summary.telemetry.cross_source_edges_count} corroboration links
                      </p>
                    </div>
                  </div>

                  <span className="text-[11px] font-mono text-zinc-400 tabular">
                    {formatDateTime(summary.generated_at)}
                  </span>
                </div>

                {/* Executive BLUF Paragraphs */}
                <div className="space-y-3 text-sm text-zinc-200 leading-relaxed">
                  {summary.executive_bluf.split('\n\n').map((para, idx) => (
                    <p key={idx} className="text-[13.5px] leading-relaxed text-zinc-200">
                      {para}
                    </p>
                  ))}
                </div>

                {/* Strategic & Telemetry Assessment Callout */}
                {summary.strategic_assessment && (
                  <div className="p-4 rounded-xl bg-surface-raised/70 border border-white/10 space-y-1.5">
                    <div className="flex items-center space-x-1.5 text-xs font-mono uppercase tracking-wider text-accent-cyan">
                      <TrendingUp className="w-3.5 h-3.5" />
                      <span>Cross-Cluster Dynamics & Telemetry Assessment</span>
                    </div>
                    <p className="text-xs text-zinc-300 leading-relaxed">
                      {summary.strategic_assessment}
                    </p>
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
                      <span className="text-[11px] text-zinc-500 font-mono">
                        Click Inspect to view source intelligence
                      </span>
                    </div>

                    <div className="space-y-2">
                      {summary.key_developments.map((dev, idx) => (
                        <div
                          key={idx}
                          className="flex items-start justify-between gap-3 p-3 rounded-lg bg-surface-card/90 hover:bg-surface-raised/80 hairline-border transition-colors"
                        >
                          <div className="flex items-start space-x-2.5 min-w-0">
                            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded bg-accent-cyan/10 border border-accent-cyan/25 text-[11px] font-mono text-accent-cyan tabular">
                              {idx + 1}
                            </span>
                            <div className="space-y-1 min-w-0">
                              <p className="text-xs text-zinc-200 leading-relaxed">{dev.bullet}</p>
                              <div className="flex flex-wrap items-center gap-2">
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
                                  <span
                                    className={cn(
                                      'text-[10px] font-mono px-1.5 py-0.5 rounded border',
                                      getTrustBadgeStyles(dev.trustworthiness_label)
                                    )}
                                  >
                                    {dev.trustworthiness_label}
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>

                          {dev.event_id && citationMap[dev.event_id] && (
                            <button
                              onClick={() => handleInspectEventId(dev.event_id)}
                              className="shrink-0 flex items-center space-x-1 px-2.5 py-1 rounded bg-surface-raised hover:bg-surface-overlay hairline-border text-[11px] font-medium text-accent-cyan transition-colors"
                            >
                              <Eye className="w-3 h-3" />
                              <span>Inspect</span>
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Semantic Embedding Storyline Clusters Section */}
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center space-x-2">
                    <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/25">
                      <Layers className="w-4 h-4" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-zinc-100 tracking-tight">
                        Semantic Embedding Storyline Clusters ({summary.clusters.length})
                      </h3>
                      <p className="text-xs text-zinc-400">
                        Reports grouped by high-dimensional vector proximity and weighted by cross-source corroboration
                      </p>
                    </div>
                  </div>

                  <Link
                    href="/map"
                    className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised hairline-border text-xs font-medium text-accent-cyan transition-colors"
                  >
                    <Compass className="w-3.5 h-3.5" />
                    <span>Explore 2D Relevance Map</span>
                  </Link>
                </div>

                {summary.clusters.length === 0 ? (
                  <div className="card-slick rounded-xl p-8 text-center space-y-2">
                    <p className="text-sm text-zinc-300 font-medium">
                      No multi-report semantic clusters formed in this time window.
                    </p>
                    <p className="text-xs text-zinc-500">
                      Try expanding the Time Window filter to &quot;All Available Intelligence&quot; or &quot;Last 7 Days&quot;.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {summary.clusters.map((cluster: SummaryClusterBreakdown) => {
                      const isExpanded = !!expandedClusters[cluster.cluster_id];
                      const visibleEvents = isExpanded ? cluster.events : cluster.events.slice(0, 3);

                      return (
                        <div
                          key={cluster.cluster_id}
                          className="card-slick rounded-xl p-5 space-y-4 transition-all"
                        >
                          {/* Cluster Header */}
                          <div className="flex flex-wrap items-start justify-between gap-3 pb-3 hairline-b">
                            <div className="space-y-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="px-2 py-0.5 rounded bg-accent-cyan/10 border border-accent-cyan/25 text-accent-cyan font-mono text-[11px] font-semibold tabular">
                                  CLUSTER #{cluster.cluster_id}
                                </span>
                                <span
                                  className={cn(
                                    'px-2 py-0.5 rounded border text-[10px] font-mono uppercase font-semibold',
                                    getThreatLevelBadge(cluster.threat_level)
                                  )}
                                >
                                  {cluster.threat_level} threat
                                </span>
                                <span
                                  className={cn(
                                    'inline-flex items-center space-x-1 px-2 py-0.5 rounded border text-[11px] font-mono tabular',
                                    getTrustBadgeStyles(cluster.trustworthiness_label)
                                  )}
                                >
                                  <ShieldCheck className="w-3 h-3" />
                                  <span>
                                    {cluster.avg_trustworthiness_score}% • {cluster.trustworthiness_label}
                                  </span>
                                </span>
                              </div>
                              <h4 className="text-base font-semibold text-zinc-100 tracking-tight pt-0.5">
                                {cluster.title}
                              </h4>
                            </div>

                            {/* Cluster Right Metrics */}
                            <div className="flex items-center space-x-3 text-xs font-mono tabular">
                              <div className="px-2.5 py-1 rounded bg-surface-raised border border-white/5 text-zinc-300">
                                <span className="text-zinc-500">Reports: </span>
                                <span className="font-semibold text-zinc-100">{cluster.report_count}</span>
                              </div>
                              <div className="px-2.5 py-1 rounded bg-surface-raised border border-white/5 text-zinc-300">
                                <span className="text-zinc-500">Peak Score: </span>
                                <span className="font-semibold text-amber-400">
                                  {cluster.max_importance_score}/10
                                </span>
                              </div>
                            </div>
                          </div>

                          {/* Cluster AI Executive Summary */}
                          <p className="text-xs sm:text-[13px] text-zinc-200 leading-relaxed">
                            {cluster.executive_summary}
                          </p>

                          {/* Operational Implication Box */}
                          {cluster.operational_implication && (
                            <div className="p-3 rounded-lg bg-surface-raised/60 border border-white/5 flex items-start space-x-2">
                              <Crosshair className="w-3.5 h-3.5 text-accent-amber shrink-0 mt-0.5" />
                              <div className="text-xs text-zinc-300">
                                <span className="font-semibold text-zinc-100">Operational Implication: </span>
                                {cluster.operational_implication}
                              </div>
                            </div>
                          )}

                          {/* Corroborating Sources & Entities Row */}
                          <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-[11px]">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="text-zinc-500 font-mono">
                                Sources ({cluster.independent_sources.length}):
                              </span>
                              {cluster.independent_sources.map((src) => (
                                <span
                                  key={src}
                                  className="px-2 py-0.5 rounded bg-surface-raised border border-white/5 text-zinc-300 font-medium"
                                >
                                  {src}
                                </span>
                              ))}
                            </div>

                            {cluster.top_entities.length > 0 && (
                              <div className="flex flex-wrap items-center gap-1">
                                {cluster.top_entities.slice(0, 4).map((ent) => (
                                  <span
                                    key={ent}
                                    className="px-2 py-0.5 rounded bg-accent-cyan/10 text-accent-cyan font-mono text-[10px]"
                                  >
                                    #{ent}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>

                          {/* Underlying Clustered Reports (Interactive Citations) */}
                          <div className="pt-2 space-y-2 border-t border-white/5">
                            <div className="flex items-center justify-between text-[11px] font-mono text-zinc-400">
                              <span>CORROBORATING CLUSTER REPORTS ({cluster.events.length})</span>
                              {cluster.events.length > 3 && (
                                <button
                                  onClick={() => toggleClusterExpand(cluster.cluster_id)}
                                  className="flex items-center space-x-1 text-accent-cyan hover:underline"
                                >
                                  <span>
                                    {isExpanded
                                      ? 'Show Fewer'
                                      : `Show All ${cluster.events.length} Reports`}
                                  </span>
                                  {isExpanded ? (
                                    <ChevronUp className="w-3 h-3" />
                                  ) : (
                                    <ChevronDown className="w-3 h-3" />
                                  )}
                                </button>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              {visibleEvents.map((ev) => (
                                <div
                                  key={ev.id}
                                  onClick={() => handleInspectCitation(ev)}
                                  className="group flex items-center justify-between gap-3 p-2.5 rounded-lg bg-surface-card hover:bg-surface-raised hairline-border cursor-pointer transition-colors"
                                >
                                  <div className="flex items-center space-x-2.5 min-w-0">
                                    <ImportanceBadge score={ev.importance_score} size="sm" />
                                    <SourceBadge sourceType={ev.source_type} sourceName={ev.source_name} />
                                    <div className="min-w-0">
                                      <p className="text-xs font-medium text-zinc-200 group-hover:text-accent-cyan truncate transition-colors">
                                        {ev.headline || ev.title}
                                      </p>
                                      <p className="text-[11px] text-zinc-400 line-clamp-1">
                                        {ev.summary}
                                      </p>
                                    </div>
                                  </div>

                                  <div className="flex items-center space-x-2 shrink-0">
                                    <span className="hidden sm:inline-block text-[10px] font-mono text-zinc-400 tabular">
                                      Trust {ev.trustworthiness_score}%
                                    </span>
                                    <span className="text-[10px] font-mono text-zinc-500 tabular">
                                      {formatRelativeTime(ev.published_at)}
                                    </span>
                                    <span className="px-2 py-0.5 rounded bg-surface-raised group-hover:bg-accent-cyan/20 text-[10px] font-mono text-accent-cyan transition-colors">
                                      Inspect
                                    </span>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Uncorroborated / Isolated Outlier Watch Section */}
              <div className="card-slick rounded-xl p-5 space-y-4 border border-amber-500/20">
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2.5">
                    <div className="p-2 rounded-lg bg-amber-500/10 text-amber-400 border border-amber-500/25">
                      <ShieldAlert className="w-4 h-4" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-zinc-100 tracking-tight">
                        Single-Source & Isolated Outlier Watch
                      </h3>
                      <p className="text-xs text-zinc-400">
                        Reports flagged by embedding vector distance as lacking independent multi-source corroboration
                      </p>
                    </div>
                  </div>
                  <span className="px-2.5 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/25 text-amber-400 text-xs font-mono tabular">
                    {summary.unverified_outliers.events.length} unverified
                  </span>
                </div>

                <p className="text-xs text-zinc-300 leading-relaxed bg-surface-raised/60 p-3.5 rounded-lg border border-white/5">
                  {summary.unverified_outliers.assessment}
                </p>

                {summary.unverified_outliers.events.length > 0 && (
                  <div className="space-y-2">
                    {summary.unverified_outliers.events.slice(0, 5).map((ev) => (
                      <div
                        key={ev.id}
                        onClick={() => handleInspectCitation(ev)}
                        className="flex items-center justify-between gap-3 p-2.5 rounded-lg bg-surface-card hover:bg-surface-raised hairline-border cursor-pointer transition-colors"
                      >
                        <div className="flex items-center space-x-2.5 min-w-0">
                          <ImportanceBadge score={ev.importance_score} size="sm" />
                          <SourceBadge sourceType={ev.source_type} sourceName={ev.source_name} />
                          <div className="min-w-0">
                            <p className="text-xs font-medium text-zinc-200 truncate">
                              {ev.headline || ev.title}
                            </p>
                            <p className="text-[11px] text-zinc-400 line-clamp-1">{ev.summary}</p>
                          </div>
                        </div>

                        <div className="flex items-center space-x-2 shrink-0">
                          <span
                            className={cn(
                              'text-[10px] font-mono px-2 py-0.5 rounded border',
                              getTrustBadgeStyles(ev.trustworthiness_label)
                            )}
                          >
                            {ev.trustworthiness_label} ({ev.trustworthiness_score}%)
                          </span>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleInspectCitation(ev);
                            }}
                            className="px-2 py-0.5 rounded bg-surface-raised text-[10px] font-mono text-accent-cyan"
                          >
                            Inspect
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* RIGHT SIDEBAR COLUMN (4 cols) */}
            <div className="lg:col-span-4 space-y-6">
              {/* 12-24h Tactical Watchlist Indicators */}
              <div className="card-slick rounded-xl p-5 space-y-4">
                <div className="flex items-center space-x-2 pb-2 hairline-b">
                  <div className="p-1.5 rounded-lg bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/25">
                    <Crosshair className="w-4 h-4" />
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
                      12–24h Tactical Watchlist
                    </h4>
                    <p className="text-[11px] text-zinc-400">
                      AI-identified triggers & escalation indicators
                    </p>
                  </div>
                </div>

                <ul className="space-y-2.5">
                  {summary.watchlist_indicators.map((indicator, idx) => (
                    <li
                      key={idx}
                      className="flex items-start space-x-2.5 p-3 rounded-lg bg-surface-raised/60 border border-white/5 text-xs text-zinc-200 leading-relaxed"
                    >
                      <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent-cyan/20 text-accent-cyan font-mono text-[10px] font-semibold tabular">
                        {idx + 1}
                      </span>
                      <span>{indicator}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Pentagon Pizza Meter Defense Corridor Telemetry (When Enabled) */}
              {pizza && (
                <div className="card-slick rounded-xl p-5 space-y-3.5">
                  <div className="flex items-center justify-between pb-2 hairline-b">
                    <div className="flex items-center space-x-2">
                      <div className="p-1.5 rounded-lg bg-amber-500/10 text-amber-400 border border-amber-500/25">
                        <Pizza className="w-4 h-4" />
                      </div>
                      <div>
                        <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
                          Pentagon Pizza Meter
                        </h4>
                        <p className="text-[11px] text-zinc-400">
                          Arlington DoD Corridor OSINT Indicator
                        </p>
                      </div>
                    </div>

                    <span
                      className={cn(
                        'px-2 py-0.5 rounded text-[10px] font-mono font-semibold border tabular',
                        pizza.status === 'CRITICAL'
                          ? 'bg-rose-500/15 text-rose-400 border-rose-500/30'
                          : pizza.status === 'HIGH' || pizza.status === 'ELEVATED'
                          ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                          : 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                      )}
                    >
                      {pizza.status} ({pizza.baseline_multiplier}x)
                    </span>
                  </div>

                  <p className="text-xs text-zinc-300 leading-relaxed">{pizza.description}</p>

                  {pizza.locations && pizza.locations.length > 0 && (
                    <div className="space-y-2 pt-1">
                      {pizza.locations.map((loc) => (
                        <div key={loc.name} className="space-y-1 text-[11px]">
                          <div className="flex items-center justify-between text-zinc-300">
                            <span className="truncate font-medium">{loc.name}</span>
                            <span className="font-mono text-zinc-400 tabular">
                              {loc.busyness}% (norm {loc.normal_busyness ?? 25}%)
                            </span>
                          </div>
                          <div className="w-full h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                            <div
                              className={cn(
                                'h-full rounded-full transition-all',
                                loc.status === 'Spike'
                                  ? 'bg-rose-500'
                                  : loc.status === 'Elevated'
                                  ? 'bg-amber-400'
                                  : 'bg-emerald-400'
                              )}
                              style={{ width: `${Math.min(100, loc.busyness)}%` }}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Active Entities & Geographic Hotspots */}
              <div className="card-slick rounded-xl p-5 space-y-4">
                <div className="flex items-center space-x-2 pb-2 hairline-b">
                  <div className="p-1.5 rounded-lg bg-accent-amber/10 text-accent-amber border border-accent-amber/25">
                    <Tag className="w-4 h-4" />
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
                      Extracted Entities & Hotspots
                    </h4>
                    <p className="text-[11px] text-zinc-400">
                      High-frequency actors & locations in current briefing
                    </p>
                  </div>
                </div>

                {/* Entities */}
                <div className="space-y-2">
                  <span className="text-[11px] font-mono uppercase text-zinc-400">
                    Top Named Entities
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {summary.telemetry.top_entities.length > 0 ? (
                      summary.telemetry.top_entities.map((ent) => (
                        <span
                          key={ent.name}
                          className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-md bg-surface-raised hairline-border text-xs text-zinc-200"
                        >
                          <span className="font-mono text-accent-cyan">#{ent.name}</span>
                          <span className="text-[10px] font-mono text-zinc-400 tabular">
                            ({ent.count})
                          </span>
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-zinc-500">No entities extracted yet.</span>
                    )}
                  </div>
                </div>

                {/* Locations */}
                {summary.telemetry.top_locations.length > 0 && (
                  <div className="space-y-2 pt-2 border-t border-white/5">
                    <span className="text-[11px] font-mono uppercase text-zinc-400 flex items-center space-x-1">
                      <MapPin className="w-3 h-3 text-rose-400" />
                      <span>Geographic Hotspots</span>
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {summary.telemetry.top_locations.map((loc) => (
                        <span
                          key={loc.name}
                          className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-md bg-surface-raised hairline-border text-xs text-zinc-200"
                        >
                          <span>{loc.name}</span>
                          <span className="text-[10px] font-mono text-zinc-400 tabular">
                            ({loc.count})
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Trend Categories */}
                {summary.telemetry.top_categories.length > 0 && (
                  <div className="space-y-2 pt-2 border-t border-white/5">
                    <span className="text-[11px] font-mono uppercase text-zinc-400">
                      Dominant Trend Categories
                    </span>
                    <div className="space-y-1.5">
                      {summary.telemetry.top_categories.map((cat) => (
                        <div
                          key={cat.name}
                          className="flex items-center justify-between text-xs px-2.5 py-1.5 rounded bg-surface-raised/60 border border-white/5"
                        >
                          <span className="text-zinc-200 truncate">{cat.name}</span>
                          <span className="font-mono text-[11px] text-accent-cyan tabular">
                            {cat.count} reports
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Source Mix Telemetry Card */}
              <div className="card-slick rounded-xl p-5 space-y-3.5">
                <div className="flex items-center justify-between pb-2 hairline-b">
                  <span className="text-xs font-semibold text-zinc-100 uppercase font-mono">
                    Source Intelligence Mix
                  </span>
                  <span className="text-[11px] font-mono text-zinc-400 tabular">
                    {summary.telemetry.total_events} total
                  </span>
                </div>

                <div className="space-y-2.5">
                  {Object.entries(summary.telemetry.source_breakdown).map(([srcType, count]) => {
                    const pct =
                      summary.telemetry.total_events > 0
                        ? Math.round((count / summary.telemetry.total_events) * 100)
                        : 0;
                    return (
                      <div key={srcType} className="space-y-1 text-xs">
                        <div className="flex items-center justify-between">
                          <span className="uppercase font-mono text-[11px] text-zinc-300">
                            {srcType}
                          </span>
                          <span className="font-mono text-[11px] text-zinc-400 tabular">
                            {count} ({pct}%)
                          </span>
                        </div>
                        <div className="w-full h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                          <div
                            className="h-full rounded-full bg-accent-cyan transition-all"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        </>
      ) : null}

      {/* Event Inspector Modal */}
      <EventDetailsModal
        event={selectedEvent}
        isOpen={!!selectedEvent}
        onClose={() => setSelectedEvent(null)}
      />
    </div>
  );
}
