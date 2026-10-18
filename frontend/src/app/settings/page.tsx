'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  Sliders,
  Layers,
  Radio,
  Cpu,
  Globe,
  Key,
  RotateCw,
  Terminal,
} from 'lucide-react';
import {
  Theme,
  Source,
  AIPipelineSettings,
  SystemSettings,
  EmbeddingStatus,
  UsageTelemetryResponse,
} from '@/lib/types';
import {
  getThemes,
  getActiveTheme,
  getSources,
  getAIPipelineSettings,
  getSettings,
  getEmbeddingStatus,
  getProviderUsage,
} from '@/lib/api';
import { ThemeForm } from '@/components/settings/ThemeForm';
import { SourceManager } from '@/components/settings/SourceManager';
import { AIConfigMatrix } from '@/components/settings/AIConfigMatrix';
import { ApiKeyManager } from '@/components/settings/ApiKeyManager';
import { WorldTheaterManager } from '@/components/settings/WorldTheaterManager';
import { ServerLogsViewer } from '@/components/settings/ServerLogsViewer';
import { ProviderRateLimitsBar } from '@/components/settings/ProviderRateLimitsBar';
import { AppIcon } from '@/components/AppIcon';
import { cn } from '@/lib/utils';

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<'themes' | 'sources' | 'ai' | 'theater' | 'keys' | 'logs'>('themes');
  const [loading, setLoading] = useState(true);

  // Loaded data
  const [themes, setThemes] = useState<Theme[]>([]);
  const [activeTheme, setActiveTheme] = useState<Theme | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [aiSettings, setAiSettings] = useState<AIPipelineSettings | null>(null);
  const [systemSettings, setSystemSettings] = useState<SystemSettings | null>(null);
  const [embeddingStatus, setEmbeddingStatus] = useState<EmbeddingStatus | null>(null);
  const [providerUsage, setProviderUsage] = useState<UsageTelemetryResponse | null>(null);

  // Sync tab from URL query param (?tab=logs, etc.)
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const urlParams = new URLSearchParams(window.location.search);
      const tabParam = urlParams.get('tab');
      if (
        tabParam === 'themes' ||
        tabParam === 'sources' ||
        tabParam === 'ai' ||
        tabParam === 'theater' ||
        tabParam === 'keys' ||
        tabParam === 'logs'
      ) {
        setActiveTab(tabParam);
      }
    }
  }, []);

  const loadAllSettings = useCallback(async () => {
    try {
      const [themesData, activeThemeData, sourcesData, aiData, sysData, embedData, usageData] =
        await Promise.all([
          getThemes().catch(() => []),
          getActiveTheme().catch(() => null),
          getSources().catch(() => []),
          getAIPipelineSettings().catch(() => null),
          getSettings().catch(() => null),
          getEmbeddingStatus().catch(() => null),
          getProviderUsage().catch(() => null),
        ]);

      setThemes(themesData);
      setActiveTheme(activeThemeData);
      setSources(sourcesData);
      setAiSettings(aiData);
      setSystemSettings(sysData);
      setEmbeddingStatus(embedData);
      setProviderUsage(usageData);
    } catch (err) {
      console.error('Failed to load settings:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAllSettings();
  }, [loadAllSettings]);

  const tabs = [
    {
      id: 'themes' as const,
      label: 'Topic Themes & Prompts',
      icon: Layers,
      desc: 'Zero-code topic reconfiguration & dynamic scoring instructions',
    },
    {
      id: 'sources' as const,
      label: 'Ingestion Sources',
      icon: Radio,
      desc: 'RSS feeds, Telegram channels, News APIs, Reddit, and AIS',
    },
    {
      id: 'ai' as const,
      label: 'AI Pipeline Matrix',
      icon: Cpu,
      desc: 'Per-stage Gemini vs Groq model and temperature selection',
    },
    {
      id: 'theater' as const,
      label: 'World Theater Context',
      icon: Globe,
      desc: 'Live geopolitical baseline, sovereign world leadership roster, and active flashpoints',
    },
    {
      id: 'keys' as const,
      label: 'API Keys & Notifications',
      icon: Key,
      desc: 'External credentials, Telegram alerts, and polling intervals',
    },
    {
      id: 'logs' as const,
      label: 'Server Logs',
      icon: Terminal,
      desc: 'Real-time stdout stream, pipeline traces, log level filtering, and diagnostics',
    },
  ];

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 hairline-b">
        <div className="flex items-center space-x-3">
          <AppIcon size={38} />
          <div>
            <h2 className="text-lg font-semibold text-zinc-100 tracking-tight">
              System Settings & Zero-Code Hot Reload
            </h2>
            <p className="text-xs text-zinc-400">
              All modifications apply immediately without server restart via Redis config broadcast
            </p>
          </div>
        </div>

        <button
          onClick={() => {
            setLoading(true);
            loadAllSettings();
          }}
          className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-surface-raised hairline-border text-xs text-zinc-300 hover:text-white transition-colors pressable"
        >
          <RotateCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>Reload Config</span>
        </button>
      </div>

      {/* Connected Provider Rate Limits & Quotas Shortcut Bar */}
      <ProviderRateLimitsBar
        systemSettings={systemSettings}
        aiSettings={aiSettings}
        sources={sources}
        hfSpaceConfigured={embeddingStatus?.hf_space_configured}
        providerUsage={providerUsage}
      />

      {/* Tab Selector Navigation */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isSelected = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => {
                setActiveTab(tab.id);
                if (typeof window !== 'undefined') {
                  const url = new URL(window.location.href);
                  url.searchParams.set('tab', tab.id);
                  window.history.replaceState(null, '', url.toString());
                }
              }}
              className={cn(
                'card-slick rounded-xl p-3.5 text-left flex flex-col justify-between transition-all pressable',
                isSelected
                  ? 'border-accent-cyan/50 bg-surface-raised shadow-sm'
                  : 'hover:border-white/10'
              )}
            >
              <div className="flex items-center space-x-2.5 mb-2">
                <div
                  className={cn(
                    'p-1.5 rounded-lg border hairline-border',
                    isSelected
                      ? 'bg-accent-cyan/15 text-accent-cyan border-accent-cyan/30'
                      : 'bg-canvas-recessed text-zinc-400'
                  )}
                >
                  <Icon className="w-4 h-4" />
                </div>
                <span
                  className={cn(
                    'text-xs font-semibold tracking-tight',
                    isSelected ? 'text-zinc-100' : 'text-zinc-300'
                  )}
                >
                  {tab.label}
                </span>
              </div>
              <p className="text-[11px] text-zinc-500 line-clamp-2">{tab.desc}</p>
            </button>
          );
        })}
      </div>

      {/* Tab Panels */}
      <div>
        {activeTab === 'themes' && (
          <ThemeForm
            themes={themes}
            activeTheme={activeTheme}
            onRefresh={loadAllSettings}
          />
        )}

        {activeTab === 'sources' && (
          <SourceManager
            sources={sources}
            activeThemeId={activeTheme?.id}
            onRefresh={loadAllSettings}
          />
        )}

        {activeTab === 'ai' && (
          <AIConfigMatrix
            initialSettings={aiSettings}
            systemSettings={systemSettings}
            onRefresh={loadAllSettings}
          />
        )}

        {activeTab === 'theater' && <WorldTheaterManager />}

        {activeTab === 'keys' && (
          <ApiKeyManager
            initialSettings={systemSettings}
            aiSettings={aiSettings}
            sources={sources}
            onRefresh={loadAllSettings}
          />
        )}

        {activeTab === 'logs' && <ServerLogsViewer />}
      </div>
    </div>
  );
}
