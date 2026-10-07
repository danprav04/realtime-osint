'use client';

import React, { useState } from 'react';
import { Activity } from 'lucide-react';

interface VolumePoint {
  hour: string;
  count: number;
}

interface VolumeChartProps {
  data: VolumePoint[];
}

export function VolumeChart({ data }: VolumeChartProps) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  // If data is empty or too short, fill 24 hour slots with 0s
  const chartData = data && data.length > 0 ? data : Array.from({ length: 24 }).map((_, i) => ({
    hour: `${i.toString().padStart(2, '0')}:00`,
    count: 0,
  }));

  const maxVal = Math.max(...chartData.map((d) => d.count), 10);
  const height = 180;
  const width = 600;
  const paddingX = 30;
  const paddingY = 25;

  const innerWidth = width - paddingX * 2;
  const innerHeight = height - paddingY * 2;

  // Calculate points
  const points = chartData.map((item, index) => {
    const x = paddingX + (index / (chartData.length - 1)) * innerWidth;
    const y = height - paddingY - (item.count / maxVal) * innerHeight;
    return { x, y, ...item };
  });

  // Create SVG path
  const linePath = points.reduce((acc, curr, idx) => {
    return idx === 0 ? `M ${curr.x} ${curr.y}` : `${acc} L ${curr.x} ${curr.y}`;
  }, '');

  // Create Area gradient path
  const areaPath = `${linePath} L ${points[points.length - 1].x} ${height - paddingY} L ${points[0].x} ${height - paddingY} Z`;

  const hoveredItem = hoverIndex !== null ? points[hoverIndex] : null;

  return (
    <div className="card-slick rounded-xl p-5 flex flex-col justify-between">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 rounded-lg bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/20">
            <Activity className="w-4 h-4" />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-zinc-100 tracking-tight">24-Hour Ingestion Velocity</h4>
            <p className="text-xs text-zinc-500">Real-time intelligence flow volume over time</p>
          </div>
        </div>

        {hoveredItem && (
          <div className="flex items-center space-x-2 px-3 py-1 rounded bg-surface-raised hairline-border text-xs tabular font-mono">
            <span className="text-zinc-400">{hoveredItem.hour}:</span>
            <span className="text-accent-cyan font-bold">{hoveredItem.count} events</span>
          </div>
        )}
      </div>

      {/* SVG Canvas */}
      <div className="relative w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-44 overflow-visible"
          preserveAspectRatio="none"
        >
          <defs>
            <linearGradient id="volumeGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#06B6D4" stopOpacity="0.35" />
              <stop offset="85%" stopColor="#06B6D4" stopOpacity="0.02" />
              <stop offset="100%" stopColor="#06B6D4" stopOpacity="0.0" />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          <line
            x1={paddingX}
            y1={paddingY}
            x2={width - paddingX}
            y2={paddingY}
            stroke="rgba(255,255,255,0.05)"
            strokeDasharray="4 4"
          />
          <line
            x1={paddingX}
            y1={paddingY + innerHeight / 2}
            x2={width - paddingX}
            y2={paddingY + innerHeight / 2}
            stroke="rgba(255,255,255,0.05)"
            strokeDasharray="4 4"
          />
          <line
            x1={paddingX}
            y1={height - paddingY}
            x2={width - paddingX}
            y2={height - paddingY}
            stroke="rgba(255,255,255,0.1)"
          />

          {/* Area underfill */}
          <path d={areaPath} fill="url(#volumeGradient)" />

          {/* Line stroke */}
          <path
            d={linePath}
            fill="none"
            stroke="#06B6D4"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Hover points */}
          {points.map((pt, idx) => (
            <g
              key={idx}
              onMouseEnter={() => setHoverIndex(idx)}
              onMouseLeave={() => setHoverIndex(null)}
              className="cursor-pointer"
            >
              {/* Invisible touch target */}
              <rect
                x={pt.x - innerWidth / chartData.length / 2}
                y={0}
                width={innerWidth / chartData.length}
                height={height}
                fill="transparent"
              />
              {/* Point circle */}
              <circle
                cx={pt.x}
                cy={pt.y}
                r={hoverIndex === idx ? 5 : 2.5}
                className={`transition-all ${
                  hoverIndex === idx
                    ? 'fill-accent-cyan stroke-surface-overlay stroke-2'
                    : 'fill-accent-cyan/80'
                }`}
              />
            </g>
          ))}
        </svg>
      </div>

      {/* X-Axis labels */}
      <div className="flex justify-between items-center text-[10px] text-zinc-500 font-mono pt-2 border-t border-white/5 tabular">
        <span>{chartData[0]?.hour || '00:00'}</span>
        <span>{chartData[Math.floor(chartData.length / 2)]?.hour || '12:00'}</span>
        <span>{chartData[chartData.length - 1]?.hour || 'Now'}</span>
      </div>
    </div>
  );
}
