"""Auto-generated Lovelace dashboard for the Sunsynk integration."""
from __future__ import annotations

from typing import Any, Callable


def build_dashboard(
    prefix: str,
    eid: Callable[[str], str | None] | None = None,
    forecast_eid: Callable[[str], str | None] | None = None,
    tariff_eid: Callable[[str], str | None] | None = None,
    entry_id: str = "",
) -> dict[str, Any]:
    """Return a full Lovelace dashboard config dict with correct entity IDs.

    Args:
        prefix: Slugified device name used as fallback for entity ID generation.
        eid: Optional callable that maps sensor key to the actual registered
             entity_id, or None if not in registry.  When None is returned the
             entity is omitted from optional card sections.
    """
    def e(key: str, suffix: str | None = None, domain: str = "sensor") -> str:
        """Resolve entity_id: try registry lookup first, fall back to prefix."""
        if eid is not None:
            actual = eid(key)
            if actual:
                return actual
        return f"{domain}.{prefix}_{suffix or key}"

    def e_opt(key: str, suffix: str | None = None, domain: str = "sensor") -> str | None:
        """Resolve optional entity_id — returns None if not in registry."""
        if eid is not None:
            return eid(key)
        return f"{domain}.{prefix}_{suffix or key}"

    # Sensor entity IDs (key → fallback suffix)
    solar_power      = e("pv_pac",               "pv_total_power")
    pv1_power        = e_opt("pv_mppt0_ppv",     "pv_mppt_1_power")
    pv2_power        = e_opt("pv_mppt1_ppv",     "pv_mppt_2_power")
    pv1_voltage      = e_opt("pv_mppt0_vpv",     "pv_mppt_1_voltage")
    pv2_voltage      = e_opt("pv_mppt1_vpv",     "pv_mppt_2_voltage")
    pv_today         = e("pv_etoday",            "pv_generation_today")
    pv_total         = e("pv_etotal",            "pv_generation_total")
    bat_soc          = e("battery_soc")
    bat_power        = e("battery_power")
    bat_voltage      = e("battery_voltage")
    bat_current      = e("battery_current")
    bat_temp         = e("battery_temp",          "battery_temperature")
    bat_bms_soc      = e("battery_bms_soc")
    bat_bms_volt     = e("battery_bms_volt",      "battery_bms_voltage")
    bat_bms_curr     = e("battery_bms_current")
    bat_bms_temp     = e("battery_bms_temp",      "battery_bms_temperature")
    bat_chg_today    = e("battery_etoday_charge", "battery_charge_today")
    bat_dschg_today  = e("battery_etoday_discharge", "battery_discharge_today")
    bat_chg_total    = e("battery_etotal_charge", "battery_charge_total")
    bat_dschg_total  = e("battery_etotal_discharge", "battery_discharge_total")
    bat_capacity     = e("battery_capacity")
    bat_chg_volt     = e("battery_charge_volt",   "battery_charge_voltage")
    bat_dschg_volt   = e("battery_discharge_volt", "battery_discharge_voltage")
    bat_chg_lim      = e("battery_charge_current_limit")
    bat_dschg_lim    = e("battery_discharge_current_limit")
    bat_count        = e("battery_number_of_batteries", "number_of_batteries")
    grid_power       = e("grid_pac",              "grid_power")
    grid_freq        = e("grid_fac",              "grid_frequency")
    grid_qac         = e("grid_qac",              "grid_reactive_power")
    grid_pf          = e("grid_pf",               "grid_power_factor")
    grid_status      = e("grid_status")
    grid_imp_today   = e("grid_etoday_from",      "grid_import_today")
    grid_exp_today   = e("grid_etoday_to",        "grid_export_today")
    grid_imp_total   = e("grid_etotal_from",      "grid_import_total")
    grid_exp_total   = e("grid_etotal_to",        "grid_export_total")
    load_power       = e("load_total_power")
    load_today       = e("load_daily_used",       "load_energy_used_today")
    inv_pac          = e("inverter_pac",          "ac_power")
    inv_rated        = e("inverter_rate_power",   "rated_power")
    inv_status       = e("inverter_status",       "status")
    inv_run_status   = e("inverter_run_status",   "run_status")
    inv_sn           = e("inverter_sn",           "serial_number")
    inv_brand        = e("inverter_brand",        "brand")
    inv_model        = e("inverter_model",        "model")
    inv_update       = e("inverter_update_at",    "last_cloud_update")
    inv_fw_master    = e("inverter_master_ver",   "firmware_master_version")
    inv_fw_soft      = e("inverter_soft_ver",     "firmware_soft_version")
    inv_gen_total    = e("inverter_etotal",       "generation_total")
    inv_dc_temp      = e("inverter_dc_temp",      "inverter_dc_temperature")
    inv_ac_temp      = e("inverter_ac_temp",      "inverter_ac_igbt_temperature")

    # Switch / Number / Text entity IDs
    sw = lambda k: e(k, k, "switch")   # noqa: E731
    nm = lambda k: e(k, k, "number")   # noqa: E731
    tx = lambda k: e(k, k, "text")     # noqa: E731

    # Forecast entity IDs — only populated when forecast is configured
    def fc(key: str, fallback: str) -> str | None:
        if forecast_eid is not None:
            return forecast_eid(key)
        return None

    fc_today    = fc("forecast_today_kwh",    "solar_forecast_today")
    fc_tomorrow = fc("forecast_tomorrow_kwh", "solar_forecast_tomorrow")
    fc_cloud    = fc("forecast_cloud_cover",  "solar_forecast_cloud_cover")
    fc_precip   = fc("forecast_precipitation","solar_forecast_precipitation")
    fc_ghi      = fc("forecast_ghi",          "solar_forecast_ghi")
    fc_dni      = fc("forecast_dni",          "solar_forecast_dni")
    has_forecast = fc_today is not None

    # Tariff entity IDs — only populated when tariff manager is configured
    def t(key: str) -> str | None:
        return tariff_eid(key) if tariff_eid else None

    tariff_mode            = t("mode")
    tariff_sw              = t("enabled")
    tariff_cheap_thr       = t("cheap_threshold")
    tariff_cheap_cur       = t("cheap_charge_current")
    tariff_normal_chg_cur  = t("normal_charge_current")
    tariff_target_soc      = t("target_soc")
    tariff_exp_thr         = t("expensive_threshold")
    tariff_peak_dis_cur    = t("peak_discharge_current")
    tariff_normal_dis_cur  = t("normal_discharge_current")
    tariff_min_soc         = t("discharge_min_soc")
    has_tariff = tariff_mode is not None

    return {
        "views": [
            # ── VIEW 1: OVERVIEW ─────────────────────────────────────────
            {
                "title": "Overview",
                "icon": "mdi:solar-power-variant",
                "type": "panel",
                "cards": [
                    {
                        "type": "vertical-stack",
                        "cards": [
                            {
                                "type": "custom:sunsynk-power-flow-card",
                                "cardstyle": "lite",
                                "show_solar": True,
                                "show_battery": True,
                                "show_grid": True,
                                "show_daily_solar": True,
                                "show_daily_grid": True,
                                "show_daily_battery": True,
                                "entities": {
                                    k: v for k, v in {
                                        "solar_power": solar_power,
                                        "pv1_power": pv1_power,
                                        "pv2_power": pv2_power,
                                        "pv1_voltage": pv1_voltage,
                                        "pv2_voltage": pv2_voltage,
                                        "battery_power": bat_power,
                                        "battery_soc": bat_soc,
                                        "battery_voltage": bat_voltage,
                                        "battery_current": bat_current,
                                        "battery_temp": bat_temp,
                                        "grid_power": grid_power,
                                        "grid_frequency": grid_freq,
                                        "load_power": load_power,
                                        "day_solar_energy": pv_today,
                                        "day_battery_charge": bat_chg_today,
                                        "day_battery_discharge": bat_dschg_today,
                                        "day_grid_import": grid_imp_today,
                                        "day_grid_export": grid_exp_today,
                                        "day_load_energy": load_today,
                                    }.items() if v is not None
                                },
                                "battery": {"shutdown_soc": 10},
                                "grid": {"show_nonessential": False},
                            },
                            {
                                "type": "horizontal-stack",
                                "cards": [
                                    {
                                        "type": "gauge",
                                        "entity": bat_soc,
                                        "name": "Battery",
                                        "min": 0,
                                        "max": 100,
                                        "needle": True,
                                        "severity": {"green": 60, "yellow": 30, "red": 0},
                                    },
                                    {
                                        "type": "entities",
                                        "title": "Solar PV",
                                        "entities": [
                                            {"entity": solar_power, "name": "Power now"},
                                            {"entity": pv_today, "name": "Today"},
                                            {"entity": pv_total, "name": "Total"},
                                        ],
                                    },
                                    {
                                        "type": "entities",
                                        "title": "Battery",
                                        "entities": [
                                            {"entity": bat_soc, "name": "SOC"},
                                            {"entity": bat_power, "name": "Power"},
                                            {"entity": bat_voltage, "name": "Voltage"},
                                            {"entity": bat_current, "name": "Current"},
                                            {"entity": bat_temp, "name": "Temp"},
                                        ],
                                    },
                                    {
                                        "type": "entities",
                                        "title": "Grid",
                                        "entities": [
                                            {"entity": grid_power, "name": "Power"},
                                            {"entity": grid_freq, "name": "Frequency"},
                                            {"entity": grid_imp_today, "name": "Import today"},
                                            {"entity": grid_exp_today, "name": "Export today"},
                                        ],
                                    },
                                ],
                            },
                            {
                                "type": "glance",
                                "title": "Today's Energy",
                                "show_state": True,
                                "entities": [
                                    {"entity": pv_today, "name": "PV", "icon": "mdi:solar-power"},
                                    {"entity": load_today, "name": "Load", "icon": "mdi:home-lightning-bolt"},
                                    {"entity": bat_chg_today, "name": "Charged", "icon": "mdi:battery-arrow-up"},
                                    {"entity": bat_dschg_today, "name": "Discharged", "icon": "mdi:battery-arrow-down"},
                                    {"entity": grid_imp_today, "name": "Import", "icon": "mdi:transmission-tower-import"},
                                    {"entity": grid_exp_today, "name": "Export", "icon": "mdi:transmission-tower-export"},
                                ],
                            },
                            *([
                                {
                                    "type": "horizontal-stack",
                                    "cards": [
                                        {
                                            "type": "tile",
                                            "entity": fc_today,
                                            "name": "Forecast Today",
                                            "icon": "mdi:solar-power",
                                            "color": "amber",
                                        },
                                        {
                                            "type": "tile",
                                            "entity": fc_tomorrow,
                                            "name": "Forecast Tomorrow",
                                            "icon": "mdi:weather-partly-cloudy",
                                            "color": "orange",
                                        },
                                    ],
                                },
                                {
                                    "type": "glance",
                                    "title": "Weather Conditions (Open-Meteo)",
                                    "show_state": True,
                                    "entities": [
                                        {"entity": fc_cloud,  "name": "Cloud Cover",    "icon": "mdi:cloud-percent"},
                                        {"entity": fc_precip, "name": "Precipitation",  "icon": "mdi:weather-rainy"},
                                        {"entity": fc_ghi,    "name": "Irradiance GHI", "icon": "mdi:weather-sunny"},
                                        {"entity": fc_dni,    "name": "Irradiance DNI", "icon": "mdi:sun-wireless"},
                                    ],
                                },
                            ] if has_forecast else []),
                            *([
                                {
                                    "type": "entities",
                                    "title": "Tariff Manager",
                                    "entities": [
                                        *([{"entity": tariff_sw, "name": "Enable"}] if tariff_sw else []),
                                        *([{"entity": tariff_mode, "name": "Mode"}] if tariff_mode else []),
                                    ],
                                },
                            ] if has_tariff else []),
                        ],
                    }
                ],
            },

            # ── VIEW 2: CHARTS ───────────────────────────────────────────
            {
                "title": "Charts",
                "icon": "mdi:chart-line",
                "cards": [
                    {
                        "type": "history-graph",
                        "title": "Power — last 24 hours",
                        "hours_to_show": 24,
                        "entities": [
                            {"entity": solar_power, "name": "Solar"},
                            {"entity": bat_power, "name": "Battery"},
                            {"entity": grid_power, "name": "Grid"},
                            {"entity": load_power, "name": "Load"},
                        ],
                    },
                    {
                        "type": "history-graph",
                        "title": "Battery SOC — last 48 hours",
                        "hours_to_show": 48,
                        "entities": [
                            {"entity": bat_soc, "name": "SOC"},
                            {"entity": bat_bms_soc, "name": "BMS SOC"},
                        ],
                    },
                    {
                        "type": "statistics-graph",
                        "title": "Daily PV Generation — last 30 days",
                        "entities": [{"entity": pv_today, "name": "PV"}],
                        "stat_types": ["sum"],
                        "days_to_show": 30,
                        "chart_type": "bar",
                        "period": "day",
                    },
                    {
                        "type": "statistics-graph",
                        "title": "Daily Energy Balance — last 14 days",
                        "entities": [
                            {"entity": grid_imp_today, "name": "Grid Import"},
                            {"entity": bat_chg_today, "name": "Bat Charge"},
                            {"entity": bat_dschg_today, "name": "Bat Discharge"},
                            {"entity": load_today, "name": "Load"},
                        ],
                        "stat_types": ["sum"],
                        "days_to_show": 14,
                        "chart_type": "bar",
                        "period": "day",
                    },
                    {
                        "type": "history-graph",
                        "title": "Temperatures — last 24 hours",
                        "hours_to_show": 24,
                        "entities": [
                            {"entity": bat_temp, "name": "Battery"},
                            {"entity": inv_dc_temp, "name": "DC"},
                            {"entity": inv_ac_temp, "name": "AC (IGBT)"},
                        ],
                    },
                ],
            },

            # ── VIEW 3: SETTINGS ─────────────────────────────────────────
            {
                "title": "Settings",
                "icon": "mdi:cog",
                "cards": [
                    {
                        "type": "entities",
                        "title": "System Mode",
                        "entities": [
                            sw("setting_solar_sell"),
                            sw("setting_battery_on"),
                            sw("setting_grid_always_on"),
                            sw("setting_allow_remote_control"),
                            sw("setting_peak_and_valley"),
                            nm("setting_sys_work_mode"),
                            nm("setting_batt_mode"),
                            nm("setting_energy_mode"),
                            nm("setting_zero_export_power"),
                            nm("setting_solar_max_sell_power"),
                            nm("setting_pv_max_limit"),
                        ],
                    },
                    {
                        "type": "entities",
                        "title": "Battery Settings",
                        "entities": [
                            nm("setting_battery_shutdown_cap"),
                            nm("setting_battery_restart_cap"),
                            nm("setting_battery_low_cap"),
                            nm("setting_battery_max_current_charge"),
                            nm("setting_battery_max_current_discharge"),
                            nm("setting_charge_current"),
                            nm("setting_discharge_current"),
                        ],
                    },
                    {
                        "type": "entities",
                        "title": "Time Slots",
                        "entities": [
                            *[
                                item
                                for i in range(1, 7)
                                for item in [
                                    {"type": "section", "label": f"Slot {i}"},
                                    sw(f"setting_time{i}on"),
                                    {"entity": tx(f"setting_sell_time{i}"), "name": "Start Time"},
                                    nm(f"setting_cap{i}"),
                                    nm(f"setting_sell_time{i}_pac"),
                                ]
                            ]
                        ],
                    },
                    {
                        "type": "entities",
                        "title": "Active Days",
                        "entities": [
                            sw("setting_monday_on"),
                            sw("setting_tuesday_on"),
                            sw("setting_wednesday_on"),
                            sw("setting_thursday_on"),
                            sw("setting_friday_on"),
                            sw("setting_saturday_on"),
                            sw("setting_sunday_on"),
                        ],
                    },
                    {
                        "type": "entities",
                        "title": "Generator",
                        "entities": [
                            sw("setting_gen_charge_on"),
                            sw("setting_sd_charge_on"),
                            nm("setting_generator_start_cap"),
                            nm("setting_gen_on_cap"),
                            nm("setting_gen_off_cap"),
                            {"type": "section", "label": "Generator Time Slots"},
                            *[sw(f"setting_gen_time{i}on") for i in range(1, 7)],
                        ],
                    },
                ],
            },

            # ── VIEW 4: TARIFF ───────────────────────────────────────────
            {
                "title": "Tariff",
                "icon": "mdi:cash-clock",
                "cards": [
                    *([] if has_tariff else [{
                        "type": "markdown",
                        "content": (
                            "## Tariff Manager not configured\n\n"
                            "Set an **Electricity Price Sensor** to enable automatic cheap-rate "
                            "charging and peak-rate discharging.\n\n"
                            "[Open Integration Settings](/config/integrations/integration/sunsynk)"
                        ),
                    }]),
                    *([
                        {
                            "type": "entities",
                            "title": "Tariff Manager",
                            "entities": [
                                *([{"entity": tariff_sw, "name": "Enable"}] if tariff_sw else []),
                                *([{"entity": tariff_mode, "name": "Mode"}] if tariff_mode else []),
                                *([{"entity": t("price_quality"), "name": "Price Quality"}] if t("price_quality") else []),
                            ],
                        },
                        {
                            "type": "entities",
                            "title": "Cheap-rate Charging",
                            "entities": [
                                *([{"entity": tariff_cheap_thr, "name": "Cheap threshold (price ≤)"}] if tariff_cheap_thr else []),
                                *([{"entity": tariff_cheap_cur, "name": "Charge current (A)"}] if tariff_cheap_cur else []),
                                *([{"entity": tariff_normal_chg_cur, "name": "Normal charge current (A)"}] if tariff_normal_chg_cur else []),
                                *([{"entity": tariff_target_soc, "name": "Target SOC (%)"}] if tariff_target_soc else []),
                            ],
                        },
                        {
                            "type": "entities",
                            "title": "Peak-rate Discharging",
                            "entities": [
                                *([{"entity": tariff_exp_thr, "name": "Expensive threshold (price ≥)"}] if tariff_exp_thr else []),
                                *([{"entity": tariff_peak_dis_cur, "name": "Discharge current (A)"}] if tariff_peak_dis_cur else []),
                                *([{"entity": tariff_normal_dis_cur, "name": "Normal discharge current (A)"}] if tariff_normal_dis_cur else []),
                                *([{"entity": tariff_min_soc, "name": "Min SOC (%)"}] if tariff_min_soc else []),
                            ],
                        },
                        {
                            "type": "history-graph",
                            "title": "Tariff Mode & Battery SOC — last 24 hours",
                            "hours_to_show": 24,
                            "entities": [
                                *([{"entity": tariff_mode, "name": "Mode"}] if tariff_mode else []),
                                {"entity": bat_soc, "name": "SOC"},
                            ],
                        },
                    ] if has_tariff else []),
                ],
            },

            # ── VIEW 5: DIAGNOSTICS ──────────────────────────────────────
            {
                "title": "Diagnostics",
                "icon": "mdi:information-outline",
                "cards": [
                    {
                        "type": "entities",
                        "title": "Inverter Info",
                        "entities": [
                            inv_status,
                            inv_run_status,
                            inv_pac,
                            inv_rated,
                            inv_update,
                            inv_fw_master,
                            inv_fw_soft,
                            inv_sn,
                            inv_brand,
                            inv_model,
                            bat_count,
                        ],
                    },
                    {
                        "type": "entities",
                        "title": "Grid Details",
                        "entities": [
                            grid_power,
                            grid_qac,
                            grid_freq,
                            grid_pf,
                            grid_status,
                            grid_imp_total,
                            grid_exp_total,
                        ],
                    },
                    {
                        "type": "entities",
                        "title": "Battery Details",
                        "entities": [
                            bat_soc,
                            bat_bms_soc,
                            bat_power,
                            bat_voltage,
                            bat_current,
                            bat_temp,
                            bat_bms_volt,
                            bat_bms_curr,
                            bat_bms_temp,
                            bat_capacity,
                            bat_chg_volt,
                            bat_dschg_volt,
                            bat_chg_lim,
                            bat_dschg_lim,
                        ],
                    },
                    {
                        "type": "entities",
                        "title": "All-Time Totals",
                        "entities": [
                            {"entity": inv_gen_total, "name": "Inverter Generation"},
                            {"entity": pv_total, "name": "PV Generation"},
                            bat_chg_total,
                            bat_dschg_total,
                            grid_imp_total,
                            grid_exp_total,
                        ],
                    },
                ],
            },
        ]
    }
