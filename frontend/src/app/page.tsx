'use client';

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
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
  ArrowUpDown,
} from 'lucide-react';
import { NormalizedEvent, DashboardStats, Theme, AlertItem } from '@/lib/types';
import { getEvents, getStats, getActiveTheme, getSettings } from '@/lib/api';
import { useRealtimeFeed } from '@/lib/socket';
import { StatCards } from '@/components/stats/StatCards';
import { PentagonPizzaMeter } from '@/components/stats/PentagonPizzaMeter';
import { EventCard } from '@/components/timeline/EventCard';
import { EventDetailsModal } from '@/components/timeline/EventDetailsModal';
import { AlertWheel } from '@/components/timeline/AlertWheel';
import { SourceBadge } from '@/components/timeline/SourceBadge';
import { AppIcon } from '@/components/AppIcon';
import { cn, sortEvents, SortOption } from '@/lib/utils';

export default function DashboardPage() {
  const [events, setEvents] = useState<NormalizedEvent[]>([]);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [activeTheme, setActiveTheme] = useState<Theme | null>(null);
  const [loading, setLoading] = useState(true);
  const [isPaused, setIsPaused] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<NormalizedEvent | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [pizzaMeterEnabled, setPizzaMeterEnabled] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      const cached = localStorage.getItem('pizza_meter_enabled');
      if (cached !== null) return cached === 'true';
    }
    return true;
  });

  // Filters & Sorting
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSource, setSelectedSource] = useState<string>('all');
  const [minScore, setMinScore] = useState<number>(0);
  const [sortBy, setSortBy] = useState<SortOption>('newest');
  const sortByRef = useRef<SortOption>(sortBy);

  useEffect(() => {
    sortByRef.current = sortBy;
  }, [sortBy]);

  // Load initial data
  const fetchData = useCallback(async (sortOpt?: SortOption) => {
    const activeSort = sortOpt || sortByRef.current;
    try {
      const backendSortParam =
        activeSort === 'score_desc'
          ? 'score_desc'
          : activeSort === 'score_asc'
          ? 'score_asc'
          : activeSort === 'ingested_desc'
          ? 'ingested_desc'
          : activeSort === 'oldest'
          ? 'published_asc'
          : 'published_desc';

      const [eventsData, statsData, themeData, settingsData] = await Promise.all([
        getEvents({ limit: 100, sort_by: backendSortParam }),
        getStats(),
        getActiveTheme().catch(() => null),
        getSettings().catch(() => null),
      ]);
      const sorted = sortEvents(eventsData || [], activeSort);
      setEvents(sorted);
      setStats(statsData || null);
      if (themeData) setActiveTheme(themeData);
      if (settingsData && settingsData.pizza_meter_enabled !== undefined) {
        setPizzaMeterEnabled(settingsData.pizza_meter_enabled);
        if (typeof window !== 'undefined') {
          localStorage.setItem('pizza_meter_enabled', String(settingsData.pizza_meter_enabled));
        }
      }

      // Seed Alert Wheel with existing high-importance / critical events
      const threshold = themeData?.alert_threshold ?? 8;
      const initialAlerts: AlertItem[] = (eventsData || [])
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
          published_at: ev.published_at,
          timestamp: ev.published_at,
          created_at: ev.ingested_at,
        }));

      if (initialAlerts.length > 0) {
        setAlerts((prev) => {
          const existingIds = new Set(prev.map((a) => a.id || a.event_id));
          const toAdd = initialAlerts.filter((a) => !existingIds.has(a.id));
          return [...prev, ...toAdd];
        });
      }
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
      // Update stats: only increment count if this is genuinely a new card, and use ingestion heartbeat
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

      // Check if newly arrived event qualifies for the Alert Wheel
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
    },
    onCriticalAlert: (alertData) => {
      const item: AlertItem = {
        id: alertData.id || alertData.event_id || `alt-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        event_id: alertData.event_id || alertData.id,
        title: alertData.headline || alertData.title || 'Critical Alert',
        headline: alertData.headline || alertData.title,
        summary: alertData.summary || alertData.raw_text,
        importance_score: alertData.importance_score ?? 9,
        source_name: alertData.source_name || 'REAL-TIME OSINT',
        source_url: alertData.source_url,
        published_at: alertData.published_at || alertData.sent_at || alertData.timestamp || alertData.created_at || new Date().toISOString(),
        timestamp: alertData.timestamp || alertData.published_at || new Date().toISOString(),
        created_at: alertData.created_at || new Date().toISOString(),
        received_at: new Date().toISOString(),
      };
      setAlerts((prev) => {
        const filtered = prev.filter((a) => a.id !== item.id && a.event_id !== item.event_id);
        return [item, ...filtered].slice(0, 20);
      });
    },
    onStatsUpdate: (updatedStats) => {
      setStats(updatedStats);
    },
  });

  const handleSortChange = (newSort: SortOption) => {
    setSortBy(newSort);
    sortByRef.current = newSort;
    setEvents((prev) => sortEvents(prev, newSort));
    fetchData(newSort);
  };

  // Filtered timeline stream
  const filteredEvents = useMemo(() => {
    const list = events.filter((ev) => {
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

    return sortEvents(list, sortBy);
  }, [events, selectedSource, minScore, searchQuery, sortBy]);

  const sourceFilters = [
    { id: 'all', label: 'All Sources' },
    { id: 'rss', label: 'RSS' },
    { id: 'telegram', label: 'Telegram' },
    { id: 'newsapi', label: 'News APIs' },
    { id: 'reddit', label: 'Reddit' },
    { id: 'ais', label: 'Maritime AIS' },
  ];

  const handleDismissAlert = (alertId: string) => {
    setAlerts((prev) => prev.filter((a) => a.id !== alertId && a.event_id !== alertId));
  };

  const handleDismissAll = () => {
    setAlerts([]);
  };

  const handleOpenAlertDetails = (alert: AlertItem) => {
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
        published_at: alert.published_at || new Date().toISOString(),
        ingested_at: alert.created_at || new Date().toISOString(),
      });
    }
  };

  return (
    <div className="space-y-6">
      {/* Critical Flash Alert Wheel */}
      {alerts.length > 0 && (
        <AlertWheel
          alerts={alerts}
          onDismissAlert={handleDismissAlert}
          onDismissAll={handleDismissAll}
          onOpenDetails={handleOpenAlertDetails}
        />
      )}

      {/* 4 Summary Stat Cards */}
      <StatCards stats={stats} loading={loading} />

      {/* Pentagon Pizza Meter (Unobtrusive OSINT Info Source) */}
      {pizzaMeterEnabled && (
        <PentagonPizzaMeter onDisabled={() => setPizzaMeterEnabled(false)} />
      )}

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

          {/* Controls: Sort, Score Slider, Pause Stream, Refresh */}
          <div className="flex flex-wrap items-center gap-3">
            {/* Sort Control */}
            <div className="flex items-center space-x-2 bg-canvas-recessed px-2.5 py-1.5 rounded-lg hairline-border text-xs">
              <ArrowUpDown className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
              <span className="text-zinc-400 text-[11px] font-medium hidden sm:inline">Sort:</span>
              <select
                value={sortBy}
                onChange={(e) => handleSortChange(e.target.value as SortOption)}
                className="bg-transparent text-xs text-zinc-200 font-medium focus:outline-none cursor-pointer pr-1"
                aria-label="Sort timeline events"
              >
                <option value="newest" className="bg-[#141518] text-zinc-200">
                  Newest First (Time)
                </option>
                <option value="score_desc" className="bg-[#141518] text-zinc-200">
                  Highest Score (10 → 1)
                </option>
                <option value="score_asc" className="bg-[#141518] text-zinc-200">
                  Lowest Score (1 → 10)
                </option>
                <option value="ingested_desc" className="bg-[#141518] text-zinc-200">
                  Recently Received
                </option>
                <option value="oldest" className="bg-[#141518] text-zinc-200">
                  Oldest First
                </option>
              </select>
            </div>

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
              onClick={() => fetchData()}
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
            <div className="p-2 mx-auto w-16 h-16 rounded-2xl bg-surface-raised hairline-border flex items-center justify-center">
              <AppIcon size={52} animated />
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
