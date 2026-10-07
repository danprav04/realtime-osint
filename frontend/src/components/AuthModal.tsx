'use client';

import React, { useState, useEffect } from 'react';
import { Key, X, Check, Shield } from 'lucide-react';
import { getStoredApiKey, setStoredApiKey } from '@/lib/api';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

export function AuthModal({ isOpen, onClose, onSaved }: AuthModalProps) {
  const [apiKey, setApiKey] = useState('');
  const [savedSuccess, setSavedSuccess] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setApiKey(getStoredApiKey());
      setSavedSuccess(false);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    setStoredApiKey(apiKey.trim());
    setSavedSuccess(true);
    if (onSaved) onSaved();
    setTimeout(() => {
      onClose();
    }, 600);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="relative w-full max-w-md rounded-xl bg-surface-overlay p-6 hairline-border shadow-2xl">
        <div className="flex items-center justify-between pb-4 hairline-b mb-5">
          <div className="flex items-center space-x-2">
            <div className="p-2 rounded-lg bg-accent-indigo/15 text-accent-indigo">
              <Shield className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-zinc-100 tracking-tight">API Key Authentication</h3>
              <p className="text-xs text-zinc-400">Configure your authorization header</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-surface-raised transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-zinc-300 mb-1.5">
              System API Key (<code className="text-accent-cyan">X-API-Key</code>)
            </label>
            <div className="relative">
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="osint_secret_key_2026"
                className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2.5 text-sm text-zinc-100 placeholder-zinc-500 hairline-border focus:outline-none focus:border-accent-indigo focus:ring-1 focus:ring-accent-indigo tabular"
              />
              <Key className="absolute right-3 top-3 w-4 h-4 text-zinc-500" />
            </div>
            <p className="mt-1.5 text-[11px] text-zinc-400">
              Default in development is <code className="kbd-cap">osint_secret_key_2026</code>. Saved in browser storage.
            </p>
          </div>

          <div className="flex items-center justify-end space-x-3 pt-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-xs font-medium text-zinc-300 hover:text-zinc-100 hover:bg-surface-raised rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="inline-flex items-center space-x-2 px-4 py-2 text-xs font-semibold text-white bg-accent-indigo hover:bg-accent-indigo/90 rounded-lg pressable shadow-sm"
            >
              {savedSuccess ? (
                <>
                  <Check className="w-4 h-4 text-emerald-300" />
                  <span>Saved!</span>
                </>
              ) : (
                <span>Save Key</span>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
