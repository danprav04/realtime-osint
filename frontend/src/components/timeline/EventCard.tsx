'use client';

import React, { useState } from 'react';
import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Globe,
  Maximize2,
  Sparkles,
  MapPin,
  Clock,
} from 'lucide-react';
import { NormalizedEvent } from '@/lib/types';
import { ImportanceBadge } from './ImportanceBadge';
import { SourceBadge } from './SourceBadge';
import { formatRelativeTime, formatDateTime } from '@/lib/utils';

interface EventCardProps {
  event: NormalizedEvent;
  onOpenDetails: (event: NormalizedEvent) => void;
}

export function EventCard({ event, onOpenDetails }: EventCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const isTranslated = !!event.translated_text && event.language && event.language !== 'en';
  const entities = event.entities_json || [];

  return (
    <article className="card-slick rounded-xl p-4 sm:p-5 relative transition-all duration-150">
      {/* Top attribution bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2.5">
        <div className="flex items-center space-x-2">
          <SourceBadge sourceType={event.source_type} sourceName={event.source_name} />
          {isTranslated && (
            <span
              className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded text-[10px] bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/20 font-mono"
              title={`Translated from original language (${event.language})`}
            >
              <Globe className="w-2.5 h-2.5" />
              <span>{event.language?.toUpperCase()} → EN</span>
            </span>
          )}
        </div>

        <div className="flex items-center space-x-2">
          <div
            className="flex items-center space-x-1 text-xs text-zinc-400 tabular"
            title={`Published: ${formatDateTime(event.published_at)}`}
          >
            <Clock className="w-3 h-3 text-zinc-500" />
            <span>{formatRelativeTime(event.published_at)}</span>
          </div>
          <ImportanceBadge score={event.importance_score} size="sm" />
        </div>
      </div>

      {/* Main Title / Headline */}
      <h3
        onClick={() => onOpenDetails(event)}
        className="text-sm sm:text-base font-semibold text-zinc-100 hover:text-accent-cyan transition-colors tracking-tight cursor-pointer leading-snug mb-2"
      >
        {event.headline || event.title}
      </h3>

      {/* AI Intelligence Summary */}
      <div className="text-xs sm:text-sm text-zinc-300 leading-relaxed mb-3">
        {event.summary ? (
          <p className="line-clamp-3">{event.summary}</p>
        ) : (
          <p className="line-clamp-2 text-zinc-400 font-mono text-xs">{event.raw_text}</p>
        )}
      </div>

      {/* Entities pills & Location */}
      <div className="flex flex-wrap items-center gap-1.5 mb-3">
        {event.location_json && (
          <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] bg-accent-amber/10 text-accent-amber border border-accent-amber/20">
            <MapPin className="w-2.5 h-2.5" />
            <span>{event.location_json.name || `${event.location_json.lat?.toFixed(2)}, ${event.location_json.lon?.toFixed(2)}`}</span>
          </span>
        )}
        {entities.slice(0, 4).map((ent, idx) => (
          <span
            key={idx}
            className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface-raised hairline-border text-zinc-400"
          >
            #{ent}
          </span>
        ))}
        {entities.length > 4 && (
          <span className="px-1.5 py-0.5 rounded text-[10px] text-zinc-500 font-mono">
            +{entities.length - 4} more
          </span>
        )}
      </div>

      {/* Expandable in-card details accordion */}
      {isExpanded && (
        <div className="pt-3 mt-2 hairline-t space-y-3 text-xs animate-in fade-in duration-150">
          {event.score_rationale && (
            <div className="p-3 rounded-lg bg-canvas-recessed hairline-border">
              <span className="font-semibold text-accent-amber text-[11px] block mb-1">
                AI SCORING RATIONALE
              </span>
              <p className="text-zinc-300 text-xs leading-relaxed">{event.score_rationale}</p>
            </div>
          )}

          {isTranslated && event.translated_text && (
            <div className="p-3 rounded-lg bg-canvas-recessed hairline-border">
              <span className="font-semibold text-accent-indigo text-[11px] block mb-1">
                TRANSLATED CONTENT
              </span>
              <p className="text-zinc-300 text-xs leading-relaxed">{event.translated_text}</p>
            </div>
          )}

          <div className="p-3 rounded-lg bg-canvas-recessed hairline-border">
            <span className="font-semibold text-zinc-400 text-[11px] block mb-1">
              RAW SOURCE EXCERPT
            </span>
            <pre className="text-zinc-400 font-mono text-[11px] whitespace-pre-wrap max-h-32 overflow-y-auto">
              {event.raw_text}
            </pre>
          </div>
        </div>
      )}

      {/* Card Footer Bar */}
      <div className="flex items-center justify-between pt-2.5 hairline-t text-xs">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="inline-flex items-center space-x-1 text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          {isExpanded ? (
            <>
              <ChevronUp className="w-3.5 h-3.5" />
              <span>Hide Details</span>
            </>
          ) : (
            <>
              <ChevronDown className="w-3.5 h-3.5" />
              <span>Quick Preview</span>
            </>
          )}
        </button>

        <div className="flex items-center space-x-3">
          {event.source_url && (
            <a
              href={event.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center space-x-1 text-zinc-400 hover:text-accent-cyan transition-colors"
              title="View original publisher page"
            >
              <span>Source</span>
              <ExternalLink className="w-3 h-3" />
            </a>
          )}
          <button
            onClick={() => onOpenDetails(event)}
            className="inline-flex items-center space-x-1 text-accent-cyan hover:text-accent-cyan/80 font-medium transition-colors"
            title="Inspect full intelligence modal"
          >
            <span>Inspect</span>
            <Maximize2 className="w-3 h-3" />
          </button>
        </div>
      </div>
    </article>
  );
}
