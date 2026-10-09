'use client';

import React, { useState, useRef, useMemo, useEffect, useCallback } from 'react';
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  RefreshCw,
  Search,
  SlidersHorizontal,
  ShieldCheck,
  Radio,
  Flame,
  Layers,
  Sparkles,
  Info,
  Clock,
  Compass,
  Cpu,
} from 'lucide-react';
import {
  EmbeddingsMapData,
  MapNode,
  MapEdge,
  MapCluster,
  QuotaTracker,
} from '@/lib/types';
import { cn, formatRelativeTime } from '@/lib/utils';
import { EventInspector } from './EventInspector';

type ColorMode = 'trust' | 'source' | 'score';

interface RelevanceMapProps {
  data: EmbeddingsMapData;
  isLoading: boolean;
  onRefresh: () => void;
  onBackfill: () => void;
  isBackfilling: boolean;
}

export function RelevanceMap({
  data,
  isLoading,
  onRefresh,
  onBackfill,
  isBackfilling,
}: RelevanceMapProps) {
  // Navigation & Zoom state
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

  // Filter & Display state
  const [colorMode, setColorMode] = useState<ColorMode>('trust');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<MapNode | null>(null);
  const [minTrustScore, setMinTrustScore] = useState<number>(0);

  const containerRef = useRef<HTMLDivElement>(null);

  // Selected node object
  const selectedNode = useMemo(() => {
    return data.nodes.find((n) => n.id === selectedNodeId) || null;
  }, [data.nodes, selectedNodeId]);

  // Filtered nodes
  const filteredNodes = useMemo(() => {
    return data.nodes.filter((node) => {
      if (node.trustworthiness.score < minTrustScore) return false;
      return true;
    });
  }, [data.nodes, minTrustScore]);

  // Match set for search highlighting
  const searchMatches = useMemo(() => {
    if (!searchQuery.trim()) return null;
    const query = searchQuery.toLowerCase();
    const set = new Set<string>();
    filteredNodes.forEach((node) => {
      if (
        node.title.toLowerCase().includes(query) ||
        (node.headline && node.headline.toLowerCase().includes(query)) ||
        node.source_name.toLowerCase().includes(query) ||
        (node.summary && node.summary.toLowerCase().includes(query))
      ) {
        set.add(node.id);
      }
    });
    return set;
  }, [filteredNodes, searchQuery]);

  // Zoom handlers
  const handleZoomIn = () => setScale((s) => Math.min(s * 1.3, 4.5));
  const handleZoomOut = () => setScale((s) => Math.max(s / 1.3, 0.5));
  const handleResetView = () => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  };

  // Mouse pan drag handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).tagName.toLowerCase() === 'circle') return;
    setIsDragging(true);
    setDragStart({ x: e.clientX - offset.x, y: e.clientY - offset.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    setOffset({
      x: e.clientX - dragStart.x,
      y: e.clientY - dragStart.y,
    });
  };

  const handleMouseUp = () => setIsDragging(false);

  // Wheel zoom
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.88;
    setScale((s) => Math.min(Math.max(s * zoomFactor, 0.45), 5.0));
  };

  // Color mapper helper
  const getNodeFill = (node: MapNode) => {
    if (colorMode === 'trust') {
      const s = node.trustworthiness.score;
      if (s >= 80) return '#10b981'; // Emerald (High trust)
      if (s >= 60) return '#06b6d4'; // Cyan (Cross-checked)
      if (s >= 40) return '#f59e0b'; // Amber (Single-source unverified)
      return '#f43f5e'; // Rose (Outlier)
    }

    if (colorMode === 'source') {
      switch (node.source_type.toLowerCase()) {
        case 'telegram':
          return '#38bdf8';
        case 'newsapi':
          return '#a855f7';
        case 'rss':
          return '#34d399';
        case 'reddit':
          return '#fb923c';
        case 'ais':
          return '#60a5fa';
        default:
          return '#94a3b8';
      }
    }

    // colorMode === 'score'
    const sc = node.importance_score;
    if (sc >= 9) return '#ef4444'; // Red (Critical)
    if (sc >= 7) return '#f97316'; // Orange (High)
    if (sc >= 4) return '#3b82f6'; // Blue (Medium)
    return '#64748b'; // Slate (Low)
  };

  // Jump to neighbor node
  const handleSelectNeighbor = (eventId: string) => {
    const target = data.nodes.find((n) => n.event_id === eventId || n.id === eventId);
    if (target) {
      setSelectedNodeId(target.id);
      // Pan camera towards target
      setOffset({
        x: -target.x * 3.5,
        y: -target.y * 3.5,
      });
    }
  };

  const quotaTracker = data.stats?.quota_tracker;
  const m1Count = quotaTracker?.models?.['gemini-embedding-001']?.requests_today ?? 0;
  const m2Count = quotaTracker?.models?.['gemini-embedding-2']?.requests_today ?? 0;

  return (
    <div className="relative flex flex-col h-[calc(100vh-8.5rem)] rounded-xl card-slick overflow-hidden border border-white/5 bg-canvas-base select-none">
      {/* Top Map Toolbar */}
      <div className="z-10 flex flex-wrap items-center justify-between gap-3 p-3 hairline-b bg-canvas-subtle/80 backdrop-blur-md">
        {/* Left: Search & Mode Controls */}
        <div className="flex items-center space-x-2 flex-wrap gap-y-2">
          {/* Search box */}
          <div className="relative flex items-center w-52 sm:w-64">
            <Search className="absolute left-2.5 w-3.5 h-3.5 text-zinc-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Highlight news by keyword..."
              className="w-full pl-8 pr-3 py-1.5 rounded-lg text-xs bg-surface-card border border-white/5 text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:border-accent-cyan/50 transition-colors"
            />
          </div>

          {/* Color Mode Switcher */}
          <div className="flex items-center p-0.5 rounded-lg bg-surface-card border border-white/5 text-xs">
            <button
              onClick={() => setColorMode('trust')}
              className={cn(
                'flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors',
                colorMode === 'trust'
                  ? 'bg-surface-raised text-accent-cyan shadow-sm border border-white/10'
                  : 'text-zinc-400 hover:text-zinc-200'
              )}
              title="Color nodes by Trustworthiness & Corroboration"
            >
              <ShieldCheck className="w-3 h-3" />
              <span>Trust</span>
            </button>

            <button
              onClick={() => setColorMode('source')}
              className={cn(
                'flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors',
                colorMode === 'source'
                  ? 'bg-surface-raised text-accent-cyan shadow-sm border border-white/10'
                  : 'text-zinc-400 hover:text-zinc-200'
              )}
              title="Color nodes by Source platform"
            >
              <Radio className="w-3 h-3" />
              <span>Source</span>
            </button>

            <button
              onClick={() => setColorMode('score')}
              className={cn(
                'flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors',
                colorMode === 'score'
                  ? 'bg-surface-raised text-accent-cyan shadow-sm border border-white/10'
                  : 'text-zinc-400 hover:text-zinc-200'
              )}
              title="Color nodes by Importance Priority Score"
            >
              <Flame className="w-3 h-3" />
              <span>Priority</span>
            </button>
          </div>
        </div>

        {/* Right: Quota Monitor & Actions */}
        <div className="flex items-center space-x-2 flex-wrap gap-y-2">
          {/* Quota Counter Pill */}
          <div
            title="Daily Gemini Embedding API Quotas (1K RPD each, batched)"
            className="hidden lg:flex items-center space-x-2 px-2.5 py-1 rounded-md bg-surface-card border border-white/5 text-[10px] font-mono text-zinc-300"
          >
            <Cpu className="w-3 h-3 text-accent-cyan" />
            <span>
              Gemini 1: <strong className="text-zinc-100">{m1Count}</strong>/1K
            </span>
            <span className="text-zinc-600">|</span>
            <span>
              Gemini 2: <strong className="text-zinc-100">{m2Count}</strong>/1K
            </span>
            <span className="text-zinc-600">|</span>
            <span className="text-emerald-400 font-semibold">HF TEI: Unlimited</span>
          </div>

          {/* Backfill Button */}
          <button
            onClick={onBackfill}
            disabled={isBackfilling}
            title="Batch-embed pending articles using Gemini batchEmbedContents"
            className={cn(
              'flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium pressable border transition-all',
              isBackfilling
                ? 'bg-accent-cyan/10 border-accent-cyan/30 text-accent-cyan cursor-wait'
                : 'bg-surface-card hover:bg-surface-raised border-white/5 text-zinc-300 hover:text-zinc-100'
            )}
          >
            <Sparkles className={cn('w-3.5 h-3.5', isBackfilling && 'animate-spin text-accent-cyan')} />
            <span>{isBackfilling ? 'Embedding Batch...' : 'Sync Embeddings'}</span>
          </button>

          {/* Refresh Map */}
          <button
            onClick={onRefresh}
            disabled={isLoading}
            className="p-1.5 rounded-lg bg-surface-card hover:bg-surface-raised border border-white/5 text-zinc-400 hover:text-zinc-100 transition-colors"
            title="Reload Relevance Map"
          >
            <RefreshCw className={cn('w-4 h-4', isLoading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* SVG Canvas Map Area */}
      <div
        ref={containerRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onWheel={handleWheel}
        className={cn(
          'relative flex-1 w-full h-full overflow-hidden cursor-grab active:cursor-grabbing bg-canvas-base',
          isDragging && 'cursor-grabbing'
        )}
      >
        <svg
          className="w-full h-full"
          viewBox="-110 -110 220 220"
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            {/* Glow filters for corroborated nodes */}
            <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="2" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
            <filter id="glow-strong" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="3.5" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          </defs>

          {/* Transformed Content Group */}
          <g
            transform={`translate(${offset.x * 0.25}, ${offset.y * 0.25}) scale(${scale})`}
            className="transition-transform duration-75"
          >
            {/* Radar Coordinates Background Grid */}
            <g opacity="0.12" stroke="#71717a" strokeWidth="0.5" fill="none">
              <circle cx="0" cy="0" r="25" strokeDasharray="2,2" />
              <circle cx="0" cy="0" r="50" strokeDasharray="3,3" />
              <circle cx="0" cy="0" r="75" strokeDasharray="4,4" />
              <circle cx="0" cy="0" r="95" />
              {/* Crosshair Axes */}
              <line x1="-100" y1="0" x2="100" y2="0" />
              <line x1="0" y1="-100" x2="0" y2="100" />
            </g>

            {/* Clusters Background Label & Centroids */}
            {data.clusters.map((cluster) => {
              if (!cluster.center_x && !cluster.center_y) return null;
              return (
                <g key={`cluster-${cluster.id}`} opacity="0.6">
                  <circle
                    cx={cluster.center_x}
                    cy={cluster.center_y}
                    r={Math.max(12, cluster.size * 3.5)}
                    fill="rgba(6, 182, 212, 0.04)"
                    stroke="rgba(6, 182, 212, 0.2)"
                    strokeWidth="0.5"
                    strokeDasharray="2,2"
                  />
                  <text
                    x={cluster.center_x}
                    y={cluster.center_y - Math.max(14, cluster.size * 3.5) - 2}
                    textAnchor="middle"
                    fill="#a1a1aa"
                    fontSize="3"
                    fontFamily="monospace"
                    className="select-none pointer-events-none"
                  >
                    Cluster #{cluster.id} • {cluster.size} reports
                  </text>
                </g>
              );
            })}

            {/* Semantic Link Edges between Corroborated Reports */}
            {data.edges.map((edge, idx) => {
              const srcNode = data.nodes.find((n) => n.id === edge.source);
              const tgtNode = data.nodes.find((n) => n.id === edge.target);
              if (!srcNode || !tgtNode) return null;

              const isEdgeHighlighted =
                selectedNodeId === srcNode.id || selectedNodeId === tgtNode.id;

              return (
                <line
                  key={`edge-${idx}`}
                  x1={srcNode.x}
                  y1={srcNode.y}
                  x2={tgtNode.x}
                  y2={tgtNode.y}
                  stroke={isEdgeHighlighted ? '#06b6d4' : '#52525b'}
                  strokeWidth={isEdgeHighlighted ? 1.0 : 0.35}
                  strokeOpacity={isEdgeHighlighted ? 0.8 : 0.25}
                  strokeDasharray={edge.similarity > 0.8 ? 'none' : '1,1'}
                />
              );
            })}

            {/* Data Nodes */}
            {filteredNodes.map((node) => {
              const isSelected = selectedNodeId === node.id;
              const isMatch = searchMatches ? searchMatches.has(node.id) : true;
              const isDimmed = searchMatches ? !isMatch : false;
              const nodeColor = getNodeFill(node);
              const radius = 2.5 + Math.min(node.importance_score * 0.45, 4.0);

              return (
                <g
                  key={node.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedNodeId(node.id);
                  }}
                  onMouseEnter={() => setHoveredNode(node)}
                  onMouseLeave={() => setHoveredNode(null)}
                  className="cursor-pointer group"
                  opacity={isDimmed ? 0.15 : 1}
                >
                  {/* Selected Ripple Outer Ring */}
                  {isSelected && (
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={radius + 4}
                      fill="none"
                      stroke="#06b6d4"
                      strokeWidth="0.8"
                      strokeDasharray="2,1"
                      className="animate-spin origin-center"
                    />
                  )}

                  {/* Halo Glow for High-Trust Corroborated Nodes */}
                  {node.trustworthiness.score >= 80 && !isDimmed && (
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={radius + 2}
                      fill={nodeColor}
                      opacity="0.25"
                      filter="url(#glow)"
                    />
                  )}

                  {/* Main Node Point */}
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={radius}
                    fill={nodeColor}
                    stroke={isSelected ? '#ffffff' : 'rgba(0,0,0,0.4)'}
                    strokeWidth={isSelected ? 0.8 : 0.3}
                    className="transition-transform duration-150 group-hover:scale-125"
                  />
                </g>
              );
            })}
          </g>
        </svg>

        {/* Hover Tooltip Overlay Card */}
        {hoveredNode && !selectedNode && (
          <div
            className="absolute pointer-events-none z-30 max-w-xs p-3 rounded-lg bg-surface-card/95 backdrop-blur border border-white/10 shadow-xl space-y-1.5 transition-opacity"
            style={{
              left: '20px',
              bottom: '20px',
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-mono text-zinc-400">
                {hoveredNode.source_name}
              </span>
              <span
                className="text-[10px] font-semibold px-1.5 py-0.2 rounded"
                style={{ color: getNodeFill(hoveredNode) }}
              >
                {hoveredNode.trustworthiness.score}% • {hoveredNode.trustworthiness.label}
              </span>
            </div>
            <p className="text-xs font-semibold text-zinc-100 line-clamp-2">
              {hoveredNode.headline || hoveredNode.title}
            </p>
            <p className="text-[11px] text-zinc-400 line-clamp-2">
              {hoveredNode.summary}
            </p>
          </div>
        )}

        {/* Map Floating Navigation Controls */}
        <div className="absolute right-4 bottom-4 z-20 flex flex-col items-center p-1 rounded-lg bg-surface-card/90 backdrop-blur border border-white/10 shadow-lg space-y-1 text-zinc-400">
          <button
            onClick={handleZoomIn}
            title="Zoom In"
            className="p-1.5 hover:text-zinc-100 hover:bg-surface-raised rounded transition-colors"
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button
            onClick={handleZoomOut}
            title="Zoom Out"
            className="p-1.5 hover:text-zinc-100 hover:bg-surface-raised rounded transition-colors"
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <div className="w-4 h-px bg-white/10 my-0.5" />
          <button
            onClick={handleResetView}
            title="Reset Map Center & Zoom"
            className="p-1.5 hover:text-zinc-100 hover:bg-surface-raised rounded transition-colors"
          >
            <Maximize2 className="w-4 h-4" />
          </button>
        </div>

        {/* Bottom Left Legend */}
        <div className="absolute left-4 bottom-4 z-20 hidden md:flex items-center space-x-3 px-3 py-1.5 rounded-lg bg-surface-card/90 backdrop-blur border border-white/10 text-[11px] text-zinc-300">
          <span className="font-semibold text-zinc-400">
            {colorMode === 'trust' ? 'Trust:' : colorMode === 'source' ? 'Source:' : 'Priority:'}
          </span>
          {colorMode === 'trust' ? (
            <>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                <span className="text-zinc-300">High Trust (&ge;80%)</span>
              </div>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-cyan-500" />
                <span className="text-zinc-300">Verified (60-79%)</span>
              </div>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-amber-500" />
                <span className="text-zinc-300">Unverified (40-59%)</span>
              </div>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-rose-500" />
                <span className="text-zinc-300">Outlier (&lt;40%)</span>
              </div>
            </>
          ) : colorMode === 'source' ? (
            <>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-sky-400" />
                <span>Telegram</span>
              </div>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-purple-500" />
                <span>NewsAPI</span>
              </div>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-emerald-400" />
                <span>RSS</span>
              </div>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-orange-400" />
                <span>Reddit</span>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-red-500" />
                <span>Critical (9-10)</span>
              </div>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-orange-500" />
                <span>High (7-8)</span>
              </div>
              <div className="flex items-center space-x-1">
                <span className="w-2 h-2 rounded-full bg-blue-500" />
                <span>Medium (4-6)</span>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Selected Node Inspector Drawer */}
      <EventInspector
        node={selectedNode}
        onClose={() => setSelectedNodeId(null)}
        onSelectNeighbor={handleSelectNeighbor}
      />
    </div>
  );
}
