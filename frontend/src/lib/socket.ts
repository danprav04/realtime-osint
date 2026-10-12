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
    if (window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      const port = process.env.NEXT_PUBLIC_API_PORT || '8080';
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

export function useSocketConnection() {
  const [isConnected, setIsConnected] = useState(false);
  const [lastPing, setLastPing] = useState<number | null>(null);

  useEffect(() => {
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
