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
} from 'lucide-react';
import { DashboardStats, NormalizedEvent } from '@/lib/types';
import { getStats, getEvents } from '@/lib/api';
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
  const [loading, setLoading] = useState(true);
  const [selectedEvent, setSelectedEvent] = useState<NormalizedEvent | null>(null);

  useEffect(() => {
    async function loadData() {
      try {
        const [statsData, eventsData] = await Promise.all([
          getStats(),
          getEvents({ min_score: 7, limit: 10 }),
        ]);
        setStats(statsData);
        setHighPriorityEvents(eventsData || []);
      } catch (err) {
        console.error('Failed to load trends data:', err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

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

      <EventDetailsModal
        event={selectedEvent}
        isOpen={!!selectedEvent}
        onClose={() => setSelectedEvent(null)}
      />
    </div>
  );
}
