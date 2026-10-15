'use client';

import { useEffect, useState } from 'react';
import { io, Socket } from 'socket.io-client';
import { getStoredApiKey } from './api';
import type { NormalizedEvent, DashboardStats } from './types';

let socketInstance: Socket | null = null;

export function getWsBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const custom = localStorage.getItem('osint_ws_url') || localStorage.getItem('osint_api_url');
    if (custom) return custom;
    // When served over standard ports or behind a reverse proxy (e.g. ir-osint.danprav.me),
    // always use current origin without appending a custom port.
    if (!window.location.port || window.location.port === '80' || window.location.port === '443') {
      return window.location.origin;
    }
    // Direct access to host ports (e.g. Tailscale http://production:26816)
    if (window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      const port = process.env.NEXT_PUBLIC_API_PORT || '24176';
      return `${window.location.protocol}//${window.location.hostname}:${port}`;
    }
  }
  return process.env.NEXT_PUBLIC_WS_URL || 'http://localhost:8000';
}

export function getSocket(): Socket | null {
  if (typeof window === 'undefined') return null;

  if (!socketInstance) {
    const wsUrl = getWsBaseUrl();
    const apiKey = getStoredApiKey();

    socketInstance = io(wsUrl, {
      path: '/socket.io',
      auth: { api_key: apiKey },
      query: { api_key: apiKey },
      transports: ['websocket', 'polling'],
      reconnectionAttempts: 15,
      reconnectionDelay: 2000,
      autoConnect: true,
    });
  }
  return socketInstance;
}

let viewerSocketInstance: Socket | null = null;
let currentViewerToken: string | null = null;

export function getViewerSocket(viewerToken?: string | null): Socket | null {
  if (typeof window === 'undefined') return null;
  if (!viewerToken) {
    if (viewerSocketInstance) {
      viewerSocketInstance.disconnect();
      viewerSocketInstance = null;
      currentViewerToken = null;
    }
    return null;
  }

  // If token changed, recreate connection
  if (viewerSocketInstance && currentViewerToken !== viewerToken) {
    viewerSocketInstance.disconnect();
    viewerSocketInstance = null;
  }

  if (!viewerSocketInstance) {
    currentViewerToken = viewerToken;
    const wsUrl = getWsBaseUrl();
    viewerSocketInstance = io(wsUrl, {
      path: '/socket.io',
      auth: { viewer_token: viewerToken },
      query: { viewer_token: viewerToken },
      transports: ['websocket', 'polling'],
      reconnectionAttempts: 15,
      reconnectionDelay: 2000,
      autoConnect: true,
    });
  }
  return viewerSocketInstance;
}


export function useSocketConnection(enabled: boolean = true) {
  const [isConnected, setIsConnected] = useState(false);
  const [lastPing, setLastPing] = useState<number | null>(null);

  useEffect(() => {
    if (!enabled) {
      setIsConnected(false);
      return;
    }

    const socket = getSocket();
    if (!socket) return;

    const onConnect = () => {
      setIsConnected(true);
      setLastPing(Date.now());
    };

    const onDisconnect = () => {
      setIsConnected(false);
    };

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);

    if (socket.connected) {
      setIsConnected(true);
      setLastPing(Date.now());
    }

    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
    };
  }, []);

  return { isConnected, lastPing };
}

export function useRealtimeFeed(options: {
  themeId?: string;
  onNewEvent?: (event: NormalizedEvent) => void;
  onCriticalAlert?: (alert: any) => void;
  onStatsUpdate?: (stats: DashboardStats) => void;
}) {
  const { themeId, onNewEvent, onCriticalAlert, onStatsUpdate } = options;

  useEffect(() => {
    const socket = getSocket();
    if (!socket) return;

    if (themeId) {
      socket.emit('subscribe_theme', { theme_id: themeId });
    }

    const handleNew = (data: NormalizedEvent) => {
      if (onNewEvent) onNewEvent(data);
    };

    const handleAlert = (data: any) => {
      if (onCriticalAlert) onCriticalAlert(data);
    };

    const handleStats = (data: DashboardStats) => {
      if (onStatsUpdate) onStatsUpdate(data);
    };

    socket.on('event:new', handleNew);
    socket.on('alert:critical', handleAlert);
    socket.on('stats:update', handleStats);

    return () => {
      if (themeId) {
        socket.emit('unsubscribe_theme', { theme_id: themeId });
      }
      socket.off('event:new', handleNew);
      socket.off('alert:critical', handleAlert);
      socket.off('stats:update', handleStats);
    };
  }, [themeId, onNewEvent, onCriticalAlert, onStatsUpdate]);
}
