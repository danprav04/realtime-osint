import type { Metadata, Viewport } from 'next';

export const viewport: Viewport = {
  themeColor: '#090a0f',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
};

export const metadata: Metadata = {
  title: 'OSINT Real-time Display | Public SITREP',
  description:
    'Public read-only real-time OSINT situational display, live threat stream, and consensus radar.',
  applicationName: 'OSINT Display',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'OSINT Display',
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
  manifest: '/display.webmanifest',
  other: {
    'mobile-web-app-capable': 'yes',
  },
};

export default function DisplayLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
