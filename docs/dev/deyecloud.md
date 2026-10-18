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

## Differences that shape the design

1. **Per-slot Sell exists, but can't be read back.** The update takes `enableSell` per slot (Swagger; the samples never use it), so VSS can work as on Sunsynk. `/config/tou` doesn't report it, so the user's own sell ticks can't be captured for restore: either assume off, or read them through another route. To decide with a tester.
2. **Writes are slow and asynchronous.** Each order is confirmed through `/order/{id}`. One TOU order replaces the whole table, which suits VSS (it already writes all six slots).
3. **Currents are the inverter's maximums.** There is no separate "charge current" next to "max charge current", so Tariff Manager would change MAX_CHARGE/DISCHARGE_CURRENT and must put the user's own values back.
4. **Quotas.** Deye has announced rate limits. Poll no more often than the logger uploads (about every 5 minutes), and keep writes to real changes.
5. **Control permission.** Some reports say control needs a business-member token (companyId) or a "Commission Control" permission on the app. To be confirmed with a tester.

## Plan

1. ✅ API client + tests, aligned with the Swagger definition; probe script for testers.
2. **Collect real data** from a tester (poligor84, #37): run `python -m scripts.deyecloud_probe`, share the redacted output. Needed for measure-point keys, config field names, units.
3. **Backend interface:** the coordinator reads through a backend (`SunsynkBackend` = today's code, `DeyeCloudBackend` = new) that returns the same data shape (inverter / pv / grid / battery / load / settings), and writes Sunsynk-style setting keys, which the Deye backend maps to orders: `sysWorkMode` → work mode, `energyMode` → energy pattern, `solarSell` → solar sell, `batteryMaxCurrentCharge/Discharge` and `chargeCurrent/dischargeCurrent` → battery parameters, `solarMaxSellPower` → max sell power, `sellTime{n}` / `cap{n}` / `sellTime{n}Pac` / `time{n}on` → one TOU order.
4. **Config flow:** choose the cloud (Sunsynk Connect / Inteless / Deye Cloud); for Deye Cloud: region, App ID, App Secret, login, password (hashed before storing), optional company ID; pick inverters from the account.
5. **VSS on Deye Cloud:** selling via work mode (difference 1), all six slots per write, user's timer and work mode captured and put back.
6. **Beta for testers** before 2.1.0.

## Open questions for a tester

- Exact measure-point keys and units from `/device/latest` for a hybrid with a battery.
- Field names in `/config/system` and `/config/battery`.
- Whether a personal account can send control orders, or a company ID is needed.
- How long orders take on a real logger, and whether the inverter's own timer screen reflects TOU writes.
