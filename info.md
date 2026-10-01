# Sunsynk / Deye Solar Inverter

Native Home Assistant integration for **Sunsynk** and **Deye / Inteless** solar inverters via the cloud API.

No add-on, virtual machine, container, MQTT bridge or extra YAML — configure
everything through the Home Assistant UI.

This began as a small project to make an inverter behave like a native Home
Assistant device. Real-world reports revealed a much more complex landscape:
different firmware behaviour, delayed cloud writes, grouped timer settings,
independent multi-inverter plants and parallel master/slave systems. Thank you to
everyone who shared bugs, diagnostics and hardware test results — those reports
continue to shape a safer integration.

---

## What you get

### ~60 sensor entities per inverter
PV generation · Battery (SOC, power, temperature, BMS) · Grid (import/export, frequency) · Load · Inverter diagnostics

### ~30 writable number entities
Charge/discharge current · Battery thresholds · Time slot settings · Zero export · Sell power

### ~25 switch entities
Solar sell · Battery on · Grid always on · Time slots · Active days

### Solar Forecast (optional)
6 sensors powered by [Open-Meteo](https://open-meteo.com) — free, no API key:
- PV yield today & tomorrow (kWh)
- Cloud cover · Precipitation · GHI · DNI

### Tariff-aware charging & discharging (optional)
Works with any price sensor — Octopus Agile, NordPool, Tibber, G12:
- **Cheap rate** → charge battery to target SOC
- **Expensive rate** → discharge battery (sell to grid)
- Scheduler, price quality check, HA notifications

### Optional generated dashboard
Power flow card + charts + settings — opt in through the integration options.

---

## Supported hardware

| Brand | API server |
|---|---|
| Sunsynk | `api.sunsynk.net` |
| Deye / Inteless | `pv.inteless.com` |

Independent and parallel multi-inverter systems are supported (multiple serial
numbers per account).

---

## Documentation

Full documentation is available in the [Wiki](https://github.com/MarcinG81/SunSynk_HA_Integration/wiki).
