# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.6.9] - 2026-05-26

### Added
- **Translations** — UI strings (config flow, options, error messages, repair issues) are now translated into 8 languages: Polish (`pl`), German (`de`), French (`fr`), Afrikaans (`af`), Russian (`ru`), Spanish (`es`), Czech (`cs`), Chinese Simplified (`zh-Hans`).

### Fixed
- GitHub release workflow: added `draft: false` to ensure releases are published immediately rather than saved as drafts.

## [1.6.8] - 2026-05-25

### Changed
- Tariff dashboard tab is now always visible — when no price entity is configured, shows a markdown card with a direct link to the integration settings to set one up.

## [1.6.7] - 2026-05-25

### Fixed
- Dashboard not appearing after delete + reload — `async_create_item` was creating its own empty `LovelaceStorage` internally, overwriting the content saved beforehand. Fixed by registering the dashboard first, then saving content into the registered object.

## [1.6.6] - 2026-05-25

### Added
- Dedicated **Tariff** dashboard view (tab 4) with three cards: Tariff Manager status & enable switch, Cheap-rate Charging config, Peak-rate Discharging config, and 24h history graph (mode + SOC). Tariff config removed from Settings view and Charts view.

### Fixed
- Diagnostics download returning HTTP 500 — `last_update_success_time` accessed via safe `getattr`; coordinator data now recursively converted to JSON-safe types before serialisation.
- Auto-created GitHub release workflow — tags pushed as `vX.Y.Z-beta.N` or `vX.Y.Z-rc.N` create pre-releases; plain `vX.Y.Z` tags create stable releases. Release body extracted automatically from CHANGELOG.

## [1.6.5] - 2026-05-25

### Fixed
- Tariff Manager config number entities (cheap threshold, charge/discharge currents, target SOC, min SOC) were never registered because the tariff manager was created **after** `async_forward_entry_setups`. Moved tariff manager creation before platform setup so `number.py` finds it in `hass.data` when entities are registered.

## [1.6.4] - 2026-05-25

### Fixed
- Dashboard not appearing after delete + reload — `LovelaceStorage` constructor requires an `"id"` field which was missing, causing a silent `KeyError` and preventing dashboard creation.
- `SolarForecastSensor` for Today/Tomorrow raised a HA warning about incompatible `state_class=measurement` with `device_class=energy`. Fixed by setting `state_class=None` on forecast energy sensors (they are point-in-time predictions, not accumulating counters).

## [1.6.3] - 2026-05-25

### Added
- **HA Diagnostics** — download a full diagnostic snapshot (inverter data, coordinator state, config, forecast, tariff) via **Settings → Devices & Services → Sunsynk → Download diagnostics**. Sensitive fields (password, serial numbers) are automatically redacted.
- **Repair Flows** — the integration now raises issues in the HA Repair dashboard when cloud authentication fails or an inverter goes offline. Issues clear automatically when the problem is resolved.
- **HA Services / Actions** — three new services callable from automations and scripts:
  - `sunsynk.force_charge` — immediately set battery charge current
  - `sunsynk.force_discharge` — immediately set battery discharge current
  - `sunsynk.set_work_mode` — switch inverter work mode on demand
- **Tariff Manager config entities** — all Tariff Manager thresholds and currents are now exposed as **Number entities** (entity category: Config). Adjust cheap threshold, charge currents, target SOC, expensive threshold, discharge currents and minimum SOC directly from the HA UI without restarting the integration. Changes take effect immediately and trigger a re-evaluation.
- Tariff Manager Configuration card added to the auto-generated **Settings** dashboard view.

### Added (CI/Dev)
- pytest test suite covering mode property, price quality, schedule logic (including midnight wrap), charging/discharging evaluation, `set_enabled`, and no-op cases when thresholds are `None`.
- `tests.yaml` GitHub Actions workflow — runs pytest on every push and pull request.
- `Tests` and `GitHub release` badges added to README.

## [1.6.2] - 2026-05-25

### Fixed
- `NameError: name 'callback' is not defined` crash in `switch.py` — `callback` was used as a decorator in `TariffManagerSwitch` but never imported from `homeassistant.core`.

## [1.6.1] - 2026-05-25

### Fixed
- Resolved `Error setting up entry` crash on startup caused by `switch` and `sensor` platforms importing `tariff.py` at module level — HA detects this as a blocking call inside the event loop. Fixed by moving to lazy imports inside `async_setup_entry`.

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

[1.6.9]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.8...v1.6.9
[1.6.8]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.7...v1.6.8
[1.6.7]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.6...v1.6.7
[1.6.6]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.5...v1.6.6
[1.6.5]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.4...v1.6.5
[1.6.4]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.3...v1.6.4
[1.6.3]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.2...v1.6.3
[1.6.2]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.1...v1.6.2
[1.6.1]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.0.0...v1.5.0
[1.0.0]: https://github.com/MarcinG81/SunSynk_HA_Integration/releases/tag/v1.0.0
