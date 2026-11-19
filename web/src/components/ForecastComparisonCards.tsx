import { CloudRain, Thermometer, Wind } from 'lucide-react';
import type { ForecastDay } from '../types/api';

interface ForecastComparisonCardsProps {
  days: ForecastDay[];
  loading?: boolean;
}

function getAlertBadge(level: ForecastDay['alert_level']) {
  if (level === 'WARNING_P0') {
    return (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-pill text-[12px] font-bold bg-danger-bg text-danger-text">
        WARNING P0
      </span>
    );
  }
  if (level === 'ADVISORY') {
    return (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-pill text-[12px] font-bold bg-amber-100 text-amberAlert-text">
        ADVISORY
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-pill text-[12px] font-bold bg-safe-bg text-safe-text">
      NORMAL
    </span>
  );
}

export function ForecastComparisonCards({ days, loading = false }: ForecastComparisonCardsProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((idx) => (
          <div
            key={idx}
            className="rounded-card bg-surface border border-surface-border p-5 animate-pulse space-y-3"
          >
            <div className="h-4 w-24 bg-slate-200 rounded" />
            <div className="h-6 w-32 bg-slate-200 rounded" />
            <div className="h-16 w-full bg-slate-100 rounded" />
          </div>
        ))}
      </div>
    );
  }

  if (days.length === 0) {
    return (
      <div className="rounded-card bg-surface border border-surface-border p-6 text-guidance text-slate-700">
        No 4-day forecast records available for this municipality yet. Run the ingestion and ML
        scoring pipeline to populate forecasts.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {days.slice(0, 4).map((day) => {
        const mlTmax = day.pred_tmax_p50 ?? day.raw_tmax_c;
        const mlTmin = day.pred_tmin_p50 ?? day.raw_tmin_c;
        const biasDelta = mlTmax - day.raw_tmax_c;
        const isHighlighted = day.alert_level !== 'NORMAL';

        return (
          <article
            key={day.valid_date}
            className={`rounded-card p-5 border transition-shadow ${
              isHighlighted
                ? 'bg-amberAlert-surface border-2 border-amberAlert-border shadow-sm'
                : 'bg-surface border-surface-border shadow-sm'
            }`}
          >
            <div className="flex items-center justify-between gap-2 mb-3">
              <div>
                <p className="text-[15px] font-bold text-navy">{day.valid_date}</p>
                <p className="text-timestamp text-slate-600">Lead Day +{day.lead_day}</p>
              </div>
              {getAlertBadge(day.alert_level)}
            </div>

            {/* ML-Calibrated vs Raw Tmax */}
            <div className="space-y-3">
              <div className="rounded-control bg-surface border border-surface-border p-3">
                <div className="flex items-center justify-between text-timestamp text-slate-600 mb-1">
                  <span className="inline-flex items-center gap-1 font-semibold text-navy">
                    <Thermometer className="w-3.5 h-3.5" aria-hidden="true" />
                    Tmax (ML MOS)
                  </span>
                  <span>
                    Bias Δ: {biasDelta >= 0 ? `+${biasDelta.toFixed(1)}` : biasDelta.toFixed(1)} °C
                  </span>
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-metric text-navy">{mlTmax.toFixed(1)} °C</span>
                  <span className="text-timestamp text-slate-600">
                    Raw NWP: {day.raw_tmax_c.toFixed(1)} °C
                  </span>
                </div>
                {day.pred_tmax_p10 !== null && day.pred_tmax_p90 !== null && (
                  <p className="text-timestamp text-slate-600 mt-1">
                    80% Interval: [{day.pred_tmax_p10.toFixed(1)}° – {day.pred_tmax_p90.toFixed(1)}°]
                  </p>
                )}
              </div>

              {/* ML-Calibrated vs Raw Tmin */}
              <div className="rounded-control bg-surface border border-surface-border p-3">
                <div className="flex items-center justify-between text-timestamp text-slate-600 mb-1">
                  <span className="font-semibold text-navy">Tmin (ML MOS)</span>
                  <span>Raw NWP: {day.raw_tmin_c.toFixed(1)} °C</span>
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-metric text-navy">{mlTmin.toFixed(1)} °C</span>
                  {day.pred_tmin_p10 !== null && day.pred_tmin_p90 !== null && (
                    <span className="text-timestamp text-slate-600">
                      [{day.pred_tmin_p10.toFixed(1)}° – {day.pred_tmin_p90.toFixed(1)}°]
                    </span>
                  )}
                </div>
              </div>

              {/* Calibrated Rain Probability & Wind */}
              <div className="flex items-center justify-between pt-1 text-[13px] text-slate-700">
                <span className="inline-flex items-center gap-1.5 font-medium">
                  <CloudRain className="w-4 h-4 text-navy" aria-hidden="true" />
                  {day.calibrated_rain_prob !== null
                    ? `${Math.round(day.calibrated_rain_prob * 100)}% cal.`
                    : day.raw_precip_prob_pct !== null
                      ? `${day.raw_precip_prob_pct}% raw`
                      : '—'}
                  {day.precip_mm !== null ? ` (${day.precip_mm.toFixed(1)} mm)` : ''}
                </span>
                {day.wind_gust_kmh !== null && day.wind_gust_kmh !== undefined && (
                  <span className="inline-flex items-center gap-1 text-timestamp text-slate-600">
                    <Wind className="w-3.5 h-3.5" aria-hidden="true" />
                    {day.wind_gust_kmh.toFixed(0)} km/h
                  </span>
                )}
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}
