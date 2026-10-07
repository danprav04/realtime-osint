'use client';

import React from 'react';
import { Rss, Send, Newspaper, MessageSquare, Anchor, Radio, Share2 } from 'lucide-react';

interface SourceActivityProps {
  breakdown: {
    rss: number;
    telegram: number;
    newsapi: number;
    reddit: number;
    ais: number;
    [key: string]: number;
  } | null;
}

export function SourceActivityChart({ breakdown }: SourceActivityProps) {
  const sources = [
    {
      id: 'rss',
      label: 'RSS / Atom',
      icon: Rss,
      color: 'bg-amber-400',
      textColor: 'text-amber-400',
      count: breakdown?.rss ?? 0,
    },
    {
      id: 'telegram',
      label: 'Telegram MTProto',
      icon: Send,
      color: 'bg-sky-400',
      textColor: 'text-sky-400',
      count: breakdown?.telegram ?? 0,
    },
    {
      id: 'newsapi',
      label: 'News APIs & GDELT',
      icon: Newspaper,
      color: 'bg-emerald-400',
      textColor: 'text-emerald-400',
      count: breakdown?.newsapi ?? 0,
    },
    {
      id: 'reddit',
      label: 'Reddit AsyncPRAW',
      icon: MessageSquare,
      color: 'bg-rose-400',
      textColor: 'text-rose-400',
      count: breakdown?.reddit ?? 0,
    },
    {
      id: 'ais',
      label: 'Maritime AIS Tracking',
      icon: Anchor,
      color: 'bg-cyan-400',
      textColor: 'text-cyan-400',
      count: breakdown?.ais ?? 0,
    },
  ];

  const total = sources.reduce((acc, s) => acc + s.count, 0);
  const maxVal = Math.max(...sources.map((s) => s.count), 1);

  return (
    <div className="card-slick rounded-xl p-5 flex flex-col justify-between">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 rounded-lg bg-accent-indigo/10 text-accent-indigo border border-accent-indigo/20">
            <Share2 className="w-4 h-4" />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
              Source Plugin Ingestion Share
            </h4>
            <p className="text-xs text-zinc-500">Activity breakdown across 5 ingestion engines</p>
          </div>
        </div>
        <span className="text-xs font-mono text-zinc-400 tabular">{total} total items</span>
      </div>

      <div className="space-y-3.5 pt-1">
        {sources.map((source) => {
          const Icon = source.icon;
          const percent = total > 0 ? Math.round((source.count / total) * 100) : 0;
          const barWidthPercent = (source.count / maxVal) * 100;

          return (
            <div key={source.id} className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center space-x-2">
                  <Icon className={`w-3.5 h-3.5 ${source.textColor}`} />
                  <span className="font-medium text-zinc-300">{source.label}</span>
                </div>
                <div className="flex items-center space-x-2 font-mono tabular">
                  <span className="text-zinc-200 font-semibold">{source.count}</span>
                  <span className="text-zinc-500 text-[11px]">({percent}%)</span>
                </div>
              </div>

              {/* Progress track */}
              <div className="w-full h-2 rounded-full bg-canvas-recessed hairline-border overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-300 ${source.color}`}
                  style={{ width: `${Math.max(barWidthPercent, 2)}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
