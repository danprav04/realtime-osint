'use client';

import React from 'react';
import { BarChart3 } from 'lucide-react';

interface ScoreDistributionProps {
  distribution: {
    '1-3': number;
    '4-6': number;
    '7-8': number;
    '9-10': number;
    [key: string]: number;
  } | null;
}

export function ScoreDistributionChart({ distribution }: ScoreDistributionProps) {
  const buckets = [
    {
      key: '1-3',
      label: 'Routine (1-3)',
      desc: 'Standard unflagged data',
      color: 'bg-slate-500',
      textColor: 'text-slate-400',
      borderColor: 'border-slate-500/30',
      glow: '',
      count: distribution?.['1-3'] ?? 0,
    },
    {
      key: '4-6',
      label: 'Advisory (4-6)',
      desc: 'Moderate thematic relevance',
      color: 'bg-accent-cyan',
      textColor: 'text-accent-cyan',
      borderColor: 'border-accent-cyan/30',
      glow: '',
      count: distribution?.['4-6'] ?? 0,
    },
    {
      key: '7-8',
      label: 'High (7-8)',
      desc: 'Triggers notification alert',
      color: 'bg-amber-400',
      textColor: 'text-amber-400',
      borderColor: 'border-amber-400/30',
      glow: 'shadow-[0_0_8px_rgba(245,158,11,0.3)]',
      count: distribution?.['7-8'] ?? 0,
    },
    {
      key: '9-10',
      label: 'Critical (9-10)',
      desc: 'Immediate emergency alert',
      color: 'bg-rose-500',
      textColor: 'text-rose-400',
      borderColor: 'border-rose-500/40',
      glow: 'shadow-[0_0_12px_rgba(239,68,68,0.4)]',
      count: distribution?.['9-10'] ?? 0,
    },
  ];

  const total = buckets.reduce((acc, b) => acc + b.count, 0);
  const maxVal = Math.max(...buckets.map((b) => b.count), 1);

  return (
    <div className="card-slick rounded-xl p-5 flex flex-col justify-between">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 rounded-lg bg-amber-400/10 text-amber-400 border border-amber-400/20">
            <BarChart3 className="w-4 h-4" />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">
              Importance Score Distribution
            </h4>
            <p className="text-xs text-zinc-500">Tier breakdown across intelligence scale (1-10)</p>
          </div>
        </div>
        <span className="text-xs font-mono text-zinc-400 tabular">{total} events total</span>
      </div>

      <div className="space-y-4 pt-1">
        {buckets.map((bucket) => {
          const percent = total > 0 ? Math.round((bucket.count / total) * 100) : 0;
          const barWidthPercent = (bucket.count / maxVal) * 100;

          return (
            <div key={bucket.key} className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center space-x-2">
                  <span className={`font-semibold ${bucket.textColor}`}>{bucket.label}</span>
                  <span className="text-[11px] text-zinc-500 hidden sm:inline">• {bucket.desc}</span>
                </div>
                <div className="flex items-center space-x-2 font-mono tabular">
                  <span className="text-zinc-200 font-semibold">{bucket.count}</span>
                  <span className="text-zinc-500 text-[11px]">({percent}%)</span>
                </div>
              </div>

              {/* Progress track */}
              <div className="w-full h-2.5 rounded-full bg-canvas-recessed hairline-border overflow-hidden p-0.5">
                <div
                  className={`h-full rounded-full transition-all duration-300 ${bucket.color} ${bucket.glow}`}
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
