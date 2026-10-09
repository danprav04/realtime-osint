'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  Compass,
  Layers,
  Sparkles,
  ShieldCheck,
  Clock,
  RefreshCw,
  Filter,
  Activity,
  AlertTriangle,
  Server,
} from 'lucide-react';
import {
  EmbeddingsMapData,
  EmbeddingStatus,
  Theme,
} from '@/lib/types';
import {
  getEmbeddingsMap,
  triggerEmbeddingBackfill,
  getEmbeddingStatus,
  getThemes,
  getActiveTheme,
} from '@/lib/api';
import { getSocket, useSocketConnection } from '@/lib/socket';
import { RelevanceMap } from '@/components/map/RelevanceMap';
import { AppIcon } from '@/components/AppIcon';

export default function RelevanceMapPage() {
  const [mapData, setMapData] = useState<EmbeddingsMapData>({
    nodes: [],
    edges: [],
    clusters: [],
    stats: {
      total_nodes: 0,
      total_edges: 0,
      total_clusters: 0,
      active_model: 'gemini-embedding-001',
    },
  });

  const [status, setStatus] = useState<EmbeddingStatus | null>(null);
  const [themes, setThemes] = useState<Theme[]>([]);
  const [selectedTheme, setSelectedTheme] = useState<string>('all');
  const [timeRange, setTimeRange] = useState<string>('all');
  const [minScore, setMinScore] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isBackfilling, setIsBackfilling] = useState<boolean>(false);
  const [notification, setNotification] = useState<string | null>(null);

  const { isConnected } = useSocketConnection();

  // Load map and metadata
  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [mapRes, statusRes, themesRes] = await Promise.all([
        getEmbeddingsMap({
          theme_id: selectedTheme !== 'all' ? selectedTheme : undefined,
          time_range: timeRange !== 'all' ? timeRange : undefined,
          min_score: minScore > 0 ? minScore : undefined,
        }),
        getEmbeddingStatus().catch(() => null),
        getThemes().catch(() => []),
      ]);

      setMapData(mapRes);
      if (statusRes) setStatus(statusRes);
      if (themesRes) setThemes(themesRes);
    } catch (err) {
      console.error('Failed to load relevance map data:', err);
    } finally {
      setIsLoading(false);
    }
  }, [selectedTheme, timeRange, minScore]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Handle backfill
  const handleBackfill = async () => {
    setIsBackfilling(true);
    try {
      const res = await triggerEmbeddingBackfill(100);
      setNotification(`Embedded ${res.processed_count} articles into map successfully.`);
      setTimeout(() => setNotification(null), 4000);
      await loadData();
    } catch (err) {
      console.error('Backfill error:', err);
      setNotification('Backfill failed. Check console or API credentials.');
      setTimeout(() => setNotification(null), 4000);
    } finally {
      setIsBackfilling(false);
    }
  };

  // Real-time WebSocket event listener
  useEffect(() => {
    const socket = getSocket();
    if (!socket) return;

    const handleMapUpdate = () => {
      loadData();
    };

    socket.on('map:update', handleMapUpdate);
    return () => {
      socket.off('map:update', handleMapUpdate);
    };
  }, [loadData]);

  // Compute corroboration metric
  const corroboratedCount = mapData.nodes.filter(
    (n) => n.trustworthiness.score >= 60
  ).length;
  const corroborationRate =
    mapData.nodes.length > 0
      ? Math.round((corroboratedCount / mapData.nodes.length) * 100)
      : 0;

  return (
    <div className="space-y-4">
      {/* Page Header & Quick Metric Badges */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-2 hairline-b">
        <div className="flex items-center space-x-3">
          <AppIcon size={38} />
          <div>
            <div className="flex items-center space-x-2">
              <h2 className="text-lg font-semibold text-zinc-100 tracking-tight">
                Intelligence Relevance & Embeddings Map
              </h2>
              {isConnected && (
                <span className="flex items-center space-x-1 text-[10px] font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  <span>LIVE RELEVANCE</span>
                </span>
              )}
            </div>
            <p className="text-xs text-zinc-400">
              High-dimensional Gemini embeddings projected onto 2D spatial coordinates.
              Nearby articles cluster by content, time, and field to determine report trustworthiness.
            </p>
          </div>
        </div>

        {/* Top Metric Chips */}
        <div className="flex items-center space-x-2.5 text-xs">
          {/* Total Reports */}
          <div className="px-3 py-1.5 rounded-lg bg-surface-card border border-white/5 flex items-center space-x-2">
            <span className="text-zinc-400 text-[11px]">Mapped Reports:</span>
            <span className="font-semibold text-zinc-100 font-mono">
              {mapData.nodes.length}
            </span>
          </div>

          {/* Corroboration Ratio */}
          <div className="px-3 py-1.5 rounded-lg bg-surface-card border border-white/5 flex items-center space-x-2">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-zinc-400 text-[11px]">Corroborated:</span>
            <span className="font-semibold text-emerald-400 font-mono">
              {corroborationRate}%
            </span>
          </div>

          {/* Clusters Detected */}
          <div className="px-3 py-1.5 rounded-lg bg-surface-card border border-white/5 flex items-center space-x-2">
            <Layers className="w-3.5 h-3.5 text-accent-cyan" />
            <span className="text-zinc-400 text-[11px]">Clusters:</span>
            <span className="font-semibold text-accent-cyan font-mono">
              {mapData.clusters.length}
            </span>
          </div>
        </div>
      </div>

      {/* Filter Ribbon */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-2.5 rounded-xl bg-surface-card/60 border border-white/5 text-xs">
        <div className="flex flex-wrap items-center gap-3">
          {/* Theme Selector */}
          <div className="flex items-center space-x-1.5">
            <Layers className="w-3.5 h-3.5 text-zinc-400" />
            <span className="text-zinc-400">Theme:</span>
            <select
              value={selectedTheme}
              onChange={(e) => setSelectedTheme(e.target.value)}
              className="bg-surface-raised border border-white/10 rounded-md px-2 py-1 text-xs text-zinc-200 focus:outline-none focus:border-accent-cyan/50"
            >
              <option value="all">All Topics</option>
              {themes.map((t) => (
                <option key={t.id} value={t.slug || t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>

          {/* Time Window */}
          <div className="flex items-center space-x-1.5">
            <Clock className="w-3.5 h-3.5 text-zinc-400" />
            <span className="text-zinc-400">Window:</span>
            <select
              value={timeRange}
              onChange={(e) => setTimeRange(e.target.value)}
              className="bg-surface-raised border border-white/10 rounded-md px-2 py-1 text-xs text-zinc-200 focus:outline-none focus:border-accent-cyan/50"
            >
              <option value="all">All Available</option>
              <option value="6h">Last 6 Hours</option>
              <option value="24h">Last 24 Hours</option>
              <option value="3d">Last 3 Days</option>
              <option value="7d">Last 7 Days</option>
            </select>
          </div>

          {/* Minimum Importance Score */}
          <div className="flex items-center space-x-1.5">
            <Filter className="w-3.5 h-3.5 text-zinc-400" />
            <span className="text-zinc-400">Min Score:</span>
            <select
              value={minScore}
              onChange={(e) => setMinScore(Number(e.target.value))}
              className="bg-surface-raised border border-white/10 rounded-md px-2 py-1 text-xs text-zinc-200 focus:outline-none focus:border-accent-cyan/50"
            >
              <option value={0}>All Scores (1-10)</option>
              <option value={4}>Medium+ (4+)</option>
              <option value={7}>High Priority (7+)</option>
              <option value={9}>Critical Only (9+)</option>
            </select>
          </div>
        </div>

        {/* Active Model Indicator */}
        <div className="flex items-center space-x-1.5 text-[11px] text-zinc-400 font-mono">
          <Server className="w-3 h-3 text-accent-cyan" />
          <span>Active Model:</span>
          <span className="text-zinc-200 font-medium">
            {mapData.stats?.active_model || 'gemini-embedding-001'}
          </span>
        </div>
      </div>

      {/* Notification Toast */}
      {notification && (
        <div className="p-2.5 rounded-lg bg-accent-cyan/10 border border-accent-cyan/30 text-xs text-accent-cyan flex items-center space-x-2 animate-fade-in">
          <Sparkles className="w-4 h-4 shrink-0" />
          <span>{notification}</span>
        </div>
      )}

      {/* Interactive Map Visualizer */}
      <RelevanceMap
        data={mapData}
        isLoading={isLoading}
        onRefresh={loadData}
        onBackfill={handleBackfill}
        isBackfilling={isBackfilling}
      />
    </div>
  );
}
