'use client';

import React from 'react';
import { X, Download, Share, PlusSquare, Monitor, CheckCircle2 } from 'lucide-react';
import { AppIcon } from '@/components/AppIcon';

interface PwaInstallModalProps {
  isOpen: boolean;
  onClose: () => void;
  isIOS?: boolean;
  appName?: string;
}

export function PwaInstallModal({
  isOpen,
  onClose,
  isIOS = false,
  appName = 'OSINT Realtime',
}: PwaInstallModalProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-md card-slick rounded-2xl p-6 border border-white/10 shadow-2xl space-y-5">
        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-1 rounded-lg text-zinc-400 hover:text-zinc-100 hover:bg-white/5 transition-colors"
          aria-label="Close"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Header */}
        <div className="flex items-center space-x-3.5 pt-1">
          <div className="relative p-2.5 rounded-xl bg-surface-raised border border-white/10 shadow-inner">
            <AppIcon size={38} withBeacon />
          </div>
          <div>
            <h3 className="text-sm font-bold text-zinc-100 uppercase tracking-tight">
              Install {appName}
            </h3>
            <p className="text-xs text-zinc-400 mt-0.5">
              Download as Progressive Web App (PWA)
            </p>
          </div>
        </div>

        {/* Benefits list */}
        <div className="p-3 rounded-xl bg-canvas-recessed border border-white/5 space-y-2 text-xs text-zinc-300">
          <div className="flex items-center space-x-2">
            <CheckCircle2 className="w-3.5 h-3.5 text-accent-cyan shrink-0" />
            <span>Standalone full-screen window without browser address bars</span>
          </div>
          <div className="flex items-center space-x-2">
            <CheckCircle2 className="w-3.5 h-3.5 text-accent-cyan shrink-0" />
            <span>Dedicated desktop/home screen launcher &amp; taskbar icon</span>
          </div>
          <div className="flex items-center space-x-2">
            <CheckCircle2 className="w-3.5 h-3.5 text-accent-cyan shrink-0" />
            <span>Offline shell caching and low-latency situational updates</span>
          </div>
        </div>

        {/* Platform Instructions */}
        {isIOS ? (
          <div className="space-y-3">
            <div className="text-xs font-mono font-semibold text-zinc-300 uppercase tracking-wider flex items-center space-x-1.5">
              <Share className="w-3.5 h-3.5 text-accent-cyan" />
              <span>Safari on iOS / iPadOS</span>
            </div>
            <ol className="space-y-2 text-xs text-zinc-300 list-decimal list-inside pl-1">
              <li className="leading-relaxed">
                Tap the <strong className="text-zinc-100">Share</strong> button{' '}
                <Share className="inline-block w-3.5 h-3.5 text-accent-cyan mx-0.5 align-text-bottom" /> in Safari's bottom toolbar.
              </li>
              <li className="leading-relaxed">
                Scroll down and select <strong className="text-zinc-100">Add to Home Screen</strong>{' '}
                <PlusSquare className="inline-block w-3.5 h-3.5 text-accent-cyan mx-0.5 align-text-bottom" />.
              </li>
              <li className="leading-relaxed">
                Tap <strong className="text-accent-cyan">Add</strong> in the top-right corner to install.
              </li>
            </ol>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="text-xs font-mono font-semibold text-zinc-300 uppercase tracking-wider flex items-center space-x-1.5">
              <Monitor className="w-3.5 h-3.5 text-accent-cyan" />
              <span>Chrome / Edge / Desktop / Android</span>
            </div>
            <ul className="space-y-2 text-xs text-zinc-300">
              <li className="flex items-start space-x-2">
                <span className="font-mono text-accent-cyan font-bold">1.</span>
                <span>
                  Look for the <strong className="text-zinc-100">Install app</strong> icon{' '}
                  <Download className="inline-block w-3.5 h-3.5 text-accent-cyan mx-0.5 align-text-bottom" /> in your browser address bar (top right).
                </span>
              </li>
              <li className="flex items-start space-x-2">
                <span className="font-mono text-accent-cyan font-bold">2.</span>
                <span>
                  Alternatively, click your browser menu <strong className="text-zinc-100">(⋮ or ⋯)</strong> &rarr; <strong className="text-zinc-100">Save and share / Install {appName}</strong>.
                </span>
              </li>
            </ul>
          </div>
        )}

        <div className="pt-2">
          <button
            onClick={onClose}
            className="w-full py-2.5 rounded-xl bg-surface-raised hover:bg-surface-raised/80 hairline-border text-xs font-medium text-zinc-200 transition-colors"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
