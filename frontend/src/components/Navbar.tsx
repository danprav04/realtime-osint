'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Radio,
  BarChart3,
  Sliders,
  Shield,
  Activity,
  Layers,
  Wifi,
  WifiOff,
  Compass,
  Gauge,
  ExternalLink,
  ChevronDown,
  Sparkles,
} from 'lucide-react';
import { useSocketConnection } from '@/lib/socket';
import { getActiveTheme, getSettings, getAIPipelineSettings, getSources } from '@/lib/api';
import { Theme, SystemSettings, AIPipelineSettings, Source } from '@/lib/types';
import { AuthModal } from './AuthModal';
import { cn, getProviderRateLimitStatuses } from '@/lib/utils';

import { AppIcon } from '@/components/AppIcon';
import { InstallPwaButton } from '@/components/pwa/InstallPwaButton';
import { PublicNavbar } from './PublicNavbar';

export function Navbar() {
  const pathname = usePathname();
  const isDisplay =
    pathname?.startsWith('/display') ||
    (typeof window !== 'undefined' && window.location.hostname.includes('ir-osint'));

  const { isConnected } = useSocketConnection(!isDisplay);
  const [activeTheme, setActiveTheme] = useState<Theme | null>(null);
  const [sysSettings, setSysSettings] = useState<SystemSettings | null>(null);
  const [aiSettings, setAiSettings] = useState<AIPipelineSettings | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [isLimitsOpen, setIsLimitsOpen] = useState(false);
  const limitsMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isDisplay) return;
    let isMounted = true;
    Promise.all([
      getActiveTheme().catch(() => null),
      getSettings().catch(() => null),
      getAIPipelineSettings().catch(() => null),
      getSources().catch(() => []),
    ]).then(([theme, sys, ai, srcList]) => {
      if (!isMounted) return;
      if (theme) setActiveTheme(theme);
      if (sys) setSysSettings(sys);
      if (ai) setAiSettings(ai);
      if (srcList) setSources(srcList);
    });
    return () => {
      isMounted = false;
    };
  }, [pathname, isDisplay]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (limitsMenuRef.current && !limitsMenuRef.current.contains(e.target as Node)) {
        setIsLimitsOpen(false);
      }
    };
    if (isLimitsOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isLimitsOpen]);

  const allProviderStatuses = getProviderRateLimitStatuses({
    apiKeys: sysSettings?.api_keys,
    aiSettings,
    sources,
  });
  const connectedProviders = allProviderStatuses.filter((p) => p.isConnected);
  const menuProviders =
    connectedProviders.length > 0
      ? connectedProviders
      : allProviderStatuses.filter((p) => p.id === 'gemini' || p.id === 'groq');

  const navItems = [
    { href: '/', label: 'Live Feed', shortLabel: 'Feed', icon: Radio },
    { href: '/summary', label: 'AI Summary', shortLabel: 'Summary', icon: Sparkles },
    { href: '/map', label: 'Relevance Map', shortLabel: 'Map', icon: Compass },
    { href: '/trends', label: 'Trends & Analytics', shortLabel: 'Trends', icon: BarChart3 },
    { href: '/settings', label: 'Settings', shortLabel: 'Settings', icon: Sliders },
  ];

  if (isDisplay) {
    return <PublicNavbar />;
  }

  return (
    <>
      <header className="sticky top-0 z-40 w-full bg-canvas-subtle/95 backdrop-blur hairline-b">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8 gap-4">
          {/* Logo & Brand */}
          <div className="flex items-center gap-3 lg:gap-6 shrink-0 min-w-0">
            <Link href="/" className="flex items-center gap-2.5 group shrink-0">
              <div className="relative flex items-center justify-center rounded-lg bg-surface-card hairline-border group-hover:border-accent-cyan/50 transition-colors p-1">
                <AppIcon size={28} withBeacon />
              </div>
              <div className="flex flex-col whitespace-nowrap min-w-0">
                <span className="text-xs font-bold tracking-tight text-zinc-100 uppercase whitespace-nowrap leading-none">
                  OSINT <span className="text-accent-cyan">REALTIME</span>
                </span>
                <span className="text-[10px] text-zinc-400 font-mono tracking-tight whitespace-nowrap mt-1 leading-none">
                  v1.0 <span className="text-zinc-600">•</span> PRODUCTION
                </span>
              </div>
            </Link>

            {/* Navigation links */}
            <nav className="hidden md:flex items-center gap-1 xl:gap-1.5 shrink-0">
              {navItems.map((item) => {
                const Icon = item.icon;
                const isActive = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      'flex items-center gap-1.5 h-8 px-2.5 xl:px-3 rounded-lg text-xs font-medium transition-all pressable whitespace-nowrap shrink-0',
                      isActive
                        ? 'bg-surface-raised text-zinc-100 hairline-border shadow-sm'
                        : 'text-zinc-400 hover:text-zinc-200 hover:bg-surface-card/60'
                    )}
                  >
                    <Icon className={cn('w-3.5 h-3.5 shrink-0', isActive ? 'text-accent-cyan' : 'text-zinc-400')} />
                    <span className="hidden xl:inline">{item.label}</span>
                    <span className="xl:hidden">{item.shortLabel}</span>
                  </Link>
                );
              })}
            </nav>
          </div>

          {/* Right Header Status Bar */}
          <div className="flex items-center gap-2 sm:gap-2.5 shrink-0">
            {/* Active Theme Chip */}
            {activeTheme && (
              <Link
                href="/settings"
                title={`Active Theme: ${activeTheme.name} (Click to switch or edit in Settings)`}
                className="hidden lg:flex items-center gap-1.5 h-8 px-2.5 rounded-md bg-surface-card hairline-border text-[11px] text-zinc-300 hover:bg-surface-raised transition-colors shrink-0 whitespace-nowrap"
              >
                <Layers className="w-3.5 h-3.5 text-accent-amber shrink-0" />
                <span className="text-zinc-400 font-normal">Theme:</span>
                <span className="font-medium text-zinc-200 truncate max-w-[105px] xl:max-w-[150px]">
                  {activeTheme.name}
                </span>
              </Link>
            )}

            {/* Connected Provider Rate Limits Shortcut Dropdown */}
            <div className="relative" ref={limitsMenuRef}>
              <button
                type="button"
                onClick={() => setIsLimitsOpen((prev) => !prev)}
                title="Shortcut links to rate limits of connected providers"
                className={cn(
                  'flex items-center gap-1.5 h-8 px-2.5 rounded-md text-[11px] hairline-border transition-colors pressable shrink-0 whitespace-nowrap',
                  isLimitsOpen
                    ? 'bg-surface-raised text-accent-cyan border-accent-cyan/40'
                    : 'bg-surface-card text-zinc-300 hover:text-zinc-100 hover:bg-surface-raised'
                )}
              >
                <Gauge className="w-3.5 h-3.5 text-accent-cyan shrink-0" />
                <span className="hidden sm:inline font-medium whitespace-nowrap">Rate Limits</span>
                <ChevronDown
                  className={cn(
                    'w-3 h-3 text-zinc-400 transition-transform duration-150 shrink-0',
                    isLimitsOpen && 'rotate-180 text-accent-cyan'
                  )}
                />
              </button>

              {isLimitsOpen && (
                <div className="absolute right-0 mt-2 w-80 rounded-xl bg-surface-overlay/95 backdrop-blur-md border border-white/10 shadow-2xl p-3 z-50 space-y-2.5 text-xs">
                  <div className="flex items-center justify-between pb-2 border-b border-white/10">
                    <div>
                      <span className="font-semibold text-zinc-100 block">
                        Connected Provider Rate Limits
                      </span>
                      <span className="text-[10px] text-zinc-400">
                        Open live RPM / RPD quota dashboards
                      </span>
                    </div>
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 tabular">
                      {menuProviders.length} Active
                    </span>
                  </div>

                  <div className="space-y-1.5 max-h-72 overflow-y-auto pr-0.5">
                    {menuProviders.map((provider) => (
                      <div
                        key={provider.id}
                        className="p-2 rounded-lg bg-surface-card/90 hairline-border flex items-center justify-between gap-2 hover:border-white/15 transition-colors"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center space-x-1.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)] shrink-0" />
                            <span className="font-semibold text-zinc-100 truncate text-[11px]">
                              {provider.name}
                            </span>
                          </div>
                          <span className="block text-[10px] font-mono text-zinc-400 pl-3 tabular truncate">
                            {provider.quotaSummary}
                          </span>
                        </div>

                        <div className="flex items-center space-x-1 shrink-0">
                          <a
                            href={provider.rateLimitUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={() => setIsLimitsOpen(false)}
                            className="inline-flex items-center space-x-1 px-2 py-1 rounded bg-accent-cyan/10 hover:bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/25 text-[10px] font-mono font-semibold transition-colors"
                          >
                            <span>Limits</span>
                            <ExternalLink className="w-2.5 h-2.5" />
                          </a>
                          <a
                            href={provider.usageUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={() => setIsLimitsOpen(false)}
                            title={`Open ${provider.name} Usage / Console`}
                            className="p-1 rounded bg-surface-raised hover:bg-white/10 text-zinc-400 hover:text-zinc-200 hairline-border transition-colors"
                          >
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="pt-1.5 border-t border-white/10 flex items-center justify-between text-[11px]">
                    <Link
                      href="/settings"
                      onClick={() => setIsLimitsOpen(false)}
                      className="text-accent-cyan hover:underline font-medium"
                    >
                      Configure Keys &amp; Fallbacks &rarr;
                    </Link>
                  </div>
                </div>
              )}
            </div>

            {/* WebSocket Connection Status Beacon */}
            <div
              className={cn(
                'flex items-center gap-1.5 h-8 px-2.5 rounded-md text-[11px] font-mono tracking-wide tabular hairline-border shrink-0 whitespace-nowrap',
                isConnected
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
              )}
            >
              {isConnected ? (
                <>
                  <span className="relative flex h-2 w-2 shrink-0">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500 shadow-beacon"></span>
                  </span>
                  <span className="font-semibold text-[10px] whitespace-nowrap">LIVE</span>
                </>
              ) : (
                <>
                  <WifiOff className="w-3.5 h-3.5 text-rose-400 animate-pulse shrink-0" />
                  <span className="font-semibold text-[10px] whitespace-nowrap">OFFLINE</span>
                </>
              )}
            </div>

            {/* Install PWA Button */}
            <InstallPwaButton
              label="Install App"
              variant="compact"
              appName="OSINT Realtime"
              hideLabelOnMobile={true}
              className="hidden sm:flex shrink-0 whitespace-nowrap"
            />

            {/* Auth Key config button */}
            <button
              onClick={() => setIsAuthOpen(true)}
              title="Configure API Key"
              className="flex items-center justify-center h-8 w-8 rounded-md bg-surface-card hairline-border text-zinc-400 hover:text-zinc-200 hover:bg-surface-raised pressable transition-colors shrink-0"
            >
              <Shield className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Mobile Navigation bar */}
        <div className="flex md:hidden border-t border-white/5 px-2.5 py-2 gap-1 bg-canvas-base items-center">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'flex-1 flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-colors gap-1 whitespace-nowrap min-w-0',
                  isActive
                    ? 'bg-surface-raised text-zinc-100 hairline-border'
                    : 'text-zinc-400 hover:text-zinc-200'
                )}
              >
                <Icon className={cn('w-4 h-4 shrink-0', isActive ? 'text-accent-cyan' : 'text-zinc-400')} />
                <span className="truncate">{item.shortLabel}</span>
              </Link>
            );
          })}
          <InstallPwaButton
            label="Install"
            variant="compact"
            appName="OSINT Realtime"
            hideLabelOnMobile={true}
            className="h-8 px-2 text-xs shrink-0"
          />
        </div>
      </header>

      <AuthModal isOpen={isAuthOpen} onClose={() => setIsAuthOpen(false)} />
    </>
  );
}
