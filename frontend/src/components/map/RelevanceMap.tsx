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
  HelpCircle,
  X,
  Info,
  CheckCircle2,
  AlertTriangle,
  ExternalLink,
  Tag,
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

function truncateWords(text: string, maxLen: number): string {
  const cleaned = (text || '').trim().replace(/\s+/g, ' ');
  if (cleaned.length <= maxLen) return cleaned;
  const cut = cleaned.slice(0, maxLen);
  const lastSpace = cut.lastIndexOf(' ');
  if (lastSpace >= Math.floor(maxLen * 0.55)) {
    return `${cut.slice(0, lastSpace).replace(/[,:;.\-–—]+$/, '')}…`;
  }
  return `${cut.replace(/[,:;.\-–—]+$/, '')}…`;
}

interface PillBox {
  id: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

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
  const galaxiesBarRef = useRef<HTMLDivElement>(null);

  // High-performance animation / interaction refs (prevents DOM thrashing & React re-render lag)
  const offsetRef = useRef({ x: 0, y: 0 });
  const scaleRef = useRef(1);
  const isDraggingRef = useRef(false);
  const dragStartRef = useRef({ x: 0, y: 0 });
  const animFrameIdRef = useRef<number | null>(null);
  const clusterPillBoxesRef = useRef<PillBox[]>([]);

  // Reactive UI state
  const [colorMode, setColorMode] = useState<ColorMode>('trust');
  const [showKeyHeadlines, setShowKeyHeadlines] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<MapNode | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [selectedClusterFilter, setSelectedClusterFilter] = useState<number | 'all'>('all');
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [showGuide, setShowGuide] = useState(false);

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

