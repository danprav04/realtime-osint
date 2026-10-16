'use client';

import React from 'react';
import Link from 'next/link';
import { ArrowLeft, Sliders, Layers, Radio, Cpu, Key, Terminal } from 'lucide-react';
import { ServerLogsViewer } from '@/components/settings/ServerLogsViewer';
import { AppIcon } from '@/components/AppIcon';

export default function ServerLogsPage() {
  const quickLinks = [
    { href: '/settings?tab=themes', label: 'Topic Themes', icon: Layers },
    { href: '/settings?tab=sources', label: 'Ingestion Sources', icon: Radio },
    { href: '/settings?tab=ai', label: 'AI Pipeline Matrix', icon: Cpu },
    { href: '/settings?tab=keys', label: 'API Keys & Notifications', icon: Key },
  ];

  return (
    <div className="space-y-6">
      {/* Page Header & Breadcrumb */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 hairline-b">
        <div className="flex items-center space-x-3">
          <Link
            href="/settings"
            className="p-2 rounded-lg bg-surface-raised hairline-border text-zinc-400 hover:text-white transition-colors pressable"
            title="Back to System Settings"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <AppIcon size={38} />
          <div>
            <div className="flex items-center space-x-2">
              <Link
                href="/settings"
                className="text-xs text-zinc-400 hover:text-accent-cyan transition-colors"
              >
                Settings
              </Link>
              <span className="text-zinc-600">/</span>
              <span className="text-xs text-zinc-200 font-medium">Server Logs</span>
            </div>
            <h2 className="text-lg font-semibold text-zinc-100 tracking-tight">
              Server Logs & Telemetry Console
            </h2>
          </div>
        </div>

        {/* Quick links to other settings sections */}
        <div className="flex flex-wrap items-center gap-1.5">
          {quickLinks.map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hairline-border text-xs text-zinc-400 hover:text-zinc-200 hover:bg-surface-raised transition-colors pressable"
              >
                <Icon className="w-3.5 h-3.5 text-zinc-500" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
      </div>

      {/* Interactive Server Logs Viewer */}
      <ServerLogsViewer />
    </div>
  );
}
