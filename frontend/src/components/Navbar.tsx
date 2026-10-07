'use client';

import React, { useState, useEffect } from 'react';
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
} from 'lucide-react';
import { useSocketConnection } from '@/lib/socket';
import { getActiveTheme } from '@/lib/api';
import { Theme } from '@/lib/types';
import { AuthModal } from './AuthModal';
import { cn } from '@/lib/utils';

export function Navbar() {
  const pathname = usePathname();
  const { isConnected } = useSocketConnection();
  const [activeTheme, setActiveTheme] = useState<Theme | null>(null);
  const [isAuthOpen, setIsAuthOpen] = useState(false);

  useEffect(() => {
    let isMounted = true;
    getActiveTheme()
      .then((theme) => {
        if (isMounted) setActiveTheme(theme);
      })
      .catch(() => {
        // Fallback or retry silently
      });
    return () => {
      isMounted = false;
    };
  }, [pathname]);

  const navItems = [
    { href: '/', label: 'Live Feed', icon: Radio },
    { href: '/trends', label: 'Trends & Analytics', icon: BarChart3 },
    { href: '/settings', label: 'Settings', icon: Sliders },
  ];

  return (
    <>
      <header className="sticky top-0 z-40 w-full bg-canvas-subtle/95 backdrop-blur hairline-b">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          {/* Logo & Brand */}
          <div className="flex items-center space-x-6">
            <Link href="/" className="flex items-center space-x-2.5 group">
              <div className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-surface-card hairline-border group-hover:border-accent-cyan/50 transition-colors">
                <Activity className="h-4 w-4 text-accent-cyan" />
                <span className="absolute -top-0.5 -right-0.5 flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent-cyan opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-accent-cyan"></span>
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-xs font-bold tracking-tight text-zinc-100 uppercase">
                  OSINT <span className="text-accent-cyan">REALTIME</span>
                </span>
                <span className="text-[10px] text-zinc-400 font-mono">v1.0 • PRODUCTION</span>
              </div>
            </Link>

            {/* Navigation links */}
            <nav className="hidden md:flex items-center space-x-1">
              {navItems.map((item) => {
                const Icon = item.icon;
                const isActive = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      'flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors pressable',
                      isActive
                        ? 'bg-surface-raised text-zinc-100 hairline-border shadow-sm'
                        : 'text-zinc-400 hover:text-zinc-200 hover:bg-surface-card/60'
                    )}
                  >
                    <Icon className={cn('w-3.5 h-3.5', isActive ? 'text-accent-cyan' : 'text-zinc-400')} />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>
          </div>

          {/* Right Header Status Bar */}
          <div className="flex items-center space-x-3">
            {/* Active Theme Chip */}
            {activeTheme && (
              <Link
                href="/settings"
                title="Active Topic Theme (Click to switch or edit in Settings)"
                className="hidden sm:flex items-center space-x-1.5 px-2.5 py-1 rounded-md bg-surface-card hairline-border text-[11px] text-zinc-300 hover:bg-surface-raised transition-colors"
              >
                <Layers className="w-3 h-3 text-accent-amber" />
                <span className="text-zinc-400">Theme:</span>
                <span className="font-medium text-zinc-200 truncate max-w-[130px]">{activeTheme.name}</span>
              </Link>
            )}

            {/* WebSocket Connection Status Beacon */}
            <div
              className={cn(
                'flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-[11px] font-mono tracking-wide tabular hairline-border',
                isConnected
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
              )}
            >
              {isConnected ? (
                <>
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500 shadow-beacon"></span>
                  </span>
                  <span className="font-semibold text-[10px]">LIVE</span>
                </>
              ) : (
                <>
                  <WifiOff className="w-3 h-3 text-rose-400 animate-pulse" />
                  <span className="font-semibold text-[10px]">OFFLINE</span>
                </>
              )}
            </div>

            {/* Auth Key config button */}
            <button
              onClick={() => setIsAuthOpen(true)}
              title="Configure API Key"
              className="p-1.5 rounded-lg bg-surface-card hairline-border text-zinc-400 hover:text-zinc-200 hover:bg-surface-raised pressable transition-colors"
            >
              <Shield className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Mobile Navigation bar */}
        <div className="flex md:hidden border-t border-white/5 px-4 py-2 space-x-1 bg-canvas-base">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'flex-1 flex items-center justify-center space-x-1.5 py-1.5 rounded-md text-xs font-medium',
                  isActive
                    ? 'bg-surface-raised text-zinc-100 hairline-border'
                    : 'text-zinc-400 hover:text-zinc-200'
                )}
              >
                <Icon className={cn('w-3.5 h-3.5', isActive ? 'text-accent-cyan' : 'text-zinc-400')} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </div>
      </header>

      <AuthModal isOpen={isAuthOpen} onClose={() => setIsAuthOpen(false)} />
    </>
  );
}