  // Node radius helper for clear visual hierarchy across scores 1-10 and cluster leads
  const getNodeBaseRadius = useCallback((node: MapNode) => {
    const score = Math.max(1, Math.min(10, node.importance_score || 5));
    const scaled = 3.6 + Math.pow(score / 10, 1.35) * 5.6;
    return node.is_cluster_lead ? scaled + 1.1 : scaled;
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

    const occupiedLabelRects: Array<{ x1: number; y1: number; x2: number; y2: number }> = [];
    const newPillBoxes: PillBox[] = [];

    const rectsOverlap = (
      a: { x1: number; y1: number; x2: number; y2: number },
      b: { x1: number; y1: number; x2: number; y2: number },
      pad = 4
    ) => {
      return !(
        a.x2 + pad < b.x1 ||
        a.x1 - pad > b.x2 ||
        a.y2 + pad < b.y1 ||
        a.y1 - pad > b.y2
      );
    };

    // -------------------------------------------------------------
    // Layer 1: Core-to-Periphery Consensus Radar Grid & Zone Rings
    // -------------------------------------------------------------
    ctx.save();

    // Subtle radial core glow to anchor high-consensus center
    const coreGlowRadius = 48 * baseScale * currentScale;
    const coreGrad = ctx.createRadialGradient(
      centerX,
      centerY,
      2,
      centerX,
      centerY,
      coreGlowRadius
    );
    coreGrad.addColorStop(0, 'rgba(16, 185, 129, 0.055)');
    coreGrad.addColorStop(0.55, 'rgba(6, 182, 212, 0.025)');
    coreGrad.addColorStop(1, 'rgba(6, 182, 212, 0)');
    ctx.fillStyle = coreGrad;
    ctx.beginPath();
    ctx.arc(centerX, centerY, coreGlowRadius, 0, Math.PI * 2);
    ctx.fill();

    const radarZones = [
      { r: 22, label: 'CORE CONSENSUS', stroke: 'rgba(16, 185, 129, 0.20)', text: 'rgba(52, 211, 153, 0.45)' },
      { r: 46, label: 'CORROBORATED ORBIT', stroke: 'rgba(6, 182, 212, 0.18)', text: 'rgba(34, 211, 238, 0.42)' },
      { r: 68, label: 'DEVELOPING', stroke: 'rgba(161, 161, 170, 0.16)', text: 'rgba(161, 161, 170, 0.38)' },
      { r: 82, label: 'UNVERIFIED PERIMETER', stroke: 'rgba(244, 63, 94, 0.22)', text: 'rgba(251, 113, 133, 0.45)' },
    ];

    ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);
    radarZones.forEach((zone) => {
      const screenR = zone.r * baseScale * currentScale;
      ctx.strokeStyle = zone.stroke;
      ctx.beginPath();
      ctx.arc(centerX, centerY, screenR, 0, Math.PI * 2);
      ctx.stroke();
    });

    // Subtle radar crosshairs
    ctx.strokeStyle = 'rgba(113, 113, 122, 0.12)';
    ctx.beginPath();
    ctx.moveTo(centerX - 92 * baseScale * currentScale, centerY);
    ctx.lineTo(centerX + 92 * baseScale * currentScale, centerY);
    ctx.moveTo(centerX, centerY - 92 * baseScale * currentScale);
    ctx.lineTo(centerX, centerY + 92 * baseScale * currentScale);
    ctx.stroke();

    // Zone labels placed along top-left diagonal (-135 deg) so they stay clear of horizontal pills
    ctx.setLineDash([]);
    ctx.font = '600 8.5px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textAlign = 'center';
    radarZones.forEach((zone) => {
      const screenR = zone.r * baseScale * currentScale;
      if (screenR < 28) return;
      ctx.fillStyle = zone.text;
      ctx.fillText(zone.label, centerX, centerY - screenR + 10);
    });
    ctx.restore();

    // -------------------------------------------------------------
    // Layer 2: Topical Cluster Constellations & Non-Occluding Badges
    // -------------------------------------------------------------
    const screenClusters: Array<{
      cluster: MapCluster;
      scX: number;
      scY: number;
      scRadius: number;
    }> = [];

    data.clusters.forEach((cluster) => {
      if (cluster.center_x === undefined || cluster.center_y === undefined) return;
      if (selectedClusterFilter !== 'all' && cluster.id !== selectedClusterFilter) return;

      // Special rendering for cluster.id === 0 (Uncorroborated Perimeter ring)
      if (cluster.id === 0 || cluster.is_perimeter) {
        const perimR = 81 * baseScale * currentScale;
        ctx.save();
        ctx.beginPath();
        ctx.arc(centerX, centerY, perimR, 0, Math.PI * 2);
        ctx.strokeStyle =
          selectedClusterFilter === 0
            ? 'rgba(244, 63, 94, 0.55)'
            : 'rgba(244, 63, 94, 0.24)';
        ctx.lineWidth = selectedClusterFilter === 0 ? 1.8 : 1.1;
        ctx.setLineDash([5, 5]);
        ctx.stroke();
        ctx.restore();
        return;
      }

      const scX = toScreenX(cluster.center_x);
      const scY = toScreenY(cluster.center_y);
      const rawRadius = cluster.radius || Math.max(12, cluster.size * 2.8);
      const scRadius = Math.max(22, rawRadius * baseScale * currentScale);
      screenClusters.push({ cluster, scX, scY, scRadius });

      const avgTrust = cluster.avg_trust ?? 65;
      const fillRgba =
        avgTrust >= 75
          ? 'rgba(16, 185, 129, 0.045)'
          : avgTrust >= 58
          ? 'rgba(6, 182, 212, 0.04)'
          : 'rgba(245, 158, 11, 0.04)';
      const strokeRgba =
        avgTrust >= 75
          ? 'rgba(16, 185, 129, 0.32)'
          : avgTrust >= 58
          ? 'rgba(6, 182, 212, 0.28)'
          : 'rgba(245, 158, 11, 0.28)';

      // Celestial cluster disc
      ctx.save();
      ctx.beginPath();
      ctx.arc(scX, scY, scRadius, 0, Math.PI * 2);
      ctx.fillStyle = fillRgba;
      ctx.fill();

      ctx.strokeStyle = strokeRgba;
      ctx.lineWidth = 1.15;
      ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.restore();
    });

    // Render Cluster Label Pills with Bounding-Box-Aware Radial Clearance
    screenClusters.forEach(({ cluster, scX, scY, scRadius }) => {
      ctx.save();
      const cleanLabel = truncateWords(cluster.label, 30);
      const labelText = `C#${cluster.id}: ${cleanLabel} (${cluster.size})`;

      ctx.font = '600 11px system-ui, -apple-system, sans-serif';
      ctx.textAlign = 'center';
      const textWidth = ctx.measureText(labelText).width;
      const pillW = textWidth + 26;
      const pillH = 22;
      const halfW = pillW / 2;
      const halfH = pillH / 2;

      // Determine outward radial direction from map center (or straight up for core cluster)
      const dx = scX - centerX;
      const dy = scY - centerY;
      const distFromCenter = Math.hypot(dx, dy);

      let baseAngle = -Math.PI / 2;
      if (distFromCenter > 14) {
        baseAngle = Math.atan2(dy, dx);
      }

      // Try candidate angles around baseAngle to avoid overlapping any cluster disc or existing pill
      const angleOffsets = [0, 0.35, -0.35, 0.7, -0.7, 1.05, -1.05, Math.PI];
      let chosenX = scX;
      let chosenY = scY - scRadius - halfH - 8;
      let chosenRect = {
        x1: chosenX - halfW,
        y1: chosenY - halfH,
        x2: chosenX + halfW,
        y2: chosenY + halfH,
      };

      for (const offset of angleOffsets) {
        const ang = baseAngle + offset;
        const uX = Math.cos(ang);
        const uY = Math.sin(ang);
        // Exact distance from rectangle center to its edge along (uX, uY)
        const rectClearance = Math.abs(uX) * halfW + Math.abs(uY) * halfH;
        const candX = scX + uX * (scRadius + rectClearance + 7);
        const candY = scY + uY * (scRadius + rectClearance + 7);
        const candRect = {
          x1: candX - halfW,
          y1: candY - halfH,
          x2: candX + halfW,
          y2: candY + halfH,
        };

        // Check collision with other pills
        const hitsPill = occupiedLabelRects.some((r) => rectsOverlap(candRect, r, 4));
        if (hitsPill) continue;

        // Check collision with other cluster discs
        const hitsOtherDisc = screenClusters.some((other) => {
          if (other.cluster.id === cluster.id) return false;
          const nearestX = Math.max(candRect.x1, Math.min(other.scX, candRect.x2));
          const nearestY = Math.max(candRect.y1, Math.min(other.scY, candRect.y2));
          const d = Math.hypot(nearestX - other.scX, nearestY - other.scY);
          return d < other.scRadius - 2;
        });

        if (!hitsOtherDisc) {
          chosenX = candX;
          chosenY = candY;
          chosenRect = candRect;
          break;
        }
      }

      occupiedLabelRects.push(chosenRect);
      newPillBoxes.push({
        id: cluster.id,
        x1: chosenRect.x1,
        y1: chosenRect.y1,
        x2: chosenRect.x2,
        y2: chosenRect.y2,
      });

      const avgTrust = cluster.avg_trust ?? 65;
      const borderColor =
        avgTrust >= 75
          ? 'rgba(16, 185, 129, 0.6)'
          : avgTrust >= 58
          ? 'rgba(6, 182, 212, 0.55)'
          : 'rgba(245, 158, 11, 0.55)';
      const pipColor =
        avgTrust >= 75 ? '#10b981' : avgTrust >= 58 ? '#06b6d4' : '#f59e0b';
      const textColor =
        avgTrust >= 75 ? '#d1fae5' : avgTrust >= 58 ? '#e0f2fe' : '#fef3c7';

      // Subtle leader tick from cluster rim to pill
      const dirX = chosenX - scX;
      const dirY = chosenY - scY;
      const dirLen = Math.hypot(dirX, dirY);
      if (dirLen > scRadius + 4) {
        ctx.beginPath();
        ctx.moveTo(scX + (dirX / dirLen) * scRadius, scY + (dirY / dirLen) * scRadius);
        ctx.lineTo(chosenX, chosenY);
        ctx.strokeStyle = borderColor;
        ctx.lineWidth = 0.9;
        ctx.setLineDash([2, 2]);
        ctx.stroke();
      }

      // Pill background
      ctx.fillStyle = 'rgba(9, 9, 11, 0.92)';
      ctx.beginPath();
      ctx.roundRect(chosenRect.x1, chosenRect.y1, pillW, pillH, 6);
      ctx.fill();

      ctx.strokeStyle = borderColor;
      ctx.lineWidth = 1.2;
      ctx.setLineDash([]);
      ctx.stroke();

      // Status pip
      ctx.beginPath();
      ctx.arc(chosenX - textWidth / 2 - 4, chosenY, 3, 0, Math.PI * 2);
      ctx.fillStyle = pipColor;
      ctx.fill();

      // Pill text
      ctx.fillStyle = textColor;
      ctx.fillText(labelText, chosenX + 4, chosenY + 3.5);
      ctx.restore();
    });

    clusterPillBoxesRef.current = newPillBoxes;

    // -------------------------------------------------------------
    // Layer 3: Curved Intra-Cluster & Highlighted Semantic Edges
    // -------------------------------------------------------------
    const nodeMap = new Map<string, MapNode>();
    data.nodes.forEach((n) => nodeMap.set(n.id, n));

    data.edges.forEach((edge) => {
      const src = nodeMap.get(edge.source);
      const tgt = nodeMap.get(edge.target);
      if (!src || !tgt) return;
      if (
        selectedClusterFilter !== 'all' &&
        src.cluster_id !== selectedClusterFilter &&
        tgt.cluster_id !== selectedClusterFilter
      ) {
        return;
      }

      const isConnectedToSelected =
        selectedNodeId === src.id || selectedNodeId === tgt.id;
      const isConnectedToHovered =
        hoveredNode?.id === src.id || hoveredNode?.id === tgt.id;
      const isHighlighted = isConnectedToSelected || isConnectedToHovered;
      const isIntraCluster =
        edge.intra_cluster !== undefined
          ? edge.intra_cluster
          : (src.cluster_id ?? 0) > 0 && src.cluster_id === tgt.cluster_id;

      // Declutter default view: hide cross-cluster edges unless hovered or selected
      if (!isIntraCluster && !isHighlighted) {
        return;
      }

      const x1 = toScreenX(src.x);
      const y1 = toScreenY(src.y);
      const x2 = toScreenX(tgt.x);
      const y2 = toScreenY(tgt.y);

      const midX = (x1 + x2) / 2;
      const midY = (y1 + y2) / 2;
      const dx = x2 - x1;
      const dy = y2 - y1;
      // Gentle curve offset perpendicular to the segment
      const ctrlX = midX - dy * 0.12;
      const ctrlY = midY + dx * 0.12;

      ctx.save();
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.quadraticCurveTo(ctrlX, ctrlY, x2, y2);

      if (isHighlighted) {
        ctx.strokeStyle = '#06b6d4';
        ctx.lineWidth = 1.75;
        ctx.setLineDash([]);
      } else {
        const simAlpha = Math.min(0.42, Math.max(0.14, (edge.similarity - 0.72) * 1.4));
        ctx.strokeStyle = `rgba(148, 163, 184, ${simAlpha.toFixed(2)})`;
        ctx.lineWidth = edge.similarity >= 0.85 ? 1.25 : 0.85;
        ctx.setLineDash(edge.similarity >= 0.78 ? [] : [2, 2]);
      }
      ctx.stroke();
      ctx.restore();
    });

    // -------------------------------------------------------------
    // Layer 4: Intelligence Data Nodes & Key Headline Callouts
    // -------------------------------------------------------------
    const visibleNodes: Array<{
      node: MapNode;
      scX: number;
      scY: number;
      radius: number;
      isSelected: boolean;
      isHovered: boolean;
      isDimmed: boolean;
    }> = [];

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
      const baseRadius = getNodeBaseRadius(node);
      const radius = isHovered ? baseRadius + 2.5 : isSelected ? baseRadius + 1.5 : baseRadius;
      const fillColor = getNodeFill(node);

      visibleNodes.push({ node, scX, scY, radius, isSelected, isHovered, isDimmed });

      ctx.save();
      ctx.globalAlpha = isDimmed ? 0.15 : 1.0;

      // Halo ring for High-Trust Corroborated Reports (>= 80%)
      if (node.trustworthiness.score >= 80 && !isDimmed) {
        ctx.beginPath();
        ctx.arc(scX, scY, radius + 4.2, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(16, 185, 129, 0.22)';
        ctx.fill();
      }

      // Outer reticle for selected node
      if (isSelected) {
        ctx.beginPath();
        ctx.arc(scX, scY, radius + 6, 0, Math.PI * 2);
        ctx.strokeStyle = '#06b6d4';
        ctx.lineWidth = 1.6;
        ctx.stroke();

        // Target ticks
        ctx.beginPath();
        ctx.moveTo(scX - radius - 10, scY);
        ctx.lineTo(scX + radius + 10, scY);
        ctx.moveTo(scX, scY - radius - 10);
        ctx.lineTo(scX, scY + radius + 10);
        ctx.strokeStyle = 'rgba(6, 182, 212, 0.45)';
        ctx.lineWidth = 0.8;
        ctx.stroke();
      }

      // Hover ring
      if (isHovered) {
        ctx.beginPath();
        ctx.arc(scX, scY, radius + 3, 0, Math.PI * 2);
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.3;
        ctx.stroke();
      }

      // Main Node Disc
      ctx.beginPath();
      ctx.arc(scX, scY, radius, 0, Math.PI * 2);
      ctx.fillStyle = fillColor;
      ctx.fill();
      ctx.lineWidth = node.is_cluster_lead ? 1.5 : 1.0;
      ctx.strokeStyle = isSelected
        ? '#ffffff'
        : node.is_cluster_lead
        ? 'rgba(255, 255, 255, 0.85)'
        : 'rgba(0, 0, 0, 0.65)';
      ctx.stroke();

      // Lead story anchor core dot inside medoid node
      if (node.is_cluster_lead && (node.cluster_id ?? 0) > 0) {
        ctx.beginPath();
        ctx.arc(scX, scY, Math.max(1.8, radius * 0.32), 0, Math.PI * 2);
        ctx.fillStyle = '#ffffff';
        ctx.fill();
      }

      ctx.restore();
    });

