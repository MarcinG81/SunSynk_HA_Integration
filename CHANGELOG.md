# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.8.2] - 2026-08-27

### Fixed
- **Sequential settings writes within a few seconds of each other could silently revert one another.** `async_write_setting()` builds its "preserve every other field" payload from the coordinator's local cache, which was only refreshed via a trailing `async_request_refresh()` call — but that call is debounced by Home Assistant (10-second cooldown), so only the *first* write in a fast sequence actually got a confirmed cache refresh; every write after it, within that 10s window, built its payload from data that predated the writes in between. Most visibly, this hit the **Virtual Slot Scheduler**, which writes a physical slot's `on`/`cap`/`sellPower`/`start` as four back-to-back calls: `time{n}on` (written first) could get silently reverted to its pre-update value by the writes that followed it in the same burst — the slot could end up left off even though the code explicitly turned it on, with only the last field written in a burst reliably sticking. Reproduced and confirmed with a regression test. Fixed by updating the coordinator's own cache immediately after each successful write, rather than waiting on the debounced refresh — no extra API calls, and every write in a burst now sees the true current state regardless of Home Assistant's debounce timing. If you've had trouble getting Virtual Slot Scheduler slots to stay enabled, please update and try again.

## [1.8.1] - 2026-08-25

### Fixed
- The auto-generated dashboard is now wired up for the entities added in 1.8.0 — Grid Charge Current (Battery Settings card), Internal Power (Inverter Info), Battery SOH (Battery Details), and a new **Plant Pricing** diagnostics card for Current/Manual Energy Price. Only the Sell Time N Enabled switches had made it into the dashboard generator; the rest were live entities with no card to see them on.

## [1.8.0] - 2026-08-21

