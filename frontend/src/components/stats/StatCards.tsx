'use client';

import React from 'react';
import { Database, AlertTriangle, Radio, Clock } from 'lucide-react';
import { DashboardStats } from '@/lib/types';
import { formatRelativeTime } from '@/lib/utils';

interface StatCardsProps {
  stats: DashboardStats | null;
  loading?: boolean;
}

export function StatCards({ stats, loading = false }: StatCardsProps) {
  const totalEvents = stats?.total_events ?? 0;
  const highPriority = stats?.high_priority_events ?? 0;
  const activeSources = stats?.active_sources ?? 0;
  const lastUpdated = stats?.last_updated || stats?.last_update_timestamp;

  const highPriorityPercent = totalEvents > 0 ? Math.round((highPriority / totalEvents) * 100) : 0;

  const cards = [
    {
      title: 'Total Ingested Events',
      value: totalEvents.toLocaleString(),
      subtext: 'Normalized & AI-analyzed',
      icon: Database,
      accentColor: 'text-accent-cyan',
      iconBg: 'bg-accent-cyan/10 border-accent-cyan/20',
      badge: `${totalEvents > 0 ? 'STREAMING' : 'READY'}`,
      badgeColor: 'text-accent-cyan bg-accent-cyan/10',
    },
    {
      title: 'High Priority (Score ≥ 7)',
      value: highPriority.toLocaleString(),
      subtext: `${highPriorityPercent}% of all events`,
      icon: AlertTriangle,
      accentColor: 'text-amber-400',
      iconBg: 'bg-amber-400/10 border-amber-400/20',
      badge: highPriority > 0 ? 'ALERT TRIGGER' : 'NOMINAL',
      badgeColor: highPriority > 0 ? 'text-amber-300 bg-amber-500/15' : 'text-zinc-400 bg-zinc-800',
    },
    {
      title: 'Active Monitored Sources',
      value: activeSources.toString(),
      subtext: 'RSS, TG, News, Reddit, AIS',
      icon: Radio,
      accentColor: 'text-emerald-400',
      iconBg: 'bg-emerald-400/10 border-emerald-400/20',
      badge: '5 INGESTION ENGINES',
      badgeColor: 'text-emerald-400 bg-emerald-500/10',
    },
    {
      title: 'Last Ingestion Heartbeat',
      value: formatRelativeTime(lastUpdated),
      subtext: 'Pub/Sub stream latency < 1s',
      icon: Clock,
      accentColor: 'text-accent-indigo',
      iconBg: 'bg-accent-indigo/10 border-accent-indigo/20',
      badge: 'REALTIME',
      badgeColor: 'text-accent-indigo bg-accent-indigo/10',
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((card, idx) => {
        const Icon = card.icon;
        return (
          <div
            key={idx}
            className="card-slick rounded-xl p-4 flex flex-col justify-between relative overflow-hidden group"
          >
            {/* Top row */}
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                {card.title}
              </span>
              <div
                className={`p-2 rounded-lg border hairline-border ${card.iconBg} ${card.accentColor} transition-transform group-hover:scale-105`}
              >
                <Icon className="w-4 h-4" />
              </div>
            </div>

            {/* Value & subtext */}
            <div className="space-y-1">
              <div className="text-2xl sm:text-3xl font-semibold tracking-tight text-zinc-100 tabular">
                {loading ? <span className="text-zinc-600 animate-pulse">---</span> : card.value}
              </div>
              <div className="flex items-center justify-between pt-1">
                <span className="text-xs text-zinc-500 truncate">{card.subtext}</span>
                <span
                  className={`text-[10px] font-mono px-2 py-0.5 rounded font-semibold tracking-wide ${card.badgeColor}`}
                >
                  {card.badge}
                </span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
