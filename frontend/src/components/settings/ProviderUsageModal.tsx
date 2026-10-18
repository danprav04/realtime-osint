'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  X,
  RotateCw,
  ExternalLink,
  Cpu,
  Key,
  ShieldAlert,
  Clock,
  Layers,
  Sparkles,
  Zap,
  Activity,
  AlertTriangle,
  Server,
  Gauge,
  Copy,
  Check,
} from 'lucide-react';
import {
  KeyUsageTelemetry,
  ModelUsageTelemetry,
  ProviderUsageTelemetry,
  UsageTelemetryResponse,
} from '@/lib/types';
import { getProviderUsage } from '@/lib/api';
import { cn, formatRelativeTime, PROVIDER_RATE_LIMITS } from '@/lib/utils';

interface ProviderUsageModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialProviderId?: string;
  initialUsage?: UsageTelemetryResponse | null;
}

export function ProviderUsageModal({
  isOpen,
  onClose,
  initialProviderId = 'all',
  initialUsage,
}: ProviderUsageModalProps) {
  const [selectedProvider, setSelectedProvider] = useState<string>(initialProviderId);
  const [activeTab, setActiveTab] = useState<'keys' | 'models'>('keys');
  const [usage, setUsage] = useState<UsageTelemetryResponse | null>(initialUsage || null);
  const [loading, setLoading] = useState<boolean>(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Sync initialProviderId when modal opens
  useEffect(() => {
    if (isOpen) {
      setSelectedProvider(initialProviderId);
    }
  }, [isOpen, initialProviderId]);

  // Fetch live usage telemetry
  const fetchUsage = useCallback(async (isSilent = false) => {
    if (!isSilent) setLoading(true);
    try {
      const data = await getProviderUsage();
      setUsage(data);
    } catch (err) {
      console.error('Failed to fetch provider usage telemetry:', err);
    } finally {
      if (!isSilent) setLoading(false);
    }
  }, []);

  // Initial load and 5s auto-refresh while modal is open
  useEffect(() => {
    if (!isOpen) return;
    fetchUsage(Boolean(usage));
    const timer = setInterval(() => {
      fetchUsage(true);
    }, 5000);
    return () => clearInterval(timer);
  }, [isOpen, fetchUsage]);

  // Keyboard shortcut: Escape to close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(id);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const providersList = useMemo(() => {
    return [
      { id: 'all', name: 'All Providers' },
      { id: 'gemini', name: 'Google Gemini' },
      { id: 'groq', name: 'Groq Cloud' },
      { id: 'mistral', name: 'Mistral AI' },
      { id: 'aihubmix', name: 'AI Hub Mix' },
      { id: 'cloudflare', name: 'Cloudflare Workers AI' },
      { id: 'zai', name: 'Z.ai / Zhipu' },
      { id: 'siliconflow', name: 'SiliconFlow' },
      { id: 'huggingface', name: 'Hugging Face TEI' },
      { id: 'telegram', name: 'Telegram API' },
      { id: 'reddit', name: 'Reddit Data API' },
      { id: 'newsapi', name: 'NewsAPI / Currents' },
    ];
  }, []);

  // Filter keys and models based on selected provider
  const filteredKeys = useMemo<KeyUsageTelemetry[]>(() => {
    if (!usage) return [];
    if (selectedProvider === 'all') return usage.all_keys || [];
    return (usage.providers?.[selectedProvider]?.keys || []).concat();
  }, [usage, selectedProvider]);

  const filteredModels = useMemo<ModelUsageTelemetry[]>(() => {
    if (!usage) return [];
    if (selectedProvider === 'all') return usage.all_models || [];
    return (usage.providers?.[selectedProvider]?.models || []).concat();
  }, [usage, selectedProvider]);

  // Provider rate limit reference info
  const providerMeta = useMemo(() => {
    return PROVIDER_RATE_LIMITS.find((p) => p.id === selectedProvider);
  }, [selectedProvider]);

  // Aggregated totals for the active view
  const activeMetrics = useMemo(() => {
    if (!usage) {
      return { requests: 0, tokens: 0, successRate: 100, activeKeys: 0, inCooldown: 0 };
    }
    if (selectedProvider === 'all') {
      const totalReq = usage.summary.total_requests;
      const succReq = (usage.all_keys || []).reduce((acc, k) => acc + (k.successful_requests || 0), 0);
      const successRate = totalReq > 0 ? Math.round((succReq / totalReq) * 100) : 100;
      return {
        requests: totalReq,
        tokens: usage.summary.total_tokens,
        successRate,
        activeKeys: usage.summary.active_keys,
        inCooldown: usage.summary.keys_in_cooldown + usage.summary.models_in_cooldown,
      };
    }
    const pData = usage.providers?.[selectedProvider];
    if (!pData) {
      return { requests: 0, tokens: 0, successRate: 100, activeKeys: 0, inCooldown: 0 };
    }
    const succRate =
      pData.total_requests > 0
        ? Math.round((pData.successful_requests / pData.total_requests) * 100)
        : 100;
    return {
      requests: pData.total_requests,
      tokens: pData.total_tokens,
      successRate: succRate,
      activeKeys: pData.active_keys_count,
      inCooldown: pData.cooldown_keys_count + pData.cooldown_models_count,
    };
  }, [usage, selectedProvider]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 overflow-y-auto">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/80 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />

      {/* Modal Dialog */}
      <div className="relative w-full max-w-5xl bg-surface-card border border-white/10 rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh] z-10 animate-in fade-in-0 zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 bg-canvas-recessed/60">
          <div className="flex items-center space-x-3">
            <div className="p-2 rounded-xl bg-accent-cyan/15 text-accent-cyan border border-accent-cyan/30">
              <Activity className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h2 className="text-base font-semibold text-zinc-100 tracking-tight">
                  App Perceived Provider & Model Usage
                </h2>
                <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-accent-cyan/15 text-accent-cyan border border-accent-cyan/25">
                  Live Telemetry
                </span>
              </div>
              <p className="text-xs text-zinc-400">
                Live request counters, token usage, cooldown timers, and provider quota headers perceived by the app
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <button
              type="button"
              onClick={() => fetchUsage(false)}
              disabled={loading}
              title="Refresh Telemetry"
              className="p-1.5 rounded-lg bg-surface-raised hover:bg-white/10 text-zinc-300 hover:text-white hairline-border transition-colors pressable"
            >
              <RotateCw className={cn('w-4 h-4', loading && 'animate-spin text-accent-cyan')} />
            </button>
            <button
              type="button"
              onClick={onClose}
              title="Close (Esc)"
              className="p-1.5 rounded-lg bg-surface-raised hover:bg-white/10 text-zinc-400 hover:text-white hairline-border transition-colors pressable"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Provider Selector Tabs */}
        <div className="px-5 pt-3 pb-2 border-b border-white/5 bg-surface-raised/40 overflow-x-auto no-scrollbar flex items-center space-x-2">
          {providersList.map((prov) => {
            const isSelected = selectedProvider === prov.id;
            const pData = prov.id === 'all' ? null : usage?.providers?.[prov.id];
            const hasCooldown =
              pData && (pData.cooldown_keys_count > 0 || pData.cooldown_models_count > 0);
            return (
              <button
                key={prov.id}
                type="button"
                onClick={() => setSelectedProvider(prov.id)}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-all flex items-center space-x-1.5 pressable',
                  isSelected
                    ? 'bg-accent-cyan text-zinc-950 font-semibold shadow-sm'
                    : 'bg-surface-card hover:bg-surface-raised text-zinc-300 hover:text-white hairline-border'
                )}
              >
                <span>{prov.name}</span>
                {hasCooldown && (
                  <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                )}
              </button>
            );
          })}
        </div>

        {/* Content Body */}
        <div className="p-5 overflow-y-auto space-y-4 flex-1">
          {/* Summary Strip */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="p-3 rounded-xl bg-canvas-recessed hairline-border space-y-1">
              <span className="text-[10px] font-mono uppercase tracking-wider text-zinc-400 flex items-center space-x-1">
                <Gauge className="w-3 h-3 text-accent-cyan" />
                <span>Perceived Requests</span>
              </span>
              <div className="flex items-baseline space-x-2">
                <span className="text-xl font-bold font-mono text-zinc-100 tabular">
                  {activeMetrics.requests.toLocaleString()}
                </span>
                <span className="text-xs font-mono text-emerald-400">
                  {activeMetrics.successRate}% OK
                </span>
              </div>
            </div>

            <div className="p-3 rounded-xl bg-canvas-recessed hairline-border space-y-1">
              <span className="text-[10px] font-mono uppercase tracking-wider text-zinc-400 flex items-center space-x-1">
                <Sparkles className="w-3 h-3 text-purple-400" />
                <span>Tokens Consumed</span>
              </span>
              <div className="flex items-baseline space-x-2">
                <span className="text-xl font-bold font-mono text-zinc-100 tabular">
                  {activeMetrics.tokens >= 1000
                    ? `${(activeMetrics.tokens / 1000).toFixed(1)}k`
                    : activeMetrics.tokens.toLocaleString()}
                </span>
                <span className="text-[11px] text-zinc-400 font-mono">tokens</span>
              </div>
            </div>

            <div className="p-3 rounded-xl bg-canvas-recessed hairline-border space-y-1">
              <span className="text-[10px] font-mono uppercase tracking-wider text-zinc-400 flex items-center space-x-1">
                <Key className="w-3 h-3 text-amber-400" />
                <span>Active Keys</span>
              </span>
              <div className="flex items-baseline space-x-2">
                <span className="text-xl font-bold font-mono text-zinc-100 tabular">
                  {activeMetrics.activeKeys}
                </span>
                <span className="text-[11px] font-mono text-emerald-400">ready</span>
              </div>
            </div>

            <div className="p-3 rounded-xl bg-canvas-recessed hairline-border space-y-1">
              <span className="text-[10px] font-mono uppercase tracking-wider text-zinc-400 flex items-center space-x-1">
                <ShieldAlert className="w-3 h-3 text-rose-400" />
                <span>Rate Limits & Cooldowns</span>
              </span>
              <div className="flex items-baseline space-x-2">
                <span
                  className={cn(
                    'text-xl font-bold font-mono tabular',
                    activeMetrics.inCooldown > 0 ? 'text-amber-400' : 'text-zinc-100'
                  )}
                >
                  {activeMetrics.inCooldown}
                </span>
                <span className="text-[11px] text-zinc-400 font-mono">
                  {activeMetrics.inCooldown > 0 ? 'in cooldown' : 'healthy'}
                </span>
              </div>
            </div>
          </div>

          {/* Sub-view Navigation (Keys vs Models) */}
          <div className="flex items-center justify-between border-b border-white/10 pb-2">
            <div className="flex items-center space-x-2">
              <button
                type="button"
                onClick={() => setActiveTab('keys')}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition-colors pressable',
                  activeTab === 'keys'
                    ? 'bg-surface-raised text-zinc-100 border border-white/15'
                    : 'text-zinc-400 hover:text-zinc-200'
                )}
              >
                <Key className="w-3.5 h-3.5 text-accent-cyan" />
                <span>Perceived Keys ({filteredKeys.length})</span>
              </button>

              <button
                type="button"
                onClick={() => setActiveTab('models')}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition-colors pressable',
                  activeTab === 'models'
                    ? 'bg-surface-raised text-zinc-100 border border-white/15'
                    : 'text-zinc-400 hover:text-zinc-200'
                )}
              >
                <Cpu className="w-3.5 h-3.5 text-accent-cyan" />
                <span>Perceived Models ({filteredModels.length})</span>
              </button>
            </div>

            {/* External Links */}
            {providerMeta && (
              <div className="flex items-center space-x-2">
                <a
                  href={providerMeta.usageUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-surface-card hover:bg-surface-raised hairline-border text-[11px] font-mono text-zinc-300 hover:text-white transition-colors pressable"
                >
                  <span>Open Official {providerMeta.shortName} Console</span>
                  <ExternalLink className="w-3 h-3 text-accent-cyan" />
                </a>
              </div>
            )}
          </div>

          {/* TAB 1: KEYS BREAKDOWN */}
          {activeTab === 'keys' && (
            <div className="space-y-3">
              {filteredKeys.length === 0 ? (
                <div className="p-8 rounded-xl bg-canvas-recessed hairline-border text-center space-y-2">
                  <Key className="w-8 h-8 mx-auto text-zinc-600" />
                  <p className="text-xs font-medium text-zinc-300">No API keys detected</p>
                  <p className="text-[11px] text-zinc-500 max-w-sm mx-auto">
                    Configure your API keys in the &quot;API Keys & Notifications&quot; tab or in your .env file to enable live usage tracking.
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {filteredKeys.map((k, idx) => {
                    const isCooldown = k.cooldown_remaining_sec > 0;
                    const hasHeaders =
                      k.remaining_requests !== null && k.remaining_requests !== undefined;

                    return (
                      <div
                        key={k.key_hash || idx}
                        className={cn(
                          'p-4 rounded-xl bg-canvas-recessed hairline-border space-y-3 transition-colors',
                          isCooldown ? 'border-amber-500/40 bg-amber-500/5' : 'hover:border-white/15'
                        )}
                      >
                        {/* Key Top Row */}
                        <div className="flex items-start justify-between gap-2">
                          <div className="space-y-0.5">
                            <div className="flex items-center space-x-2">
                              <span className="text-xs font-bold text-zinc-100 font-mono tracking-tight">
                                {k.masked_key || '***'}
                              </span>
                              {k.key_index !== undefined && k.key_index !== null && (
                                <span className="px-1.5 py-0.2 rounded text-[9px] font-mono font-semibold bg-white/5 text-zinc-400 border border-white/10">
                                  Key #{k.key_index + 1}
                                </span>
                              )}
                              <button
                                type="button"
                                onClick={() => handleCopy(k.masked_key, k.key_hash)}
                                title="Copy masked identifier"
                                className="text-zinc-500 hover:text-zinc-300 transition-colors"
                              >
                                {copiedKey === k.key_hash ? (
                                  <Check className="w-3 h-3 text-emerald-400" />
                                ) : (
                                  <Copy className="w-3 h-3" />
                                )}
                              </button>
                            </div>
                            <span className="text-[10px] font-mono text-zinc-400 block">
                              {k.key_field} • {k.provider.toUpperCase()}
                            </span>
                          </div>

                          {/* Status Pill */}
                          <div className="shrink-0">
                            {isCooldown ? (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30">
                                <Clock className="w-3 h-3 animate-spin" />
                                <span>Cooldown ({Math.ceil(k.cooldown_remaining_sec)}s)</span>
                              </span>
                            ) : k.status === 'unauthorized' ? (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-rose-500/15 text-rose-300 border border-rose-500/30">
                                <span>Unauthorized (401)</span>
                              </span>
                            ) : k.status === 'daily_exhausted' ? (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-rose-500/15 text-rose-300 border border-rose-500/30">
                                <span>Daily Quota Exhausted</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                                <span>Active</span>
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Cooldown reason banner if applicable */}
                        {isCooldown && k.cooldown_reason && (
                          <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-[11px] font-mono text-amber-300 flex items-center space-x-1.5">
                            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                            <span>{k.cooldown_reason}</span>
                          </div>
                        )}

                        {/* Per-model throttled notice when key is active for other models */}
                        {!isCooldown && k.throttled_models && k.throttled_models.length > 0 && (
                          <div className="p-2 rounded-lg bg-amber-500/5 border border-amber-500/15 text-[11px] font-mono text-amber-300/90 flex items-center space-x-1.5">
                            <Clock className="w-3.5 h-3.5 shrink-0 text-amber-400" />
                            <span>
                              Throttled on {k.throttled_models.join(', ')} • Active for other models
                            </span>
                          </div>
                        )}

                        {/* Metric Grid */}
                        <div className="grid grid-cols-3 gap-2 pt-2 border-t border-white/5 text-center">
                          <div className="p-2 rounded-lg bg-surface-card/60 hairline-border">
                            <span className="text-[9px] font-mono uppercase text-zinc-400 block">
                              Requests
                            </span>
                            <span className="text-sm font-bold font-mono text-zinc-100 tabular">
                              {k.total_requests.toLocaleString()}
                            </span>
                            <span className="text-[9px] font-mono text-emerald-400 block">
                              {k.success_rate_pct}% ok
                            </span>
                          </div>

                          <div className="p-2 rounded-lg bg-surface-card/60 hairline-border">
                            <span className="text-[9px] font-mono uppercase text-zinc-400 block">
                              Tokens
                            </span>
                            <span className="text-sm font-bold font-mono text-zinc-100 tabular">
                              {k.total_tokens >= 1000
                                ? `${(k.total_tokens / 1000).toFixed(1)}k`
                                : k.total_tokens.toLocaleString()}
                            </span>
                            <span className="text-[9px] font-mono text-zinc-400 block">
                              consumed
                            </span>
                          </div>

                          <div className="p-2 rounded-lg bg-surface-card/60 hairline-border">
                            <span className="text-[9px] font-mono uppercase text-zinc-400 block">
                              429 Hits
                            </span>
                            <span
                              className={cn(
                                'text-sm font-bold font-mono tabular',
                                k.rate_limited_count > 0 ? 'text-amber-400' : 'text-zinc-100'
                              )}
                            >
                              {k.rate_limited_count}
                            </span>
                            <span className="text-[9px] font-mono text-zinc-400 block">
                              throttles
                            </span>
                          </div>
                        </div>

                        {/* Provider Header Quota Live Status (if returned from provider API) */}
                        {hasHeaders && (
                          <div className="p-2.5 rounded-lg bg-surface-raised/40 border border-white/5 space-y-1.5">
                            <span className="text-[10px] font-mono uppercase font-semibold text-accent-cyan flex items-center space-x-1">
                              <Zap className="w-3 h-3" />
                              <span>Live Provider Headers ({k.provider ? k.provider.toUpperCase() : 'QUOTA'})</span>
                            </span>
                            <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono text-zinc-300">
                              <span>
                                Remaining:{' '}
                                <strong className="text-zinc-100">
                                  {k.remaining_requests?.toLocaleString() || 'N/A'}
                                </strong>
                                {k.limit_requests ? ` / ${k.limit_requests.toLocaleString()}` : ''} reqs
                              </span>
                              {k.reset_requests_sec !== null && k.reset_requests_sec !== undefined && (
                                <span className="text-zinc-400">
                                  Reset in {Math.round(k.reset_requests_sec)}s
                                </span>
                              )}
                            </div>
                            {k.remaining_tokens !== null && k.remaining_tokens !== undefined && (
                              <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono text-zinc-300">
                                <span>
                                  Tokens:{' '}
                                  <strong className="text-zinc-100">
                                    {k.remaining_tokens.toLocaleString()}
                                  </strong>
                                  {k.limit_tokens ? ` / ${k.limit_tokens.toLocaleString()}` : ''}
                                </span>
                                {k.reset_tokens_sec !== null && k.reset_tokens_sec !== undefined && (
                                  <span className="text-zinc-400">
                                    Tokens reset in {Math.round(k.reset_tokens_sec)}s
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        )}

                        {/* Footer Last Used */}
                        <div className="flex items-center justify-between text-[10px] font-mono text-zinc-400 pt-1">
                          <span>
                            Last used:{' '}
                            <strong className="text-zinc-300">
                              {k.last_used_at ? formatRelativeTime(k.last_used_at) : 'Never'}
                            </strong>
                          </span>
                          <span>Fprint: {k.key_hash}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* TAB 2: MODELS BREAKDOWN */}
          {activeTab === 'models' && (
            <div className="space-y-3">
              {filteredModels.length === 0 ? (
                <div className="p-8 rounded-xl bg-canvas-recessed hairline-border text-center space-y-2">
                  <Cpu className="w-8 h-8 mx-auto text-zinc-600" />
                  <p className="text-xs font-medium text-zinc-300">No models active</p>
                  <p className="text-[11px] text-zinc-500 max-w-sm mx-auto">
                    Select AI models in the &quot;AI Pipeline Matrix&quot; tab to activate extraction, scoring, summarization, and visual generation.
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {filteredModels.map((m, idx) => {
                    const isCooldown = m.cooldown_remaining_sec > 0;
                    return (
                      <div
                        key={`${m.provider}-${m.model_name}-${idx}`}
                        className={cn(
                          'p-4 rounded-xl bg-canvas-recessed hairline-border space-y-3 transition-colors',
                          isCooldown ? 'border-amber-500/40 bg-amber-500/5' : 'hover:border-white/15'
                        )}
                      >
                        {/* Model Top Row */}
                        <div className="flex items-start justify-between gap-2">
                          <div className="space-y-1">
                            <div className="flex items-center space-x-2">
                              <span className="text-xs font-bold text-zinc-100 font-mono tracking-tight">
                                {m.model_name}
                              </span>
                              <span className="px-1.5 py-0.2 rounded text-[9px] font-mono font-semibold uppercase bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/25">
                                {m.provider}
                              </span>
                            </div>

                            {/* Assigned Stages Badges */}
                            <div className="flex flex-wrap items-center gap-1">
                              {m.stages.length > 0 ? (
                                m.stages.map((st) => (
                                  <span
                                    key={st}
                                    className="px-1.5 py-0.5 rounded text-[9px] font-mono font-medium bg-surface-raised text-zinc-300 border border-white/10"
                                  >
                                    {st}
                                  </span>
                                ))
                              ) : (
                                <span className="text-[10px] font-mono text-zinc-500 italic">
                                  Standby Catalog Model
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Status Pill */}
                          <div className="shrink-0">
                            {isCooldown ? (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30">
                                <Clock className="w-3 h-3 animate-spin" />
                                <span>Cooldown ({Math.ceil(m.cooldown_remaining_sec)}s)</span>
                              </span>
                            ) : m.total_requests > 0 ? (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                                <span>Active</span>
                              </span>
                            ) : (
                              <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-zinc-800 text-zinc-400 border border-zinc-700">
                                <span>Ready</span>
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Metric Grid */}
                        <div className="grid grid-cols-4 gap-2 pt-2 border-t border-white/5 text-center">
                          <div className="p-2 rounded-lg bg-surface-card/60 hairline-border">
                            <span className="text-[9px] font-mono uppercase text-zinc-400 block">
                              Requests
                            </span>
                            <span className="text-xs sm:text-sm font-bold font-mono text-zinc-100 tabular">
                              {m.total_requests.toLocaleString()}
                            </span>
                            <span className="text-[9px] font-mono text-emerald-400 block">
                              {m.success_rate_pct}% ok
                            </span>
                          </div>

                          <div className="p-2 rounded-lg bg-surface-card/60 hairline-border">
                            <span className="text-[9px] font-mono uppercase text-zinc-400 block">
                              Tokens
                            </span>
                            <span className="text-xs sm:text-sm font-bold font-mono text-zinc-100 tabular">
                              {m.total_tokens >= 1000
                                ? `${(m.total_tokens / 1000).toFixed(1)}k`
                                : m.total_tokens.toLocaleString()}
                            </span>
                            <span className="text-[9px] font-mono text-zinc-400 block">
                              total
                            </span>
                          </div>

                          <div className="p-2 rounded-lg bg-surface-card/60 hairline-border">
                            <span className="text-[9px] font-mono uppercase text-zinc-400 block">
                              Latency
                            </span>
                            <span className="text-xs sm:text-sm font-bold font-mono text-zinc-100 tabular">
                              {m.avg_latency_ms > 0 ? `${m.avg_latency_ms}ms` : '—'}
                            </span>
                            <span className="text-[9px] font-mono text-zinc-400 block">
                              average
                            </span>
                          </div>

                          <div className="p-2 rounded-lg bg-surface-card/60 hairline-border">
                            <span className="text-[9px] font-mono uppercase text-zinc-400 block">
                              429 Rate
                            </span>
                            <span
                              className={cn(
                                'text-xs sm:text-sm font-bold font-mono tabular',
                                m.rate_limited_count > 0 ? 'text-amber-400' : 'text-zinc-100'
                              )}
                            >
                              {m.rate_limited_count}
                            </span>
                            <span className="text-[9px] font-mono text-zinc-400 block">
                              limits
                            </span>
                          </div>
                        </div>

                        {/* Error Callout if any */}
                        {m.last_error && (
                          <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/20 text-[10px] font-mono text-rose-300 truncate">
                            <strong>Last Error:</strong> {m.last_error}
                          </div>
                        )}

                        {/* Footer Info */}
                        <div className="flex items-center justify-between text-[10px] font-mono text-zinc-400 pt-1">
                          <span>
                            Last call:{' '}
                            <strong className="text-zinc-300">
                              {m.last_used_at ? formatRelativeTime(m.last_used_at) : 'Never'}
                            </strong>
                          </span>
                          <span>
                            {m.is_primary ? 'Primary Model' : m.is_fallback ? 'Fallback Candidate' : 'Catalog'}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 border-t border-white/10 bg-canvas-recessed/60 text-xs">
          <div className="flex items-center space-x-2 text-zinc-400 font-mono text-[11px]">
            <Server className="w-3.5 h-3.5 text-accent-cyan" />
            <span>Updated {usage?.updated_at ? formatRelativeTime(usage.updated_at) : 'just now'}</span>
            <span>• Auto-refreshing every 5s</span>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-surface-raised hover:bg-white/10 hairline-border text-xs font-semibold text-zinc-200 hover:text-white transition-colors pressable"
          >
            Close Inspector
          </button>
        </div>
      </div>
    </div>
  );
}