### Added
- **Sell Time 1-6 Enabled** switches (`sellTime1En`…`sellTime6En`) — already part of the settings write payload, but never had their own entity. (#14)
- **Grid Charge Current** writable Number entity, mapped to `sdBatteryCurrent` — the Sunsynk portal's "Grid Amps" setting (the current limit specifically for charging from the grid, distinct from `chargeCurrent`/Battery Max Charge Current). Identified from a diagnostics dump: a real, populated value distinct from both of those fields, with a settings-group structure mirroring the already-exposed Generator group. (#15)
- **Internal Power** diagnostic sensor — `pv + grid + battery - load`, approximating inverter conversion loss. Verified against a real diagnostics dump (323 W, a plausible figure). (#14)
- **Current Energy Price** sensor and **Manual Energy Price** writable Number entity — plant-level pricing, reached via a different API endpoint (`/api/v1/plant/{plantId}`) than the rest of this integration. Resolves the currently-active pricing slot for Constant/Time-of-Use plants. ⚠️ Writing the manual price replaces the plant's *entire* pricing configuration on the Sunsynk portal with a single Constant Price entry — see the wiki before using it. Unverified against a live account with pricing configured. (#14)
- **Battery SOH** diagnostic sensor — `correctCap / capacity * 100` (BMS-corrected capacity vs rated capacity). ⚠️ Unverified against an actually-degraded battery; the only real data point available had both fields equal (trivial 100%). A different formula based on lifetime charge/discharge totals was tried first and rejected — it produced 139% on real data, since those accumulators can drift out of sync independently of battery health. (#14)

### Changed
- Renamed several entities to match Sunsynk portal terminology — only the friendly name changes, `unique_id`/`entity_id` are untouched so no automations break:
  - `Time Slot N On` → `Grid Charge Slot N`
  - `Generator Time Slot N On` → `Generator Charge Slot N`
  - `Time Slot N Capacity` → `Time Slot N Limit`
  - `Sell Time N Power` → `Slot N Power`
  (#14)
- Power flow card on the auto-generated dashboard's Overview view now renders at 50% width instead of stretching full browser width.

### Fixed
- `sunsynk.set_work_mode` service description corrected: `2 = Limited to Home`, not `Time-of-Use` as previously documented — confirmed against real hardware. Values 3/4 (Self Use / Time of Use) are still unconfirmed. (#14)

## [1.7.1] - 2026-07-31

### Fixed
- **Virtual Slot Scheduler now owns physical time slots 1 and 6, not 1 and 2.** Sunsynk's own documentation ("Avoiding Conflicts in the System Mode Timer") requires the 6 System Mode Timer slots to be strictly chronological by index, and only Timer 6 is allowed to wrap past midnight into Timer 1 — no other pair can. The 1.7.0 scheduler owned slots 1 and 2 and could assign a *later* time-of-day to slot 1 than slot 2, which violates that ordering rule; a real Sunsynk Acure inverter silently ignored the out-of-order slot as a result (reported in discussion #10). Ownership now enforces the invariant that slot 1 always holds the earlier time-of-day boundary and slot 6 the later one (including anything that wraps past midnight), recomputed fresh on every tick instead of tracked as mutable state. If you configured virtual slots on 1.7.0 and slot 1 didn't seem to do anything, update — no changes to your `sunsynk.set_virtual_slot` calls are needed, the scheduler works out physical placement itself.

## [1.7.0] - 2026-07-31

### Added
- **Virtual Slot Scheduler.** Define up to 10 HA-side virtual charge/discharge windows (start/end time, mode, current, target SOC, weekdays, priority) via the new `sunsynk.set_virtual_slot` / `sunsynk.clear_virtual_slot` services. They're resolved onto physical Sunsynk time slots 1 & 2, which the scheduler takes exclusive ownership of (slots 3-6 are disabled and left untouched, so any manual ToU config you already have elsewhere is unaffected). Sunsynk/Deye time slots have no independent end time — a slot runs until whichever slot has the next start time — so the scheduler rolls slots 1 & 2 as a "current / next" pair to give you more granular scheduling than the inverter's native 6 slots support directly. A live Tariff Manager price decision always takes priority over the virtual schedule and is applied instantly, without waiting for a slot boundary. New **Virtual Slot Scheduler** switch (starts OFF, same pattern as the Tariff Manager switch) and diagnostic sensor showing what's active and why. Closes #11 — the Tariff Manager previously only ever changed the global charge/discharge current limit, never the time slots that actually gate whether charging/discharging happens at all. See the [Virtual Slot Scheduler wiki page](https://github.com/MarcinG81/SunSynk_HA_Integration/wiki/Virtual-Slot-Scheduler) for the full field reference.
- New **Virtual Slots** dashboard tab (shown once the scheduler is configured): enable switch, current mode/physical slot/next transition, a live table of configured slots, and deep links into Developer Tools → Actions for the two new services — those forms are already fully rendered thanks to the selectors defined in `services.yaml`, no custom frontend card needed.

### Fixed
- Options flow no longer throws `expected str` when opening or saving **Settings → Devices & Services → Sunsynk → Configure**, even without touching any tariff fields. Cheap/expensive thresholds, charge/discharge currents and the active-schedule hours are stored as numbers once saved, but the options form was redisplaying them through a schema that declared them as plain text — the redisplayed default disagreed with its own validator. Pre-existing since the original Tariff Manager release (1.6.x); the other numeric fields (latitude/longitude/panel size/performance ratio) were already unaffected.
- Manifest-version and frontend-JS-module registration failures at startup are now logged instead of silently swallowed.

## [1.6.18] - 2026-07-07

### Added
- **Self-calibrating solar forecast.** The solar forecast (`today_kwh`/`tomorrow_kwh`) previously scaled Open-Meteo irradiance by a single fixed `performance_ratio` from the config. It now learns a separate ratio for each calendar month by comparing actual daily PV generation (`pv.etoday`, summed across inverters) against what the irradiance model predicted, and blends new observations in with an exponential moving average. This should make the forecast track real-world panel/inverter losses (soiling, temperature, seasonal sun angle) more accurately over time, without any user action. The configured Performance Ratio is now just the seed value used until enough daily samples accumulate for a given month.
- New diagnostic sensor **Performance Ratio (Calibrated)** showing the currently learned ratio for the active month.

## [1.6.17] - 2026-07-07

### Added
- Sunsynk/Deye login credentials (API server, account email, password) can now be updated from **Settings → Devices & Services → Sunsynk → Configure** — no need to delete and re-add the integration after changing your portal password. The password field can be left blank to keep the currently saved one. Changed credentials are re-validated against the API before saving, and switching to an account already configured elsewhere is rejected. (Discussion #8)

## [1.6.16] - 2026-07-02

### Fixed
- Auto-generated dashboards no longer throw a "Configuration error: Please include the attribute and entity ID e.g: pv1_power_186: sensor.example" on the Overview tab. The bundled `sunsynk-power-flow-card` expects specific numbered entity keys (e.g. `pv1_power_186`, `battery_soc_184`, `grid_power_169`) rather than the plain names (`pv1_power`, `battery_soc`, `grid_power`) the dashboard generator was previously producing — those keys were silently ignored by the card, so most of the power flow visualization (and the 1.6.15 solar-block fix) never actually reached the screen. Entity keys are now aligned with the card's real schema, `pv1_power_186` always resolves to a sensor (falling back to total solar power if no per-MPPT sensor exists), and `show_daily` flags are now nested under `solar`/`battery`/`grid`/`load` as the card expects instead of ignored top-level flags.
- Updated `dashboards/sunsynk-dashboard.yaml` with the corrected entity keys for anyone who copy-pasted the manual dashboard example.

## [1.6.15] - 2026-07-02

### Fixed
- Auto-generated dashboards no longer show a "No solar attributes defined" card error — the bundled `sunsynk-power-flow-card` config now includes the required `solar` block, with `mppts` set based on whether a second MPPT sensor is present.
- The Electricity Price Sensor field in the integration options can now be left blank. It was already optional in the schema, but Home Assistant's entity selector couldn't be cleared once defaulted, forcing users without tariff management to pick an unrelated sensor just to save settings.

## [1.6.14] - 2026-07-02

### Fixed
- Auto-generated dashboards now load the bundled `sunsynk-power-flow-card` reliably by registering `/sunsynk/sunsynk-power-flow-card.js` as a Lovelace `module` resource during integration setup.
- The bundled card URL is versioned from the integration manifest to reduce stale frontend cache issues after updates.
- Frontend, HTTP and Lovelace are now declared as integration dependencies so card resource registration runs after the required Home Assistant subsystems are loaded.

### Changed
- Updated the bundled dashboard YAML instructions to clarify that the Power Flow Card is included with the integration and does not need a separate HACS frontend install.

## [1.6.13] - 2026-07-01

### Fixed
- Sensors with a numeric `state_class` (e.g. `PV Grid Tip Power`) now report `unknown` instead of the raw API placeholder `"--"` when the field doesn't apply to a given inverter. Home Assistant rejected the non-numeric string and logged an error on every coordinator refresh, which could appear unrelated to any automation targeting a different entity around the same time. (#6)

## [1.6.12] - 2026-05-26

### Fixed
- **Inverter Model sensor** now constructs a human-readable value from available API fields: tries `model` string, then `equipType` string, then falls back to `{brand} {kW}kW` (e.g. `Deye 8kW`). The Sunsynk/Deye API returns `model` as an empty string and `equipType` as an integer type code — neither is a readable name.
- **Device card** in HA also shows the constructed model name correctly.
- Added `value_fn` support to `SunsynkSensorEntityDescription` for sensors that need computed values rather than a simple field lookup.

## [1.6.11] - 2026-05-26

### Fixed
- **Inverter Model sensor** now reads `equipType` (e.g. `SUN-8K-SG01HP3-EU-AM2`) — the API `model` field is a numeric type code, not a human-readable name.

## [1.6.10] - 2026-05-26

### Fixed
- **Inverter Model sensor** now reads `equipType` (e.g. `SUN-8K-SG01HP3-EU-AM2`) instead of the `model` field which is a numeric type code in the Sunsynk API.
- **Number of Batteries sensor** was always unavailable — primary field changed to `batteryNum` (Sunsynk API naming convention) with `numberOfBatteries` as fallback.
- Added `fallback_data_key` support to `SunsynkSensorEntityDescription` for sensors where the API may use different field names.
- Added DEBUG-level logging of `inverter` and `battery` field names on each fetch to aid future diagnostics.

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

[1.6.14]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.13...v1.6.14
[1.6.13]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.12...v1.6.13
[1.6.12]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.11...v1.6.12
[1.6.11]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.10...v1.6.11
[1.6.10]: https://github.com/MarcinG81/SunSynk_HA_Integration/compare/v1.6.9...v1.6.10
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