    // Pass 2 of Layer 4: Collision-checked node callout labels (hovered/selected first, then lead stories, then zoomed high-priority)
    const labelCandidates = visibleNodes
      .filter(({ node, isHovered, isSelected, isDimmed }) => {
        if (isDimmed) return false;
        if (isHovered || isSelected) return true;
        if (showKeyHeadlines && node.is_cluster_lead && (node.cluster_id ?? 0) > 0) return true;
        if (currentScale >= 1.3 && (node.importance_score || 5) >= 7) return true;
        return false;
      })
      .sort((a, b) => {
        const priA = (a.isHovered ? 1000 : 0) + (a.isSelected ? 500 : 0) + (a.node.is_cluster_lead ? 100 : 0) + (a.node.importance_score || 5);
        const priB = (b.isHovered ? 1000 : 0) + (b.isSelected ? 500 : 0) + (b.node.is_cluster_lead ? 100 : 0) + (b.node.importance_score || 5);
        return priB - priA;
      });

    labelCandidates.forEach(({ node, scX, scY, radius, isHovered, isSelected }) => {
      const rawTitle = node.headline || node.title;
      const shortTitle = truncateWords(rawTitle, isHovered || isSelected ? 42 : 30);

      ctx.save();
      ctx.font =
        node.is_cluster_lead || isHovered || isSelected
          ? '600 9.5px system-ui, -apple-system, sans-serif'
          : '500 9px system-ui, -apple-system, sans-serif';
      const tWidth = ctx.measureText(shortTitle).width;
      const boxW = tWidth + 10;
      const boxH = 15;

      // Try placing right, left, below, or above the node
      const placements = [
        { x1: scX + radius + 5, y1: scY - boxH / 2 },
        { x1: scX - radius - 5 - boxW, y1: scY - boxH / 2 },
        { x1: scX - boxW / 2, y1: scY + radius + 5 },
        { x1: scX - boxW / 2, y1: scY - radius - 5 - boxH },
      ];

      let chosenPlacement: { x1: number; y1: number; x2: number; y2: number } | null = null;
      for (const p of placements) {
        const rect = { x1: p.x1, y1: p.y1, x2: p.x1 + boxW, y2: p.y1 + boxH };
        const overlaps = occupiedLabelRects.some((r) => rectsOverlap(rect, r, 2));
        if (!overlaps || isHovered || isSelected) {
          chosenPlacement = rect;
          break;
        }
      }

      if (!chosenPlacement) {
        ctx.restore();
        return;
      }

      occupiedLabelRects.push(chosenPlacement);

      ctx.fillStyle =
        isHovered || isSelected
          ? 'rgba(9, 9, 11, 0.95)'
          : 'rgba(15, 23, 42, 0.88)';
      ctx.beginPath();
      ctx.roundRect(chosenPlacement.x1, chosenPlacement.y1, boxW, boxH, 3.5);
      ctx.fill();

      ctx.strokeStyle =
        isHovered || isSelected
          ? 'rgba(6, 182, 212, 0.8)'
          : node.is_cluster_lead
          ? 'rgba(56, 189, 248, 0.4)'
          : 'rgba(255, 255, 255, 0.14)';
      ctx.lineWidth = 0.85;
      ctx.stroke();

      ctx.textAlign = 'left';
      ctx.fillStyle =
        isHovered || isSelected
          ? '#ffffff'
          : node.is_cluster_lead
          ? '#f1f5f9'
          : '#cbd5e1';
      ctx.fillText(shortTitle, chosenPlacement.x1 + 5, chosenPlacement.y1 + 10.5);
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
    showKeyHeadlines,
    getNodeFill,
    getNodeBaseRadius,
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
        const radius = getNodeBaseRadius(node) + 2;

        const dx = screenX - scX;
        const dy = screenY - scY;
        if (dx * dx + dy * dy <= (radius + 5) * (radius + 5)) {
          return node;
        }
      }
      return null;
    },
    [data.nodes, dimensions, selectedClusterFilter, getNodeBaseRadius]
  );

  // Hit-test for clickable cluster pills on the canvas
  const getClusterPillAtScreenPos = useCallback((screenX: number, screenY: number): number | null => {
    for (const box of clusterPillBoxesRef.current) {
      if (screenX >= box.x1 && screenX <= box.x2 && screenY >= box.y1 && screenY <= box.y2) {
        return box.id;
      }
    }
    return null;
  }, []);

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

    const hitClusterPill = getClusterPillAtScreenPos(clickX, clickY);
    if (hitClusterPill !== null) {
      handleJumpToCluster(hitClusterPill);
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
    const hitPill = hitNode ? null : getClusterPillAtScreenPos(mouseX, mouseY);
    if (canvasRef.current) {
      canvasRef.current.style.cursor = hitNode || hitPill !== null ? 'pointer' : 'grab';
    }
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
    if (canvasRef.current) {
      canvasRef.current.style.cursor = hoveredNode ? 'pointer' : 'grab';
    }
  };

  // Release dragging even if mouse is released outside canvas boundary
  useEffect(() => {
    const handleGlobalMouseUp = () => {
      if (isDraggingRef.current) {
        isDraggingRef.current = false;
        if (canvasRef.current) {
          canvasRef.current.style.cursor = hoveredNode ? 'pointer' : 'grab';
        }
      }
    };
    window.addEventListener('mouseup', handleGlobalMouseUp);
    return () => {
      window.removeEventListener('mouseup', handleGlobalMouseUp);
    };
  }, [hoveredNode]);

  // Wheel zoom centered at mouse position
  // Uses a native, non-passive event listener so e.preventDefault() reliably prevents the browser from scrolling the page
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleNativeWheel = (e: WheelEvent) => {
      // Stop the browser window / page from scrolling
      e.preventDefault();
      e.stopPropagation();

      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      if (!rect || rect.width === 0 || rect.height === 0) return;

      // Smooth zoom factor: handles both discrete mouse wheel ticks and smooth trackpad gestures
      let zoomFactor = 1;
      if (Math.abs(e.deltaY) >= 50) {
        zoomFactor = e.deltaY < 0 ? 1.15 : 0.88;
      } else {
        zoomFactor = Math.exp(-e.deltaY * 0.0035);
      }

      const newScale = Math.min(Math.max(scaleRef.current * zoomFactor, 0.45), 5.0);
      const mouseX = e.clientX - rect.left - rect.width / 2;
      const mouseY = e.clientY - rect.top - rect.height / 2;
      const scaleRatio = newScale / scaleRef.current;

      offsetRef.current = {
        x: mouseX - (mouseX - offsetRef.current.x) * scaleRatio,
        y: mouseY - (mouseY - offsetRef.current.y) * scaleRatio,
      };

      scaleRef.current = newScale;
      if (animFrameIdRef.current) cancelAnimationFrame(animFrameIdRef.current);
      animFrameIdRef.current = requestAnimationFrame(renderCanvas);
    };

    container.addEventListener('wheel', handleNativeWheel, { passive: false });
    return () => {
      container.removeEventListener('wheel', handleNativeWheel);
    };
  }, [renderCanvas]);

  // Horizontal wheel scroll for galaxy constellation pills bar to avoid scrolling the page vertically
  useEffect(() => {
    const bar = galaxiesBarRef.current;
    if (!bar) return;

    const handleBarWheel = (e: WheelEvent) => {
      if (bar.scrollWidth > bar.clientWidth && Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault();
        bar.scrollLeft += e.deltaY;
      }
    };

    bar.addEventListener('wheel', handleBarWheel, { passive: false });
    return () => {
      bar.removeEventListener('wheel', handleBarWheel);
    };
  }, [data.clusters]);

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

    if (clusterId === 0) {
      scaleRef.current = 0.96;
      offsetRef.current = { x: 0, y: 0 };
      renderCanvas();
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

          {/* Key Headlines Callouts Toggle */}
          <button
            onClick={() => setShowKeyHeadlines((prev) => !prev)}
            className={cn(
              'flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium border transition-colors',
              showKeyHeadlines
                ? 'bg-accent-cyan/15 text-accent-cyan border-accent-cyan/30'
                : 'bg-surface-card text-zinc-400 hover:text-zinc-200 border-white/5'
            )}
            title="Show or hide lead story headline callouts on the map at default zoom"
          >
            <Tag className="w-3 h-3" />
            <span>Key Headlines</span>
          </button>
        </div>

        {/* Right: Quota Monitor & Actions */}
        <div className="flex items-center space-x-2 flex-wrap gap-y-2">
          {/* Quota Counter Pill */}
          <div
            title="Daily Gemini Embedding API Quotas (1K RPD each, batched) — Click to view Rate Limits"
            className="hidden lg:flex items-center space-x-2 px-2.5 py-1 rounded-md bg-surface-card border border-white/5 text-[10px] font-mono text-zinc-300"
          >
            <Cpu className="w-3 h-3 text-accent-cyan" />
            <a
              href="https://aistudio.google.com/rate-limit"
              target="_blank"
              rel="noopener noreferrer"
              title="Open Google AI Studio Rate Limits"
              className="inline-flex items-center space-x-1.5 hover:text-accent-cyan transition-colors"
            >
              <span>
                Gemini 1: <strong className="text-zinc-100">{m1Count}</strong>/1K
              </span>
              <span className="text-zinc-600">|</span>
              <span>
                Gemini 2: <strong className="text-zinc-100">{m2Count}</strong>/1K
              </span>
              <ExternalLink className="w-2.5 h-2.5 text-accent-cyan" />
            </a>
            <span className="text-zinc-600">|</span>
            <a
              href="https://huggingface.co/settings/billing"
              target="_blank"
              rel="noopener noreferrer"
              title="Open Hugging Face Rate Limits & Billing"
              className="inline-flex items-center space-x-1 text-emerald-400 hover:text-emerald-300 font-semibold transition-colors"
            >
              <span>HF TEI: Unlimited</span>
              <ExternalLink className="w-2.5 h-2.5" />
            </a>
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

          {/* Map Guide / Explainer */}
          <button
            onClick={() => setShowGuide(true)}
            className="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised border border-white/5 text-zinc-300 hover:text-accent-cyan transition-colors text-xs font-medium"
            title="How to read the Relevance Map & Trustworthiness scores"
          >
            <HelpCircle className="w-3.5 h-3.5 text-accent-cyan" />
            <span className="hidden sm:inline">Map Guide</span>
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
        <div
          ref={galaxiesBarRef}
          className="z-10 flex items-center space-x-1.5 px-3 py-1.5 hairline-b bg-canvas-subtle/50 overflow-x-auto scrollbar-none text-xs touch-none overscroll-contain"
        >
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
              title={c.id === 0 ? 'Single-source uncorroborated reports on the outer perimeter' : c.label}
              className={cn(
                'px-2 py-0.5 rounded text-[11px] font-medium whitespace-nowrap transition-colors',
                selectedClusterFilter === c.id
                  ? 'bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30'
                  : c.id === 0
                  ? 'bg-rose-950/30 text-rose-300/90 hover:text-rose-200 border border-rose-500/20'
                  : 'bg-surface-card text-zinc-400 hover:text-zinc-200 border border-white/5'
              )}
            >
              {c.id === 0
                ? `Perimeter (${c.size})`
                : `C#${c.id}: ${truncateWords(c.label, 22)} (${c.size})`}
            </button>
          ))}
        </div>
      )}

      {/* Hardware-Accelerated 60 FPS HTML5 Canvas Map Area */}
      <div
        ref={containerRef}
        className="relative flex-1 w-full h-full overflow-hidden bg-canvas-base touch-none overscroll-none"
      >
        <canvas
          ref={canvasRef}
          width={dimensions.width * (typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1)}
          height={dimensions.height * (typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1)}
          style={{
            width: `${dimensions.width}px`,
            height: `${dimensions.height}px`,
            display: 'block',
            touchAction: 'none',
          }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          className="w-full h-full cursor-grab active:cursor-grabbing touch-none overscroll-none"
        />

        {/* Hover Tooltip Overlay Card with Trust Rationale & Corroborating Outlets */}
        {hoveredNode && !selectedNode && (
          <div
            className="absolute pointer-events-none z-30 max-w-sm p-3.5 rounded-xl bg-surface-card/95 backdrop-blur-md border border-white/10 shadow-2xl space-y-2 transition-all"
            style={{
              left: `${Math.min(tooltipPos.x + 15, dimensions.width - 340)}px`,
              top: `${Math.min(tooltipPos.y + 15, dimensions.height - 190)}px`,
            }}
          >
            {/* Source & Trust Badge */}
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-surface-raised border border-white/5 text-zinc-300 truncate max-w-[180px]">
                {hoveredNode.source_name}
              </span>
              <span
                className="text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
                style={{
                  color: getNodeFill(hoveredNode),
                  borderColor: `${getNodeFill(hoveredNode)}40`,
                  backgroundColor: `${getNodeFill(hoveredNode)}15`,
                }}
              >
                {hoveredNode.trustworthiness.score}% • {hoveredNode.trustworthiness.label}
              </span>
            </div>

            {/* Headline */}
            <p className="text-xs font-bold text-zinc-100 leading-snug">
              {hoveredNode.headline || hoveredNode.title}
            </p>

            {/* Verification Verdict & Rationale Box */}
            <div className="p-2 rounded-lg bg-surface-raised/80 border border-white/5 space-y-1 text-[11px]">
              <div className="flex items-center justify-between text-zinc-400 font-semibold text-[10px]">
                <span>Trust Rationale:</span>
                {hoveredNode.cluster_label && (
                  <span className="text-accent-cyan truncate max-w-[140px]">
                    {hoveredNode.cluster_label}
                  </span>
                )}
              </div>
              <p className="text-zinc-300 leading-relaxed text-[10.5px]">
                {hoveredNode.trustworthiness.verdict}
              </p>
              {hoveredNode.trustworthiness.independent_sources &&
                hoveredNode.trustworthiness.independent_sources.length > 0 && (
                  <div className="pt-1 flex flex-wrap gap-1">
                    {hoveredNode.trustworthiness.independent_sources.slice(0, 3).map((src, i) => (
                      <span
                        key={i}
                        className="text-[9.5px] px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 font-mono"
                      >
                        ✓ {src}
                      </span>
                    ))}
                  </div>
                )}
            </div>

            {/* Summary */}
            <p className="text-[10.5px] text-zinc-400 line-clamp-2 leading-relaxed">
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

      {/* Map Intel & Trustworthiness Guide Modal */}
      {showGuide && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-fade-in">
          <div className="relative w-full max-w-2xl max-h-[88vh] flex flex-col rounded-2xl bg-canvas-subtle border border-white/10 shadow-2xl overflow-hidden text-zinc-200">
            {/* Modal Header */}
            <div className="flex items-center justify-between p-5 hairline-b bg-canvas-base/60 backdrop-blur-md">
              <div className="flex items-center space-x-3">
                <div className="p-2 rounded-xl bg-accent-cyan/10 border border-accent-cyan/20 text-accent-cyan">
                  <Compass className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-zinc-100">Relevance Map & Trustworthiness Guide</h3>
                  <p className="text-xs text-zinc-400">How to interpret the Core-to-Periphery Consensus Radar, story galaxies, and trust ratings</p>
                </div>
              </div>
              <button
                onClick={() => setShowGuide(false)}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-surface-raised transition-colors"
                title="Close Guide"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Scrollable Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-6 text-xs text-zinc-300 scrollbar-thin">
              {/* 1. The Core Concept */}
              <div className="space-y-2">
                <h4 className="flex items-center space-x-2 text-sm font-semibold text-zinc-100">
                  <span className="w-2 h-2 rounded-full bg-accent-cyan" />
                  <span>1. Core-to-Periphery Consensus Radar</span>
                </h4>
                <p className="leading-relaxed text-zinc-300">
                  This map combines <strong>semantic similarity</strong> (angle) with <strong>multi-source consensus</strong> (radial distance from center):
                </p>
                <div className="p-3 rounded-lg bg-surface-card border border-white/5 space-y-1.5">
                  <div className="flex items-start space-x-2">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />
                    <span><strong>Center = Core Consensus:</strong> The most heavily corroborated, high-priority story galaxy anchors the inner core (`C#1`), surrounded by secondary corroborated story galaxies in the middle orbit.</span>
                  </div>
                  <div className="flex items-start space-x-2">
                    <CheckCircle2 className="w-4 h-4 text-accent-cyan mt-0.5 shrink-0" />
                    <span><strong>Angle = Semantic Affinity:</strong> Story galaxies discussing related geopolitical fronts or topics sit at similar compass angles.</span>
                  </div>
                  <div className="flex items-start space-x-2">
                    <CheckCircle2 className="w-4 h-4 text-rose-400 mt-0.5 shrink-0" />
                    <span><strong>Outer Ring = Unverified Perimeter:</strong> Single-source or isolated dispatches without cross-outlet corroboration sit on the outer perimeter ring until confirmed.</span>
                  </div>
                </div>
              </div>

              {/* 2. Story Galaxies */}
              <div className="space-y-2">
                <h4 className="flex items-center space-x-2 text-sm font-semibold text-zinc-100">
                  <span className="w-2 h-2 rounded-full bg-purple-400" />
                  <span>2. Topic Galaxies &amp; Lead Anchor Nodes</span>
                </h4>
                <p className="leading-relaxed text-zinc-300">
                  Reports are grouped into <strong>coherent story galaxies</strong> using cosine-distance threshold clustering so only genuinely related reports share a galaxy.
                </p>
                <div className="p-3 rounded-lg bg-surface-card border border-white/5 space-y-2">
                  <p>
                    Click any pill in the <strong>Galaxies bar</strong> or <strong>directly on the map canvas</strong> to fly the camera into that story cluster.
                  </p>
                  <p className="text-zinc-400">
                    Inside each galaxy, the <strong>Medoid Lead Node</strong> (marked with a bright white center dot) sits at the center of the cluster and displays its headline when <strong>Key Headlines</strong> is active.
                  </p>
                </div>
              </div>

              {/* 3. Corroboration Edges */}
              <div className="space-y-2">
                <h4 className="flex items-center space-x-2 text-sm font-semibold text-zinc-100">
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  <span>3. Corroboration Edges (Connecting Lines)</span>
                </h4>
                <p className="leading-relaxed text-zinc-300">
                  Curved lines connect reports within a galaxy that <strong>directly corroborate each other</strong> across semantic content, entities, and time window. Cross-cluster links appear on hover or selection.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <div className="p-2.5 rounded-lg bg-surface-card border border-white/5 space-y-1">
                    <span className="font-semibold text-emerald-400">Densely Interconnected</span>
                    <p className="text-[11px] text-zinc-400">A tightly linked nexus signifies a major breaking story confirmed by multiple independent outlets (Telegram, NewsAPI, RSS).</p>
                  </div>
                  <div className="p-2.5 rounded-lg bg-surface-card border border-white/5 space-y-1">
                    <span className="font-semibold text-amber-400">Isolated / Unlinked</span>
                    <p className="text-[11px] text-zinc-400">A lone report with zero edges has not yet been echoed by any other source. Treat with caution pending secondary confirmation.</p>
                  </div>
                </div>
              </div>

              {/* 4. Trustworthiness Scoring */}
              <div className="space-y-2">
                <h4 className="flex items-center space-x-2 text-sm font-semibold text-zinc-100">
                  <span className="w-2 h-2 rounded-full bg-emerald-500" />
                  <span>4. Trustworthiness Calibration</span>
                </h4>
                <p className="leading-relaxed text-zinc-300">
                  Trust is dynamically calibrated based on independent outlet count, semantic &amp; temporal corroboration, and domain reputation:
                </p>
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  <div className="flex items-start space-x-2 p-2 rounded-lg bg-surface-card border border-emerald-500/20">
                    <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 mt-1 shrink-0" />
                    <div>
                      <strong className="text-emerald-400">High Trust (&ge; 80%)</strong>
                      <p className="text-zinc-400">Corroborated by 3+ independent sources or verified tier-1 agencies.</p>
                    </div>
                  </div>
                  <div className="flex items-start space-x-2 p-2 rounded-lg bg-surface-card border border-cyan-500/20">
                    <div className="w-2.5 h-2.5 rounded-full bg-cyan-500 mt-1 shrink-0" />
                    <div>
                      <strong className="text-cyan-400">Verified (60-79%)</strong>
                      <p className="text-zinc-400">Confirmed by 2 separate dispatches. Developing consensus.</p>
                    </div>
                  </div>
                  <div className="flex items-start space-x-2 p-2 rounded-lg bg-surface-card border border-amber-500/20">
                    <div className="w-2.5 h-2.5 rounded-full bg-amber-500 mt-1 shrink-0" />
                    <div>
                      <strong className="text-amber-400">Single-Source (40-59%)</strong>
                      <p className="text-zinc-400">Only 1 source reporting. High potential for rumor or unverified claim.</p>
                    </div>
                  </div>
                  <div className="flex items-start space-x-2 p-2 rounded-lg bg-surface-card border border-rose-500/20">
                    <div className="w-2.5 h-2.5 rounded-full bg-rose-500 mt-1 shrink-0" />
                    <div>
                      <strong className="text-rose-400">Outlier (&lt; 40%)</strong>
                      <p className="text-zinc-400">Isolated fringe claim with no semantic matches across any feed.</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* 5. Navigation & Shortcuts */}
              <div className="space-y-2">
                <h4 className="flex items-center space-x-2 text-sm font-semibold text-zinc-100">
                  <span className="w-2 h-2 rounded-full bg-sky-400" />
                  <span>5. Navigation &amp; Shortcuts</span>
                </h4>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
                  <div className="p-2 rounded-lg bg-surface-card border border-white/5">
                    <span className="font-mono font-bold text-accent-cyan">Click Node / Pill</span>
                    <p className="text-[10px] text-zinc-400 mt-0.5">Inspect report or zoom into galaxy</p>
                  </div>
                  <div className="p-2 rounded-lg bg-surface-card border border-white/5">
                    <span className="font-mono font-bold text-accent-cyan">Drag</span>
                    <p className="text-[10px] text-zinc-400 mt-0.5">Smooth Pan Map (60 FPS)</p>
                  </div>
                  <div className="p-2 rounded-lg bg-surface-card border border-white/5">
                    <span className="font-mono font-bold text-accent-cyan">Scroll</span>
                    <p className="text-[10px] text-zinc-400 mt-0.5">Zoom in / out at cursor</p>
                  </div>
                  <div className="p-2 rounded-lg bg-surface-card border border-white/5">
                    <span className="font-mono font-bold text-accent-cyan">Key Headlines</span>
                    <p className="text-[10px] text-zinc-400 mt-0.5">Toggle lead titles on map</p>
                  </div>
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="p-4 hairline-t bg-canvas-base/80 flex items-center justify-between">
              <span className="text-[11px] text-zinc-500">
                Powered by Gemini 3072d Embeddings &amp; Cosine-Threshold Agglomerative Clustering
              </span>
              <button
                onClick={() => setShowGuide(false)}
                className="px-4 py-1.5 rounded-lg bg-accent-cyan text-zinc-950 font-semibold hover:bg-accent-cyan/90 transition-colors text-xs"
              >
                Got It
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
