'use client';

import React, { useState } from 'react';
import { ExternalLink, Gauge, Activity, CheckCircle2, CircleDashed } from 'lucide-react';
import { AIPipelineSettings, Source, SystemSettings } from '@/lib/types';
import { cn, getProviderRateLimitStatuses, ProviderRateLimitStatus } from '@/lib/utils';

interface ProviderRateLimitsBarProps {
  systemSettings?: SystemSettings | null;
  aiSettings?: AIPipelineSettings | null;
  sources?: Source[] | null;
  apiKeysOverride?: Record<string, string> | null;
  hfSpaceConfigured?: boolean;
  compact?: boolean;
  title?: string;
}

export function ProviderRateLimitsBar({
  systemSettings,
  aiSettings,
  sources,
  apiKeysOverride,
  hfSpaceConfigured,
  compact = false,
  title = 'Connected Provider Rate Limits & Quotas',
}: ProviderRateLimitsBarProps) {
  const [showDisconnected, setShowDisconnected] = useState(false);

  const effectiveKeys = apiKeysOverride ?? systemSettings?.api_keys ?? {};
  const allProviders = getProviderRateLimitStatuses({
    apiKeys: effectiveKeys,
    aiSettings,
    sources,
    hfSpaceConfigured,
  });

  const connectedProviders = allProviders.filter((p) => p.isConnected);
  const disconnectedProviders = allProviders.filter((p) => !p.isConnected);

  // If no providers are detected as connected yet (e.g. initial load), display Gemini & Groq by default
  const displayedProviders: ProviderRateLimitStatus[] = showDisconnected
    ? allProviders
    : connectedProviders.length > 0
    ? connectedProviders
    : allProviders.filter((p) => p.id === 'gemini' || p.id === 'groq');

  if (compact) {
    return (
      <div className="p-3.5 rounded-xl bg-canvas-recessed hairline-border space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-2">
            <Gauge className="w-3.5 h-3.5 text-accent-cyan" />
            <span className="text-xs font-semibold text-zinc-200 tracking-tight">{title}</span>
            <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 tabular">
              {connectedProviders.length} Connected
            </span>
          </div>
          {disconnectedProviders.length > 0 && (
            <button
              type="button"
              onClick={() => setShowDisconnected((prev) => !prev)}
              className="text-[10px] font-mono text-zinc-400 hover:text-zinc-200 transition-colors"
            >
              {showDisconnected ? 'Show Connected Only' : `+ Show ${disconnectedProviders.length} Unconfigured`}
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {displayedProviders.map((provider) => (
            <div
              key={provider.id}
              className={cn(
                'inline-flex items-center space-x-2 px-2.5 py-1.5 rounded-lg text-xs hairline-border transition-all',
                provider.isConnected
                  ? 'bg-surface-card/90 text-zinc-200 border-white/10'
                  : 'bg-surface-card/40 text-zinc-400 opacity-75'
              )}
            >
              <span
                className={cn(
                  'w-1.5 h-1.5 rounded-full shrink-0',
                  provider.isConnected
                    ? 'bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
                    : 'bg-zinc-600'
                )}
                title={provider.connectionLabel}
              />
              <span className="font-semibold text-[11px] text-zinc-100">{provider.name}</span>
              <span className="text-[10px] font-mono text-zinc-400 hidden sm:inline tabular">
                ({provider.quotaSummary})
              </span>
              <a
                href={provider.rateLimitUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={`Open ${provider.name} Rate Limits (${provider.description})`}
                className="inline-flex items-center space-x-1 px-2 py-0.5 rounded bg-accent-cyan/10 hover:bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/25 text-[10px] font-mono font-semibold transition-colors pressable"
              >
                <span>Rate Limits</span>
                <ExternalLink className="w-2.5 h-2.5" />
              </a>
              <a
                href={provider.usageUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={`Open ${provider.name} Usage / Console`}
                className="inline-flex items-center space-x-0.5 text-[10px] font-mono text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                <span>Console</span>
                <ExternalLink className="w-2.5 h-2.5" />
              </a>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="card-slick rounded-xl p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center space-x-2.5">
          <div className="p-1.5 rounded-lg bg-accent-cyan/15 text-accent-cyan border border-accent-cyan/30">
            <Activity className="w-4 h-4" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h3 className="text-xs sm:text-sm font-semibold text-zinc-100 tracking-tight">
                {title}
              </h3>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 tabular">
                {connectedProviders.length} Connected
              </span>
            </div>
            <p className="text-[11px] text-zinc-400">
              Direct shortcuts to live RPM, TPM, and daily RPD quota dashboards for each connected provider
            </p>
          </div>
        </div>

        {disconnectedProviders.length > 0 && (
          <button
            type="button"
            onClick={() => setShowDisconnected((prev) => !prev)}
            className="px-2.5 py-1 rounded-lg bg-surface-raised hover:bg-white/10 hairline-border text-[11px] font-mono text-zinc-300 hover:text-zinc-100 transition-colors pressable"
          >
            {showDisconnected
              ? 'Show Connected Only'
              : `Show All Providers (${allProviders.length})`}
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
        {displayedProviders.map((provider) => (
          <div
            key={provider.id}
            className={cn(
              'p-3 rounded-lg bg-canvas-recessed hairline-border flex flex-col justify-between gap-2.5 transition-colors',
              provider.isConnected
                ? 'hover:border-accent-cyan/30'
                : 'opacity-70 hover:opacity-100'
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="space-y-0.5">
                <div className="flex items-center space-x-1.5">
                  <span
                    className={cn(
                      'w-2 h-2 rounded-full shrink-0',
                      provider.isConnected
                        ? 'bg-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.5)]'
                        : 'bg-zinc-600'
                    )}
                  />
                  <span className="text-xs font-semibold text-zinc-100 tracking-tight">
                    {provider.name}
                  </span>
                </div>
                <p className="text-[10px] font-mono text-zinc-400 tabular pl-3.5">
                  {provider.quotaSummary}
                </p>
              </div>

              <span
                className={cn(
                  'inline-flex items-center space-x-1 px-1.5 py-0.5 rounded text-[9px] font-mono uppercase tracking-wider border shrink-0',
                  provider.hasApiKey
                    ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/25'
                    : provider.isConnected
                    ? 'bg-accent-cyan/10 text-accent-cyan border-accent-cyan/25'
                    : 'bg-zinc-800/60 text-zinc-500 border-zinc-700'
                )}
              >
                {provider.isConnected ? (
                  <CheckCircle2 className="w-2.5 h-2.5" />
                ) : (
                  <CircleDashed className="w-2.5 h-2.5" />
                )}
                <span>
                  {provider.hasApiKey
                    ? 'Key Active'
                    : provider.isConnected
                    ? 'Connected'
                    : 'Unconfigured'}
                </span>
              </span>
            </div>

            <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-white/5">
              <a
                href={provider.rateLimitUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 inline-flex items-center justify-center space-x-1.5 px-2.5 py-1.5 rounded-md bg-surface-raised hover:bg-accent-cyan/15 text-zinc-100 hover:text-accent-cyan hairline-border hover:border-accent-cyan/30 text-[11px] font-medium transition-colors pressable"
              >
                <span>View Rate Limits</span>
                <ExternalLink className="w-3 h-3 text-accent-cyan" />
              </a>
              <a
                href={provider.usageUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={`Open ${provider.name} Usage & Console`}
                className="inline-flex items-center space-x-1 px-2.5 py-1.5 rounded-md bg-surface-card hover:bg-surface-raised text-zinc-400 hover:text-zinc-200 hairline-border text-[11px] font-mono transition-colors pressable"
              >
                <span>Usage</span>
                <ExternalLink className="w-2.5 h-2.5" />
              </a>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
