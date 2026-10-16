'use client';

import React, { useState, useEffect } from 'react';
import {
  BarChart3,
  TrendingUp,
  Flame,
  Tag,
  ShieldAlert,
  Clock,
  Sparkles,
  ExternalLink,
  Award,
  Archive,
  RefreshCw,
  Layers,
} from 'lucide-react';
import { DashboardStats, NormalizedEvent, SourceCredibilityItem, HistoricalRollup } from '@/lib/types';
import { getStats, getEvents, getSourceCredibility, getHistoricalRollups, triggerMemoryCompaction } from '@/lib/api';
import { VolumeChart } from '@/components/charts/VolumeChart';
import { ScoreDistributionChart } from '@/components/charts/ScoreDistributionChart';
import { SourceActivityChart } from '@/components/charts/SourceActivityChart';
import { EventDetailsModal } from '@/components/timeline/EventDetailsModal';
import { ImportanceBadge } from '@/components/timeline/ImportanceBadge';
import { SourceBadge } from '@/components/timeline/SourceBadge';
import { AIModelBadge } from '@/components/common/AIModelBadge';
import { AppIcon } from '@/components/AppIcon';
import { formatRelativeTime, getEventAIProvenance } from '@/lib/utils';

export default function TrendsPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [highPriorityEvents, setHighPriorityEvents] = useState<NormalizedEvent[]>([]);
  const [sourceCredibility, setSourceCredibility] = useState<SourceCredibilityItem[]>([]);
  const [historicalRollups, setHistoricalRollups] = useState<HistoricalRollup[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedEvent, setSelectedEvent] = useState<NormalizedEvent | null>(null);
  const [compacting, setCompacting] = useState(false);
  const [compactionMsg, setCompactionMsg] = useState<string | null>(null);

  useEffect(() => {
    async function loadData() {
      try {
        const [statsData, eventsData, credRes, rollupsRes] = await Promise.all([
          getStats(),
          getEvents({ min_score: 7, limit: 10 }),
          getSourceCredibility().catch(() => ({ status: 'ok', count: 0, sources: [] })),
          getHistoricalRollups({ limit: 10 }).catch(() => []),
        ]);
        setStats(statsData);
        setHighPriorityEvents(eventsData || []);
        if (credRes?.sources) setSourceCredibility(credRes.sources);
        if (rollupsRes) setHistoricalRollups(rollupsRes);
      } catch (err) {
        console.error('Failed to load trends data:', err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  const handleTriggerCompaction = async () => {
    setCompacting(true);
    setCompactionMsg(null);
    try {
      const res = await triggerMemoryCompaction(7);
      setCompactionMsg(`Compaction complete: ${res.rollups_created} rollups created, ${res.vectors_pruned} routine vectors pruned.`);
      const updatedRollups = await getHistoricalRollups({ limit: 10 });
      setHistoricalRollups(updatedRollups);
    } catch (err: any) {
      setCompactionMsg(`Compaction failed: ${err.message || 'Unknown error'}`);
    } finally {
      setCompacting(false);
    }
  };

  // Compute top entities from high priority events
  const entityCounts = React.useMemo(() => {
    const counts: Record<string, number> = {};
    highPriorityEvents.forEach((ev) => {
      (ev.entities_json || []).forEach((ent) => {
        counts[ent] = (counts[ent] || 0) + 1;
      });
    });
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 12);
  }, [highPriorityEvents]);

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 hairline-b">
        <div className="flex items-center space-x-3">
          <AppIcon size={38} />
          <div>
            <h2 className="text-lg font-semibold text-zinc-100 tracking-tight">
              Intelligence Trends & Threat Analytics
            </h2>
            <p className="text-xs text-zinc-400">
              Cross-source velocity metrics, score distribution, and hotspot cluster tracking
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2 text-xs font-mono text-zinc-400 tabular">
          <Clock className="w-3.5 h-3.5 text-zinc-500" />
          <span>Last Updated: {formatRelativeTime(stats?.last_updated)}</span>
        </div>
      </div>

      {/* 24h Volume Chart (Full width) */}
      <VolumeChart data={stats?.hourly_volume || []} />

      {/* 2 Column Grid: Score Distribution & Source Share */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ScoreDistributionChart distribution={stats?.score_distribution || null} />
        <SourceActivityChart breakdown={stats?.source_breakdown || null} />
      </div>

      {/* Strategic Threat Hotspots & Tracked Entities */}
      <div className="card-slick rounded-xl p-5 space-y-3">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 rounded-lg bg-accent-amber/10 text-accent-amber border border-accent-amber/20">
            <Tag className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
                Active Threat Entities & Regional Hotspots
              </h4>
              <AIModelBadge
                provenance={getEventAIProvenance(highPriorityEvents[0] as any, 'extraction')}
                size="xs"
                linkToSettings
              />
            </div>
            <p className="text-xs text-zinc-500">
              High-frequency entities detected across critical intelligence events
            </p>
          </div>
        </div>

        <div className="flex flex-wrap gap-2 pt-2">
          {entityCounts.length > 0 ? (
            entityCounts.map(([entity, count]) => (
              <span
                key={entity}
                className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-surface-raised hairline-border text-xs text-zinc-200"
              >
                <span className="font-mono text-accent-cyan font-medium">#{entity}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-zinc-800 text-zinc-400 font-mono tabular">
                  {count} mentions
                </span>
              </span>
            ))
          ) : (
            <span className="text-xs text-zinc-500">
              Entities will populate as multi-stage AI extraction processes live events.
            </span>
          )}
        </div>
      </div>

      {/* High-Impact Critical Events Table */}
      <div className="card-slick rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <div className="p-1.5 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/20">
              <Flame className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
                  Recent High-Priority Flash Events (Score ≥ 7)
                </h4>
                <AIModelBadge
                  provenance={getEventAIProvenance(highPriorityEvents[0] as any, 'scoring')}
                  size="xs"
                  linkToSettings
                />
              </div>
              <p className="text-xs text-zinc-500">Events that triggered automated alert evaluation</p>
            </div>
          </div>
          <span className="text-xs font-mono text-zinc-400 tabular">
            {highPriorityEvents.length} critical items
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-white/5 text-zinc-500 font-mono uppercase text-[11px]">
                <th className="pb-3 font-semibold">Priority</th>
                <th className="pb-3 font-semibold">Source</th>
                <th className="pb-3 font-semibold">Intelligence Headline</th>
                <th className="pb-3 font-semibold">Time</th>
                <th className="pb-3 font-semibold text-right">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {highPriorityEvents.map((ev) => (
                <tr key={ev.id} className="hover:bg-surface-card/60 transition-colors">
                  <td className="py-3 pr-4">
                    <ImportanceBadge score={ev.importance_score} size="sm" />
                  </td>
                  <td className="py-3 pr-4">
                    <SourceBadge sourceType={ev.source_type} sourceName={ev.source_name} />
                  </td>
                  <td className="py-3 pr-4 max-w-md">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <p className="font-semibold text-zinc-200 truncate">{ev.headline || ev.title}</p>
                      <AIModelBadge provenance={getEventAIProvenance(ev, 'summarization')} size="xs" linkToSettings />
                    </div>
                    <p className="text-[11px] text-zinc-400 line-clamp-1">{ev.summary}</p>
                  </td>
                  <td className="py-3 pr-4 font-mono text-zinc-400 tabular whitespace-nowrap">
                    {formatRelativeTime(ev.published_at)}
                  </td>
                  <td className="py-3 text-right">
                    <button
                      onClick={() => setSelectedEvent(ev)}
                      className="px-2.5 py-1 rounded bg-surface-raised hover:bg-surface-overlay hairline-border text-accent-cyan text-xs font-medium transition-colors"
                    >
                      Inspect
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Dynamic Source Credibility & Recency Calibration */}
      <div className="card-slick rounded-xl p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2 pb-2 hairline-b">
          <div className="flex items-center space-x-2">
            <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <Award className="w-4 h-4" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
                Dynamic Source Credibility & Calibration Leaderboard
              </h4>
              <p className="text-xs text-zinc-400">
                Continuous empirical source scoring weighted by continuous recency decay and independent corroboration
              </p>
            </div>
          </div>
          <span className="text-xs font-mono text-zinc-400 tabular">
            {sourceCredibility.length} tracked sources
          </span>
        </div>

        {sourceCredibility.length === 0 ? (
          <p className="text-xs text-zinc-500 py-3">No source credibility metrics calculated yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-white/5 text-zinc-500 font-mono uppercase text-[11px]">
                  <th className="pb-2.5 font-semibold">Source</th>
                  <th className="pb-2.5 font-semibold">Credibility Tier</th>
                  <th className="pb-2.5 font-semibold">Score</th>
                  <th className="pb-2.5 font-semibold">Corroboration</th>
                  <th className="pb-2.5 font-semibold">Avg Importance</th>
                  <th className="pb-2.5 font-semibold">Reports</th>
                  <th className="pb-2.5 font-semibold text-right">Recency Weight</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {sourceCredibility.map((src) => (
                  <tr key={src.source_name} className="hover:bg-surface-card/60 transition-colors">
                    <td className="py-2.5 pr-4 font-medium text-zinc-200">
                      <div className="flex items-center space-x-2">
                        <span>{src.source_name}</span>
                        <span className="text-[10px] uppercase font-mono text-zinc-500 bg-surface-raised px-1.5 py-0.5 rounded">
                          {src.source_type}
                        </span>
                      </div>
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-[11px]">
                      <span className="px-2 py-0.5 rounded bg-surface-raised hairline-border text-zinc-300">
                        {src.tier}
                      </span>
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-emerald-400 font-semibold tabular">
                      {src.credibility_score.toFixed(1)}/10
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-zinc-300 tabular">
                      {Math.round(src.corroboration_rate * 100)}%
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-amber-400 tabular">
                      {src.avg_importance_score.toFixed(1)}/10
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-zinc-400 tabular">
                      {src.total_reports}
                    </td>
                    <td className="py-2.5 text-right font-mono text-accent-cyan tabular">
                      {src.recency_weight.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Historical Memory Rollups & Compaction Engine */}
      <div className="card-slick rounded-xl p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 pb-2 hairline-b">
          <div className="flex items-center space-x-2">
            <div className="p-1.5 rounded-lg bg-accent-indigo/10 text-accent-indigo border border-accent-indigo/20">
              <Archive className="w-4 h-4" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
                Historical Memory Rollups & Compaction Engine
              </h4>
              <p className="text-xs text-zinc-400">
                Aged intelligence (&gt;7 days) compacted into semantic daily epochs to preserve long-term context while pruning routine vectors
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <button
              onClick={handleTriggerCompaction}
              disabled={compacting}
              className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-surface-raised hover:bg-surface-overlay hairline-border text-xs font-medium text-accent-cyan transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${compacting ? 'animate-spin' : ''}`} />
              <span>{compacting ? 'Compacting...' : 'Trigger Compaction'}</span>
            </button>
          </div>
        </div>

        {compactionMsg && (
          <div className="p-3 rounded-lg bg-accent-cyan/10 border border-accent-cyan/20 text-xs font-mono text-accent-cyan">
            {compactionMsg}
          </div>
        )}

        {historicalRollups.length === 0 ? (
          <div className="p-4 rounded-lg bg-surface-card/60 text-center space-y-1">
            <p className="text-xs text-zinc-400">
              No historical memory rollups generated yet. Click &quot;Trigger Compaction&quot; or wait for the automatic daily background task.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {historicalRollups.map((rollup) => (
              <div
                key={rollup.id}
                className="p-4 rounded-lg bg-surface-card hairline-border space-y-2.5"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-semibold text-accent-indigo">
                    Epoch: {rollup.epoch_key}
                  </span>
                  <span className="font-mono text-[10px] text-zinc-500 tabular">
                    {rollup.period_start?.slice(0, 10)} to {rollup.period_end?.slice(0, 10)}
                  </span>
                </div>

                <div className="flex items-center space-x-3 text-xs font-mono tabular">
                  <span className="text-zinc-300">
                    <strong className="text-zinc-100 font-semibold">{rollup.total_events}</strong> events
                  </span>
                  <span className="text-rose-400">
                    <strong className="font-semibold">{rollup.critical_events_count}</strong> critical
                  </span>
                  <span className="text-amber-400">
                    Peak: <strong className="font-semibold">{rollup.max_importance_score}/10</strong>
                  </span>
                  <span className="text-zinc-400">
                    Avg: {rollup.avg_importance_score.toFixed(1)}/10
                  </span>
                </div>

                <p className="text-xs text-zinc-300 leading-relaxed line-clamp-3">
                  {rollup.executive_summary}
                </p>

                {rollup.entity_counts_json && Object.keys(rollup.entity_counts_json).length > 0 && (
                  <div className="flex flex-wrap gap-1 pt-1">
                    {Object.entries(rollup.entity_counts_json).slice(0, 5).map(([ent, count]) => (
                      <span
                        key={ent}
                        className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-raised text-zinc-400"
                      >
                        #{ent} ({count})
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <EventDetailsModal
        event={selectedEvent}
        isOpen={!!selectedEvent}
        onClose={() => setSelectedEvent(null)}
      />
    </div>
  );
}
