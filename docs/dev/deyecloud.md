# Deye Cloud support (2.1.0) — design notes

Goal: Deye inverters that report to **Deye Cloud** (SOLARMAN / LSW loggers), which Sunsynk Connect and Inteless refuse (discussion #37), work with this integration, including Tariff Manager, the Virtual Slot Scheduler and the safety limits.

Sources, in order of authority:

1. Deye's **Swagger definition**, `https://eu1-developer.deyecloud.com/v2/api-docs` — field names and types. (Its examples contain a few invalid JSON literals such as `[12583SS]`; quote them before parsing.)
2. Deye's [sample code](https://github.com/DeyeCloudDevelopers/deye-openapi-client-sample-code) and the [developer portal](https://developer.deyecloud.com/).
3. Facts other projects report from live inverters — used as facts only, no code copied: [Predbat's DEYE component](https://springfall2008.github.io/batpred/inverter-setup/) (personal-use licence, so nothing from it may be copied) and [maxbanton/hass-deye-cloud](https://github.com/maxbanton/hass-deye-cloud) (MIT).

Nothing here has been tested against a live account by us yet.

## What the API gives us

| Need | DeyeCloud | Notes |
|---|---|---|
| Login | `POST /account/token?appId=` with appSecret, email or username, SHA-256 of the password, optional companyId | Developer app per user. Store only the password hash. |
| Inverters | `POST /station/listWithDevice` (deviceType INVERTER) | Paged. |
| Live data | `POST /device/latest`, ≤10 devices per call | Measure points as key/value pairs; **key names not known yet** |
| Settings read | `POST /config/system`, `/config/battery`, `/config/tou` | TOU times come back as "HHMM" |
| Work mode | `/order/sys/workMode/update` | SELLING_FIRST / ZERO_EXPORT_TO_LOAD / ZERO_EXPORT_TO_CT = our 0 / 1 / 2 |
| Energy pattern | `/order/sys/energyPattern/update` | BATTERY_FIRST / LOAD_FIRST |
| Solar sell | `/order/sys/solarSell/control` | on / off |
| Battery currents | `/order/battery/parameter/update` | MAX_CHARGE_CURRENT / MAX_DISCHARGE_CURRENT (field is spelled `paramterType`) |
| Max sell power | `/order/sys/power/update` | MAX_SELL_POWER |
| Timer (6 slots) | `/order/sys/tou/update`, `/order/sys/tou/switch` | Whole table per write: time, power, soc, enableGridCharge, **enableSell**, enableGeneration. The switch takes `days` (all seven) when turning on |
| Limit control | `/order/sys/limitControl` | SELL_FIRST / ZERO_EXPORT_TO_UPS_LOAD / ZERO_EXPORT_TO_CT / ZERO_EXPORT_TO_WIRELESS_CT |
| Result of a write | `GET /order/{orderId}` | 0 to send, 100 sending, 300 upgrading, 400 terminated, 500 failed, 666 succeeded; `error` gives the reason (501 Conflicting Command, …) |

**Read field names (Swagger):**
- `/config/system`: `systemWorkMode`, `energyPattern`, `maxSellPower`, `maxSolarPower`, `zeroExportPower`.
- `/config/battery`: `maxChargeCurrent`, `maxDischargeCurrent`, `battLowCapacity`, `battShutDownCapacity`, `battCapacity` (**Ah**, not kWh).
- `/config/tou`: `touAction`, `timeUseSettingItems` (time "HHMM", power, soc, voltage, enableGridCharge, enableGeneration — the read has no enableSell).
- Responses are flat: fields sit next to `code` / `success` / `msg`, not under `data`.

**Measure points reported live by other projects** (`/device/latest`, a three-phase hybrid; to confirm on single-phase): `SOC`, `BatteryPower` (negative while charging), `TotalGridPower` (positive while importing), `TotalSolarPower`, `TotalConsumptionPower`, `Temperature- Battery` (with the space and hyphen), `BatteryVoltage`, `BatteryRatedCapacity` (Ah), `BMSChargeVoltage`, `RatedPower` (W), and the daily counters `DailyEnergyPurchased`, `DailyGridFeedIn`, `DailyActiveProduction` (PV only), `DailyConsumption`, `DailyChargingEnergy`, `DailyDischargingEnergy`. The lifetime `Total*` counters reportedly drift; the daily ones balance.

**Other reported behaviour:** an expired token can come back as HTTP 200 with `msg` "auth invalid token"; code `2104004` "command concurrent running" means another order is still running — retry, don't fail; `/config/battery` answers code `2106001` "config point not supported" on some models.

Done: `custom_components/sunsynk/deyecloud/client.py` (all of the above, with tests) and `scripts/deyecloud_probe.py` (read-only, redacted dump for testers).

## Real data (poligor84, #37, 2026-10-19)

Personal account (no company ID), developer app with Station and Device Monitoring only. Three inverters:

| | 1 | 2 | 3 |
|---|---|---|---|
| RatedPower | 8000 W | 8000 W | 15000 W |
| Phases | single (`…L1L2` keys) | single | three (`…L1/L2/L3` keys) |
| Measure points | 55 | 58 | 96 |
| `/config/system`, `/config/battery`, `/config/tou` | all `2106001` "config point not supported" | same | all succeed |

- **Login and reading work with monitoring-only permissions.** The earlier "auth appId not found" went away with the updated probe (which strips pasted spaces); the exact cause isn't known.
- **Settings can be read for some inverters on the same login and not others**: the 15 kW three-phase works, both 8 kW single-phase units give `2106001`. Not known yet whether that's the model or the tester's access (some stations on the account are shared with him by their owners); asked in #37. Either way the backend must treat `2106001` as "this inverter can't be configured through Deye Cloud" (read-only entities, no Tariff Manager/VSS), not as an error.
- **`TotalSolarPower` is missing on single-phase.** Use `TotalSolarPower` when present, otherwise the sum of `DCPowerPV1..n` (`TotalDCInputPower` was 0.00 at night on both, so may also work).
- **Signs** match the reports: `BatteryPower` −36 W with `BatteryCurrent` −0.63 A at 99% (charging is negative); `TotalGridPower` +666 W with `DailyGridFeedIn` 0 (importing is positive).
- **Single-phase keys** differ from three-phase: `InverterOutputPowerL1L2`, `GridVoltageL1L2`, `GridCurrentL1L2`, `ExternalCTPowerL1L2`, `LoadVoltageL1L2`; three-phase has per-phase `GridPowerL1..3`, `LoadPowerL1..3`, `TotalInverterOutputPower`, `TotalExternalCTPower`.
- **Battery current** is `BatteryCurrent` on single-phase; three-phase has `BatteryCurrent1`, `BatteryCurrent2` and `BatteryTotalCurrent`.
- **Lifetime counters** differ by model: single-phase has `CumulativeGridFeedIn`, `CumulativeEnergyPurchased`, `CumulativeConsumption`; three-phase has `TotalEnergySell`, `TotalEnergyBuy`, `TotalConsumption`. Both have the `Daily*` counters, so use those.
- **Config values** (15 kW): `systemWorkMode` "ZERO_EXPORT_TO_LOAD", `energyPattern` "BATTERY_FIRST", `maxSellPower` 30000 (above the rated 15000, so don't treat it as a hardware limit), `maxSolarPower` 12000, `zeroExportPower` 20; battery `battCapacity` 300 (Ah), `battLowCapacity` 25, `battShutDownCapacity` 20, `maxChargeCurrent` 80, `maxDischargeCurrent` 170.
- **TOU read** confirmed: `touAction` "on", six `timeUseSettingItems` with `time` "HHMM", `power`, `soc`, `voltage`, `enableGridCharge`, `enableGeneration`, and no `enableSell`.
- Firmware strings come as `MAIN` / `HMI` / `ProtocolVersion`; `LithiumBatteryVersionNumber` is "0000" without a BMS link.

### Second test, 17 inverters (poligor84, 2026-10-20)

Diagnostics from the first Home Assistant build: 12 single-phase (6, 8 kW) and 5 three-phase (12–30 kW) inverters.

- **Settings by model:** all 12 single-phase answer `2106001` for every config group; all 5 three-phase return system and time-of-use settings, 4 of them battery settings too. The tester owns the single-phase ones and can change their settings in the Deye Cloud app, so it isn't access: the OpenAPI doesn't offer settings for those models (or that firmware). To ask Deye.
- Names and signs hold across all 17: battery positive while discharging, grid positive while importing; PV + grid + battery − load leaves 50–90 W (inverter losses).
- One inverter reports a battery temperature of `-100`: no sensor. Temperatures below -50 are dropped.
- Unused points worth having, now mapped: `TotalExternalCTPower` / `ExternalCTPowerL1L2` (meter, Sunsynk `limiterTotalPower`), `TotalGridReactivePower` (`qac`), `TotalGenPower` / `GeneratorActivePower` (generator; the port reports noise with nothing connected, so the sensor appears once it delivers power). Not mapped: per-phase reactive power, generator counters and run time, `ProtocolVersion`, three-phase `BatteryCurrent1/2`.
- 17 of the 18 inverters on the account were listed; which one is missing, and why, is still to find out.
- The logger uploads about every 5 minutes, so the cloud can't give second-by-second data. Local access (Solarman V5 on the logger, TCP 8899) would; out of scope for 2.1.

## Differences that shape the design

1. **Per-slot Sell exists, but can't be read back.** The update takes `enableSell` per slot (Swagger; the samples never use it), so VSS can work as on Sunsynk. `/config/tou` doesn't report it, so the user's own sell ticks can't be captured for restore: either assume off, or read them through another route. To decide with a tester.
2. **Writes are slow and asynchronous.** Each order is confirmed through `/order/{id}`. One TOU order replaces the whole table, which suits VSS (it already writes all six slots).
3. **Currents are the inverter's maximums.** There is no separate "charge current" next to "max charge current", so Tariff Manager would change MAX_CHARGE/DISCHARGE_CURRENT and must put the user's own values back.
4. **Quotas.** Deye has announced rate limits. Poll no more often than the logger uploads (about every 5 minutes), and keep writes to real changes.
5. **Control permission.** Some reports say control needs a business-member token (companyId) or a "Commission Control" permission on the app. To be confirmed with a tester.

## Plan

1. ✅ API client + tests, aligned with the Swagger definition; probe script for testers.
2. ✅ **Real data** from poligor84 (#37), above; kept as `tests/fixtures/deyecloud_37.json` without serials.
3. ✅ **Reading:** `deyecloud/mapping.py` turns measure points and config groups into the Sunsynk-shaped sections; `deyecloud/coordinator.py` (`DeyeCloudCoordinator`, a `SunsynkCoordinator` subclass so entities, Tariff Manager and VSS see the same interface) reads live data every refresh and settings every 30 minutes, skips config groups that answer `2106001`, and refuses every write for now.
3a. **Writes** (after a control test with a tester): `DeyeCloudCoordinator.async_write_settings` takes the same Sunsynk-style setting keys as today and maps them to orders: `sysWorkMode` → work mode, `energyMode` → energy pattern, `solarSell` → solar sell, `batteryMaxCurrentCharge/Discharge` and `chargeCurrent/dischargeCurrent` → battery parameters, `solarMaxSellPower` → max sell power, `sellTime{n}` / `cap{n}` / `sellTime{n}Pac` / `time{n}on` → one TOU order.
4. **Config flow:** choose the cloud (Sunsynk Connect / Inteless / Deye Cloud); for Deye Cloud: region, App ID, App Secret, login, password (hashed before storing), optional company ID; pick inverters from the account.
5. **VSS on Deye Cloud:** selling via work mode (difference 1), all six slots per write, user's timer and work mode captured and put back.
6. **Beta for testers** before 2.1.0.

## Open questions for a tester

- Whether a personal account can send control orders, or a company ID is needed.
- How long orders take on a real logger, and whether the inverter's own timer screen reflects TOU writes.
