'use client';

import React from 'react';
import { Rss, Send, Newspaper, MessageSquare, Anchor, Radio, Globe, Activity, ShieldAlert } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SourceBadgeProps {
  sourceType: string;
  sourceName?: string;
  showName?: boolean;
}

export function SourceBadge({ sourceType, sourceName, showName = true }: SourceBadgeProps) {
  const normalizedType = (sourceType || '').toLowerCase();

  const getSourceConfig = (type: string) => {
    switch (type) {
      case 'rss':
        return {
          icon: Rss,
          label: 'RSS',
          textColor: 'text-amber-400',
          bgColor: 'bg-amber-400/10',
          borderColor: 'border-amber-400/20',
        };
      case 'telegram':
        return {
          icon: Send,
          label: 'Telegram',
          textColor: 'text-sky-400',
          bgColor: 'bg-sky-400/10',
          borderColor: 'border-sky-400/20',
        };
      case 'newsapi':
      case 'news':
        return {
          icon: Newspaper,
          label: 'News API',
          textColor: 'text-emerald-400',
          bgColor: 'bg-emerald-400/10',
          borderColor: 'border-emerald-400/20',
        };
      case 'reddit':
        return {
          icon: MessageSquare,
          label: 'Reddit',
          textColor: 'text-rose-400',
          bgColor: 'bg-rose-400/10',
          borderColor: 'border-rose-400/20',
        };
      case 'bluesky':
      case 'bsky':
        return {
          icon: Globe,
          label: 'Bluesky',
          textColor: 'text-blue-400',
          bgColor: 'bg-blue-400/10',
          borderColor: 'border-blue-400/20',
        };
      case 'mastodon':
      case 'fediverse':
        return {
          icon: Globe,
          label: 'Mastodon',
          textColor: 'text-purple-400',
          bgColor: 'bg-purple-400/10',
          borderColor: 'border-purple-400/20',
        };
      case 'seismic':
      case 'emsc':
      case 'geophysical':
        return {
          icon: Activity,
          label: 'EMSC Seismic',
          textColor: 'text-orange-400',
          bgColor: 'bg-orange-400/10',
          borderColor: 'border-orange-400/20',
        };
      case 'ukmto':
        return {
          icon: ShieldAlert,
          label: 'UKMTO Maritime',
          textColor: 'text-teal-400',
          bgColor: 'bg-teal-400/10',
          borderColor: 'border-teal-400/20',
        };
      case 'ais':
      case 'maritime_ais':
        return {
          icon: Anchor,
          label: 'AIS Vessel',
          textColor: 'text-cyan-400',
          bgColor: 'bg-cyan-400/10',
          borderColor: 'border-cyan-400/20',
        };
      default:
        return {
          icon: Radio,
          label: sourceType.toUpperCase() || 'STREAM',
          textColor: 'text-zinc-400',
          bgColor: 'bg-zinc-400/10',
          borderColor: 'border-zinc-400/20',
        };
    }
  };

  const config = getSourceConfig(normalizedType);
  const Icon = config.icon;

  return (
    <span
      className={cn(
        'inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-md text-[11px] font-medium border hairline-border',
        config.bgColor,
        config.textColor,
        config.borderColor
      )}
      title={`Source Channel: ${config.label}${sourceName ? ` (${sourceName})` : ''}`}
    >
      <Icon className="w-3 h-3 flex-shrink-0" />
      <span className="font-semibold uppercase tracking-wider text-[10px]">{config.label}</span>
      {showName && sourceName && (
        <span className="text-zinc-300 font-normal truncate max-w-[120px] border-l border-white/10 pl-1.5 ml-1">
          {sourceName}
        </span>
      )}
    </span>
  );
}
