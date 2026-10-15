'use client';

import { useState, useEffect, useCallback } from 'react';

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
}

let globalDeferredPrompt: BeforeInstallPromptEvent | null = null;
const listeners = new Set<() => void>();

function notifyListeners() {
  listeners.forEach((listener) => listener());
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e: Event) => {
    e.preventDefault();
    globalDeferredPrompt = e as BeforeInstallPromptEvent;
    notifyListeners();
  });

  window.addEventListener('appinstalled', () => {
    globalDeferredPrompt = null;
    notifyListeners();
  });
}

export function registerServiceWorker() {
  if (typeof window !== 'undefined' && 'serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/sw.js')
        .then((reg) => {
          // Check for worker updates
          reg.addEventListener('updatefound', () => {
            const installing = reg.installing;
            if (installing) {
              installing.addEventListener('statechange', () => {
                if (installing.state === 'installed' && navigator.serviceWorker.controller) {
                  // New update available
                  console.log('[PWA] New version ready for use.');
                }
              });
            }
          });
        })
        .catch((err) => {
          console.warn('[PWA] ServiceWorker registration failed:', err);
        });
    });
  }
}

export function usePwaInstall() {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(globalDeferredPrompt);
  const [isStandalone, setIsStandalone] = useState<boolean>(false);
  const [isIOS, setIsIOS] = useState<boolean>(false);
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);

  useEffect(() => {
    // Check if running as installed standalone PWA
    const checkStandalone = () => {
      const isDisplayStandalone = window.matchMedia('(display-mode: standalone)').matches;
      const isNavStandalone = (window.navigator as any).standalone === true;
      setIsStandalone(Boolean(isDisplayStandalone || isNavStandalone));
    };

    // Detect iOS
    const ua = window.navigator.userAgent.toLowerCase();
    const isIosDevice = /iphone|ipad|ipod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    setIsIOS(isIosDevice);

    checkStandalone();

    const updatePrompt = () => {
      setDeferredPrompt(globalDeferredPrompt);
      checkStandalone();
    };

    listeners.add(updatePrompt);
    updatePrompt();

    // Register service worker if not already done
    registerServiceWorker();

    return () => {
      listeners.delete(updatePrompt);
    };
  }, []);

  const promptInstall = useCallback(async () => {
    if (globalDeferredPrompt) {
      try {
        await globalDeferredPrompt.prompt();
        const choice = await globalDeferredPrompt.userChoice;
        if (choice.outcome === 'accepted') {
          globalDeferredPrompt = null;
          setDeferredPrompt(null);
        }
        return choice.outcome;
      } catch (err) {
        console.warn('[PWA] Error calling install prompt:', err);
        setIsModalOpen(true);
        return 'error';
      }
    } else {
      // If no native prompt is queued (e.g. iOS or desktop browser without immediate prompt)
      setIsModalOpen(true);
      return 'manual_instructions';
    }
  }, []);

  return {
    canInstall: !isStandalone && (Boolean(deferredPrompt) || isIOS || typeof window !== 'undefined'),
    hasNativePrompt: Boolean(deferredPrompt),
    isStandalone,
    isIOS,
    isModalOpen,
    setIsModalOpen,
    promptInstall,
  };
}
