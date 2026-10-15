'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Layers, Wifi, WifiOff, LogOut, Lock } from 'lucide-react';
import { AppIcon } from '@/components/AppIcon';
import { InstallPwaButton } from '@/components/pwa/InstallPwaButton';
import { getViewerSocket } from '@/lib/socket';
import { getPublicTheme } from '@/lib/publicApi';
import { Theme } from '@/lib/types';
import { cn } from '@/lib/utils';

export function PublicNavbar() {
  const [token, setToken] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [activeTheme, setActiveTheme] = useState<Theme | null>(null);

  const checkAuth = useCallback(() => {
    if (typeof window === 'undefined') return;
    const stored =
      sessionStorage.getItem('osint_viewer_token') || localStorage.getItem('osint_viewer_token');
    setToken(stored || null);
    if (stored) {
      getPublicTheme(stored)
        .then((t) => {
          if (t) setActiveTheme(t);
        })
        .catch(() => {});
    } else {
      setActiveTheme(null);
    }
  }, []);

  useEffect(() => {
    checkAuth();

    const handleAuthChange = () => checkAuth();
    window.addEventListener('storage', handleAuthChange);
    window.addEventListener('osint_viewer_login', handleAuthChange);
    window.addEventListener('osint_viewer_logout', handleAuthChange);

    return () => {
      window.removeEventListener('storage', handleAuthChange);
      window.removeEventListener('osint_viewer_login', handleAuthChange);
      window.removeEventListener('osint_viewer_logout', handleAuthChange);
    };
  }, [checkAuth]);

  useEffect(() => {
    if (!token) {
      setWsConnected(false);
      return;
    }

    const socket = getViewerSocket(token);
    if (!socket) return;

    const onConnect = () => setWsConnected(true);
    const onDisconnect = () => setWsConnected(false);

    if (socket.connected) {
      setWsConnected(true);
    }

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);

    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
    };
  }, [token]);

  const handleLock = () => {
    sessionStorage.removeItem('osint_viewer_token');
    localStorage.removeItem('osint_viewer_token');
    setToken(null);
    setWsConnected(false);
    window.dispatchEvent(new Event('osint_viewer_logout'));
    window.location.reload();
  };

  return (
    <header className="sticky top-0 z-40 w-full bg-canvas-subtle/95 backdrop-blur hairline-b">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        {/* Brand & Read-Only Pill */}
        <div className="flex items-center space-x-3">
          <div className="relative flex items-center justify-center rounded-lg bg-surface-card hairline-border p-0.5">
            <AppIcon size={30} withBeacon />
          </div>
          <div className="flex flex-col">
            <div className="flex items-center space-x-2">
              <span className="text-xs font-bold tracking-tight text-zinc-100 uppercase">
                OSINT <span className="text-accent-cyan">REALTIME</span>
              </span>
              <span className="px-2 py-0.2 rounded text-[10px] font-mono font-semibold bg-accent-cyan/10 text-accent-cyan border border-accent-cyan/25">
                PUBLIC DISPLAY
              </span>
            </div>
            <span className="text-[10px] text-zinc-400 font-mono">
              READ-ONLY PORTAL • LIVE CITATIONS &amp; RADAR
            </span>
          </div>
        </div>

        {/* Right Status Controls */}
        <div className="flex items-center space-x-2.5">
          {token ? (
            <>
              {/* Active Theme Badge */}
              {activeTheme && (
                <div className="hidden sm:flex items-center space-x-1.5 px-2.5 py-1 rounded-md bg-surface-card hairline-border text-[11px] text-zinc-300">
                  <Layers className="w-3 h-3 text-accent-amber" />
                  <span className="text-zinc-400">Theme:</span>
                  <span className="font-medium text-zinc-200">{activeTheme.name}</span>
                </div>
              )}

              {/* WebSocket Live Status */}
              <div
                className={cn(
                  'flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-[11px] font-mono tracking-wide tabular hairline-border',
                  wsConnected
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                    : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                )}
              >
                {wsConnected ? (
                  <>
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500 shadow-beacon" />
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

              {/* Install Display App Button */}
              <InstallPwaButton
                label="Install App"
                variant="compact"
                appName="OSINT Public Display"
              />

              {/* Exit / Lock Button */}
              <button
                onClick={handleLock}
                title="Lock display / Log out"
                className="flex items-center space-x-1 px-2.5 py-1 rounded-md bg-surface-card hover:bg-surface-raised hairline-border text-[11px] text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Lock</span>
              </button>
            </>
          ) : (
            <div className="flex items-center space-x-2">
              <div className="flex items-center space-x-1.5 px-2.5 py-1 rounded-md bg-surface-card hairline-border text-[11px] font-mono text-zinc-400">
                <Lock className="w-3.5 h-3.5 text-accent-cyan" />
                <span className="hidden sm:inline">SECURE ACCESS</span>
              </div>
              <InstallPwaButton
                label="Install App"
                variant="compact"
                appName="OSINT Public Display"
              />
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
