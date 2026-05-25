# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.6.0] - 2026-05-25

### Added
- **Tariff-aware charging & discharging** — works with any HA electricity price sensor (Octopus Agile, NordPool, Tibber, G12, `input_number`, etc.):
  - **Cheap-rate charging**: when price ≤ threshold and SOC < target → raises `chargeCurrent`; stops when SOC reaches target or price rises
  - **Expensive-rate discharging**: when price ≥ threshold and SOC > min → raises `dischargeCurrent` (sell to grid); stops when SOC hits minimum or price drops
  - Both modes are independent and optional
- **Tariff Manager switch** entity — starts **OFF**, must be enabled manually; disabling immediately restores normal currents
- **Tariff Mode sensor** entity — reports `disabled` / `idle` / `charging` / `discharging` in real time
- **Tariff Price Quality diagnostic sensor** — reports `ok` / `stale` / `unavailable` / `invalid` / `not_found`; icon changes to alert when data is bad
- **Price quality check**: if the price sensor stops updating beyond the configured max age (default 90 min), any active mode is stopped and normal currents are restored as a safety measure
- **Active schedule**: optional start/end hour to limit tariff activity to specific hours of the day (supports midnight wrap, e.g. 22–06)
- **HA persistent notifications** on every mode change (charging on/off, discharging on/off, manager enabled/disabled, quality issues)
- Tariff Manager card added to the auto-generated Overview dashboard; history graph (mode + SOC) added to Charts view
- Issue templates (bug report, feature request) with redirect to Discussions and Wiki
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`
- `info.md` — integration description card for HACS UI

## [1.5.0] - 2026-05-23

### Added
- **Solar Forecast** — six new sensor entities powered by [Open-Meteo](https://open-meteo.com) (free, no API key required):
  - `Solar Forecast Today` — predicted PV yield today in kWh
  - `Solar Forecast Tomorrow` — predicted PV yield tomorrow in kWh
  - `Cloud Cover` — current hour cloud coverage in %
  - `Precipitation` — current hour precipitation in mm
  - `Solar Irradiance GHI` — Global Horizontal Irradiance in W/m²
  - `Solar Irradiance DNI` — Direct Normal Irradiance in W/m²
- Forecast location defaults to the HA home coordinates — only panel kWp is required to enable
- Forecast cards automatically added to the Overview dashboard when forecast is configured
- Full [wiki](https://github.com/MarcinG81/SunSynk_HA_Integration/wiki) with installation, configuration, entity reference, automations, troubleshooting and architecture pages

### Changed
- Auto-generated Lovelace dashboard now includes Solar Forecast tile and weather glance cards when forecast entities are registered
- `build_dashboard()` accepts optional `forecast_eid` callable for forecast entity lookup

## [1.0.0] - 2026-05-11

### Added
- Initial release
- Native Home Assistant integration (Config Flow, no YAML, no add-on)
- Support for Sunsynk (`api.sunsynk.net`) and Deye / Inteless (`pv.inteless.com`) cloud API
- RSA + OAuth2 authentication with automatic token refresh
- **~60 sensor entities** per inverter:
  - PV generation (total, today, MPPT strings — dynamically discovered)
  - Battery (SOC, power, voltage, current, temperature, BMS data, charge/discharge totals)
  - Grid (power, frequency, import/export today and total, per-phase data)
  - Load (total power, daily consumption, UPS data, per-phase data)
  - Inverter output (power, frequency, temperatures)
  - Inverter diagnostics (firmware versions, serial, model, status)
  - Parallel battery pack sensors (slots 1 and 2 — dynamically discovered)
- **~30 writable number entities** — battery thresholds, charge/discharge current, time slot capacity and power, zero export, sell power, generator settings
- **~25 writable switch entities** — solar sell, battery on, grid always on, time slots, active days, generator
- **~6 text entities** — time slot start times
- Multi-inverter support (multiple serial numbers in one config entry)
- Dynamic sensor discovery for MPPT strings, grid/load/output phases and battery slots
- Auto-generated Lovelace dashboard (Power Flow Card bundled — no separate HACS install needed)
- Sunsynk Power Flow Card v7.3.3 served as a bundled frontend resource

[1.6.0]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.0.0...v1.5.0
[1.0.0]: https://github.com/MarcinG81/SunSynk_HA_Integration/releases/tag/v1.0.0
