import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowRight, CheckCircle2, LineChart } from 'lucide-react';
import type { AlertItem } from '../types/api';
import { AudioSummaryButton } from './AudioSummaryButton';

interface AlertCardProps {
  alerts: AlertItem[];
  loading?: boolean;
}

export function AlertCard({ alerts, loading = false }: AlertCardProps) {
  if (loading) {
    return (
      <section
        aria-label="Active forecast threshold alerts"
        className="rounded-card bg-amberAlert-surface border-2 border-amberAlert-border p-6 shadow-sm animate-pulse"
      >
        <div className="h-6 w-64 bg-amber-200 rounded mb-3" />
        <div className="h-4 w-full max-w-xl bg-amber-100 rounded mb-2" />
        <div className="h-4 w-3/4 bg-amber-100 rounded" />
      </section>
    );
  }

  const primaryAlert =
    alerts.find((a) => a.alert_level === 'WARNING_P0') ??
    alerts.find((a) => a.alert_level === 'ADVISORY') ??
    null;

  if (!primaryAlert) {
    const safeSummary =
      'All monitored Mexican municipalities are currently within normal forecast thresholds. No P0 warnings or advisories are active.';

    return (
      <section
        aria-label="Active forecast threshold status"
        className="rounded-card bg-surface border-2 border-safe-border p-6 shadow-sm"
      >
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-pill bg-safe-bg text-safe-text text-[12px] font-bold uppercase tracking-wide">
                <CheckCircle2 className="w-4 h-4" aria-hidden="true" />
                Normal Threshold Status
              </span>
              <span className="text-[12px] font-medium text-slate-600">
                Forecast-Threshold Monitoring Active
              </span>
            </div>
            <h1 className="text-headline text-navy">
              No Severe P0 Forecast-Threshold Alerts Active
            </h1>
            <p className="text-guidance text-slate-700 max-w-3xl">
              Monitored cohort municipalities are currently below severe heat, freeze, heavy
              precipitation, and high wind-gust thresholds. Explore live ML-calibrated forecasts
              below.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3 shrink-0">
            <AudioSummaryButton summaryText={safeSummary} />
            <Link
              to="/map"
              className="inline-flex items-center justify-center gap-2 px-5 min-h-[48px] min-w-[48px] rounded-control bg-navy text-white font-semibold text-[15px] hover:bg-navy-hover transition-colors"
            >
              <span>Open Forecast Map</span>
              <ArrowRight className="w-4 h-4" aria-hidden="true" />
            </Link>
          </div>
        </div>
      </section>
    );
  }

  const isP0 = primaryAlert.alert_level === 'WARNING_P0';
  const additionalCount = Math.max(0, alerts.length - 1);

  const audioText = `${isP0 ? 'Priority zero weather warning' : 'Weather advisory'} for ${
    primaryAlert.municipality_name
  }, ${primaryAlert.state_name}, valid ${primaryAlert.valid_date}. ${primaryAlert.reasons.join(
    '. ',
  )}. ${
    primaryAlert.tmax_c !== null ? `Forecast maximum temperature ${primaryAlert.tmax_c} degrees Celsius. ` : ''
  }${
    primaryAlert.precip_mm !== null ? `Expected precipitation ${primaryAlert.precip_mm} millimeters. ` : ''
  }${
    additionalCount > 0
      ? `There are ${additionalCount} additional municipal threshold alerts active.`
      : ''
  }`;

  return (
    <section
      aria-label="Active forecast threshold alert"
      data-testid="p0-alert-card"
      className="rounded-card bg-amberAlert-surface border-2 border-amberAlert-border p-6 shadow-md"
    >
      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-pill text-[12px] font-bold uppercase tracking-wide ${
                isP0
                  ? 'bg-danger-bg text-danger-text border border-danger-border'
                  : 'bg-amber-200 text-amberAlert-text'
              }`}
            >
              <AlertTriangle className="w-4 h-4" aria-hidden="true" />
              {isP0 ? 'WARNING P0 — Forecast Threshold' : 'ADVISORY — Forecast Threshold'}
            </span>
            <span className="inline-flex items-center px-3 py-1 rounded-pill bg-surface border border-surface-border text-[12px] font-semibold text-navy">
              {primaryAlert.municipality_name}, {primaryAlert.state_name}
            </span>
            {additionalCount > 0 && (
              <span className="text-[12px] font-semibold text-amberAlert-text">
                +{additionalCount} more active municipal alert{additionalCount > 1 ? 's' : ''}
              </span>
            )}
          </div>
          <span className="text-timestamp text-slate-700">
            Valid: {primaryAlert.valid_date}
            {primaryAlert.issued_at ? ` • Issued: ${primaryAlert.issued_at.slice(0, 16).replace('T', ' ')} UTC` : ''}
          </span>
        </div>

        <div className="space-y-2">
          <h1 className="text-headline text-navy">
            {isP0 ? 'Severe Weather Forecast Threshold Exceeded' : 'Elevated Weather Threshold Advisory'}:{' '}
            {primaryAlert.municipality_name}
          </h1>
          <p className="text-guidance text-slate-800 max-w-4xl">
            Triggered by verifiable NWP &amp; ML-calibrated forecast thresholds:{' '}
            <strong>{primaryAlert.reasons.join(' • ')}</strong>. Review municipal details on the
            interactive map or inspect historical verification trends in the Explorer.
          </p>
        </div>

        {/* Quantitative Metrics Strip (Proximity Principle) */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-surface rounded-control border border-surface-border p-3">
            <span className="block text-timestamp text-slate-600">Forecast Tmax</span>
            <span className="block text-metric text-navy">
              {primaryAlert.tmax_c !== null ? `${primaryAlert.tmax_c.toFixed(1)} °C` : '—'}
            </span>
          </div>
          <div className="bg-surface rounded-control border border-surface-border p-3">
            <span className="block text-timestamp text-slate-600">Precipitation</span>
            <span className="block text-metric text-navy">
              {primaryAlert.precip_mm !== null ? `${primaryAlert.precip_mm.toFixed(1)} mm` : '—'}
            </span>
          </div>
          <div className="bg-surface rounded-control border border-surface-border p-3">
            <span className="block text-timestamp text-slate-600">Calibrated Rain Prob.</span>
            <span className="block text-metric text-navy">
              {primaryAlert.calibrated_rain_prob !== null
                ? `${Math.round(primaryAlert.calibrated_rain_prob * 100)}%`
                : '—'}
            </span>
          </div>
          <div className="bg-surface rounded-control border border-surface-border p-3">
            <span className="block text-timestamp text-slate-600">Wind Gusts</span>
            <span className="block text-metric text-navy">
              {primaryAlert.wind_gust_kmh !== null
                ? `${primaryAlert.wind_gust_kmh.toFixed(0)} km/h`
                : '—'}
            </span>
          </div>
        </div>

        {/* Actions (WCAG 2.1 AAA 48x48px targets) */}
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Link
            to="/map"
            className="inline-flex items-center justify-center gap-2 px-5 min-h-[48px] min-w-[48px] rounded-control bg-navy text-white font-semibold text-[15px] hover:bg-navy-hover transition-colors shadow-sm"
          >
            <span>Inspect on Forecast Map</span>
            <ArrowRight className="w-4 h-4" aria-hidden="true" />
          </Link>
          <Link
            to={`/explorer?state_id=${primaryAlert.state_id}&mun_id=${primaryAlert.municipality_id}`}
            className="inline-flex items-center justify-center gap-2 px-5 min-h-[48px] min-w-[48px] rounded-control bg-surface border-2 border-navy text-navy font-semibold text-[15px] hover:bg-navy-light transition-colors"
          >
            <LineChart className="w-4 h-4" aria-hidden="true" />
            <span>Analyze Trends &amp; Export</span>
          </Link>
          <AudioSummaryButton summaryText={audioText} />
        </div>
      </div>
    </section>
  );
}
