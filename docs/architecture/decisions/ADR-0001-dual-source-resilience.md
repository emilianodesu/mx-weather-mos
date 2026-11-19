# ADR-0001: Dual-Source Resilient Ingestion (Open-Meteo Primary + CONAGUA SMN Circuit Breaker)

- **Status:** Accepted (2026-11-18)
- **Context:** Live empirical checks on 2026-11-18 showed `https://smn.cna.gob.mx/webservices/?method=1` timed out (>15s) and `https://smn.conagua.gob.mx/webservices/?method=1` redirected (`301`) to `HTTP 500` HTML. Furthermore, CONAGUA's web service does not provide historical forecast archives for ML training.
- **Decision:** Use **Open-Meteo** (ERA5-Land Archive, Historical Forecast, and Live Forecast APIs — CC BY 4.0, zero API key, `<20 ms` response time) as the primary reliable data source, keyed by the official 2,463-municipality Mexican catalog. Retain `conagua_smn` as a secondary adapter with graceful `DEGRADED_UPSTREAM` circuit-breaker logging and offline golden-fixture verification (`fixtures/smn_golden_sample.json`).
- **Consequences:** 100% reproducible local runs and 24-month ML backfills without depending on fragile upstream government uptime, while explicitly demonstrating real-world fault tolerance.
