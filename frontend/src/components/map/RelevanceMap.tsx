'use client';

import React, { useState, useRef, useMemo, useEffect, useCallback } from 'react';
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  RefreshCw,
  Search,
  ShieldCheck,
  Radio,
  Flame,
  Sparkles,
  Cpu,
  Layers,
  Compass,
} from 'lucide-react';
import {
  EmbeddingsMapData,
  MapNode,
  MapEdge,
  MapCluster,
} from '@/lib/types';
import { cn } from '@/lib/utils';
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
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // High-performance animation / interaction refs (prevents DOM thrashing & React re-render lag)
  const offsetRef = useRef({ x: 0, y: 0 });
  const scaleRef = useRef(1);
  const isDraggingRef = useRef(false);
  const dragStartRef = useRef({ x: 0, y: 0 });
  const animFrameIdRef = useRef<number | null>(null);

  // Reactive UI state
  const [colorMode, setColorMode] = useState<ColorMode>('trust');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<MapNode | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [selectedClusterFilter, setSelectedClusterFilter] = useState<number | 'all'>('all');
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

  // Selected node object
  const selectedNode = useMemo(() => {
    return data.nodes.find((n) => n.id === selectedNodeId) || null;
  }, [data.nodes, selectedNodeId]);

  // Match set for search highlighting
  const searchMatches = useMemo(() => {
    if (!searchQuery.trim()) return null;
    const query = searchQuery.toLowerCase();
    const set = new Set<string>();
    data.nodes.forEach((node) => {
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
  }, [data.nodes, searchQuery]);

  // Color mapper helper
  const getNodeFill = useCallback(
    (node: MapNode) => {
      if (colorMode === 'trust') {
        const s = node.trustworthiness.score;
        if (s >= 80) return '#10b981'; // Emerald (High trust)
        if (s >= 60) return '#06b6d4'; // Cyan (Verified)
        if (s >= 40) return '#f59e0b'; // Amber (Unverified)
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
    },
    [colorMode]
  );

  // Resize observer to keep canvas sharp at full container size
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const updateDimensions = () => {
      const rect = container.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setDimensions({
          width: Math.floor(rect.width),
          height: Math.floor(rect.height),
        });
      }
    };

    updateDimensions();
    const observer = new ResizeObserver(() => updateDimensions());
    observer.observe(container);

    return () => observer.disconnect();
  }, []);

  // Main 60 FPS Canvas Draw Routine
  const renderCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const { width, height } = dimensions;
    const dpr = window.devicePixelRatio || 1;

    // Coordinate transforms
    const centerX = width / 2 + offsetRef.current.x;
    const centerY = height / 2 + offsetRef.current.y;
    const baseScale = Math.min(width, height) / 220;
    const currentScale = scaleRef.current;

    const toScreenX = (mapX: number) => centerX + mapX * baseScale * currentScale;
    const toScreenY = (mapY: number) => centerY + mapY * baseScale * currentScale;

    // Reset transform & clear
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // -------------------------------------------------------------
    // Layer 1: Radar Background Grid & Range Rings
    // -------------------------------------------------------------
    ctx.save();
    ctx.strokeStyle = 'rgba(113, 113, 122, 0.18)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);

    const radarRadii = [25, 50, 75, 95];
    radarRadii.forEach((r) => {
      const screenR = r * baseScale * currentScale;
      ctx.beginPath();
      ctx.arc(centerX, centerY, screenR, 0, Math.PI * 2);
      ctx.stroke();
    });

    // Crosshairs
    ctx.beginPath();
    ctx.moveTo(centerX - 100 * baseScale * currentScale, centerY);
    ctx.lineTo(centerX + 100 * baseScale * currentScale, centerY);
    ctx.moveTo(centerX, centerY - 100 * baseScale * currentScale);
    ctx.lineTo(centerX, centerY + 100 * baseScale * currentScale);
    ctx.stroke();

    // Range markers
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(161, 161, 170, 0.35)';
    ctx.font = '9px monospace';
    ctx.textAlign = 'left';
    ctx.fillText('25%', centerX + 26 * baseScale * currentScale, centerY - 4);
    ctx.fillText('50%', centerX + 51 * baseScale * currentScale, centerY - 4);
    ctx.fillText('75%', centerX + 76 * baseScale * currentScale, centerY - 4);
    ctx.fillText('Perimeter', centerX + 96 * baseScale * currentScale, centerY - 4);
    ctx.restore();

    // -------------------------------------------------------------
    // Layer 2: Topical Cluster Constellations & Badges
    // -------------------------------------------------------------
    data.clusters.forEach((cluster) => {
      if (cluster.center_x === undefined || cluster.center_y === undefined) return;
      if (selectedClusterFilter !== 'all' && cluster.id !== selectedClusterFilter) return;

      const scX = toScreenX(cluster.center_x);
      const scY = toScreenY(cluster.center_y);
      const rawRadius = cluster.radius || Math.max(14, cluster.size * 3.2);
      const scRadius = Math.max(20, rawRadius * baseScale * currentScale);

      // Celestial cluster disc
      ctx.save();
      ctx.beginPath();
      ctx.arc(scX, scY, scRadius, 0, Math.PI * 2);
      ctx.fillStyle = cluster.id === 0 ? 'rgba(244, 63, 94, 0.02)' : 'rgba(6, 182, 212, 0.035)';
      ctx.fill();

      ctx.strokeStyle = cluster.id === 0 ? 'rgba(244, 63, 94, 0.25)' : 'rgba(6, 182, 212, 0.25)';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.stroke();

      // Cluster label pill
      const labelText =
        cluster.id === 0
          ? `Outliers • ${cluster.size}`
          : `Cluster #${cluster.id}: ${cluster.label.slice(0, 24)}${cluster.label.length > 24 ? '…' : ''} (${cluster.size})`;

      ctx.font = '10px sans-serif';
      ctx.textAlign = 'center';
      const textWidth = ctx.measureText(labelText).width;
      const pillY = scY - scRadius - 12;

      // Pill background
      ctx.fillStyle = 'rgba(24, 24, 27, 0.75)';
      ctx.beginPath();
      ctx.roundRect(scX - textWidth / 2 - 8, pillY - 10, textWidth + 16, 16, 4);
      ctx.fill();
      ctx.strokeStyle = cluster.id === 0 ? 'rgba(244, 63, 94, 0.35)' : 'rgba(6, 182, 212, 0.35)';
      ctx.setLineDash([]);
      ctx.stroke();

      // Pill text
      ctx.fillStyle = cluster.id === 0 ? '#fda4af' : '#67e8f9';
      ctx.fillText(labelText, scX, pillY + 2);
      ctx.restore();
    });

    // -------------------------------------------------------------
    // Layer 3: Semantic Similarity Edges
    // -------------------------------------------------------------
    const nodeMap = new Map<string, MapNode>();
    data.nodes.forEach((n) => nodeMap.set(n.id, n));

    data.edges.forEach((edge) => {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (!src || !tgt) return;

      const isConnectedToSelected =
        selectedNodeId === src.id || selectedNodeId === tgt.id;
      const isConnectedToHovered =
        hoveredNode?.id === src.id || hoveredNode?.id === tgt.id;
      const isHighlighted = isConnectedToSelected || isConnectedToHovered;

      ctx.save();
      ctx.beginPath();
      ctx.moveTo(toScreenX(src.x), toScreenY(src.y));
      ctx.lineTo(toScreenX(tgt.x), toScreenY(tgt.y));

      if (isHighlighted) {
        ctx.strokeStyle = '#06b6d4';
        ctx.lineWidth = 1.6;
        ctx.setLineDash([]);
      } else {
        ctx.strokeStyle = 'rgba(113, 113, 122, 0.22)';
        ctx.lineWidth = 0.8;
        ctx.setLineDash(edge.similarity > 0.75 ? [] : [2, 2]);
      }
      ctx.stroke();
      ctx.restore();
    });

    // -------------------------------------------------------------
    // Layer 4: Intelligence Data Nodes
    // -------------------------------------------------------------
    data.nodes.forEach((node) => {
      if (selectedClusterFilter !== 'all' && node.cluster_id !== selectedClusterFilter) {
        return;
      }

      const isSelected = selectedNodeId === node.id;
      const isHovered = hoveredNode?.id === node.id;
      const isMatch = searchMatches ? searchMatches.has(node.id) : true;
      const isDimmed = searchMatches ? !isMatch : false;

      const scX = toScreenX(node.x);
      const scY = toScreenY(node.y);
      const baseRadius = 4.5 + Math.min((node.importance_score || 5) * 0.35, 3.5);
      const radius = isHovered ? baseRadius + 2.5 : isSelected ? baseRadius + 1.5 : baseRadius;
      const fillColor = getNodeFill(node);

      ctx.save();
      ctx.globalAlpha = isDimmed ? 0.15 : 1.0;

      // Halo ring for High-Trust Corroborated Reports (>= 80%)
      if (node.trustworthiness.score >= 80 && !isDimmed) {
        ctx.beginPath();
        ctx.arc(scX, scY, radius + 4, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(16, 185, 129, 0.2)';
        ctx.fill();
      }

      // Outer animated reticle for selected node
      if (isSelected) {
        ctx.beginPath();
        ctx.arc(scX, scY, radius + 6, 0, Math.PI * 2);
        ctx.strokeStyle = '#06b6d4';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Target ticks
        ctx.beginPath();
        ctx.moveTo(scX - radius - 10, scY);
        ctx.lineTo(scX + radius + 10, scY);
        ctx.moveTo(scX, scY - radius - 10);
        ctx.lineTo(scX, scY + radius + 10);
        ctx.strokeStyle = 'rgba(6, 182, 212, 0.4)';
        ctx.lineWidth = 0.8;
        ctx.stroke();
      }

      // Hover ring
      if (isHovered) {
        ctx.beginPath();
        ctx.arc(scX, scY, radius + 3, 0, Math.PI * 2);
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }

      // Main Node Disc
      ctx.beginPath();
      ctx.arc(scX, scY, radius, 0, Math.PI * 2);
      ctx.fillStyle = fillColor;
      ctx.fill();
      ctx.strokeStyle = isSelected ? '#ffffff' : 'rgba(0, 0, 0, 0.6)';
      ctx.lineWidth = isSelected ? 1.5 : 0.8;
      ctx.stroke();

      ctx.restore();
    });
  }, [
    dimensions,
    data.clusters,
    data.edges,
    data.nodes,
    selectedClusterFilter,
    selectedNodeId,
    hoveredNode,
    searchMatches,
    getNodeFill,
  ]);

  // Request redraw whenever reactive props change
  useEffect(() => {
    if (animFrameIdRef.current) cancelAnimationFrame(animFrameIdRef.current);
    animFrameIdRef.current = requestAnimationFrame(renderCanvas);
    return () => {
      if (animFrameIdRef.current) cancelAnimationFrame(animFrameIdRef.current);
    };
  }, [renderCanvas]);

  // Fast Euclidean node hit-test
  const getNodeAtScreenPos = useCallback(
    (screenX: number, screenY: number): MapNode | null => {
      const { width, height } = dimensions;
      const centerX = width / 2 + offsetRef.current.x;
      const centerY = height / 2 + offsetRef.current.y;
      const baseScale = Math.min(width, height) / 220;
      const currentScale = scaleRef.current;

      for (let i = data.nodes.length - 1; i >= 0; i--) {
        const node = data.nodes[i];
        if (selectedClusterFilter !== 'all' && node.cluster_id !== selectedClusterFilter) {
          continue;
        }

        const scX = centerX + node.x * baseScale * currentScale;
        const scY = centerY + node.y * baseScale * currentScale;
        const radius = 6.0 + Math.min((node.importance_score || 5) * 0.35, 3.5);

        const dx = screenX - scX;
        const dy = screenY - scY;
        if (dx * dx + dy * dy <= (radius + 5) * (radius + 5)) {
          return node;
        }
      }
      return null;
    },
    [data.nodes, dimensions, selectedClusterFilter]
  );

  // Mouse pan drag handlers (100% 60 FPS - updates refs and calls rAF)
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;

    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;
    const hitNode = getNodeAtScreenPos(clickX, clickY);

    if (hitNode) {
      setSelectedNodeId(hitNode.id);
      return;
    }

    // Start drag pan
    isDraggingRef.current = true;
    dragStartRef.current = {
      x: e.clientX - offsetRef.current.x,
      y: e.clientY - offsetRef.current.y,
    };
    if (canvasRef.current) canvasRef.current.style.cursor = 'grabbing';
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;

    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    if (isDraggingRef.current) {
      offsetRef.current = {
        x: e.clientX - dragStartRef.current.x,
        y: e.clientY - dragStartRef.current.y,
      };
      if (animFrameIdRef.current) cancelAnimationFrame(animFrameIdRef.current);
      animFrameIdRef.current = requestAnimationFrame(renderCanvas);
      return;
    }

    // Hover test
    const hitNode = getNodeAtScreenPos(mouseX, mouseY);
    if (hitNode !== hoveredNode) {
      setHoveredNode(hitNode);
      if (hitNode) {
        setTooltipPos({ x: mouseX, y: mouseY });
      }
    }
    if (canvasRef.current) {
      canvasRef.current.style.cursor = hitNode ? 'pointer' : 'grab';
    }
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
    if (canvasRef.current) {
      canvasRef.current.style.cursor = hoveredNode ? 'pointer' : 'grab';
    }
  };

  // Wheel zoom centered at mouse position
  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.88;
    const newScale = Math.min(Math.max(scaleRef.current * zoomFactor, 0.45), 5.0);

    const rect = canvasRef.current?.getBoundingClientRect();
    if (rect) {
      const mouseX = e.clientX - rect.left - dimensions.width / 2;
      const mouseY = e.clientY - rect.top - dimensions.height / 2;
      const scaleRatio = newScale / scaleRef.current;
      offsetRef.current = {
        x: mouseX - (mouseX - offsetRef.current.x) * scaleRatio,
        y: mouseY - (mouseY - offsetRef.current.y) * scaleRatio,
      };
    }

    scaleRef.current = newScale;
    if (animFrameIdRef.current) cancelAnimationFrame(animFrameIdRef.current);
    animFrameIdRef.current = requestAnimationFrame(renderCanvas);
  };

  // Zoom button handlers
  const handleZoomIn = () => {
    scaleRef.current = Math.min(scaleRef.current * 1.3, 5.0);
    renderCanvas();
  };

  const handleZoomOut = () => {
    scaleRef.current = Math.max(scaleRef.current / 1.3, 0.45);
    renderCanvas();
  };

  const handleResetView = () => {
    scaleRef.current = 1;
    offsetRef.current = { x: 0, y: 0 };
    setSelectedClusterFilter('all');
    renderCanvas();
  };

  // Jump camera directly to a cluster
  const handleJumpToCluster = (clusterId: number | 'all') => {
    setSelectedClusterFilter(clusterId);
    if (clusterId === 'all') {
      handleResetView();
      return;
    }

    const cluster = data.clusters.find((c) => c.id === clusterId);
    if (cluster && cluster.center_x !== undefined && cluster.center_y !== undefined) {
      const baseScale = Math.min(dimensions.width, dimensions.height) / 220;
      scaleRef.current = 1.45;
      offsetRef.current = {
        x: -cluster.center_x * baseScale * scaleRef.current,
        y: -cluster.center_y * baseScale * scaleRef.current,
      };
      renderCanvas();
    }
  };

  // Jump to neighbor node
  const handleSelectNeighbor = (eventId: string) => {
    const target = data.nodes.find((n) => n.event_id === eventId || n.id === eventId);
    if (target) {
      setSelectedNodeId(target.id);
      const baseScale = Math.min(dimensions.width, dimensions.height) / 220;
      scaleRef.current = Math.max(scaleRef.current, 1.4);
      offsetRef.current = {
        x: -target.x * baseScale * scaleRef.current,
        y: -target.y * baseScale * scaleRef.current,
      };
      renderCanvas();
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

      {/* Cluster Navigation Quick Pills Bar */}
      {data.clusters.length > 0 && (
        <div className="z-10 flex items-center space-x-1.5 px-3 py-1.5 hairline-b bg-canvas-subtle/50 overflow-x-auto scrollbar-none text-xs">
          <span className="text-[10px] font-mono uppercase tracking-wider text-zinc-500 mr-1 flex items-center gap-1">
            <Layers className="w-3 h-3" />
            Galaxies:
          </span>
          <button
            onClick={() => handleJumpToCluster('all')}
            className={cn(
              'px-2 py-0.5 rounded text-[11px] font-medium whitespace-nowrap transition-colors',
              selectedClusterFilter === 'all'
                ? 'bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30'
                : 'bg-surface-card text-zinc-400 hover:text-zinc-200 border border-white/5'
            )}
          >
            All Reports ({data.nodes.length})
          </button>
          {data.clusters.map((c) => (
            <button
              key={`cluster-nav-${c.id}`}
              onClick={() => handleJumpToCluster(c.id)}
              className={cn(
                'px-2 py-0.5 rounded text-[11px] font-medium whitespace-nowrap transition-colors',
                selectedClusterFilter === c.id
                  ? 'bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30'
                  : 'bg-surface-card text-zinc-400 hover:text-zinc-200 border border-white/5'
              )}
            >
              {c.id === 0 ? 'Outliers' : `C#${c.id}: ${c.label.slice(0, 20)}`} ({c.size})
            </button>
          ))}
        </div>
      )}

      {/* Hardware-Accelerated 60 FPS HTML5 Canvas Map Area */}
      <div
        ref={containerRef}
        className="relative flex-1 w-full h-full overflow-hidden bg-canvas-base"
      >
        <canvas
          ref={canvasRef}
          width={dimensions.width * (typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1)}
          height={dimensions.height * (typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1)}
          style={{
            width: `${dimensions.width}px`,
            height: `${dimensions.height}px`,
            display: 'block',
          }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onWheel={handleWheel}
          className="w-full h-full cursor-grab active:cursor-grabbing"
        />

        {/* Hover Tooltip Overlay Card */}
        {hoveredNode && !selectedNode && (
          <div
            className="absolute pointer-events-none z-30 max-w-xs p-3 rounded-lg bg-surface-card/95 backdrop-blur border border-white/10 shadow-xl space-y-1.5 transition-all"
            style={{
              left: `${Math.min(tooltipPos.x + 15, dimensions.width - 290)}px`,
              top: `${Math.min(tooltipPos.y + 15, dimensions.height - 130)}px`,
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-mono text-zinc-400">
                {hoveredNode.source_name}
              </span>
              <span
                className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
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

        {/* Floating Navigation Controls */}
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
                <span className="text-zinc-300">High (&ge;80%)</span>
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
