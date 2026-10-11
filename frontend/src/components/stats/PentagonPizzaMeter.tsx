'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import {
  Pizza,
  ChevronDown,
  ChevronUp,
  TrendingUp,
  TrendingDown,
  Minus,
  RefreshCw,
  Info,
  Settings,
} from 'lucide-react';
import { PizzaMeterData } from '@/lib/types';
import { getPizzaMeter } from '@/lib/api';
import { formatRelativeTime, cn } from '@/lib/utils';

interface PentagonPizzaMeterProps {
  onDisabled?: () => void;
}

export function PentagonPizzaMeter({ onDisabled }: PentagonPizzaMeterProps) {
  const [data, setData] = useState<PizzaMeterData | null>(null);
  const [loading, setLoading] = useState(true);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await getPizzaMeter();
      setData(res);
      if (res.enabled === false && onDisabled) {
        onDisabled();
      }
    } catch (err) {
      // In case backend is offline, maintain graceful fallback state
      console.warn('Pizza meter fetch failed, using fallback telemetry:', err);
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, [onDisabled]);

  useEffect(() => {
    fetchData();
    // Poll every 90 seconds
    const interval = setInterval(fetchData, 90000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // If explicitly disabled in settings, do not render
  if (data && data.enabled === false) {
    return null;
  }

  const handleManualRefresh = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsRefreshing(true);
    fetchData();
  };

  const level = data?.level ?? 1;
  const status = data?.status ?? 'NOMINAL';
  const score = data?.score ?? 18;
  const trend = data?.trend ?? 'stable';
  const multiplier = data?.baseline_multiplier ?? 1.05;

  // Level status badge style configuration
  const statusConfig = {
    NOMINAL: {
      label: 'NOMINAL',
      badgeClass: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/25',
      gaugeFillClass: 'bg-emerald-400',
    },
    ELEVATED: {
      label: 'ELEVATED',
      badgeClass: 'text-amber-400 bg-amber-500/10 border-amber-500/25',
      gaugeFillClass: 'bg-amber-400',
    },
    HIGH: {
      label: 'HIGH',
      badgeClass: 'text-orange-400 bg-orange-500/10 border-orange-500/25',
      gaugeFillClass: 'bg-orange-400',
    },
    CRITICAL: {
      label: 'CRITICAL',
      badgeClass: 'text-rose-400 bg-rose-500/15 border-rose-500/30 animate-pulse',
      gaugeFillClass: 'bg-rose-500',
    },
  }[status] || {
    label: 'NOMINAL',
    badgeClass: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/25',
    gaugeFillClass: 'bg-emerald-400',
  };

  return (
    <div className="card-slick rounded-xl overflow-hidden hairline-border transition-all">
      {/* Compact Primary Bar (Low profile, small visual footprint) */}
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3.5 py-2 flex items-center justify-between text-xs cursor-pointer select-none hover:bg-surface-raised/40 transition-colors"
      >
        {/* Left: Icon + Title + Region Chip */}
        <div className="flex items-center space-x-2.5">
          <div className="p-1 rounded bg-amber-500/10 border hairline-border border-amber-500/20 text-amber-400/90 flex items-center justify-center">
            <Pizza className="w-3.5 h-3.5" />
          </div>
          <div className="flex items-center space-x-2">
            <span className="text-[11px] font-mono font-semibold tracking-wider text-zinc-300 uppercase">
              Pentagon Pizza Meter
            </span>
            <span className="hidden sm:inline-block text-[10px] font-mono text-zinc-400 bg-surface-raised px-1.5 py-0.5 rounded hairline-border">
              Arlington DoD
            </span>
            <span
              className="hidden md:inline-block text-[9px] font-mono text-zinc-400 bg-surface-raised px-1.5 py-0.5 rounded hairline-border"
              title="Calibrated against historical normal business per hour"
            >
              Hourly Calibrated
            </span>
          </div>
        </div>

        {/* Center: Status Badge + 4-Segment Gauge + Metric */}
        <div className="flex items-center space-x-3">
          {/* Status Badge */}
          <span
            className={cn(
              'text-[10px] font-mono font-semibold px-2 py-0.5 rounded border tracking-wide uppercase',
              statusConfig.badgeClass
            )}
          >
            {loading ? 'SYNCING...' : statusConfig.label}
          </span>

          {/* 4-Segment Level Gauge */}
          <div className="hidden xs:flex items-center space-x-1" title={`Threat Level ${level}/4`}>
            {[1, 2, 3, 4].map((seg) => (
              <div
                key={seg}
                className={cn(
                  'w-2.5 h-1.5 rounded-[1px] transition-colors',
                  seg <= level ? statusConfig.gaugeFillClass : 'bg-zinc-800'
                )}
              />
            ))}
          </div>

          {/* Activity Percentage & Trend (Calibrated against hourly normal) */}
          <div
            className="hidden md:flex items-center space-x-1.5 text-zinc-400 font-mono text-[11px] tabular"
            title={`Current busyness: ${score}% (${multiplier.toFixed(1)}x normal business for this hour)`}
          >
            <span>{score}%</span>
            <span className="text-zinc-400">({multiplier.toFixed(1)}x)</span>
            {trend === 'increasing' && <TrendingUp className="w-3 h-3 text-amber-400" />}
            {trend === 'decreasing' && <TrendingDown className="w-3 h-3 text-emerald-400" />}
            {trend === 'stable' && <Minus className="w-3 h-3 text-zinc-400" />}
          </div>
        </div>

        {/* Right: Last updated + Refresh + Details Toggle + Settings */}
        <div className="flex items-center space-x-2">
          {data?.last_updated && (
            <span className="hidden lg:inline text-[10px] text-zinc-400 font-mono">
              {formatRelativeTime(data.last_updated)}
            </span>
          )}

          <button
            onClick={handleManualRefresh}
            className="p-1 rounded text-zinc-400 hover:text-zinc-300 hover:bg-surface-raised transition-colors"
            title="Refresh Pizza Index Telemetry"
          >
            <RefreshCw className={cn('w-3 h-3', isRefreshing && 'animate-spin text-zinc-300')} />
          </button>

          <Link
            href="/settings"
            onClick={(e) => e.stopPropagation()}
            className="p-1 rounded text-zinc-400 hover:text-zinc-300 hover:bg-surface-raised transition-colors"
            title="Configure Pentagon Pizza Meter in Settings"
          >
            <Settings className="w-3 h-3" />
          </Link>

          <button
            className="flex items-center space-x-1 text-[11px] text-zinc-400 hover:text-zinc-300 transition-colors pl-1"
            title={isExpanded ? 'Collapse inspection drawer' : 'Expand outlet telemetry'}
          >
            <span className="hidden sm:inline">Details</span>
            {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Expanded Inspection Drawer (Reveals breakdown only when requested) */}
      {isExpanded && (
        <div className="border-t border-white/5 bg-canvas-recessed/60 px-4 py-3 space-y-3 animate-in fade-in slide-in-from-top-1">
          {/* Outlets Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {(data?.locations || []).map((loc, idx) => {
              const statusColor =
                loc.status === 'Spike'
                  ? 'text-rose-400 bg-rose-500/10 border-rose-500/20'
                  : loc.status === 'Elevated'
                  ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                  : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20';

              return (
                <div
                  key={idx}
                  className="p-2.5 rounded-lg bg-surface-card hairline-border flex flex-col justify-between space-y-1.5"
                >
                  <div className="flex items-start justify-between">
                    <div className="space-y-0.5">
                      <div className="text-xs font-semibold text-zinc-200 truncate">{loc.name}</div>
                      <div className="text-[10px] text-zinc-400 truncate">{loc.address}</div>
                    </div>
                    <span
                      className={cn(
                        'text-[9px] font-mono font-semibold px-1.5 py-0.5 rounded border uppercase',
                        statusColor
                      )}
                      title={`Alert status calibrated against normal business for current hour`}
                    >
                      {loc.status}
                    </span>
                  </div>

                  <div className="flex items-center justify-between pt-1 border-t border-white/5 text-[11px] font-mono">
                    <span className="text-zinc-400">{loc.distance}</span>
                    <div className="flex items-center space-x-1 tabular">
                      <span className="text-zinc-300 font-medium">{loc.busyness}% busy</span>
                      {loc.normal_busyness !== undefined && (
                        <span
                          className="text-zinc-400 text-[10px]"
                          title={`Expected normal business for this hour: ${loc.normal_busyness}%`}
                        >
                          (norm: {loc.normal_busyness}%)
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Context footnote and trend history */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pt-1 border-t border-white/5 text-[11px] text-zinc-400">
            <div className="flex items-center space-x-1.5 max-w-xl">
              <Info className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
              <span>
                {data?.description ||
                  'Calibrated OSINT indicator: surges in late-night DoD-area orders historically correlate with crisis activations.'}
              </span>
            </div>

            {/* Sparkline trend indicators */}
            {data?.history && data.history.length > 0 && (
              <div className="flex items-center space-x-1 shrink-0 self-end sm:self-auto font-mono text-[10px]">
                <span className="text-zinc-400 mr-1">Trend:</span>
                {data.history.map((pt, i) => (
                  <div
                    key={i}
                    className="flex flex-col items-center"
                    title={`Calibrated Score: ${pt.score}%`}
                  >
                    <div
                      className={cn(
                        'w-2 rounded-[1px] transition-all',
                        pt.score >= 70
                          ? 'bg-rose-500'
                          : pt.score >= 40
                          ? 'bg-amber-400'
                          : 'bg-emerald-500/70'
                      )}
                      style={{ height: `${Math.max(4, Math.round((pt.score / 100) * 16))}px` }}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
