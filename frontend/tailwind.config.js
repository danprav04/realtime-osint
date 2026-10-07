/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        canvas: {
          base: '#08090A',
          recessed: '#050506',
          subtle: '#0F1012',
        },
        surface: {
          card: '#141518',
          raised: '#1A1B1F',
          overlay: '#212227',
          active: '#2A2C33',
        },
        accent: {
          indigo: '#5E6AD2',
          cyan: '#06B6D4',
          amber: '#F59E0B',
          rose: '#EF4444',
          emerald: '#10B981',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Consolas', 'monospace'],
      },
      letterSpacing: {
        tightest: '-0.035em',
        tighter: '-0.025em',
      },
      boxShadow: {
        specular: 'inset 0 1px 0 0 rgba(255, 255, 255, 0.12), 0 4px 16px -2px rgba(0, 0, 0, 0.45)',
        'specular-hover': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.22), 0 8px 24px -4px rgba(0, 0, 0, 0.6)',
        beacon: '0 0 10px rgba(16, 185, 129, 0.6)',
        alert: '0 0 14px rgba(239, 68, 68, 0.5)',
      },
    },
  },
  plugins: [],
};
