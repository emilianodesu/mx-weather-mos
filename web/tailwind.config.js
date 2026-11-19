/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        navy: {
          DEFAULT: '#0B2545',
          hover: '#133764',
          light: '#E8EEF5',
        },
        amberAlert: {
          surface: '#FFFBEB',
          border: '#D97706',
          text: '#92400E',
        },
        danger: {
          bg: '#FEE2E2',
          text: '#B91C1C',
          border: '#FCA5A5',
        },
        safe: {
          bg: '#DCFCE7',
          text: '#15803D',
          border: '#86EFAC',
        },
        surface: {
          DEFAULT: '#FFFFFF',
          muted: '#F8FAFC',
          border: '#E2E8F0',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      fontSize: {
        headline: ['24px', { lineHeight: '32px', fontWeight: '700' }],
        metric: ['18px', { lineHeight: '26px', fontWeight: '600' }],
        guidance: ['15px', { lineHeight: '22px', fontWeight: '400' }],
        timestamp: ['12px', { lineHeight: '16px', fontWeight: '500' }],
      },
      borderRadius: {
        card: '16px',
        control: '12px',
        pill: '24px',
      },
    },
  },
  plugins: [],
};
