'use client';

import React, { useState } from 'react';
import {
  X,
  ExternalLink,
  Copy,
  Check,
  Globe,
  Tag,
  MapPin,
  Clock,
  Sparkles,
  FileText,
  Code,
} from 'lucide-react';
import { NormalizedEvent } from '@/lib/types';
import { ImportanceBadge } from './ImportanceBadge';
import { SourceBadge } from './SourceBadge';
import { formatDateTime } from '@/lib/utils';

interface EventDetailsModalProps {
  event: NormalizedEvent | null;
  isOpen: boolean;
  onClose: () => void;
}

export function EventDetailsModal({ event, isOpen, onClose }: EventDetailsModalProps) {
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<'details' | 'raw' | 'json'>('details');

  if (!isOpen || !event) return null;

  const handleCopyJson = () => {
    navigator.clipboard.writeText(JSON.stringify(event, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const entities: string[] = (event.entities_json || (event as any).entities || []) as string[];
  const isTranslated = !!event.translated_text && event.language && event.language !== 'en';
  const isPending = !event.is_analyzed && (event.importance_score === null || event.importance_score === undefined);

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
        return first.length > 90 ? first.slice(0, 87) + '...' : first;
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-black/75 backdrop-blur-sm overflow-y-auto">
      <div className="relative w-full max-w-3xl rounded-xl bg-surface-overlay p-6 hairline-border shadow-2xl my-auto">
        {/* Header */}
        <div className="flex items-start justify-between pb-4 hairline-b">
          <div className="space-y-2 pr-4">
            <div className="flex flex-wrap items-center gap-2">
              <ImportanceBadge score={event.importance_score} isPending={isPending} size="md" />
              <SourceBadge sourceType={event.source_type} sourceName={event.source_name} />
              {isTranslated && (
                <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[11px] bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/20">
                  <Globe className="w-3 h-3" />
                  <span>Translated from {event.language?.toUpperCase()}</span>
                </span>
              )}
            </div>
            <h2 className="text-lg font-semibold text-zinc-100 tracking-tight leading-snug">
              {displayTitle}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-surface-raised transition-colors flex-shrink-0"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Metadata sub-bar */}
        <div className="flex flex-wrap items-center gap-y-2 gap-x-4 py-3 text-xs text-zinc-400 hairline-b tabular">
          <div className="flex items-center space-x-1.5">
            <Clock className="w-3.5 h-3.5 text-zinc-500" />
            <span>Published: <strong className="text-zinc-300 font-normal">{formatDateTime(event.published_at)}</strong></span>
          </div>
          <div className="flex items-center space-x-1.5">
            <Clock className="w-3.5 h-3.5 text-zinc-500" />
            <span>Ingested: <strong className="text-zinc-300 font-normal">{formatDateTime(event.ingested_at)}</strong></span>
          </div>
          {event.location_json && (
            <div className="flex items-center space-x-1.5 text-accent-amber">
              <MapPin className="w-3.5 h-3.5" />
              <span>
                {event.location_json.name || `${event.location_json.lat}, ${event.location_json.lon}`}
              </span>
            </div>
          )}
          {event.source_url && (
            <a
              href={event.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center space-x-1 text-accent-cyan hover:underline ml-auto"
            >
              <span>Original Source</span>
              <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>

        {/* Tabs */}
        <div className="flex items-center space-x-2 pt-3 border-b border-white/5 text-xs">
          <button
            onClick={() => setActiveTab('details')}
            className={`px-3 py-1.5 border-b-2 font-medium transition-colors ${
              activeTab === 'details'
                ? 'border-accent-cyan text-zinc-100'
                : 'border-transparent text-zinc-400 hover:text-zinc-300'
            }`}
          >
            Intelligence Details
          </button>
          <button
            onClick={() => setActiveTab('raw')}
            className={`px-3 py-1.5 border-b-2 font-medium transition-colors ${
              activeTab === 'raw'
                ? 'border-accent-cyan text-zinc-100'
                : 'border-transparent text-zinc-400 hover:text-zinc-300'
            }`}
          >
            Raw Source Text
          </button>
          <button
            onClick={() => setActiveTab('json')}
            className={`px-3 py-1.5 border-b-2 font-medium transition-colors ${
              activeTab === 'json'
                ? 'border-accent-cyan text-zinc-100'
                : 'border-transparent text-zinc-400 hover:text-zinc-300'
            }`}
          >
            Full JSON Metadata
          </button>
        </div>

        {/* Body based on tab */}
        <div className="py-4 space-y-4 max-h-[60vh] overflow-y-auto pr-1">
          {activeTab === 'details' && (
            <>
              {/* AI Summary Section */}
              <div className="p-3.5 rounded-lg bg-surface-card hairline-border">
                <div className="flex items-center space-x-2 text-xs font-semibold text-accent-cyan mb-2">
                  <Sparkles className="w-3.5 h-3.5" />
                  <span>AI INTELLIGENCE SUMMARY</span>
                </div>
                <p className="text-sm text-zinc-200 leading-relaxed">
                  {displaySummary || 'Summary pending processing...'}
                </p>
              </div>

              {/* Score Rationale */}
              {event.score_rationale && (
                <div className="p-3.5 rounded-lg bg-surface-card hairline-border">
                  <div className="text-xs font-semibold text-accent-amber mb-1.5">
                    IMPORTANCE SCORING RATIONALE
                  </div>
                  <p className="text-xs text-zinc-300 leading-relaxed">
                    {event.score_rationale}
                  </p>
                </div>
              )}

              {/* Translation block if applicable */}
              {isTranslated && event.translated_text && (
                <div className="p-3.5 rounded-lg bg-surface-card hairline-border">
                  <div className="text-xs font-semibold text-accent-indigo mb-1.5 flex items-center space-x-1.5">
                    <Globe className="w-3.5 h-3.5" />
                    <span>ENGLISH TRANSLATION</span>
                  </div>
                  <p className="text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">
                    {event.translated_text}
                  </p>
                </div>
              )}

              {/* Extracted Entities */}
              {entities.length > 0 && (
                <div className="p-3.5 rounded-lg bg-surface-card hairline-border">
                  <div className="flex items-center space-x-1.5 text-xs font-semibold text-zinc-400 mb-2">
                    <Tag className="w-3.5 h-3.5" />
                    <span>DETECTED ENTITIES & KEYWORDS</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {entities.map((ent, idx) => (
                      <span
                        key={idx}
                        className="px-2 py-0.5 rounded-md text-[11px] font-mono bg-surface-raised hairline-border text-zinc-300"
                      >
                        #{ent}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {activeTab === 'raw' && (
            <div className="p-3.5 rounded-lg bg-canvas-recessed hairline-border">
              <div className="flex items-center justify-between text-xs font-semibold text-zinc-400 mb-2">
                <span className="flex items-center space-x-1.5">
                  <FileText className="w-3.5 h-3.5" />
                  <span>ORIGINAL INGESTED TEXT</span>
                </span>
                <span className="font-mono text-[11px] text-zinc-500">{event.language || 'en'}</span>
              </div>
              <pre className="text-xs text-zinc-300 font-mono whitespace-pre-wrap leading-relaxed max-h-[400px] overflow-y-auto">
                {event.raw_text}
              </pre>
            </div>
          )}

          {activeTab === 'json' && (
            <div className="relative p-3.5 rounded-lg bg-canvas-recessed hairline-border font-mono text-xs text-zinc-300">
              <div className="flex items-center justify-between pb-2 mb-2 border-b border-white/5">
                <span className="flex items-center space-x-1.5 text-zinc-400">
                  <Code className="w-3.5 h-3.5" />
                  <span>EVENT SCHEMA OBJECT</span>
                </span>
                <button
                  onClick={handleCopyJson}
                  className="inline-flex items-center space-x-1 px-2.5 py-1 rounded bg-surface-card hover:bg-surface-raised hairline-border text-zinc-300 text-[11px] transition-colors"
                >
                  {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                  <span>{copied ? 'Copied' : 'Copy JSON'}</span>
                </button>
              </div>
              <pre className="overflow-x-auto max-h-[400px] overflow-y-auto text-[11px] leading-relaxed text-zinc-400">
                {JSON.stringify(event, null, 2)}
              </pre>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between pt-4 hairline-t text-xs">
          <span className="text-zinc-500 font-mono text-[11px]">ID: {event.id}</span>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg bg-surface-card hover:bg-surface-raised hairline-border text-zinc-200 transition-colors font-medium"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
