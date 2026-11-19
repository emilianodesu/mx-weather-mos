import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  ArrowRight,
  Cpu,
  Database,
  LineChart,
  Map as MapIcon,
  MapPin,
} from 'lucide-react';
import {
  getAlerts,
  getHealth,
  getMunicipalities,
  getMunicipalityForecast,
} from '../api/client';
import type {
  AlertItem,
  HealthResponse,
  Municipality,
  MunicipalityForecastResponse,
} from '../types/api';
import { AlertCard } from '../components/AlertCard';
import { ForecastComparisonCards } from '../components/ForecastComparisonCards';

export function PortalHomePage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [municipalities, setMunicipalities] = useState<Municipality[]>([]);
  const [selectedMun, setSelectedMun] = useState<Municipality | null>(null);
  const [forecast, setForecast] = useState<MunicipalityForecastResponse | null>(null);

  const [loadingInitial, setLoadingInitial] = useState(true);
  const [loadingForecast, setLoadingForecast] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoadingInitial(true);
    setError(null);

    Promise.allSettled([
      getHealth(controller.signal),
      getAlerts(controller.signal),
      getMunicipalities({ cohort_only: true }, controller.signal),
    ])
      .then(([healthRes, alertsRes, munsRes]) => {
        if (controller.signal.aborted) return;

        if (healthRes.status === 'fulfilled') setHealth(healthRes.value);
        if (alertsRes.status === 'fulfilled') setAlerts(alertsRes.value);

        if (munsRes.status === 'fulfilled') {
          setMunicipalities(munsRes.value);
          if (munsRes.value.length > 0) {
            const preferred =
              munsRes.value.find(
                (m) =>
                  m.municipality_name.toLowerCase().includes('guadalajara') ||
                  m.municipality_name.toLowerCase().includes('cuauht'),
              ) ?? munsRes.value[0];
            setSelectedMun(preferred);
          }
        } else {
          setError('Unable to reach the weather API. Verify that the FastAPI service is running.');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingInitial(false);
      });

    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedMun) return;
    const controller = new AbortController();
    setLoadingForecast(true);

    getMunicipalityForecast(selectedMun.state_id, selectedMun.municipality_id, controller.signal)
      .then((res) => {
        if (!controller.signal.aborted) setForecast(res);
      })
      .catch(() => {
        if (!controller.signal.aborted) setForecast(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingForecast(false);
      });

    return () => controller.abort();
  }, [selectedMun]);

  const selectedKey = selectedMun
    ? `${selectedMun.state_id}-${selectedMun.municipality_id}`
    : '';

  return (
    <div className="space-y-6">
      {error && (
        <div
          role="alert"
          className="rounded-card bg-danger-bg border border-danger-border px-4 py-3 text-[15px] font-medium text-danger-text"
        >
          {error}
        </div>
      )}

      {/* P0 / Advisory Forecast-Threshold Alert Container (Gestalt Common Region) */}
      <AlertCard alerts={alerts} loading={loadingInitial} />

      {/* Municipality Quick Selector & 4-Day Raw vs ML-Calibrated Forecast */}
      <section
        aria-labelledby="forecast-section-heading"
        className="rounded-card bg-surface border border-surface-border p-6 shadow-sm space-y-5"
      >
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
          <div className="space-y-1">
            <h2 id="forecast-section-heading" className="text-headline text-navy">
              4-Day Raw NWP vs. ML-Calibrated Forecast
            </h2>
            <p className="text-guidance text-slate-700">
              Compare unadjusted Open-Meteo NWP forecasts against LightGBM MOS median (
              <code className="font-mono text-[14px]">p50</code>), 80% prediction intervals (
              <code className="font-mono text-[14px]">[p10–p90]</code>), and isotonic-calibrated
              rain probabilities.
            </p>
          </div>

          <div className="w-full md:w-80">
            <label
              htmlFor="municipality-selector"
              className="block text-[13px] font-semibold text-navy mb-1"
            >
              Select Cohort Municipality (32 Capitals)
            </label>
            <div className="relative">
              <MapPin
                className="w-4 h-4 text-navy absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
                aria-hidden="true"
              />
              <select
                id="municipality-selector"
                value={selectedKey}
                onChange={(e) => {
                  const [sId, mId] = e.target.value.split('-').map(Number);
                  const found = municipalities.find(
                    (m) => m.state_id === sId && m.municipality_id === mId,
                  );
                  if (found) setSelectedMun(found);
                }}
                className="w-full min-h-[48px] pl-9 pr-4 rounded-control border-2 border-surface-border bg-surface text-[15px] font-medium text-navy focus:border-navy"
              >
                {municipalities.length === 0 ? (
                  <option value="">Loading municipalities…</option>
                ) : (
                  municipalities.map((m) => (
                    <option
                      key={`${m.state_id}-${m.municipality_id}`}
                      value={`${m.state_id}-${m.municipality_id}`}
                    >
                      {m.municipality_name}, {m.state_name}
                      {m.elevation_m !== null ? ` (${Math.round(m.elevation_m)}m)` : ''}
                    </option>
                  ))
                )}
              </select>
            </div>
          </div>
        </div>

        {selectedMun && (
          <div className="flex flex-wrap items-center justify-between gap-2 pt-1 pb-2 border-b border-surface-border text-timestamp text-slate-600">
            <span>
              <strong>{selectedMun.municipality_name}</strong>, {selectedMun.state_name} • Lat{' '}
              {selectedMun.lat.toFixed(3)}, Lon {selectedMun.lon.toFixed(3)}
              {selectedMun.elevation_m !== null ? ` • Elev ${Math.round(selectedMun.elevation_m)} m` : ''}
            </span>
            {forecast?.model_version && (
              <span className="font-semibold text-navy">
                Model: {forecast.model_version}
              </span>
            )}
          </div>
        )}

        <ForecastComparisonCards
          days={forecast?.days ?? []}
          loading={loadingInitial || loadingForecast}
        />
      </section>

      {/* Secondary Portal Modules (Multiple Entry Points per GoogleUX Prototype) */}
      <section aria-label="Platform modules" className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-card bg-surface border border-surface-border p-6 shadow-sm flex flex-col justify-between gap-4">
          <div className="space-y-2">
            <span className="inline-flex items-center gap-1.5 text-[13px] font-bold text-navy uppercase tracking-wide">
              <MapIcon className="w-4 h-4" aria-hidden="true" />
              Geospatial View
            </span>
            <h3 className="text-metric text-navy">National ML Bias &amp; Forecast Map</h3>
            <p className="text-guidance text-slate-700">
              Visualize PostGIS-backed municipal forecasts across Mexico. Toggle between ML Tmax,
              ML Tmin, Precipitation, and ML Bias Delta (°C) to spot where MOS reduces elevation
              bias most.
            </p>
          </div>
          <Link
            to="/map"
            className="inline-flex items-center justify-center gap-2 px-5 min-h-[48px] rounded-control bg-navy text-white font-semibold text-[15px] hover:bg-navy-hover transition-colors w-fit"
          >
            <span>Open Geospatial Map</span>
            <ArrowRight className="w-4 h-4" aria-hidden="true" />
          </Link>
        </div>

        <div className="rounded-card bg-surface border border-surface-border p-6 shadow-sm flex flex-col justify-between gap-4">
          <div className="space-y-2">
            <span className="inline-flex items-center gap-1.5 text-[13px] font-bold text-navy uppercase tracking-wide">
              <LineChart className="w-4 h-4" aria-hidden="true" />
              Researcher Workspace
            </span>
            <h3 className="text-metric text-navy">Trends, ML Skill &amp; Verified Export</h3>
            <p className="text-guidance text-slate-700">
              Inspect paired ERA5 Observed vs. Raw NWP vs. ML MOS time series, walk-forward
              baseline skill tables, rain reliability curves, and export SHA-256 verified CSV or
              Parquet datasets.
            </p>
          </div>
          <Link
            to={
              selectedMun
                ? `/explorer?state_id=${selectedMun.state_id}&mun_id=${selectedMun.municipality_id}`
                : '/explorer'
            }
            className="inline-flex items-center justify-center gap-2 px-5 min-h-[48px] rounded-control border-2 border-navy bg-surface text-navy font-semibold text-[15px] hover:bg-navy-light transition-colors w-fit"
          >
            <span>Open Trends &amp; ML Explorer</span>
            <ArrowRight className="w-4 h-4" aria-hidden="true" />
          </Link>
        </div>
      </section>

      {/* System Status Strip from GET /health */}
      <section
        aria-label="System health and ingestion status"
        className="rounded-card bg-surface border border-surface-border p-5 shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center justify-center w-10 h-10 rounded-control bg-safe-bg text-safe-text">
              <Activity className="w-5 h-5" aria-hidden="true" />
            </span>
            <div>
              <p className="text-[13px] font-semibold text-slate-600">API &amp; Database Status</p>
              <p className="text-[15px] font-bold text-navy">
                {health ? `${health.status.toUpperCase()} • PostGIS ${health.postgis}` : 'Checking…'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="inline-flex items-center justify-center w-10 h-10 rounded-control bg-navy-light text-navy">
              <Cpu className="w-5 h-5" aria-hidden="true" />
            </span>
            <div>
              <p className="text-[13px] font-semibold text-slate-600">Active ML Model</p>
              <p className="text-[15px] font-bold text-navy">
                {health?.active_model_version ?? 'Not yet trained'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="inline-flex items-center justify-center w-10 h-10 rounded-control bg-navy-light text-navy">
              <Database className="w-5 h-5" aria-hidden="true" />
            </span>
            <div>
              <p className="text-[13px] font-semibold text-slate-600">Latest Ingestion Run</p>
              <p className="text-[15px] font-bold text-navy">
                {health?.latest_ingestion_run?.status
                  ? `${health.latest_ingestion_run.source_provider ?? 'pipeline'} (${health.latest_ingestion_run.status})`
                  : 'No runs logged'}
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
