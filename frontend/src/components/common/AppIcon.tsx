'use client';

import React from 'react';
import { cn } from '@/lib/utils';

export interface AppIconProps {
  /** Size in pixels (default: 32) */
  size?: number;
  className?: string;
  /** Whether to animate the radar sweep */
  animated?: boolean;
  /** Whether to show the top-right ping beacon */
  withBeacon?: boolean;
  /** Variant of the icon: 'default' | 'minimal' | 'radar' */
  variant?: 'default' | 'minimal' | 'radar';
  /** Optional title for accessibility */
  title?: string;
}

export function AppIcon({
  size = 32,
  className,
  animated = false,
  withBeacon = false,
  variant = 'default',
  title = 'Real-time OSINT Intelligence Icon',
}: AppIconProps) {
  return (
    <div
      className={cn('relative inline-flex items-center justify-center shrink-0 select-none', className)}
      style={{ width: size, height: size }}
    >
      <svg
        viewBox="0 0 512 512"
        width={size}
        height={size}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className="w-full h-full drop-shadow-sm"
        aria-label={title}
        role="img"
      >
        <defs>
          <linearGradient id="ai-bg-grad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#0a0d14" />
            <stop offset="50%" stopColor="#0e131f" />
            <stop offset="100%" stopColor="#080a10" />
          </linearGradient>

          <linearGradient id="ai-border-grad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#06b6d4" stopOpacity="0.85" />
            <stop offset="50%" stopColor="#3b82f6" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#06b6d4" stopOpacity="0.75" />
          </linearGradient>

          <linearGradient id="ai-cyan-grad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#22d3ee" />
            <stop offset="50%" stopColor="#06b6d4" />
            <stop offset="100%" stopColor="#0284c7" />
          </linearGradient>

          <radialGradient id="ai-sweep-grad" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#06b6d4" stopOpacity="0.3" />
            <stop offset="55%" stopColor="#06b6d4" stopOpacity="0.08" />
            <stop offset="100%" stopColor="#06b6d4" stopOpacity="0" />
          </radialGradient>

          <radialGradient id="ai-core-flare" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="35%" stopColor="#22d3ee" />
            <stop offset="70%" stopColor="#0891b2" stopOpacity="0.8" />
            <stop offset="100%" stopColor="#0891b2" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Squircle Housing */}
        {variant !== 'minimal' && (
          <rect
            x="16"
            y="16"
            width="480"
            height="480"
            rx="112"
            fill="url(#ai-bg-grad)"
            stroke="url(#ai-border-grad)"
            strokeWidth="8"
          />
        )}

        {/* Tactical Corner Telemetry Brackets */}
        {variant === 'default' && (
          <>
            <path d="M 54 114 L 54 54 L 114 54" stroke="#06b6d4" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" opacity="0.5" />
            <path d="M 458 114 L 458 54 L 398 54" stroke="#06b6d4" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" opacity="0.5" />
            <path d="M 54 398 L 54 458 L 114 458" stroke="#06b6d4" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" opacity="0.5" />
            <path d="M 458 398 L 458 458 L 398 458" stroke="#06b6d4" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" opacity="0.5" />
          </>
        )}

        {/* Outer Radar Sweep Radial Background */}
        <circle cx="256" cy="256" r="184" fill="url(#ai-sweep-grad)" />

        {/* Outer Radar Ring */}
        <circle
          cx="256"
          cy="256"
          r="184"
          stroke="#06b6d4"
          strokeWidth="6"
          strokeOpacity="0.4"
          strokeDasharray="16 12"
        />

        {/* Middle Radar Ring */}
        <circle cx="256" cy="256" r="128" stroke="#22d3ee" strokeWidth="7" strokeOpacity="0.65" />

        {/* Inner Reticle Ring */}
        <circle
          cx="256"
          cy="256"
          r="76"
          stroke="#38bdf8"
          strokeWidth="6"
          strokeOpacity="0.85"
          strokeDasharray="8 8"
        />

        {/* Cardinal Crosshair Ticks */}
        <line x1="256" y1="44" x2="256" y2="92" stroke="#22d3ee" strokeWidth="8" strokeLinecap="round" />
        <line x1="256" y1="420" x2="256" y2="468" stroke="#22d3ee" strokeWidth="8" strokeLinecap="round" />
        <line x1="44" y1="256" x2="92" y2="256" stroke="#22d3ee" strokeWidth="8" strokeLinecap="round" />
        <line x1="420" y1="256" x2="468" y2="256" stroke="#22d3ee" strokeWidth="8" strokeLinecap="round" />

        {/* Sub-cardinal Precision Ticks */}
        <line x1="256" y1="144" x2="256" y2="164" stroke="#06b6d4" strokeWidth="5" strokeLinecap="round" opacity="0.75" />
        <line x1="256" y1="348" x2="256" y2="368" stroke="#06b6d4" strokeWidth="5" strokeLinecap="round" opacity="0.75" />
        <line x1="144" y1="256" x2="164" y2="256" stroke="#06b6d4" strokeWidth="5" strokeLinecap="round" opacity="0.75" />
        <line x1="348" y1="256" x2="368" y2="256" stroke="#06b6d4" strokeWidth="5" strokeLinecap="round" opacity="0.75" />

        {/* Radar Sweep Sector Beam (with optional CSS rotation animation) */}
        <g className={cn(animated && 'origin-center animate-[spin_4s_linear_infinite]')}>
          <path d="M 256 256 L 386 126 A 184 184 0 0 1 436 210 Z" fill="#06b6d4" fillOpacity="0.22" />
        </g>

        {/* Surveillance Eye Aperture */}
        <path
          d="M 166 256 C 196 204 316 204 346 256 C 316 308 196 308 166 256 Z"
          stroke="url(#ai-cyan-grad)"
          strokeWidth="9"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="#0c1322"
          fillOpacity="0.88"
        />

        {/* Center Target Core Beacon */}
        <circle cx="256" cy="256" r="42" fill="url(#ai-core-flare)" />
        <circle cx="256" cy="256" r="22" fill="#06b6d4" stroke="#ffffff" strokeWidth="5" />
        <circle cx="256" cy="256" r="8" fill="#ffffff" />

        {/* Satellite Signal Blips */}
        <circle cx="372" cy="170" r="9" fill="#10b981" />
        <circle cx="372" cy="170" r="16" stroke="#10b981" strokeWidth="3" strokeOpacity="0.6" />

        <circle cx="152" cy="334" r="7" fill="#f59e0b" />
        <circle cx="152" cy="334" r="13" stroke="#f59e0b" strokeWidth="3" strokeOpacity="0.5" />
      </svg>

      {/* Optional Top-Right Live Pulse Beacon */}
      {withBeacon && (
        <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5 pointer-events-none">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent-cyan opacity-80" />
          <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-accent-cyan shadow-[0_0_8px_rgba(6,182,212,0.8)]" />
        </span>
      )}
    </div>
  );
}

export interface AppLogoProps {
  className?: string;
  iconSize?: number;
  withBeacon?: boolean;
  showSubtitle?: boolean;
}

export function AppLogo({
  className,
  iconSize = 32,
  withBeacon = true,
  showSubtitle = true,
}: AppLogoProps) {
  return (
    <div className={cn('flex items-center space-x-2.5 group select-none', className)}>
      <div className="relative flex items-center justify-center rounded-lg bg-surface-card hairline-border group-hover:border-accent-cyan/50 transition-colors p-0.5">
        <AppIcon size={iconSize} withBeacon={withBeacon} />
      </div>
      <div className="flex flex-col">
        <span className="text-xs font-bold tracking-tight text-zinc-100 uppercase flex items-center gap-1">
          OSINT <span className="text-accent-cyan">REALTIME</span>
        </span>
        {showSubtitle && (
          <span className="text-[10px] text-zinc-400 font-mono tracking-wider">v1.0 • PRODUCTION</span>
        )}
      </div>
    </div>
  );
}
