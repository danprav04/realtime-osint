'use client';

import React from 'react';
import {
  X,
  ShieldCheck,
  ShieldAlert,
  HelpCircle,
  ExternalLink,
  Layers,
  MapPin,
  Clock,
  Radio,
  Sparkles,
  Link as LinkIcon,
} from 'lucide-react';
import { MapNode, MapNeighbor } from '@/lib/types';
import { cn, formatRelativeTime, getEventAIProvenance } from '@/lib/utils';
import { AIModelBadge } from '@/components/common/AIModelBadge';

interface EventInspectorProps {
  node: MapNode | null;
  onClose: () => void;
  onSelectNeighbor: (eventId: string) => void;
}

export function EventInspector({
  node,
  onClose,
  onSelectNeighbor,
}: EventInspectorProps) {
  if (!node) return null;

  const trust = node.trustworthiness;
  const isHighTrust = trust.score >= 80;
  const isMedTrust = trust.score >= 60 && trust.score < 80;
  const isUnverified = trust.score >= 40 && trust.score < 60;
  const isOutlier = trust.score < 40;

  const summarizationProv = getEventAIProvenance(node as any, 'summarization');
  const scoringProv = getEventAIProvenance(node as any, 'scoring');
  const embeddingProv = getEventAIProvenance(node as any, 'embedding');

  const badgeColor = isHighTrust
    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
    : isMedTrust
    ? 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30'
    : isUnverified
    ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
    : 'bg-rose-500/15 text-rose-400 border-rose-500/30';

  const TrustIcon = isHighTrust
    ? ShieldCheck
    : isMedTrust
    ? Sparkles
    : isUnverified
    ? HelpCircle
    : ShieldAlert;

  return (
    <div className="fixed inset-y-0 right-0 z-50 w-full sm:w-[440px] bg-canvas-base/95 backdrop-blur-md hairline-l shadow-2xl flex flex-col transition-transform duration-300">
      {/* Drawer Header */}
      <div className="flex items-center justify-between p-4 hairline-b bg-surface-card/60">
        <div className="flex items-center space-x-2">
          <div className={cn('p-1.5 rounded-lg border', badgeColor)}>
            <TrustIcon className="w-4 h-4" />
          </div>
          <div>
            <div className="text-xs font-semibold text-zinc-100 uppercase tracking-wider">
              Intelligence Inspector
            </div>
            <div className="text-[10px] text-zinc-400 font-mono">
              Cluster #{node.cluster_id || 'N/A'} • {node.source_name}
            </div>
          </div>
        </div>

        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-surface-raised transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Drawer Body */}
      <div className="flex-1 overflow-y-auto p-5 space-y-5">
        {/* Trustworthiness Assessment Card */}
        <div className="card-slick rounded-xl p-4 space-y-3 bg-surface-card/70 border border-white/5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center space-x-2">
              <span className="text-xs font-semibold text-zinc-300">
                Trustworthiness & Corroboration
              </span>
              <AIModelBadge provenance={scoringProv} size="xs" linkToSettings />
            </div>
            <span
              className={cn(
                'px-2 py-0.5 rounded-md text-[11px] font-semibold border',
                badgeColor
              )}
            >
              {trust.score}% • {trust.label}
            </span>
          </div>

          {/* Progress Bar */}
          <div className="w-full bg-zinc-800 rounded-full h-2 overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-500',
                isHighTrust
                  ? 'bg-emerald-500'
                  : isMedTrust
                  ? 'bg-cyan-500'
                  : isUnverified
                  ? 'bg-amber-500'
                  : 'bg-rose-500'
              )}
              style={{ width: `${Math.max(5, trust.score)}%` }}
            />
          </div>

          <p className="text-xs text-zinc-400 leading-relaxed italic">
            "{trust.verdict}"
          </p>

          {/* Corroborating Sources Tags */}
          {trust.corroborating_sources && trust.corroborating_sources.length > 0 && (
            <div className="pt-2 border-t border-white/5 space-y-1.5">
              <span className="text-[11px] text-zinc-400 font-medium">
                Corroborating Sources ({trust.corroborating_sources.length}):
              </span>
              <div className="flex flex-wrap gap-1.5">
                {trust.corroborating_sources.map((src, idx) => (
                  <span
                    key={idx}
                    className="px-2 py-0.5 rounded-md text-[10px] font-mono bg-surface-raised text-zinc-300 border border-white/10"
                  >
                    {src}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Article Headline & Summary */}
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-base font-semibold text-zinc-100 leading-snug">
              {node.headline || node.title}
            </h3>
            <AIModelBadge provenance={summarizationProv} size="xs" linkToSettings />
          </div>

          <div className="flex flex-wrap items-center gap-2 text-[11px] text-zinc-400">
            <div className="flex items-center space-x-1">
              <Radio className="w-3 h-3 text-accent-cyan" />
              <span>{node.source_name}</span>
            </div>
            <span>•</span>
            <div className="flex items-center space-x-1">
              <Clock className="w-3 h-3 text-zinc-500" />
              <span>{formatRelativeTime(node.published_at)}</span>
            </div>
            {node.location && (
              <>
                <span>•</span>
                <div className="flex items-center space-x-1">
                  <MapPin className="w-3 h-3 text-accent-amber" />
                  <span>
                    {node.location.name || node.location.country || 'Location Tagged'}
                  </span>
                </div>
              </>
            )}
          </div>

          <div className="p-3.5 rounded-lg bg-surface-raised/60 border border-white/5 text-xs text-zinc-300 leading-relaxed space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="font-semibold text-zinc-400 text-[11px] uppercase tracking-wider">
                Intelligence Summary
              </div>
              <AIModelBadge provenance={summarizationProv} size="xs" linkToSettings />
            </div>
            <p>{node.summary}</p>
          </div>

          {node.source_url && (
            <a
              href={node.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center space-x-1.5 text-xs text-accent-cyan hover:underline pt-1"
            >
              <span>View original source report</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
        </div>

        {/* Nearest Corroborating Neighbors */}
        {node.nearest_neighbors && node.nearest_neighbors.length > 0 && (
          <div className="space-y-2.5 pt-2 border-t border-white/5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center space-x-1.5 text-xs font-semibold text-zinc-200">
                <LinkIcon className="w-3.5 h-3.5 text-accent-cyan" />
                <span>Semantically Linked Reports ({node.nearest_neighbors.length})</span>
              </div>
              <AIModelBadge provenance={embeddingProv} size="xs" linkToSettings />
            </div>

            <div className="space-y-2">
              {node.nearest_neighbors.map((nb) => {
                const simPct = Math.round(nb.similarity * 100);
                return (
                  <button
                    key={nb.event_id}
                    onClick={() => onSelectNeighbor(nb.event_id)}
                    className="w-full text-left p-2.5 rounded-lg bg-surface-card hover:bg-surface-raised border border-white/5 hover:border-accent-cyan/30 transition-all group"
                  >
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span className="text-[10px] font-mono text-zinc-400 truncate">
                        {nb.source_name}
                      </span>
                      <span
                        className={cn(
                          'text-[10px] font-mono px-1.5 py-0.2 rounded border',
                          simPct >= 75
                            ? 'text-emerald-400 border-emerald-500/20 bg-emerald-500/10'
                            : 'text-cyan-400 border-cyan-500/20 bg-cyan-500/10'
                        )}
                      >
                        {simPct}% match
                      </span>
                    </div>
                    <p className="text-xs text-zinc-300 group-hover:text-accent-cyan line-clamp-2 transition-colors">
                      {nb.title}
                    </p>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
