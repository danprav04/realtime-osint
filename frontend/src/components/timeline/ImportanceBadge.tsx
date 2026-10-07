'use client';

import React from 'react';
import { getScoreColors } from '@/lib/utils';
import { AlertTriangle, Flame, Info, CheckCircle2 } from 'lucide-react';

interface ImportanceBadgeProps {
  score?: number | null;
  showIcon?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

export function ImportanceBadge({ score, showIcon = true, size = 'md' }: ImportanceBadgeProps) {
  const numScore = Number(score) || 0;
  const colors = getScoreColors(numScore);

  const getIcon = () => {
    if (numScore >= 9) return <Flame className="w-3 h-3 text-rose-400" />;
    if (numScore >= 7) return <AlertTriangle className="w-3 h-3 text-amber-400" />;
    if (numScore >= 4) return <Info className="w-3 h-3 text-cyan-400" />;
    return <CheckCircle2 className="w-3 h-3 text-slate-400" />;
  };

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5 space-x-1',
    md: 'text-xs px-2 py-0.5 space-x-1.5',
    lg: 'text-sm px-3 py-1 space-x-2',
  }[size];

  const isPulse = numScore >= 9;

  return (
    <span
      className={`inline-flex items-center font-mono font-medium rounded-full border hairline-border ${colors.bg} ${colors.text} ${colors.border} ${colors.glow} ${sizeClasses} ${isPulse ? 'animate-pulse' : ''} tabular`}
      title={`Intelligence Priority: ${colors.label} (Score ${numScore}/10)`}
    >
      {showIcon && getIcon()}
      <span>
        <strong className="font-bold">{numScore}</strong>
        <span className="opacity-70 text-[10px]">/10</span>
      </span>
      <span className="text-[10px] uppercase font-sans tracking-wider ml-0.5 opacity-90 hidden sm:inline">
        {colors.label}
      </span>
    </span>
  );
}
