# ADR-0003: LightGBM MOS Quantile Regressors & Isotonic Rain Calibration

- **Status:** Accepted (2026-11-18)
- **Context:** Numerical Weather Prediction (NWP) grids exhibit local elevation and microclimate bias in temperature (`tmax`/`tmin`) and miscalibrated precipitation probabilities across Mexico's varied topography (5m to 2,660m).
- **Decision:** Implement Model Output Statistics (MOS) using LightGBM `regression_l1` (`p50`) + `quantile` (`alpha=0.10`, `0.90`) regressors with post-inference monotonicity enforcement (`p10 <= p50 <= p90`), plus a LightGBM binary rain classifier (`obs_precip_mm >= 1.0`) calibrated via `IsotonicRegression` on a strictly held-out trailing temporal slice. Evaluate with 4-fold expanding temporal walk-forward CV against `raw_nwp`, `persistence`, and `climatology_30d`.
- **Consequences:** Fast CPU training (<30s locally), calibrated uncertainty intervals, transparent baseline benchmarking, and zero future target leakage (enforced by automated future-perturbation tests).
