'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Search,
  SlidersHorizontal,
  Flame,
  Radio,
  RefreshCw,
  Pause,
  Play,
  X,
  AlertTriangle,
  Sparkles,
} from 'lucide-react';
import { NormalizedEvent, DashboardStats, Theme } from '@/lib/types';
import { getEvents, getStats, getActiveTheme } from '@/lib/api';
import { useRealtimeFeed } from '@/lib/socket';
import { StatCards } from '@/components/stats/StatCards';
import { PentagonPizzaMeter } from '@/components/stats/PentagonPizzaMeter';
import { EventCard } from '@/components/timeline/EventCard';
import { EventDetailsModal } from '@/components/timeline/EventDetailsModal';
import { SourceBadge } from '@/components/timeline/SourceBadge';
import { cn } from '@/lib/utils';

export default function DashboardPage() {
  const [events, setEvents] = useState<NormalizedEvent[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [activeTheme, setActiveTheme] = useState<Theme | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPaused, setIsPaused] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<NormalizedEvent | null>(null);
  const [criticalToast, setCriticalToast] = useState<any | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSource, setSelectedSource] = useState<string>('all');
  const [minScore, setMinScore] = useState<number>(0);

  // Load initial data
  const fetchData = useCallback(async () => {
    try {
      const [eventsData, statsData, themeData] = await Promise.all([
        getEvents({ limit: 60 }),
        getStats(),
        getActiveTheme().catch(() => null),
      ]);
      setEvents(eventsData || []);
      setStats(statsData || null);
      if (themeData) setActiveTheme(themeData);
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Real-time Socket.IO feed listener
  useRealtimeFeed({
    themeId: activeTheme?.id,
    onNewEvent: (newEvent) => {
      if (isPaused) return;
      setEvents((prev) => {
        // Optimistic deduplication by event id
        if (prev.some((e) => e.id === newEvent.id)) return prev;
        return [newEvent, ...prev.slice(0, 99)];
      });
      // Increment total count in stats
      setStats((prev) => {
        if (!prev) return null;
        const isHigh = (newEvent.importance_score ?? 0) >= 7;
        return {
          ...prev,
          total_events: prev.total_events + 1,
          high_priority_events: isHigh ? prev.high_priority_events + 1 : prev.high_priority_events,
          last_updated: newEvent.published_at || new Date().toISOString(),
        };
      });
    },
    onCriticalAlert: (alertData) => {
      setCriticalToast(alertData);
      setTimeout(() => {
        setCriticalToast(null);
      }, 7000);
    },
    onStatsUpdate: (updatedStats) => {
      setStats(updatedStats);
    },
  });

  // Filtered timeline stream
  const filteredEvents = useMemo(() => {
    return events.filter((ev) => {
      // Source filter
      if (selectedSource !== 'all') {
        const type = (ev.source_type || '').toLowerCase();
        if (selectedSource === 'ais' && !type.includes('ais')) return false;
        if (selectedSource !== 'ais' && type !== selectedSource) return false;
      }

      // Min score filter
      const score = ev.importance_score ?? 0;
      if (minScore > 0 && score < minScore) {
        return false;
      }

      // Search query filter
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
  }, [events, selectedSource, minScore, searchQuery]);

  const sourceFilters = [
    { id: 'all', label: 'All Sources' },
    { id: 'rss', label: 'RSS' },
    { id: 'telegram', label: 'Telegram' },
    { id: 'newsapi', label: 'News APIs' },
    { id: 'reddit', label: 'Reddit' },
    { id: 'ais', label: 'Maritime AIS' },
  ];

  return (
    <div className="space-y-6">
      {/* Critical Flash Alert Banner */}
      {criticalToast && (
        <div className="relative p-4 rounded-xl bg-rose-500/15 border border-rose-500/40 shadow-alert flex items-start space-x-3 animate-in fade-in slide-in-from-top-4">
          <div className="p-2 rounded-lg bg-rose-500/20 text-rose-300">
            <Flame className="w-5 h-5 animate-pulse" />
          </div>
          <div className="flex-1 space-y-1">
            <div className="flex items-center space-x-2">
              <span className="text-xs font-bold font-mono tracking-wider text-rose-400 uppercase">
                CRITICAL FLASH ALERT • SCORE {criticalToast.importance_score || 9}/10
              </span>
              <span className="text-xs text-zinc-400 font-mono">
                {criticalToast.source_name || 'REAL-TIME OSINT'}
              </span>
            </div>
            <p className="text-sm font-semibold text-zinc-100">
              {criticalToast.title || criticalToast.headline}
            </p>
            <p className="text-xs text-zinc-300">{criticalToast.summary}</p>
          </div>
          <button
            onClick={() => setCriticalToast(null)}
            className="text-zinc-400 hover:text-zinc-200 p-1"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* 4 Summary Stat Cards */}
      <StatCards stats={stats} loading={loading} />

      {/* Pentagon Pizza Meter (Unobtrusive OSINT Info Source) */}
      <PentagonPizzaMeter />

      {/* Filter and Control Toolbar */}
      <div className="card-slick rounded-xl p-4 space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          {/* Search Input */}
          <div className="relative flex-1 max-w-md">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search live intelligence, keywords, entities..."
              className="w-full rounded-lg bg-canvas-recessed pl-9 pr-3.5 py-2 text-xs text-zinc-100 placeholder-zinc-500 hairline-border focus:outline-none focus:border-accent-cyan"
            />
            <Search className="absolute left-3 top-2.5 w-3.5 h-3.5 text-zinc-500" />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-3 top-2.5 text-zinc-500 hover:text-zinc-300"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Controls: Refresh, Pause Stream, Min Score */}
          <div className="flex flex-wrap items-center gap-3">
            {/* Score Slider */}
            <div className="flex items-center space-x-2 bg-canvas-recessed px-3 py-1.5 rounded-lg hairline-border text-xs tabular">
              <span className="text-zinc-400 text-[11px] font-medium">Min Score:</span>
              <input
                type="range"
                min="0"
                max="10"
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                className="w-20 accent-accent-cyan cursor-pointer"
              />
              <span className="font-mono font-bold text-accent-cyan text-xs">
                {minScore > 0 ? `≥${minScore}` : 'All'}
              </span>
            </div>

            {/* Pause / Resume Stream Button */}
            <button
              onClick={() => setIsPaused(!isPaused)}
              className={cn(
                'inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium hairline-border transition-colors pressable',
                isPaused
                  ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
                  : 'bg-surface-raised text-zinc-300 hover:text-white'
              )}
              title={isPaused ? 'Resume real-time event stream' : 'Pause stream temporarily'}
            >
              {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              <span>{isPaused ? 'Stream Paused' : 'Live Stream'}</span>
            </button>

            {/* Refresh Button */}
            <button
              onClick={fetchData}
              className="p-1.5 rounded-lg bg-surface-raised hairline-border text-zinc-400 hover:text-zinc-200 transition-colors pressable"
              title="Refresh timeline from API"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Source Pills row */}
        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-white/5">
          <span className="text-[11px] font-medium text-zinc-500 mr-1 uppercase tracking-wider">
            Filter:
          </span>
          {sourceFilters.map((sf) => {
            const isSelected = selectedSource === sf.id;
            return (
              <button
                key={sf.id}
                onClick={() => setSelectedSource(sf.id)}
                className={cn(
                  'px-3 py-1 rounded-md text-xs font-medium transition-all hairline-border pressable',
                  isSelected
                    ? 'bg-surface-raised text-accent-cyan border-accent-cyan/40 shadow-sm'
                    : 'bg-canvas-recessed text-zinc-400 hover:text-zinc-200'
                )}
              >
                {sf.label}
              </button>
            );
          })}

          <div className="ml-auto text-xs font-mono text-zinc-500 tabular">
            Showing <strong className="text-zinc-300 font-semibold">{filteredEvents.length}</strong> of{' '}
            {events.length} events
          </div>
        </div>
      </div>

      {/* Live Timeline Stream */}
      <div className="space-y-3">
        {loading ? (
          // Loading Skeletons
          <div className="space-y-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="card-slick rounded-xl p-5 space-y-3 animate-pulse">
                <div className="h-4 bg-zinc-800 rounded w-1/4" />
                <div className="h-6 bg-zinc-800 rounded w-3/4" />
                <div className="h-4 bg-zinc-800 rounded w-full" />
              </div>
            ))}
          </div>
        ) : filteredEvents.length === 0 ? (
          // Empty State
          <div className="card-slick rounded-xl p-12 text-center space-y-3">
            <div className="p-3 mx-auto w-12 h-12 rounded-xl bg-surface-raised hairline-border flex items-center justify-center text-zinc-500">
              <Radio className="w-6 h-6" />
            </div>
            <h3 className="text-base font-semibold text-zinc-200">No Intelligence Events Match Filters</h3>
            <p className="text-xs text-zinc-500 max-w-sm mx-auto">
              Try relaxing your search query or minimum importance score filter, or wait for background ingestion engines to capture new events.
            </p>
            {(searchQuery || selectedSource !== 'all' || minScore > 0) && (
              <button
                onClick={() => {
                  setSearchQuery('');
                  setSelectedSource('all');
                  setMinScore(0);
                }}
                className="px-3.5 py-1.5 rounded-lg bg-surface-raised hairline-border text-xs text-zinc-300 hover:text-white"
              >
                Clear All Filters
              </button>
            )}
          </div>
        ) : (
          // Event Cards List
          <div className="grid grid-cols-1 gap-3.5">
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

      {/* Event Details Inspection Modal */}
      <EventDetailsModal
        event={selectedEvent}
        isOpen={!!selectedEvent}
        onClose={() => setSelectedEvent(null)}
      />
    </div>
  );
}
