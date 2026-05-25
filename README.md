# SunSynk / Deye Solar Inverter — Home Assistant Integration

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/marcingaszewski)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](custom_components/sunsynk/LICENSE)
![HA Version](https://img.shields.io/badge/HA-2024.1%2B-blue)
[![Validate](https://github.com/MarcinG81/SunSynk_HA_Integration/actions/workflows/validate.yaml/badge.svg)](https://github.com/MarcinG81/SunSynk_HA_Integration/actions/workflows/validate.yaml)

A native Home Assistant **integration** (not an add-on) for monitoring and controlling Sunsynk and Deye hybrid solar inverters via the Sunsynk cloud API.

> **Note:** This integration communicates with the **Sunsynk cloud API** (`api.sunsynk.net` or `pv.inteless.com`). Your inverter must already be connected to the cloud via the Sunsynk dongle/Wi-Fi stick.

---

## Features

- **Real-time monitoring** — PV generation, battery state, grid import/export, load consumption
- **Multi-MPPT support** — Automatically discovers all MPPT strings on your inverter
- **3-phase support** — Per-phase voltage, current and power for grid, load and inverter output
- **Writable settings** — Change key inverter parameters directly from HA (battery thresholds, charge/discharge current, time slots, sell power limits, etc.)
- **Solar Forecast** — Optional sensors for predicted PV yield today/tomorrow, cloud cover, precipitation and solar irradiance (GHI/DNI) via Open-Meteo (free, no API key)
- **Tariff Manager** — Automatic cheap-rate charging and peak-rate discharging based on any HA electricity price sensor (Octopus Agile, NordPool, Tibber, G12, `input_number`, etc.)
- **Multiple inverters** — One integration entry supports multiple serial numbers
- **Config Flow** — Fully configured through the Home Assistant UI (no YAML needed)
- **HACS compatible** — Install and update through HACS

---

## Supported Inverters

Any inverter accessible through the Sunsynk cloud API, including:

- **Sunsynk** hybrid inverters (all kW ratings)
- **Deye** hybrid inverters (using `pv.inteless.com` endpoint)

---

## Prerequisites

1. A **Sunsynk account** at [https://home.sunsynk.net](https://home.sunsynk.net) (no MFA — multi-factor authentication is not supported by the API)
2. Your inverter connected to the Sunsynk cloud (dongle shows green / data visible in the Sunsynk app)
3. Your **inverter serial number** (visible in the Sunsynk app under _Device_ → _About_)

---

## Installation

### Option A — HACS (Recommended)

1. Open **HACS** in Home Assistant
2. Click the three-dot menu (⋮) → **Custom repositories**
3. Add URL: `https://github.com/MarcinG81/SunSynk_HA_Integration`
4. Category: **Integration**
5. Click **Add** → find **SunSynk HA Integration** → **Download**
6. Restart Home Assistant

### Option B — Manual

1. Download or clone this repository
2. Copy the `custom_components/sunsynk/` folder into your HA `custom_components/` directory:
   ```
   config/
   └── custom_components/
       └── sunsynk/
           ├── __init__.py
           ├── manifest.json
           ├── config_flow.py
           ├── coordinator.py
           ├── const.py
           ├── sensor.py
           ├── number.py
           ├── switch.py
           ├── strings.json
           ├── translations/
           │   └── en.json
           └── api/
               ├── __init__.py
               ├── auth.py
               └── client.py
   ```
3. Restart Home Assistant

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Sunsynk**
3. Fill in the form:

| Field | Description |
|---|---|
| **API Server** | `api.sunsynk.net` (Sunsynk) or `pv.inteless.com` (Deye/Inteless) |
| **Email** | Your Sunsynk account email |
| **Password** | Your Sunsynk account password |
| **Serial Number(s)** | Inverter serial number. For multiple inverters separate with `;` e.g. `SN123456;SN789012` |
| **Refresh Interval** | How often to poll the API in seconds (min 60, default 300) |

4. Click **Submit** — HA will validate your credentials and create the integration

---

## Dashboard

A ready-made Lovelace dashboard is included in [`dashboards/sunsynk-dashboard.yaml`](dashboards/sunsynk-dashboard.yaml).  
It provides four views: **Overview** (Power Flow Card), **Charts**, **Settings**, and **Diagnostics**.

> **Note:** The **Sunsynk Power Flow Card** (v7.3.3 by slipx06) is **bundled with this integration** — it loads automatically. No separate HACS installation needed.

### Import steps

1. **Find your entity prefix**  
   Settings → Devices & Services → Sunsynk → click your device → click any sensor (e.g. Battery SOC)  
   Note the Entity ID, e.g. `sensor.sunsynk_battery_soc` → your prefix is **`sunsynk`**

2. **Open the dashboard file**, find & replace every occurrence of `INVERTER` with your prefix  
   (VS Code: `Ctrl+H`, search `INVERTER`, replace with e.g. `sunsynk`)

3. **Create a new HA dashboard**  
   Settings → Dashboards → Add Dashboard → give it a name (e.g. "Solar")

4. **Paste the YAML**  
   Open the new dashboard → Edit Dashboard (pencil icon) → three-dot menu → Raw Configuration Editor → paste the file content → Save

---

## Entities

Each inverter appears as a **device** in Home Assistant with the following entities:

### Sensors (read-only)

#### PV / Solar
| Entity | Unit | Description |
|---|---|---|
| PV Total Power | W | Total AC power from PV |
| PV Generation Today | kWh | Daily energy generated |
| PV Generation Total | kWh | Lifetime energy generated |
| PV MPPT 1 Power/Voltage/Current | W / V / A | Per-MPPT string data |
| PV MPPT 2 ... | W / V / A | Dynamically created based on your inverter |

#### Battery
| Entity | Unit | Description |
|---|---|---|
| Battery SOC | % | State of charge |
| Battery Power | W | Charge (+) / Discharge (–) power |
| Battery Voltage | V | Battery voltage |
| Battery Current | A | Battery current |
| Battery Temperature | °C | Battery temperature |
| Battery Charge Today | kWh | Energy charged today |
| Battery Discharge Today | kWh | Energy discharged today |
| Battery Charge Total | kWh | Lifetime charged |
| Battery Discharge Total | kWh | Lifetime discharged |
| Battery 1/2 SOC, Voltage, Power, Temp | various | Per-battery data (parallel batteries) |

#### Grid
| Entity | Unit | Description |
|---|---|---|
| Grid Power | W | Import (+) / Export (–) |
| Grid Import Today | kWh | Energy imported from grid today |
| Grid Export Today | kWh | Energy exported to grid today |
| Grid Import Total | kWh | Lifetime grid import |
| Grid Export Total | kWh | Lifetime grid export |
| Grid Frequency | Hz | Grid frequency |
| Grid Phase 1/2/3 Voltage/Current/Power | V / A / W | Per-phase data |

#### Load / Consumption
| Entity | Unit | Description |
|---|---|---|
| Load Total Power | W | Total consumption |
| Load Energy Used Today | kWh | Daily consumption |
| Load Energy Used Total | kWh | Lifetime consumption |
| Load Phase 1/2/3 Voltage/Current/Power | V / A / W | Per-phase data |
| Load UPS Power L1/L2/L3 | W | UPS output per phase |

#### Inverter Output
| Entity | Unit | Description |
|---|---|---|
| Inverter Output Power | W | AC output power |
| Inverter Output Frequency | Hz | AC output frequency |
| Inverter DC Temperature | °C | DC-side temperature |
| Inverter AC (IGBT) Temperature | °C | AC-side IGBT temperature |

#### Inverter Info (Diagnostic)
Serial number, model, firmware versions, plant name, status, run status, last cloud update, etc.

---

### Tariff Manager Entities (optional)

These entities appear on the inverter device when Tariff Manager is configured.

| Entity | Type | Description |
|---|---|---|
| Tariff Manager | Switch | Enable / disable the tariff manager (default **off**) |
| Tariff Mode | Sensor | Current mode: `disabled` / `idle` / `charging` / `discharging` |
| Tariff Price Quality | Sensor (diagnostic) | Price data quality: `ok` / `stale` / `unavailable` / `invalid` / `not_found` |

---

### Solar Forecast Sensors (optional)

These sensors appear under a separate **Solar Forecast** device (powered by [Open-Meteo](https://open-meteo.com)) when forecast is configured.

| Entity | Unit | Description |
|---|---|---|
| Solar Forecast Today | kWh | Predicted PV yield for today |
| Solar Forecast Tomorrow | kWh | Predicted PV yield for tomorrow |
| Cloud Cover | % | Current hour cloud coverage |
| Precipitation | mm | Current hour precipitation |
| Solar Irradiance GHI | W/m² | Global Horizontal Irradiance (current hour) |
| Solar Irradiance DNI | W/m² | Direct Normal Irradiance (current hour) |

> kWh estimates use: `Σ(GHI per hour) / 1000 × panel_kWp × performance_ratio`

To enable, go to **Settings → Devices & Services → Sunsynk → Configure** and fill in the forecast fields (see [Solar Forecast Setup](#solar-forecast-setup)).

---

### Writable Settings

These appear under **Settings** entities on the device page.

#### Numbers (input boxes)
| Entity | Unit | Description |
|---|---|---|
| Battery Shutdown Capacity | % | SOC at which inverter stops discharging |
| Battery Restart Capacity | % | SOC at which inverter resumes after shutdown |
| Battery Low Capacity | % | Low battery warning threshold |
| Battery Max Charge Current | A | Maximum charge current |
| Battery Max Discharge Current | A | Maximum discharge current |
| Charge Current | A | Charge current setpoint |
| Discharge Current | A | Discharge current setpoint |
| Zero Export Power | W | Power limit for zero-export mode |
| Solar Max Sell Power | W | Maximum power sold to grid |
| PV Max Limit | % | PV output power limit |
| Sell Time 1–6 Power | W | Max sell power per time slot |
| Time Slot 1–6 Capacity | % | Target SOC per time slot |
| Generator Start/On/Off Capacity | % | Generator automation thresholds |
| Battery Mode | 0–2 | 0=Lithium, 1=Lead-acid, 2=Other |
| System Work Mode | 0–4 | System operation mode |
| Energy Mode | 0–1 | Energy priority mode |

#### Switches (on/off)
| Entity | Description |
|---|---|
| Solar Sell | Enable/disable selling excess solar to grid |
| Battery On | Enable/disable battery |
| Time Slot 1–6 On | Enable/disable each time-of-use slot |
| Monday–Sunday Active | Enable/disable sell schedule per day |
| Generator Time Slot 1–6 On | Generator automation time slots |
| Generator Charge On | Allow generator to charge battery |
| Grid Always On | Keep grid connection always active |
| Peak and Valley | Enable peak/valley tariff mode |
| Allow Remote Control | Enable remote API control |

---

## Tariff Manager Setup

The Tariff Manager automatically charges the battery when electricity is cheap and discharges (sells to grid) when it's expensive. It works with any HA sensor that provides a numeric price.

1. Go to **Settings → Devices & Services → Sunsynk → Configure**
2. Fill in the tariff fields:

| Field | Description |
|---|---|
| **Price sensor** | Any HA sensor with a numeric electricity price (e.g. `sensor.octopus_current_rate`) |
| **Cheap threshold** | Price at or below which charging activates (e.g. `0.10`) |
| **Cheap charge current (A)** | Charge current during cheap rate |
| **Normal charge current (A)** | Current restored when cheap rate ends |
| **Charge target SOC (%)** | Stop charging when battery reaches this SOC (default 100) |
| **Expensive threshold** | Price at or above which discharging activates |
| **Peak discharge current (A)** | Discharge current during expensive rate |
| **Normal discharge current (A)** | Current restored when expensive rate ends |
| **Minimum SOC (%)** | Stop discharging when battery reaches this SOC (default 10) |
| **Active from / Active until** | Optional hours to restrict tariff activity (supports midnight wrap, e.g. 22–06) |
| **Price max age (minutes)** | How old price data can be before it's considered stale (default 90) |

3. After setup, go to your inverter device and **turn on the Tariff Manager switch** — it starts **off** by default.

> Both cheap-rate charging and expensive-rate discharging are independent — configure one, both, or neither.

---

## Solar Forecast Setup

1. Go to **Settings → Devices & Services → Sunsynk → Configure**
2. Fill in the forecast fields:

| Field | Description |
|---|---|
| **Panel Peak Power (kWp)** | Total installed panel capacity in kilowatt-peak (e.g. `10.5`) — **required** to enable forecast |
| **Latitude** | Optional — leave blank to use your HA home location |
| **Longitude** | Optional — leave blank to use your HA home location |
| **Performance Ratio** | System efficiency factor 0–1, default `0.80` |

Leave **Panel kWp** blank to disable forecast sensors entirely. Latitude and longitude always fall back to your Home Assistant home location if not filled in.

Forecast data refreshes every **30 minutes**. No API key or account needed.

---

## Automation Examples

### Notify when battery is low

```yaml
automation:
  - alias: "Battery Low Alert"
    trigger:
      - platform: numeric_state
        entity_id: sensor.sunsynk_YOURSERIAL_battery_soc
        below: 20
    action:
      - service: notify.mobile_app
        data:
          message: "Battery SOC below 20%!"
```

### Set zero export at night

```yaml
automation:
  - alias: "Zero export at night"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: number.set_value
        target:
          entity_id: number.sunsynk_YOURSERIAL_setting_zero_export_power
        data:
          value: 0
```

### Disable grid sell during peak hours

```yaml
automation:
  - alias: "Disable sell during peak"
    trigger:
      - platform: time
        at: "17:00:00"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.sunsynk_YOURSERIAL_setting_solar_sell
```

### Adjust charge current based on tomorrow's forecast

```yaml
automation:
  - alias: "High charge current when forecast is poor"
    trigger:
      - platform: numeric_state
        entity_id: sensor.solar_forecast_tomorrow
        below: 5
    action:
      - service: number.set_value
        target:
          entity_id: number.sunsynk_YOURSERIAL_setting_charge_current
        data:
          value: 100
```

---

## Troubleshooting

### Integration fails to authenticate
- Verify your email and password in the [Sunsynk web portal](https://home.sunsynk.net)
- **MFA (2-factor authentication) is not supported** — disable it on your Sunsynk account
- Avoid special characters in your password

### No data / sensors unavailable
- Check your inverter is online in the Sunsynk app (dongle LED should be green)
- The Sunsynk cloud updates data every ~5 minutes — polling faster than 300 s has no benefit
- Check HA logs: **Settings → System → Logs** and filter for `sunsynk`

### Settings not applying
- The Sunsynk cloud may reject out-of-range values
- Some settings require specific inverter firmware versions
- Check HA logs for `sunsynk` errors after attempting a setting change

### MPPT / phase sensors missing
- These are discovered dynamically on first successful data fetch
- If the first fetch failed, restart the integration: **Settings → Devices & Services → Sunsynk → (⋮) → Reload**

### Multiple inverters
- Enter serial numbers separated by a semicolon with no spaces: `SN123456;SN789012`
- Each inverter appears as a separate device in HA

### Tariff Manager not doing anything
- Make sure the **Tariff Manager switch** is turned **on** — it starts off by default
- Check the **Tariff Price Quality** sensor — if it shows `stale`, `unavailable`, or `not_found`, the price sensor is the issue
- Check the **Tariff Mode** sensor — `idle` means the manager is running but price conditions aren't met
- HA persistent notifications appear on every mode change — check the notification bell

### Solar forecast sensors not appearing
- Ensure all three fields (latitude, longitude, panel kWp) are filled in the options form
- Check HA logs for `Open-Meteo request failed` — verify internet access from your HA host
- Reload the integration after saving forecast settings

---

## Architecture

```
Home Assistant
└── Integration: sunsynk
    ├── Config Flow (UI setup, includes optional solar forecast config)
    ├── SunsynkCoordinator (polls every 5 min, all endpoints concurrently)
    │   ├── api/auth.py            — RSA + OAuth2 token (cached, auto-refreshed on expiry)
    │   └── api/client.py          — 8 endpoints fetched in parallel via aiohttp
    ├── SolarForecastCoordinator   — Open-Meteo fetch every 30 min (optional)
    ├── TariffChargingManager      — price-aware charge/discharge automation (optional)
    ├── sensor.py                  — ~60 static + dynamic MPPT/phase sensors + 6 forecast + 2 tariff sensors
    ├── number.py                  — ~30 writable numeric settings
    └── switch.py                  — ~25 writable boolean settings + tariff manager toggle
```

**Sunsynk API endpoints used:**

| Endpoint | Data |
|---|---|
| `GET /api/v1/inverter/{sn}` | Inverter info, energy totals |
| `GET /api/v1/inverter/{sn}/realtime/input` | PV power, MPPT data |
| `GET /api/v1/inverter/grid/{sn}/realtime` | Grid power, phases, energy |
| `GET /api/v1/inverter/battery/{sn}/realtime` | Battery SOC, power, temps |
| `GET /api/v1/inverter/load/{sn}/realtime` | Load power, phases |
| `GET /api/v1/inverter/{sn}/realtime/output` | Inverter output phases |
| `GET /api/v1/inverter/{sn}/output/day` | DC/AC temperatures |
| `GET /api/v1/common/setting/{sn}/read` | All inverter settings |
| `POST /api/v1/common/setting/{sn}/set` | Write inverter settings |

---

## Credits

Inspired by the original [SolarSynkV3 add-on](https://github.com/martinville/solarsynkv3) by martinville.

Rewritten as a native Home Assistant integration with async support, proper entity model, config flow UI, token caching and writable settings entities.

---

## License

GNU General Public License v3.0 — Copyright (c) 2026 Marcin Gaszewski

See [LICENSE](custom_components/sunsynk/LICENSE) for full text.
