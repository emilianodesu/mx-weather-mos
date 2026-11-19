import { useState, type ReactNode } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { CloudSun, FileCheck2, LineChart, Map as MapIcon, Menu, X } from 'lucide-react';

interface LayoutProps {
  children: ReactNode;
}

const NAV_ITEMS = [
  { to: '/', label: 'Portal Home', icon: CloudSun, end: true },
  { to: '/map', label: 'Forecast Map', icon: MapIcon, end: false },
  { to: '/explorer', label: 'Trends & ML Explorer', icon: LineChart, end: false },
  { to: '/export-receipt', label: 'Export Receipt', icon: FileCheck2, end: false },
];

export function Layout({ children }: LayoutProps) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <div className="min-h-screen flex flex-col bg-surface-muted text-slate-900">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-navy focus:text-white focus:px-4 focus:py-3 focus:rounded-control"
      >
        Skip to main content
      </a>

      <header className="bg-navy text-white shadow-sm sticky top-0 z-40">
        {import.meta.env.VITE_DEMO_MODE === 'true' && (
          <div className="bg-slate-900 border-b border-white/15 text-[13px] text-slate-200">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-1.5 flex flex-wrap items-center justify-between gap-2">
              <span>
                <strong>Interactive Snapshot Mode (2026-11-19):</strong> Real PostgreSQL/PostGIS &amp;
                LightGBM MOS outputs exported for GitHub Pages. Run{' '}
                <code className="font-mono text-white">docker compose up --build</code> locally for
                live API ingestion.
              </span>
              <span className="flex items-center gap-4 font-semibold">
                <a
                  href={`${import.meta.env.BASE_URL}case-study.html`}
                  className="text-sky-300 hover:text-white underline"
                >
                  Architecture &amp; Benchmarks
                </a>
                <a
                  href="https://github.com/emilianodesu/mx-weather-mos"
                  target="_blank"
                  rel="noreferrer"
                  className="text-sky-300 hover:text-white underline"
                >
                  GitHub Repo
                </a>
              </span>
            </div>
          </div>
        )}
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between min-h-[68px] py-2 gap-4">
            <Link
              to="/"
              className="flex items-center gap-3 min-h-[48px] rounded-control px-1 focus-visible:outline-white"
            >
              <span
                className="inline-flex items-center justify-center w-10 h-10 rounded-control bg-white/15 text-white shrink-0"
                aria-hidden="true"
              >
                <CloudSun className="w-6 h-6" />
              </span>
              <div className="leading-tight">
                <span className="block text-[18px] font-bold tracking-tight text-white">
                  MX Weather Intel
                </span>
                <span className="block text-[12px] font-medium text-slate-200">
                  Forecast Verification &amp; ML Bias Correction
                </span>
              </div>
            </Link>

            {/* Desktop Navigation */}
            <nav aria-label="Primary navigation" className="hidden md:flex items-center gap-2">
              {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    `inline-flex items-center gap-2 px-4 min-h-[48px] min-w-[48px] rounded-control text-[15px] font-medium transition-colors ${
                      isActive
                        ? 'bg-white text-navy font-semibold shadow-sm'
                        : 'text-slate-100 hover:bg-navy-hover hover:text-white'
                    }`
                  }
                >
                  <Icon className="w-4 h-4 shrink-0" aria-hidden="true" />
                  <span>{label}</span>
                </NavLink>
              ))}
            </nav>

            {/* Mobile Hamburger Button (48x48px minimum touch target) */}
            <button
              type="button"
              onClick={() => setMobileMenuOpen((prev) => !prev)}
              aria-expanded={mobileMenuOpen}
              aria-controls="mobile-primary-nav"
              aria-label={mobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
              className="md:hidden inline-flex items-center justify-center min-h-[48px] min-w-[48px] rounded-control bg-white/10 text-white hover:bg-white/20 transition-colors"
            >
              {mobileMenuOpen ? (
                <X className="w-6 h-6" aria-hidden="true" />
              ) : (
                <Menu className="w-6 h-6" aria-hidden="true" />
              )}
            </button>
          </div>
        </div>

        {/* Mobile Navigation Drawer */}
        {mobileMenuOpen && (
          <nav
            id="mobile-primary-nav"
            aria-label="Mobile navigation"
            className="md:hidden border-t border-white/15 bg-navy px-4 py-3 space-y-2"
          >
            {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                onClick={() => setMobileMenuOpen(false)}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-4 min-h-[48px] rounded-control text-[15px] font-medium transition-colors ${
                    isActive
                      ? 'bg-white text-navy font-semibold'
                      : 'text-slate-100 hover:bg-navy-hover'
                  }`
                }
              >
                <Icon className="w-5 h-5 shrink-0" aria-hidden="true" />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
        )}
      </header>

      <main id="main-content" className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {children}
      </main>

      <footer className="bg-surface border-t border-surface-border mt-12">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-3">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <p className="text-[15px] font-semibold text-navy">
              MX Weather Intel — Data Scope &amp; Provenance Notice
            </p>
            <span className="inline-flex items-center px-3 py-1 rounded-pill text-[12px] font-semibold bg-navy-light text-navy w-fit">
              WCAG 2.1 AAA High-Contrast UI
            </span>
          </div>
          <p className="text-[15px] leading-relaxed text-slate-700">
            Forecasts, ML bias-correction models, and historical verification in this platform use{' '}
            <strong>Open-Meteo NWP</strong> forecasts paired with <strong>ERA5 reanalysis</strong>{' '}
            ground truth and official Mexican municipal coordinates (with CONAGUA SMN supported as a
            resilient secondary adapter).{' '}
            <strong>
              Street-level flood sensors, urban road-closure feeds, and live Doppler radar tiles are
              explicitly out of scope
            </strong>{' '}
            and are never simulated. All alerts reflect verifiable municipal meteorological forecast
            thresholds only.
          </p>
          <p className="text-[12px] font-medium text-slate-600">
            Weather data attribution: Open-Meteo (CC BY 4.0) &amp; Copernicus ERA5-Land Reanalysis.
          </p>
        </div>
      </footer>
    </div>
  );
}
