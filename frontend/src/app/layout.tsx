import type { Metadata, Viewport } from 'next';
import './globals.css';
import { Navbar } from '@/components/Navbar';
import { AppIcon } from '@/components/AppIcon';
import { PwaRegistration } from '@/components/pwa/PwaRegistration';

export const viewport: Viewport = {
  themeColor: '#090a0f',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  viewportFit: 'cover',
};

export const metadata: Metadata = {
  title: 'Real-time OSINT Gathering Service',
  description:
    'Production-grade real-time OSINT intelligence dashboard with multi-source ingestion and Gemini/Groq AI processing.',
  applicationName: 'OSINT Realtime',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'OSINT Realtime',
  },
  icons: {
    icon: [
      { url: '/favicon.ico', sizes: 'any' },
      { url: '/icon.svg', type: 'image/svg+xml' },
    ],
    apple: [
      { url: '/apple-touch-icon.png', sizes: '180x180' },
    ],
  },
  manifest: '/site.webmanifest',
  other: {
    'mobile-web-app-capable': 'yes',
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-canvas-base text-zinc-100 antialiased selection:bg-accent-cyan/20 selection:text-accent-cyan">
        <PwaRegistration />
        <div className="flex min-h-screen flex-col">
          <Navbar />
          <main className="flex-1 max-w-7xl w-full mx-auto px-3 sm:px-6 lg:px-8 py-3 sm:py-6">
            {children}
          </main>
          <footer className="w-full border-t border-white/5 py-4 text-center text-[11px] text-zinc-500 font-mono pb-[calc(4.5rem+env(safe-area-inset-bottom))] lg:pb-4">
            <div className="flex items-center justify-center space-x-2">
              <AppIcon size={16} variant="minimal" />
              <span>REAL-TIME OSINT PLATFORM • ZERO-CODE THEME ENGINE • MULTI-PROVIDER AI PIPELINE</span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
