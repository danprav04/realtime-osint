'use client';

import React, { useState, useEffect } from 'react';
import { Key, Eye, EyeOff, Save, Check, AlertCircle, Bell, Shield, ExternalLink } from 'lucide-react';
import { AIPipelineSettings, Source, SystemSettings } from '@/lib/types';
import { updateSettings } from '@/lib/api';
import { cn } from '@/lib/utils';
import { ProviderRateLimitsBar } from './ProviderRateLimitsBar';

interface ApiKeyManagerProps {
  initialSettings: SystemSettings | null;
  aiSettings?: AIPipelineSettings | null;
  sources?: Source[] | null;
  onRefresh: () => void;
}

export function ApiKeyManager({ initialSettings, aiSettings, sources, onRefresh }: ApiKeyManagerProps) {
  const [apiKeys, setApiKeys] = useState<Record<string, string>>(
    initialSettings?.api_keys || {
      gemini_api_key: '',
      groq_api_key: '',
      aihubmix_api_key: '',
      hf_embedding_url: '',
      hf_api_key: '',
      telegram_bot_token: '',
      telegram_chat_id: '',
      telegram_api_id: '',
      telegram_api_hash: '',
      reddit_client_id: '',
      reddit_client_secret: '',
      newsapi_key: '',
    }
  );

  const [notificationThreshold, setNotificationThreshold] = useState<number>(
    initialSettings?.notification_threshold ?? 7
  );

  const [pollingIntervals, setPollingIntervals] = useState<Record<string, number>>(
    initialSettings?.polling_intervals || {
      rss: 300,
      telegram: 300,
      newsapi: 600,
      reddit: 600,
      ais: 900,
    }
  );

  const [maxLookbackHours, setMaxLookbackHours] = useState<Record<string, number>>(
    initialSettings?.max_lookback_hours || {
      rss: 1,
      telegram: 1,
      newsapi: 1,
      reddit: 1,
      ais: 1,
    }
  );

  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  useEffect(() => {
    if (initialSettings) {
      if (initialSettings.api_keys) {
        setApiKeys((prev) => ({ ...prev, ...initialSettings.api_keys }));
      }
      if (initialSettings.notification_threshold !== undefined) {
        setNotificationThreshold(initialSettings.notification_threshold);
      }
      if (initialSettings.polling_intervals) {
        setPollingIntervals((prev) => ({ ...prev, ...initialSettings.polling_intervals }));
      }
      if (initialSettings.max_lookback_hours) {
        setMaxLookbackHours((prev) => ({ ...prev, ...initialSettings.max_lookback_hours }));
      }
    }
  }, [initialSettings]);

  const toggleVisibility = (keyName: string) => {
    setShowKeys((prev) => ({ ...prev, [keyName]: !prev[keyName] }));
  };

  const handleKeyChange = (keyName: string, value: string) => {
    setApiKeys((prev) => ({ ...prev, [keyName]: value }));
  };

  const handleIntervalChange = (sourceKey: string, value: number) => {
    setPollingIntervals((prev) => ({ ...prev, [sourceKey]: value }));
  };

  const handleLookbackChange = (sourceKey: string, value: number) => {
    setMaxLookbackHours((prev) => ({ ...prev, [sourceKey]: value }));
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setFeedback(null);
    try {
      await updateSettings({
        api_keys: apiKeys,
        notification_threshold: notificationThreshold,
        polling_intervals: pollingIntervals,
        max_lookback_hours: maxLookbackHours,
      });
      setFeedback({
        type: 'success',
        message: 'Credentials & notification settings successfully saved!',
      });
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to update settings' });
    } finally {
      setLoading(false);
    }
  };

  const keyFields = [
    {
      id: 'gemini_api_key',
      label: 'Google Gemini API Key',
      placeholder: 'AIzaSy...',
      rateLimitUrl: 'https://aistudio.google.com/rate-limit',
      rateLimitLabel: 'AI Studio Rate Limits',
    },
    {
      id: 'groq_api_key',
      label: 'Groq Cloud API Key(s)',
      placeholder: 'gsk_..., gsk_... (Comma-separated for auto-rotation)',
      rateLimitUrl: 'https://console.groq.com/settings/limits',
      rateLimitLabel: 'Groq Rate Limits',
    },
    {
      id: 'aihubmix_api_key',
      label: 'AI Hub Mix API Key (Free Flagships)',
      placeholder: 'sk-...',
      rateLimitUrl: 'https://aihubmix.com/models/free',
      rateLimitLabel: '10 RPM • 100 RPD • 1M TPD',
    },
    {
      id: 'hf_embedding_url',
      label: 'Hugging Face Space TEI URL',
      placeholder: 'https://your-space-name.hf.space',
      rateLimitUrl: apiKeys.hf_embedding_url || 'https://huggingface.co/spaces',
      rateLimitLabel: 'HF Space Console',
    },
    {
      id: 'hf_api_key',
      label: 'Hugging Face Access Token (Optional)',
      placeholder: 'hf_...',
      rateLimitUrl: 'https://huggingface.co/settings/tokens',
      rateLimitLabel: 'HF Access Tokens',
    },
    {
      id: 'telegram_bot_token',
      label: 'Telegram Bot Token (Alerts)',
      placeholder: '123456:ABC-DEF...',
      rateLimitUrl: 'https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this',
      rateLimitLabel: 'Bot Rate Limits',
    },
    {
      id: 'telegram_chat_id',
      label: 'Telegram Alert Chat / Channel ID',
      placeholder: '-1001234567890',
      rateLimitUrl: 'https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this',
      rateLimitLabel: 'Dispatch Limits',
    },
    {
      id: 'telegram_api_id',
      label: 'Telegram MTProto API ID',
      placeholder: '1234567',
      rateLimitUrl: 'https://my.telegram.org/apps',
      rateLimitLabel: 'MTProto App Console',
    },
    {
      id: 'telegram_api_hash',
      label: 'Telegram MTProto API Hash',
      placeholder: 'abcdef0123456...',
      rateLimitUrl: 'https://my.telegram.org/apps',
      rateLimitLabel: 'MTProto App Console',
    },
    {
      id: 'newsapi_key',
      label: 'NewsAPI / Currents API Key',
      placeholder: 'news_api_key_...',
      rateLimitUrl: 'https://newsapi.org/account',
      rateLimitLabel: 'NewsAPI Quota',
    },
    {
      id: 'reddit_client_id',
      label: 'Reddit Client ID',
      placeholder: 'reddit_app_id',
      rateLimitUrl: 'https://www.reddit.com/prefs/apps',
      rateLimitLabel: 'Reddit App Console',
    },
    {
      id: 'reddit_client_secret',
      label: 'Reddit Client Secret',
      placeholder: 'reddit_secret',
      rateLimitUrl: 'https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki',
      rateLimitLabel: 'Reddit Rate Limits (100 QPM)',
    },
  ];

  return (
    <div className="card-slick rounded-xl p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 hairline-b">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-lg bg-accent-rose/15 text-accent-rose border border-accent-rose/30">
            <Key className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold text-zinc-100 tracking-tight">
              API Keys & Notification Configuration
            </h3>
            <p className="text-xs text-zinc-400">
              Manage external provider credentials, Telegram alert recipient, and polling intervals
            </p>
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={loading}
          className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 text-xs font-semibold text-white bg-accent-rose hover:bg-accent-rose/90 rounded-lg pressable shadow-sm"
        >
          <Save className="w-4 h-4" />
          <span>{loading ? 'Saving...' : 'Save Settings'}</span>
        </button>
      </div>

      {feedback && (
        <div
          className={`p-3 rounded-lg text-xs flex items-center space-x-2 hairline-border ${
            feedback.type === 'success'
              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
              : 'bg-rose-500/10 text-rose-300 border-rose-500/20'
          }`}
        >
          {feedback.type === 'success' ? (
            <Check className="w-4 h-4 text-emerald-400" />
          ) : (
            <AlertCircle className="w-4 h-4 text-rose-400" />
          )}
          <span>{feedback.message}</span>
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6 text-xs">
        {/* Telegram Notification Rule */}
        <div className="p-4 rounded-xl bg-canvas-recessed hairline-border space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2 text-zinc-200 font-semibold">
              <Bell className="w-4 h-4 text-amber-400" />
              <span>Telegram Bot Notification Rules</span>
            </div>
            <span className="text-[11px] text-zinc-500 font-mono">F40 Dispatcher</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-zinc-300 font-medium mb-1">
                Notification Importance Threshold (Events ≥ Score Trigger Alerts)
              </label>
              <div className="flex items-center space-x-3">
                <input
                  type="range"
                  min="1"
                  max="10"
                  value={notificationThreshold}
                  onChange={(e) => setNotificationThreshold(Number(e.target.value))}
                  className="w-full accent-accent-amber cursor-pointer"
                />
                <span className="px-2.5 py-1 rounded bg-surface-raised text-amber-300 font-mono font-bold text-xs hairline-border tabular">
                  Score ≥ {notificationThreshold}
                </span>
              </div>
            </div>
            <div className="text-[11px] text-zinc-400 flex items-center">
              Events evaluated at or above this threshold trigger an instant Telegram dispatch with headline, summary, score, and source link.
            </div>
          </div>
        </div>

        {/* API Keys Grid */}
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-xs font-semibold text-zinc-300 uppercase tracking-wider">
              Provider API Keys &amp; Tokens
            </h4>
            <span className="text-[11px] text-zinc-400">
              Click any provider&apos;s rate limit link to inspect live RPM/RPD quotas
            </span>
          </div>

          <ProviderRateLimitsBar
            apiKeysOverride={apiKeys}
            aiSettings={aiSettings}
            sources={sources}
            compact
            title="Connected Provider Rate Limits (Live Status)"
          />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {keyFields.map((field) => {
              const isVisible = showKeys[field.id];
              const value = apiKeys[field.id] || '';
              const isConfigured = Boolean(value && String(value).trim().length > 0);

              return (
                <div key={field.id} className="space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center space-x-2 min-w-0">
                      <label className="block text-zinc-300 font-medium truncate">
                        {field.label}
                      </label>
                      {isConfigured && (
                        <span className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded text-[9px] font-mono font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 shrink-0">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                          <span>
                            {field.id === 'groq_api_key' && String(value).includes(',')
                              ? `CONNECTED (${String(value).split(',').filter((s) => s.trim().length > 0).length} KEYS ROTATED)`
                              : 'CONNECTED'}
                          </span>
                        </span>
                      )}
                    </div>
                    {field.rateLimitUrl && (
                      <a
                        href={field.rateLimitUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={`Open ${field.rateLimitLabel} in new tab`}
                        className="inline-flex items-center space-x-1 text-[10px] font-mono text-accent-cyan hover:text-accent-cyan/80 hover:underline shrink-0 transition-colors"
                      >
                        <span>{field.rateLimitLabel}</span>
                        <ExternalLink className="w-2.5 h-2.5" />
                      </a>
                    )}
                  </div>
                  <div className="relative">
                    <input
                      type={isVisible ? 'text' : 'password'}
                      value={value}
                      onChange={(e) => handleKeyChange(field.id, e.target.value)}
                      placeholder={field.placeholder}
                      className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 pr-10 text-zinc-100 font-mono text-xs hairline-border focus:outline-none focus:border-accent-rose tabular"
                    />
                    <button
                      type="button"
                      onClick={() => toggleVisibility(field.id)}
                      className="absolute right-2.5 top-2.5 text-zinc-500 hover:text-zinc-300 transition-colors"
                    >
                      {isVisible ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Default Polling Intervals */}
        <div className="space-y-3 pt-2">
          <h4 className="text-xs font-semibold text-zinc-300 uppercase tracking-wider">
            Default Ingestion Polling Intervals (Seconds)
          </h4>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {[
              { id: 'rss', label: 'RSS Feeds' },
              { id: 'telegram', label: 'Telegram MTProto' },
              { id: 'newsapi', label: 'News APIs' },
              { id: 'reddit', label: 'Reddit PRAW' },
              { id: 'ais', label: 'AIS Stream' },
            ].map((source) => (
              <div key={source.id} className="space-y-1">
                <label className="block text-zinc-400 font-medium text-[11px]">{source.label}</label>
                <input
                  type="number"
                  min="10"
                  max="3600"
                  value={pollingIntervals[source.id] ?? 300}
                  onChange={(e) => handleIntervalChange(source.id, Number(e.target.value))}
                  className="w-full rounded-lg bg-canvas-recessed px-3 py-1.5 text-zinc-100 font-mono text-xs hairline-border focus:outline-none focus:border-accent-rose tabular"
                />
              </div>
            ))}
          </div>
        </div>

        {/* Default Historical Lookback Window */}
        <div className="space-y-3 pt-2">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1">
            <h4 className="text-xs font-semibold text-zinc-300 uppercase tracking-wider">
              Default Historical Lookback Window (Hours)
            </h4>
            <span className="text-[11px] text-zinc-400">
              Only ingests events published within this window (1h default for live intelligence)
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {[
              { id: 'rss', label: 'RSS Feeds' },
              { id: 'telegram', label: 'Telegram MTProto' },
              { id: 'newsapi', label: 'News APIs' },
              { id: 'reddit', label: 'Reddit PRAW' },
              { id: 'ais', label: 'AIS Stream' },
            ].map((source) => (
              <div key={source.id} className="space-y-1">
                <label className="block text-zinc-400 font-medium text-[11px]">{source.label}</label>
                <div className="relative flex items-center">
                  <input
                    type="number"
                    min="0.1"
                    max="72"
                    step="0.5"
                    value={maxLookbackHours[source.id] ?? 1}
                    onChange={(e) => handleLookbackChange(source.id, Number(e.target.value))}
                    className="w-full rounded-lg bg-canvas-recessed px-3 py-1.5 pr-7 text-zinc-100 font-mono text-xs hairline-border focus:outline-none focus:border-accent-rose tabular"
                  />
                  <span className="absolute right-2 text-[10px] text-zinc-500 font-mono pointer-events-none">h</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end space-x-3 pt-4 hairline-t">
          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center space-x-1.5 px-4 py-2 text-xs font-semibold text-white bg-accent-rose hover:bg-accent-rose/90 rounded-lg pressable shadow-sm"
          >
            <Check className="w-4 h-4" />
            <span>{loading ? 'Saving...' : 'Save All Credentials'}</span>
          </button>
        </div>
      </form>
    </div>
  );
}
