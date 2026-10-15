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
import { NormalizedEvent, ScoringBreakdown } from '@/lib/types';
import { ImportanceBadge } from './ImportanceBadge';
import { SourceBadge } from './SourceBadge';
import { AIModelBadge } from '../common/AIModelBadge';
import { formatRelativeTime, formatDateTime, getEventAIProvenance } from '@/lib/utils';

interface EventCardProps {
  event: NormalizedEvent;
  onOpenDetails: (event: NormalizedEvent) => void;
}

export function EventCard({ event, onOpenDetails }: EventCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const isTranslated = !!event.translated_text && event.language && event.language !== 'en';
  const entities: string[] = (event.entities_json || (event as any).entities || []) as string[];
  const isPending = !event.is_analyzed && (event.importance_score === null || event.importance_score === undefined);
  const scoringBreakdown: ScoringBreakdown | undefined =
    event.metadata_json?.scoring_breakdown || (event as any).metadata?.scoring_breakdown;

  const hasNonLatin = (str?: string | null) =>
    str ? /[\u0600-\u06FF\u0590-\u05FF\uFB50-\uFDFF\uFE70-\uFEFF]/.test(str) : false;

  const displayTitle = (() => {
    if (event.headline && !hasNonLatin(event.headline)) {
      return event.headline;
    }
    if (event.translated_text && (hasNonLatin(event.title) || (event.language && event.language !== 'en'))) {
      const clean = event.translated_text.replace(/^English translation:\s*/i, '').trim();
      const first = clean.split('.')[0]?.trim();
      if (first && first.length > 5) {
        return first.length > 85 ? first.slice(0, 82) + '...' : first;
      }
    }
    if (isPending && hasNonLatin(event.title)) {
      return `[Translating...] ${event.title}`;
    }
    return event.headline || event.title;
  })();

  const displaySummary = (() => {
    if (event.summary && !hasNonLatin(event.summary)) {
      return event.summary;
    }
    if (event.translated_text) {
      return event.translated_text.replace(/^English translation:\s*/i, '').trim();
    }
    if (isPending && hasNonLatin(event.raw_text || event.title)) {
      return "Translating and analyzing intelligence content from source...";
    }
    return event.summary || event.raw_text;
  })();

  const summarizationProv = getEventAIProvenance(event, 'summarization');
  const scoringProv = getEventAIProvenance(event, 'scoring');
  const extractionProv = getEventAIProvenance(event, 'extraction');

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
          {scoringBreakdown && scoringBreakdown.corroboration_count >= 1 && (
            <span
              className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono"
              title={`Corroborated by: ${(scoringBreakdown.corroborating_sources || []).join(', ')}`}
            >
              <span>✓ {scoringBreakdown.corroboration_count + 1} Sources</span>
            </span>
          )}
          {scoringBreakdown?.is_same_source_echo && (
            <span
              className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded text-[10px] bg-zinc-500/10 text-zinc-400 border border-zinc-500/20 font-mono"
              title="Repetitive update from the same source; score dampened"
            >
              <span>Echo Dampened</span>
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
          <ImportanceBadge score={event.importance_score} isPending={isPending} size="sm" />
          <AIModelBadge provenance={scoringProv} size="xs" linkToSettings />
        </div>
      </div>

      {/* Main Title / Headline */}
      <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
        <h3
          onClick={() => onOpenDetails(event)}
          className="text-sm sm:text-base font-semibold text-zinc-100 hover:text-accent-cyan transition-colors tracking-tight cursor-pointer leading-snug flex-1 min-w-0"
        >
          {displayTitle}
        </h3>
        <AIModelBadge provenance={summarizationProv} size="xs" linkToSettings />
      </div>

      {/* AI Intelligence Summary */}
      <div className="text-xs sm:text-sm text-zinc-300 leading-relaxed mb-3 space-y-1">
        {event.summary || event.translated_text ? (
          <p className="line-clamp-3">{displaySummary}</p>
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
        {entities.length > 0 && (
          <AIModelBadge provenance={extractionProv} size="xs" labelPrefix="Entities:" linkToSettings />
        )}
      </div>

      {/* Expandable in-card details accordion */}
      {isExpanded && (
        <div className="pt-3 mt-2 hairline-t space-y-3 text-xs animate-in fade-in duration-150">
          {(event.score_rationale || scoringBreakdown) && (
            <div className="p-3 rounded-lg bg-canvas-recessed hairline-border space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-accent-amber text-[11px]">
                    AI SCORING RATIONALE & CALIBRATION
                  </span>
                  <AIModelBadge provenance={scoringProv} size="xs" linkToSettings />
                </div>
                {scoringBreakdown?.corroboration_status && (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-surface-raised hairline-border text-zinc-300">
                    {scoringBreakdown.corroboration_status}
                  </span>
                )}
              </div>

              {event.score_rationale && (
                <p className="text-zinc-300 text-xs leading-relaxed">{event.score_rationale}</p>
              )}

              {scoringBreakdown && (
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface-card hairline-border text-zinc-300">
                    Severity: <strong className="text-zinc-100">{scoringBreakdown.severity_score}/10</strong>
                  </span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface-card hairline-border text-zinc-300">
                    Novelty: <strong className="text-zinc-100">{scoringBreakdown.novelty_score}/10</strong>
                  </span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface-card hairline-border text-zinc-300">
                    Confidence:{' '}
                    <strong className="text-zinc-100 uppercase">
                      {scoringBreakdown.verification_confidence} ({scoringBreakdown.confidence_score}/10)
                    </strong>
                  </span>
                  {scoringBreakdown.source_reliability_label && (
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface-card hairline-border text-zinc-400">
                      {scoringBreakdown.source_reliability_label}
                    </span>
                  )}
                </div>
              )}

              {scoringBreakdown?.corroborating_sources && scoringBreakdown.corroborating_sources.length > 0 && (
                <div className="text-[11px] text-emerald-400/90 font-mono">
                  Corroborated across: {scoringBreakdown.corroborating_sources.join(', ')}
                </div>
              )}
            </div>
          )}

          {isTranslated && event.translated_text && (
            <div className="p-3 rounded-lg bg-canvas-recessed hairline-border">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="font-semibold text-accent-indigo text-[11px]">
                  TRANSLATED CONTENT
                </span>
                <AIModelBadge provenance={extractionProv} size="xs" linkToSettings />
              </div>
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
