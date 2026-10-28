# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed
- **The diagnostics file showed the plant's ID** on each of the plant's tariff rates (`stationId`). It is now masked like the other IDs. (#44)

## [2.1.0-beta.2] - 2026-10-28

In HACS, turn on *Show beta versions* for this integration to see it. **If you set the daily kWh to buy or sell in beta.1 on a parallel system, set it again for the whole system** (e.g. double it for a pair).

### Changed
- **The amount to buy or sell a day is for the whole system,** like the maximum export power: 10 kWh means 10 kWh for a parallel pair, not for each inverter. If you set it per inverter in beta.1, double it for a pair. (#42)
- **The plan uses the power the system really charges or discharges at.** Per inverter the Tariff Manager current × battery voltage, now also within the BMS limit shared in a parallel group, the inverter's rated power and, for selling, the maximum export power, times the inverters. The Tariff Mode sensor shows it as `planned_power_kw`. (#42)

### Fixed
- **Energy totals could jump to impossible values and spoil the Energy dashboard.** The Sunsynk cloud sometimes returns a counter with its two halves swapped, e.g. a grid import total of 376.5 kWh as 24,674,304.5 kWh, or a lifetime total as 0, and Home Assistant counted that as real energy. The energy counters now keep their last value instead when a reading rises faster than the inverter could have managed (three times its rated power, at least 50 kW, plus 1 kWh), when a lifetime total goes down, or when a daily counter dips slightly (which Home Assistant also warns about); a daily, monthly or yearly counter falling to its reset is still taken. A warning in the log names the value ignored, and a value reported for over an hour is taken, so a counter that really was reset isn't held back. Found by nickcmaynard. (#43)
- **A day already under way got its whole amount.** Set up part way through a day, or after a restart, the plan put the day's full amount into the slots left. It now plans only the share of the amount for the slots still ahead, and plans are kept over a restart, so a restart doesn't plan a day again. Found by pay4property. (#42)

### Added
- **Tariff Plan kWh to Buy / to Sell numbers,** to change the daily amounts from a dashboard or an automation (e.g. from the solar forecast) without Configure; 0 turns that side of the plan off. Suggested by pay4property. (#42)
- **Tariff Price Plan switch** to pause the plan, e.g. while a car charges: off, nothing planned charges or discharges; on again, the slots still ahead apply. Kept over a restart. Suggested by pay4property. (#42)
- **Planned slots on the Tariff view** of the generated dashboard, with the power they were planned at and whether the plan is paused. Suggested by pay4property. (#42)

## [2.1.0-beta.1] - 2026-10-27

The first 2.1 beta. In HACS, turn on *Show beta versions* for this integration to see it. It includes everything in 2.0.0.

**Deye Cloud is new and still being tested**: reading works on a 17-inverter account; the experimental controls are off by default and have only been tried on one inverter so far. After changing a setting through Deye Cloud, Home Assistant can show the previous value for a while, until Deye Cloud reports the new one.

### Added
- **Battery helper sensors:** Battery Energy (kWh), Battery Max Charge Power and Max Discharge Power (W, from the current limits in force × voltage, also handy for Predbat), and Battery Time to Full / Time to Reserve (h, while charging / discharging).
- **Stop selling solar at a negative export price** (Configure, off by default): Tariff Manager turns Solar Sell off while the export price is below zero and back on afterwards, only where Solar Sell was on, and remembers it over a restart. The Tariff Mode sensor lists the inverters it applies to. A negative import price needs nothing new: it is below any cheap threshold, so Tariff Manager already charges then.
- **Settings backup and restore:** `sunsynk.backup_settings` saves everything the inverter reports in its settings to `/config/sunsynk_backups/`; `sunsynk.restore_settings` puts a backup back, only the settings the integration can write and check and only those that differ, listing them first (dry run by default), and not while Tariff Manager or the Virtual Slot Scheduler are driving the inverter.
- **Plan by the prices ahead:** pick the entities with the coming prices (Octopus Energy current / next day rates, Nord Pool and lookalikes) and how many kWh a day to buy and to sell, per inverter. Tariff Manager then charges in the cheapest and discharges in the dearest slots of each day at its charge / discharge power, instead of whenever the price crosses a threshold; the thresholds stay as price limits. A day is planned once, when its prices appear (Agile about 16:00), and again if they change; the Tariff Mode sensor shows the planned slots. Suggested by pay4property. (#40)
- **Charge now / discharge now actions** for automations: `sunsynk.charge_now_start`, `charge_now_stop`, `discharge_now_start` and `discharge_now_stop`. They win over the price and work with Tariff Manager on or off. Start takes an optional `duration` (minutes), `current` (A) and SOC (`target_soc` to charge to, `min_soc` to discharge to), and for a discharge `power` (W), e.g. export 3 kW down to 40 %. Without them, the new Charge now / Discharge now options in Configure apply, and without those, Tariff Manager's own values. Currents are checked against the inverter and BMS limits, power against the export limit. A discharge also ends at the minimum SOC. Switching Tariff Manager off or restarting Home Assistant ends them and puts everything back. The Tariff Mode sensor shows `manual_charge` / `manual_discharge` and when it ends. Suggested by pay4property. (#39)
- **Deye Cloud inverters.** Deye inverters on a SOLARMAN / LSW logger report to Deye Cloud, not to Sunsynk Connect or Inteless, so they couldn't be added (#37). When adding the integration you can now choose Deye Cloud, enter the App ID and App Secret of your own free developer app from developer.deyecloud.com and your Deye Cloud login, and pick inverters from the account. They get the same sensors as Sunsynk inverters, single- and three-phase, mapped from real data shared by poligor84. Tariff Manager and the Virtual Slot Scheduler don't drive Deye Cloud inverters yet. Only a hash of the password is stored. Microinverters on the account are listed too. **Experimental controls** (Configure, off by default): work mode, energy pattern, battery max charge / discharge current and max sell power, on the inverters Deye Cloud lets you configure; each change is a Deye Cloud order and waits until the inverter applies it. Not the time-of-use table yet: Deye Cloud's read of it has no Sell flags, so changing one slot would untick Sell on all of them.
- **Deye Cloud inverters get only the sensors Deye Cloud reports.** The first test build added every Sunsynk sensor, so about half of them (BMS details, status codes, plant info) only ever showed "Unknown": over 1,800 entities for 17 inverters. Each inverter now gets only the sensors its model reports, with no "Unknown": 55 on a single-phase and 75 on a three-phase hybrid in that test, about 1,035 for the 17. Also added from what Deye Cloud reports: the external CT (meter) power, grid reactive power and generator power; a battery temperature of -100 °C (no sensor) is left out. An inverter whose logger is offline is shown as offline: Deye Cloud keeps answering with its last values, so they are no longer taken as current (no SOC for Tariff Manager, a Repair instead), and Last Cloud Update shows when the inverter last reported. Found by poligor84. (#37)
- **System Work Mode as a select** with the three modes the Sunsynk portal offers: Selling First, Zero Export + Limit to Load Only, Limited to Home, translated into every language. It replaces the number entity, which is kept for existing automations but disabled on new installs.
- **Energy Mode as a select:** Battery First or Load First, instead of 0 or 1. The Energy Mode number is kept for existing automations but off on new installs.

### Changed
- **`sunsynk.set_work_mode` and the work mode number accept 0–2 only.** Modes 3 ("Self Use") and 4 ("Time of Use") were listed but don't exist on Sunsynk / Deye inverters.

### Fixed
- **Serial numbers and firmware versions made only of digits showed as numbers**, e.g. a Deye serial as "2301316667.0" or a BMS version "0000" as "0.0". They are now shown exactly as reported. (#37)

## [2.0.0] - 2026-10-26

The first stable 2.0. It is 2.0.0-rc.1, which nobody reported a problem with, plus two corrections to the README. Everything that changed since 1.9.x is listed under the 2.0.0 betas and rc.1 below.

### Before you upgrade from 1.9.x

- **Take a Home Assistant backup first.** Virtual slot schedules move to per-inverter storage and the old shared record is removed, and the virtual slot entities get new unique IDs (their entity IDs are kept). Going back to 1.9.x without restoring the backup leaves an empty virtual slot schedule and may create duplicate virtual slot entities with a `_2` suffix.
- **The auto-maintained dashboard is now opt-in.** If you rely on it, enable **Create and maintain Sunsynk dashboard** in the integration's options after upgrading. Dashboards already created stay in place.

### Highlights since 1.9.x

- Every setting write is read back from the inverter, sent again if the inverter lost it, and reported under Repairs if it still isn't applied.
- Settings are written per settings page from a fresh read, so a change made in the Sunsynk app is not undone.
- Tariff Manager and the Virtual Slot Scheduler work per inverter, including parallel master/slave systems, and put your own currents and timer back after a charge or discharge, also across a restart.
- Charge and discharge decisions are exclusive; a discharge stops at the minimum SOC, and every current stays within the battery's BMS limits.

### Fixed
- **README: Battery Power sign.** Positive is discharge and negative is charge, not the other way round.
- **README: Battery Mode.** The values 0=Lithium, 1=Lead-acid, 2=Other were not confirmed and have been removed.

## [2.0.0-rc.1] - 2026-10-21

### Fixed
- **A parallel group could be forgotten for one refresh.** If reading one inverter's settings failed, its parallel role was lost with them: the slave became a write target of its own and the battery's BMS limit was no longer shared between the inverters (e.g. 120 A each instead of 96 A). The last confirmed role is now kept until the inverter reports a different one.
- **A setting change could undo a change made in the app.** When the fresh read before a write failed, the rest of that settings page was sent from the last poll, so e.g. a battery limit lowered to 40 A in the app went back to 120 A. Such a write now fails and is retried instead; a setting written on its own still goes out.
- **The live BMS limit was ignored when a rated one was reported.** Tariff Manager and the Virtual Slot Scheduler lowered a current to the rated BMS limit only; the lower of the rated and the current limit (e.g. near full charge) now applies. Configure still checks fixed limits only.
- **A charge current could stay raised after a Tariff Manager discharge.** With a charge window's current set and a price discharge starting, the scheduler forgot that the charge current was still its to put back. The current each direction has to go back to is now tracked separately, also across a restart.
- **A restart could overwrite a timer changed in the app.** If Home Assistant stopped while the scheduler was standing by, the next start wrote back the timer captured earlier, undoing any change made in the app meanwhile. The scheduler now saves whether it had actually taken the slots over, and from standing by takes the timer the inverter has.
- **Price Quality showed "not_found" after a restart** while Tariff Manager was off, until it was switched on: the price helper loads after the integration. Quality now follows the price helper whether Tariff Manager is on or not.
- **A failed first connection left an HTTP session open**; each retry of the setup opened another. It is now closed.

## [2.0.0-beta.18] - 2026-10-19

### Fixed
- **After a discharge, Sell could stay ticked and the inverter kept exporting at full power.** When a Tariff Manager discharge ended, the write putting your own timer back could be lost, like the morning one above: the discharge slots stayed, Sell ticked, and the inverter exported over 20 kW until it was noticed. Worse, while standing by the Virtual Slot Scheduler takes what the inverter reports as your timer, so it could have kept the discharge slots as yours. Every write is now checked: one the inverter still hasn't applied after a minute, while it still reports the value from before, is sent again, up to three times, and only then reported under Repairs. A value changed elsewhere, e.g. in the Sunsynk app, is left alone. The scheduler also checks that the inverter really reports your timer once it has put it back, never takes its own slots for yours (also after a restart), and shows a notification if your timer can't be put back. Found by pay4property. (#36)
- **A Tariff Manager discharge put the house on the grid once the battery reached the minimum SOC.** Since beta.17 Tariff Manager stayed in discharge mode for the whole expensive period, so the slot holding the minimum stayed in force and the inverter supplied the house from the grid until the period ended. Discharging now stops at the minimum again and your own timer is put back, which normally has a lower SOC, so the house runs from the battery. It isn't started again in the same expensive period, so a little solar can't make it bounce at the minimum. Charging still holds the target for the whole cheap period. Found by pay4property. (#36)
- **A discharge straight after a charge could export nothing.** When the cheap period ended and the export price was already above the threshold, Tariff Manager went from charging to idle to discharging in the same second. The Virtual Slot Scheduler acted on the idle in between: it put your own timer back and took the slots over again straight after, and the two writes crossed, so the discharge slots had the right power and SOC but no Sell ticked. The scheduler now waits until Tariff Manager has finished deciding. (#36)
- **A slot the inverter didn't take stayed wrong until the window changed.** The scheduler only remembered what it had written, so a lost write was never noticed. It now also checks what the inverter reports once a write is confirmed, and writes a slot again if it differs, at most three times for the same window, with a warning in the log if the inverter still refuses it. (#36)

## [2.0.0-beta.17] - 2026-10-18

### Fixed
- **Charging bounced between 100% and 99% until the cheap period ended.** Tariff Manager stopped charging as soon as the battery reached the target SOC. With the Virtual Slot Scheduler on, that also handed the timer back, so the house ran from the battery, and at 1% below the target charging started again, each time with another round of writes to the inverter. Tariff Manager now stays in charge mode for the whole cheap period, as a slot on the inverter does. The slot carries the target SOC, so the inverter itself stops charging there and holds it, with the house on the grid. The target only decides whether to start: charging doesn't start when the battery is already at it. Discharging works the same way at the minimum SOC. Found by pay4property in test mode. (#36)

## [2.0.0-beta.16] - 2026-10-17

### Fixed
- **A Tariff Manager discharge could export nothing, depending on the time of day.** The Virtual Slot Scheduler only drove timer slots 1 and 6 and slot 2's start time. Slots 3–5 kept your own start times, so for part of the day one of your own slots was in force instead, e.g. a slot from 09:00 without Sell ticked. The scheduler now drives all six slots: slot 1 holds the earlier boundary, and slots 2–6 all hold the later one with the same settings, so none of your own start times is left in the middle of the day. Your whole timer, all six slots, is still put back when there's nothing to do. A capture taken by beta.14 or beta.15, which only covered slots 1, 2 and 6, is completed from the inverter before the other slots are first changed. (#36)
- **Discharge and idle slots had Grid Charge ticked.** The scheduler ticked Grid Charge on every slot that wasn't idle, including discharge windows, so the inverter could top the battery up from the grid during a discharge. Grid Charge is now ticked only for charge windows. (#36)

## [2.0.0-beta.15] - 2026-10-17

### Security
- **A Home Assistant user with read-only access could control the inverter through the `sunsynk.*` actions.** Unless "Allow only administrators to use control actions" was ticked, the actions didn't check the user's permissions at all, so a read-only user could e.g. force a discharge. Every action now requires a user who may control that inverter's entities, as Home Assistant's own entity controls do; with "administrators only" ticked, the user must also be an administrator. Automations and scripts are unaffected. Until you update, ticking "Allow only administrators to use control actions" in Configure closes the gap.

## [2.0.0-beta.14] - 2026-10-16

### Added
- **Tariff Manager test mode.** A new tick in **Configure**. Tariff Manager decides as usual and shows it in Tariff Mode, its notifications (marked "[Test mode]") and the log, but sends nothing to the inverter, and the Virtual Slot Scheduler ignores it. It's a safe way to check what your settings would do. Suggested by pay4property. (#21)
- **Export Power Limit sensor**, one per inverter or parallel group. Its state is your maximum export power. Its attributes show the limit per inverter and the inverters' rated power and Max Sell Power added up across the group, e.g. Max Sell Power 12000 W × 2 = 24000 W. `max_sell_power_above_limit` flags an inverter setting that allows more than your limit, which matters because the limit doesn't cover solar export. (#21)
- **Configure shows how your maximum export power splits**, e.g. "2 inverter(s) per group, so the saved value is 5500 W per inverter". The field description now also says clearly that the limit covers selling slots set by the scheduler, not solar export.

### Changed
- **Clearer logs.** Every settings write is logged at debug level, with the values changed and the inverter it went to, including a slave's write going to its master. A failed write is logged as a warning naming the settings. Tariff Manager logs the current it actually set, also when it changes mid-charge. The Virtual Slot Scheduler logs when it takes the timer over, when it hands it back, and every change of what's in force.
- **A release is only published once its commit passes the tests and validation.** The Release workflow now runs both and waits for them.
- **The Maximum export power description now says** that with several independent inverters or parallel groups in one entry, each gets the limit of its own.
- **The Virtual Slot Scheduler leaves your own timer alone when it has nothing to do.** With no enabled window and no Tariff Manager override, it no longer takes over the timer slots. When an override ends, it puts your own timer back exactly as it was, so you can use Tariff Manager without virtual slots and without your schedule being left changed. While it stands by, it keeps track of changes you make to your timer in the Sunsynk app, so those are what come back. Suggested by pay4property. (#21)

### Fixed
- **Removing or disabling a virtual window left its own current on the inverter.** When the Virtual Slot Scheduler goes back to your own timer, it now puts the normal charge and discharge currents back too.
- **The Virtual Slot Scheduler could take the timer over before it knew your own settings**, e.g. when turned on right after a restart, before the first poll. It would then have had nothing to put back. It now waits until it has captured your timer, and never captures it from a failed refresh.
- **Switching the Virtual Slot Scheduler off while it was standing by** wrote your own timer and currents back, although they were already in place. It now writes nothing.
- **A parallel slave could be routed to a master whose plant wasn't known yet**, which might belong to another installation. Once plants are known, only a master on the slave's own plant counts.
- **After an error in Configure, the form showed the saved values again** instead of what you had just typed, so the field the error pointed at no longer showed the refused value. It now keeps what you typed (except the password).
- **A virtual slot with no weekdays** (only possible by editing storage) could stop the scheduler with an error. It's now ignored.
- **The dashboard's "Open sunsynk.clear_virtual_slot" link opened Set Virtual Slot instead** (and the other way round), because Home Assistant reopens the last action used. The links now say which action to choose and to check it before running. (#21)

## [2.0.0-beta.13] - 2026-10-15

### Added
- **Maximum export power.** A new optional field in **Configure**, e.g. your G99/G100 permitted export (11000 for 11 kW). Enter the total for the whole system: in a parallel group it is shared between the inverters, because each one applies its slot's sell power on its own, so two inverters at 12 kW could otherwise export 24 kW. A Tariff Manager discharge used to sell at the inverter's full rated power when the Virtual Slot Scheduler was on. It now sells at most each inverter's share, e.g. 5500 W each for 11 kW on a pair, and a virtual discharge window set higher is lowered to it. Idle and charge slots aren't affected, because they don't export. Your inverter's own export limit still applies on top. (#21)
- **`set_virtual_slot` refuses a discharge window's sell power above what the inverter may export**, i.e. above the lower of its rated power and its share of the maximum export power, and says which limit applies, e.g. "may sell at most 5500 W (inverter rated 12000 W, maximum export 11000 W shared by 2 inverters = 5500 W each)". A window saved earlier, or before you lowered the limit, is still lowered when it's written to the inverter.

### Fixed
- **A virtual discharge window could be set above what the inverter can deliver**, e.g. 20000 W on a 12 kW inverter. Its sell power is now lowered to the inverter's rated power.

## [2.0.0-beta.12] - 2026-10-15

### Added
- **Charge and discharge currents are checked against what your hardware will actually use.** A current above your inverter's own battery limit (its "Max A" charge or discharge setting) or the limit your lithium battery's BMS reports was accepted before and simply had no effect. **Configure** now refuses a Tariff Manager current above the lower of the two and shows the maximum, e.g. "no more than 100 A (inverter 120 A, battery BMS 100 A)". The `force_charge`, `force_discharge` and `set_virtual_slot` actions refuse it in the same way. A limit your inverter doesn't report, or reports as an implausible value, is never used to refuse a current.
- **Parallel systems no longer ask a shared battery for more than its BMS allows.** In a parallel group, each inverter applies the same current to the same battery bank, so the bank supplies that current times the number of inverters: two inverters at 240 A draw 480 A. When Tariff Manager or the Virtual Slot Scheduler sets a current, it now checks the total against what the battery's BMS allows at that moment. If the total is too high, it lowers the current to the BMS limit divided by the number of inverters and logs that it did. For example, two inverters set to charge at 120 A each, with a BMS limit of 192 A, are each set to 96 A. Configure and the actions use the same per-inverter share of a fixed BMS limit. (#21)
- **Every language now has a full translation of Configure and of the setting repair.** Several newer options, including "Allow only administrators to use control actions", were still in English in Afrikaans, Czech, German, Spanish, French, Polish, Russian and Chinese.

### Fixed
- **Tariff Manager could act on an old battery SOC.** When every inverter failed to refresh, Home Assistant kept the previous data, and a price change could then start charging or discharging based on a SOC that might be hours old. Tariff Manager now treats the SOC as unavailable until a refresh succeeds: it stops anything it's doing and makes no new decision.
- **A failed restore of the normal currents was forgotten.** When Tariff Manager was switched off or stopped and putting the normal charge or discharge current back failed (e.g. the Sunsynk cloud was down), it still marked that inverter as restored, so the tariff current could stay in place with no retry. It now keeps track of it and retries on the next refresh, even while switched off.
- **Changing the cheap-rate charge current or peak discharge current while it was active had no effect** until the next time charging or discharging started. The new current is now sent straight away.
- **Virtual Slot Scheduler could leave the last window running after it ended.** Between windows, the idle period was set to start at midnight. Once a window had ended, the next one was already armed on the inverter and took over immediately, e.g. a 10:00–12:00 discharge was still in force at 13:00 while the integration showed idle. The same happened to a Tariff Manager override that started between windows. The idle period now starts when the last window ended. A window more than a day away is only armed the day before, because the inverter's timer repeats every day.
- **When the Virtual Slot Scheduler took over from Tariff Manager, it didn't restore the window's own current.** Tariff Manager's normal current stayed in place, because the scheduler assumed the window's current was still set from before.
- **The Virtual Slot Scheduler could forget a failed restore of the normal currents** when it was switched off. The restore is now saved, survives a restart, and is retried on every refresh until it succeeds.
- **One inverter's failed write stopped Tariff Manager from handling the others.** With several inverters, an error on the first one ended the whole evaluation, so another inverter was never stopped when it should have been. Each inverter is now handled separately.
- **With two parallel groups in one integration entry, a slave's settings could be sent to the other group's master.** A slave is now only redirected to a master on the same plant. If more than one master qualifies, it isn't redirected.
- **A Sunsynk cloud timeout could leave a setting showing a value the inverter never accepted.** A timed-out confirmation read wasn't handled, so the written value kept overriding what the inverter actually reported until the next reload. Timeouts are now handled like any other connection error, and a write that can't be confirmed always gives way to the next refresh.

## [2.0.0-beta.11] - 2026-10-15

### Added
- **Option: allow only administrators to use control actions.** A new tick in **Configure**, off by default. When ticked, the Sunsynk actions (`force_charge`, `force_discharge`, `set_work_mode`, `set_virtual_slot`, `clear_virtual_slot`) can only be run by Home Assistant administrators; automations and scripts are unaffected. Only administrators can open Configure, so only they can change it.

### Fixed
- **Battery BMS SOC showed a made-up percentage** when the BMS wasn't communicating (BMS voltage 0). It now shows unknown.
- **A price of "inf" or "nan" counted as valid**, and "inf" would have read as above every threshold and started a discharge. It's now treated as invalid price data.
- **Login and settings writes no longer follow HTTP redirects**, so your credentials or access token can never be sent on to another address.
- **Tariff Manager couldn't discharge on some inverters: "Unsafe cached setting blocks write of dischargeCurrent".** Since beta.1 the integration refused to send a battery setting change if any *other* battery setting your inverter reports looked out of range. A parallel master reporting a charge current of 1040 therefore blocked every battery change, including Tariff Manager's discharge current, the moment the export price crossed the threshold. Other settings are your inverter's own values and are now sent back exactly as it reports them, as the Sunsynk portal does; only the value being changed is checked. Stable 1.9 was never affected. (#21)
- **Tariff Manager errors were logged as "Task exception was never retrieved"** on every refresh. They're now logged once per evaluation with a clear message. (#21)

### Thanks
- The administrator option, the BMS SOC, non-finite price and redirect fixes come from a security and safety review by **David ([@davidatjourney](https://github.com/davidatjourney))**. Thank you!

## [2.0.0-beta.10] - 2026-10-14

### Fixed
- **The Tariff Manager switch could flip itself back off.** When Tariff Manager changes state it updates several entities in turn (its switch, the Tariff Mode and Price Quality sensors, its number entities and the Virtual Slot Scheduler). If one of them failed to update, the rest were skipped, so the switch never showed "on" and Home Assistant flipped it back after two seconds. Each entity now updates independently, and any failure is logged with full details. (#21)

## [2.0.0-beta.9] - 2026-10-14

### Added
- **The Tariff Mode sensor now shows why Tariff Manager is or isn't acting.** A new `decisions` attribute lists, for each inverter, the import and export prices it last read, the battery SOC, and a plain-English reason for charging and for discharging, e.g. "export price 0.1 is below the expensive threshold 0.16", "battery 18% is not above the minimum 20%" or "not set up (needs an expensive threshold and a peak discharge current)". (#21)

### Fixed
- **Tariff Manager fields couldn't be cleared in Configure.** Clearing a threshold, current or active hour and saving put the old value straight back, so cleared active hours came back as "0 to 0". Cleared fields now stay cleared. (#21)

## [2.0.0-beta.8] - 2026-10-13

### Fixed
- **Grid Charge slot switches on Deye inverters: the actual cause.** The [Predbat](https://github.com/springfall2008/batpred) project confirmed on a live inverter that the Sunsynk API silently ignores a slot's Grid Charge change, while saving everything else in the same request, if (a) the request is missing the slot's *Sell* flag, or (b) a flag is sent in a different form from how the inverter reports it. Deye inverters call the Sell flag `sellTime{n}En` and report flags as the text `"true"`/`"false"`. In 1.9.4 this integration renamed that field to the `sellTime{n}on` used by some Sunsynk models, so it dropped out of Deye requests, and it sent changed flags as `1`. Both are fixed: the right Sell flag is always included, and every changed value is sent exactly the way your inverter reports it. Checked against real Deye and Sunsynk diagnostics. (#26, #32, #33)
- **A change made in the Sunsynk app could be undone by the next change from Home Assistant.** Each write was built from settings read at the last refresh (up to a minute old), so it sent back whatever the app had changed since. Every write now reads the current settings first, as Predbat and other Sunsynk clients do, keeping any change from Home Assistant that the inverter hasn't confirmed yet.
- **Sell Time N switches work on Deye inverters again.** beta.7 hid them as "not supported", but Deye inverters do have them, under a different name. They now use whichever name your inverter has, and so does the Virtual Slot Scheduler. (#33)
- **Tariff Manager never activated with active hours "0 to 0".** Equal start and end hours were treated as an empty window instead of the whole day, so Tariff Manager stayed idle whatever the price. They now mean all day. (#21)
- **The Tariff Mode and Tariff Price Quality sensors showed an old price** in their attributes until the mode changed. They now refresh every time prices are checked. (#21)

## [2.0.0-beta.7] - 2026-10-12

### Changed
- **Tariff Manager no longer stops because a price "looks stale" unless you ask it to.** The age check kept blocking people whose price legitimately stays the same for a long time: fixed export rates, long off-peak blocks, prices set by a helper. It's now off by default. A price that's unavailable or not a number still stops Tariff Manager as before. To turn the age check back on, tick **Stop when price data looks out of date** in **Configure**; your *Price data max age* setting is kept and used again. (#16, #21, #30)

### Fixed
- **Switches, numbers and text fields for settings your inverter doesn't have are now unavailable** instead of looking usable and then reverting. For example, some Deye inverters have no per-slot *Sell* flag, so their *Sell Time N* switches could never stick. The Virtual Slot Scheduler also no longer writes that flag on those inverters. (#33)
- **The Sunsynk Power Flow Card logged "Cannot read properties of undefined (reading 'push')"** on pages where Home Assistant hadn't set up its custom card list yet, such as in the Android app. (#33)

## [2.0.0-beta.6] - 2026-10-12

### Fixed
- **Grid Charge slot switches still didn't stick on some Deye inverters.** Adding the slot's generator flag in beta.5 wasn't enough. Every change to the System Mode timer page (a slot's Grid Charge, limit, start time or power, or a global setting like work mode) now sends the whole page in one request, exactly the fields the Sunsynk portal sends, with every other value exactly as the inverter reported it. The Sunsynk portal, the openHAB binding and the solarsynkv3 add-on all do this, and so did 1.8. The two problems that led 1.9.5 to send only part of the page are now fixed at the source: a value you just changed is held until the inverter confirms it, so an old start time is never sent back, and parallel systems are recognised, so only the master is written. (#26, #31, #32)
- **The Virtual Slot Scheduler now sends its timer changes as one request** instead of up to three, keeping write volume down on parallel systems. (#21)
- **On a parallel system upgraded from beta.1–beta.3, beta.4 could remove the Virtual Slot Scheduler entities your dashboard used.** When merging the slave's separate schedule into the master's, it decided which entities to keep from a naming hint that doesn't apply when the slave and master are differently named devices. It now keeps the entity carried over from 1.9.x, which Home Assistant records. If your dashboard already shows the scheduler entities as unavailable, rename the master's *Virtual Slot Scheduler* switch and sensor to the entity IDs your dashboard uses. (#21)
- **A Tariff Manager price set by an `input_number` helper was marked "stale"** once *Price data max age* had passed since its last change. A helper is set by hand and only reports when it changes, so it's no longer age-checked. (#21)

## [2.0.0-beta.5] - 2026-10-11

### Fixed
- **Grid Charge slot switches didn't stick on some Deye inverters.** Turning a slot's Grid Charge on or off from Home Assistant was accepted by the Sunsynk cloud but never applied, while the same change from the Sunsynk app worked. Since 1.9.5 a slot change no longer included that slot's generator-charge flag, which these inverters appear to need alongside the grid one. It's now sent with every change to the slot. Nothing from other slots is sent, so the parallel-system fix from #21 is unaffected. (#26, #31, #32)
- **An incomplete integration entry stopped Home Assistant setting it up with a bare `KeyError: 'api_server'`.** It now fails with a clear message naming the missing details and telling you to delete that entry. In the reported case it sat next to a second, working entry for the same account, so the integration deliberately doesn't guess the missing values: that would have had both entries controlling the same inverter. (#32)

## [2.0.0-beta.4] - 2026-10-11

### Fixed
- **Parallel (master/slave) systems were never recognised.** The integration looked for the parallel role in the wrong part of the Sunsynk data, so since 1.9.2 settings meant for the master were still sent to the slave, and in 2.0 each inverter got its own Virtual Slot Scheduler: the VSS page showed one schedule while `sunsynk.set_virtual_slot` and `sunsynk.clear_virtual_slot` changed the other. The role is now read from where the API actually reports it, verified against a real parallel system's diagnostics. On the first start after updating, the slave's separate schedule is merged into the master's, keeping your existing entity IDs. Check your virtual slots afterwards: the master's schedule is the one that's kept. (#21)
- **Tariff Manager marked a price "stale" while it was still being reported.** The price's age was measured from its last *change*, so a rate that stays the same for hours (e.g. an off-peak block) looked out of date even though its integration kept reporting it. On Home Assistant 2024.3 and later the age is now measured from the sensor's last *report*. Older versions still use the last change. (#21)

## [2.0.0-beta.3] - 2026-10-10

### Fixed
- **Battery and timer settings couldn't be changed on inverters reporting Battery Mode -1.** Several Sunsynk/Deye inverters with a lithium BMS report `battMode` as -1. 2.0 re-checks every value it resends alongside a change, and -1 failed that check, so charge/discharge currents, System Work Mode, Grid Charge slots and the Virtual Slot Scheduler were all blocked with "Unsafe cached setting blocks write". A value the inverter itself reports for a setting it doesn't use is now resent unchanged; it still can't be written on purpose. (#32)
- **On a parallel system, a second Virtual Slot Scheduler could drive the master.** If the master/slave group wasn't recognised the moment Home Assistant started, the slave got its own scheduler, which then also drove the master's timer slots, and the VSS page could show a different schedule from the one `sunsynk.set_virtual_slot` updated. A scheduler that doesn't own its inverter's writes now never touches the timer. If the group is recognised later, the integration reloads once so the two schedules merge, keeping the entity IDs your dashboards and automations already use. Diagnostics now include a *virtual_slots* section showing which scheduler drives which inverter. (#21)
- **The Virtual Slot Scheduler switch turned itself off after a restart**, leaving timer slots 1 and 6 as the scheduler had last set them with nothing managing them. The switch now remembers its state. The timer settings it captured before taking over are remembered too, so switching it off after a restart still puts back *your* original timer.

### Security
- **Diagnostics still exposed some personal details.** Checked against a real diagnostics file: the plant's latitude/longitude, the dongle serial number, and the plant owner's and installer's names and contact details were not hidden. They are now. The same fix is in 1.9.9 for the stable version.

## [2.0.0-beta.2] - 2026-10-10

### Fixed
- **Most "write verification failed" warnings were false alarms, and changing a slot's limit could undo its new start time.** Some inverters take well over 2 seconds to report a newly written value, so the integration flagged writes as rejected while they were actually applying. In 2.0.0-beta.1 that early read-back also replaced the new value with the old one, so the next change to the same timer slot (e.g. its limit after its start time) sent the old start time back to the inverter. Writes now return as soon as the Sunsynk cloud accepts them and are confirmed in the background for up to 60 seconds. Until then, the integration keeps the value you sent, so later changes to the same slot can't undo it. A Repair is only raised if the value still hasn't arrived after 60 seconds. (#31)
- **Tariff Manager settings were lost on restart.** Thresholds, currents and SOC limits changed on the device page, and the Tariff Manager switch, were only kept in memory. They are now saved permanently, in the same place as the values in **Configure**, without reloading the integration. (#30)
- **Tariff Manager never discharged with a fixed export rate.** A separate export price sensor was checked against *Price data max age*, but export rates are often fixed for months (e.g. Octopus Outgoing) so the sensor rarely updates. The export price was marked "stale" and every discharge was blocked. Only the import price is age-checked now; an unavailable or non-numeric export price still blocks discharging. (#16, #21, #30)

## [2.0.0-beta.1] - 2026-10-01

### The road to 2.0 beta

This project started with a deliberately small goal: make Sunsynk and Deye
inverters feel like native Home Assistant devices. Monitoring and basic control
should not require a separate add-on, virtual machine, container, MQTT bridge or
second automation system running beside Home Assistant.

The cloud API made the first version look deceptively simple. Real installations
quickly showed how much complexity sits behind that API: different inverter and
firmware behaviour, delayed command propagation, settings that must be submitted
as groups, tariff automations, timer boundaries across midnight, independent
multi-inverter plants, and parallel master/slave systems where a slave may accept
a write before silently reverting to its master's value. Supporting those systems
safely turned a small integration into a much larger reliability project.

The work below is the result of a full code, lifecycle and operational-safety
audit. It was collected into one substantial 2.0 beta rather than shipped as
a chain of small releases.

### Before you upgrade

This is a **beta**. In HACS, enable *Show beta versions* for this integration
to see it.

- **The auto-maintained dashboard is now opt-in.** If you rely on it, enable
  **Create and maintain Sunsynk dashboard** in the integration's options after
  upgrading.
- **Take a Home Assistant backup first.** Virtual slot schedules move to
  per-inverter storage and the old shared record is removed, and virtual slot
  entities get new unique IDs (entity IDs are preserved). Going back to 1.9.x
  without restoring a backup leaves an empty virtual slot schedule and may
  create duplicate virtual slot entities with a `_2` suffix.

### Added

- **Per-inverter automation state.** Tariff Manager state and Virtual Slot
  Scheduler storage are isolated per physical write target. Independent
  inverters no longer suppress or overwrite one another, while a parallel
  slave is deliberately mapped to its master.
- **Lifecycle restoration.** Tariff automation restores normal charge/discharge
  currents, and Virtual Slot Scheduler snapshots and restores the inverter's
  timer configuration when stopped or unloaded. Reload and shutdown wait for
  cleanup instead of leaving automation-owned settings active on the inverter.
- **Exact virtual-slot boundary callbacks.** The scheduler now registers a Home
  Assistant point-in-time callback for the next boundary instead of depending
  solely on coordinator refreshes. Callbacks are cancelled and rescheduled
  safely across changes, disable, reload and unload.
- **Serialized and coalesced writes.** All setting writers share a per-inverter
  lock and same-event-loop writes are coalesced. Every physical timer slot is
  submitted as one grouped payload, eliminating the previous burst of separate
  writes and the stale-cache races between them.
- **Comprehensive regression coverage.** The suite now exercises setup/unload,
  restoration, two independent inverters, parallel master/slave routing,
  concurrent writes, coalescing, API/authentication failures, services and every
  Home Assistant platform. Coverage is enforced at 100% in CI.
- **Minimum and current Home Assistant CI lanes.** Tests run against the declared
  minimum environment on Python 3.11 as well as the current environment, with
  separately pinned test requirements.

### Changed

- **Automatic dashboard creation is now opt-in and disabled by default.** The integration no longer creates or updates a Lovelace dashboard, or registers the Power Flow Card as a Lovelace resource, unless you enable **Create and maintain Sunsynk dashboard** in the integration's options. Previously both happened on every startup. **If you upgrade and want the auto-maintained dashboard to keep updating, enable that option.** Dashboards and resources already created stay in place; they are just no longer touched.
- **Virtual slot schedules are now per inverter.** Each physical write target (an independent inverter, or a parallel group's master) has its own schedule, so the `serial` field of `sunsynk.set_virtual_slot` / `sunsynk.clear_virtual_slot` applies only to that inverter. A parallel slave's serial selects its master's schedule. On upgrade, the previous shared schedule is copied to every existing inverter, then the shared record is removed, so an inverter added later starts with an empty schedule. Existing virtual slot entity IDs are preserved.
- **Service targeting is now unambiguous.** Services resolve one explicit serial
  to one independent inverter or parallel-group master instead of treating a
  serial only as a way to find a config entry and then modifying every inverter.
- **Write verification is authoritative and adaptive.** A write is not reported
  as successful until fresh API data matches it. Read-back errors and mismatches
  propagate to the caller, authoritative values replace assumptions in the
  coordinator cache, and Repairs are created or cleared accordingly. Verification
  normally begins after 250 ms and only retries up to the existing two-second
  propagation window when required, rather than blocking every write for a fixed
  two seconds.
- **Tariff decisions are mutually exclusive and fail closed.** Charge and
  discharge can no longer become active at the same time. Missing, malformed or
  unavailable battery SOC stops automatic action instead of being interpreted as
  0% and starting a charge.
- **Plant pricing writes preserve the plant tariff.** Manual Energy Price can
  update only an existing, single Constant Price entry. Time-of-Use, live-price
  and multi-entry configurations are rejected unchanged; currency, investment
  and other plant metadata are read fresh and preserved.
- **Solar forecast uses the forecast location's timezone.** Open-Meteo's UTC
  offset now determines the current hour and local forecast date, rather than
  assuming the Home Assistant installation and forecast coordinates share a
  timezone.
- **Solar calibration survives restarts.** The tracked date and latest modeled
  and actual production samples are persisted and validated, avoiding a lost
  daily sample when Home Assistant restarts around midnight.
- **Battery health is labelled as an estimate.** The entity display name is now
  **Battery SOH Estimate**, reflecting that it is derived from lifetime energy
  counters rather than a certified BMS health value.
- **All translation catalogs have key parity.** New options, Repairs and entity
  labels are present in every bundled locale, with English fallback text where a
  translated string is not yet available.

### Fixed

- **Reload/unload could leave high currents or active timer slots behind.** The
  integration now restores the captured pre-automation state even when setup is
  interrupted or Home Assistant reloads the config entry.
- **Only the first independent inverter could receive a tariff action.** Runtime
  flags are now maintained per write target, so a state change on one inverter
  cannot make another inverter look already handled.
- **Complete endpoint outages looked like successful empty refreshes.** Partial
  data remains usable, but a poll where every endpoint fails now raises an update
  failure and retains the previous coordinator data. HTTP 401 invalidates the
  cached token and is propagated instead of being hidden as `{}`.
- **Write acknowledgements could be false positives.** Success parsing accepts
  only the API's known exact success responses; messages such as `unsuccessful`
  and `not success` are rejected.
- **Time read-back formatting could false-positive as a mismatch.** Verification
  normalizes valid `H:MM` and `HH:MM` values to minutes, so an API echo of `0:30`
  correctly matches a submitted `00:30`.
- **Unsafe values could reach the inverter.** Every writable current, SOC,
  percentage, power, enum, boolean, timer and plant-price value is validated and
  normalized before queueing. Options-flow currents use the same limits as
  entities and services; price and forecast values reject NaN/infinity; latitude,
  longitude, panel size and performance ratio enforce their documented ranges.
- **A corrupt cached sibling could hitchhike in a grouped payload.** Values copied
  from the settings cache are revalidated before the group is sent, while a valid
  caller replacement can repair its own bad cached value.
- **Diagnostics exposed identifying and location data.** Usernames, inverter and
  plant identifiers, coordinates, entity IDs and sensitive raw API fields are now
  recursively redacted without mutating live coordinator data.
- **The house ran from the grid while Virtual Slot Scheduler was waiting for a slot to start.** Any window that wasn't a discharge (the idle time before a slot, or a charge window) was written to its physical slot with a power of 0 W. With Use Timer on and Sell unticked, that power is also the most the inverter may draw from the battery for the house, so the whole house load went to the grid. For example, creating a 20:30 discharge slot at 20:10 meant grid import until 20:30. Idle and charge windows now use the inverter's rated power (`ratePower`), and discharge windows keep the virtual slot's own `sell_power`. (#21)
- **Virtual Slot Scheduler could write a 0% SOC to a time slot.** A window without a target SOC was written as `cap` = 0, relying on the inverter's own protection to stop the discharge. Every SOC the scheduler writes, including a virtual slot's own `target_soc`, is now raised to at least the inverter's **Battery Low Capacity** (`batteryLowCap`), or 20% until that setting has been read. (#21)
- **Services were not registered if the Power Flow Card's static file could not be served.** A failure registering the card's static path ended setup early, so `sunsynk.force_charge`, `sunsynk.set_virtual_slot` and the other services were missing. The failure is now only logged.

### Security and maintenance

- GitHub Actions are pinned to immutable commit SHAs instead of floating branches
  or major-version tags.
- Test dependencies are pinned, cache/build artifacts are ignored, and the test
  workflow enforces 100% coverage.
- Ruff now targets Python 3.11 and checks formatting, imports, modernization,
  Bugbear, simplifications, blind exceptions, timezone safety and unused `noqa`
  directives in CI.
- Protocol-required MD5 calls are explicitly marked `usedforsecurity=False`.
  The unusual Inteless authentication rule is documented and regression-tested:
  its payload uses `source=elinter`, while the token signature must retain the
  literal `source=sunsynk` expected by the upstream API.
- Removed the bundled frontend's reference to a source-map file that is not
  distributed with the integration.

### Thank you

Thank you to everyone who opened an issue, shared diagnostics, tested a beta,
compared Home Assistant with the Sunsynk portal, or patiently repeated a test on
real hardware. Many of the hardest bugs could not be reproduced on a simple
single-inverter development setup. Reports from independent multi-inverter and
especially parallel master/slave installations exposed API and firmware behaviour
that is neither documented nor obvious from a successful HTTP response. Those
reports did not merely fix isolated bugs; they shaped the safer write pipeline,
restoration lifecycle and per-inverter architecture delivered in this 2.0 beta.

## [1.9.13] - 2026-10-16

### Fixed
- **Tariff Manager safety:** no decision on a missing or old battery SOC, failed restores of the normal currents are retried, and a changed current applies straight away. Same fixes as 2.0.0-beta.12.
- **Virtual Slot Scheduler could leave the last window running after it ended**, and **a slave could be routed to another parallel group's master**. Same fixes as 2.0.0-beta.12.
- **A Sunsynk cloud timeout could leave a setting showing a value the inverter never accepted.** Same fix as 2.0.0-beta.12.
- **Battery BMS SOC with no BMS voltage, "inf"/"nan" prices, and HTTP redirects on login and writes.** Same fixes as 2.0.0-beta.11. Thanks to @davidatjourney.

## [1.9.12] - 2026-10-14

### Fixed
- **Tariff Manager fields couldn't be cleared in Configure**; cleared active hours came back as "0 to 0". Same fix as 2.0.0-beta.9. (#21)

## [1.9.11] - 2026-10-13

### Fixed
- **Settings writes the inverter actually applies.** Backport of the 2.0.0-beta.1 to beta.8 write fixes to the stable line: whole System Mode page per write, each slot's Sell flag under the name the inverter reports (`sellTime{n}En` on Deye-built inverters), values sent exactly as the inverter reports them, a fresh read before every write, Sell Time switches on Deye-built inverters, parallel systems recognised, Virtual Slot Scheduler idle windows at rated power and never below Battery Low Capacity, Tariff Manager hours "0 to 0" meaning all day, and the Power Flow Card `push` error. (#21, #26, #31, #32, #33)

## [1.9.10] - 2026-10-11

### Fixed
- **False "write verification failed" warnings, and a slot's start time going back after changing its limit.** Backport of the 2.0.0-beta.2 fix: writes are confirmed in the background for up to 60 seconds, and the value you sent is kept until the inverter confirms it. (#31)

## [1.9.9] - 2026-10-10

### Security
- **Diagnostics files no longer contain your personal details.** Backport of the 2.0 redaction to the stable version: login, owner and installer details, serial numbers, plant address and coordinates, and price sensor entity IDs are hidden automatically.

## [1.9.8] - 2026-10-01

### Changed
- **Relaxed the 30-minute-only slot-time restriction added in 1.9.1.** That restriction assumed, from one reporter's Sunsynk portal test, that the inverter hard-requires `:00`/`:30` minute values. A later reporter's real-world use of [Predbat](https://github.com/springfall2008/batpred) — writing 5-minute-granularity schedules straight through this same settings-write API — showed their inverter accepts and executes non-`:00`/`:30` values without issue. The portal's dropdown turned out to be a UI convention, not a universal API/firmware constraint, and apparently varies by inverter model/firmware. `sunsynk.set_virtual_slot` and the manual "Time Slot N Start" text entities now only validate a well-formed `HH:MM`; if your inverter silently ignores a non-`:00`/`:30` value, stick to `:00`/`:30` on yours. (#25)

## [1.9.7] - 2026-10-01

### Fixed
- **A Tariff Manager price-override discharge exported 0 W.** The max export power handed to the active physical slot for a price-driven discharge was hardcoded to 0 — Tariff Manager has no config option of its own for this, unlike a virtual slot's explicit `sell_power` field. A price override could correctly raise `dischargeCurrent` and still export nothing, since the slot's own `sellTime{n}Pac` silently capped it at zero. Now uses the inverter's own rated power (`ratePower`) so only Tariff Manager's `dischargeCurrent` actually limits export, falling back to a generous default if rated power isn't known yet. (#21)
- **hassfest validation failure.** Home Assistant's hassfest tooling now rejects listing `cryptography`/`aiohttp` in a custom integration's manifest `requirements`, since both are already Home Assistant core dependencies. Removed — no functional change.

## [1.9.6] - 2026-09-30

### Fixed
- **Virtual Slot Scheduler's slot 1 had no controlled end time.** Slot 1's *end* isn't a field the scheduler ever wrote — Sunsynk derives it from the next physical slot's (2) own start time, even while slot 2 stays disabled. A reporter found slot 1 silently bounded by slot 2's leftover, pre-VSS start time instead of the intended end of the active virtual window — visible on the inverter screen as e.g. "21:00 - 05:30", where 05:30 was slot 2's original, untouched grid-charge start. Slot 2's start is now pinned to slot 6's start on every relevant write — always a validly-ordered value, since slot 1 is defined as whichever boundary has the earlier time-of-day — giving slot 1 a real, controlled end for the first time. Slot 2 itself stays disabled; only its start time moves. (#21)

## [1.9.5] - 2026-09-29

### Fixed
- **A single System Mode Timer slot write resent every other slot's data.** `async_write_setting()` groups certain settings so a write doesn't accidentally blank out unrelated fields the API expects in the same payload — but the group used for slot fields (`time{n}on`/`cap{n}`/`sellTime{n}Pac`/`sellTime{n}`/`sellTime{n}on`) was all 36 System Mode Timer keys across all 6 slots plus unrelated global settings, not just the slot actually being written. On a parallel/dual-inverter system, a reporter found this multiplied write volume enough to visibly desync master/slave (inverter screen flickering many times per write, with intermittent Repairs on both units), and reintroduced a stale, chronologically-earlier start time from an untouched slot into the payload — which silently corrupted the targeted slot's own end time, since Sunsynk derives slot N's end from slot N+1's start. Each slot's write now only carries its own 6 fields (adding `sellTime{n}Volt`, previously only reachable via the 36-key group); non-slot settings (`solarSell`, `pvMaxLimit`, `sysWorkMode`, weekday flags, generator timer on-flags) keep the original full-group behavior, since only the slot-indexed fields were ever implicated. (#21)

## [1.9.4] - 2026-09-27

### Fixed
- **The Sell permission checkbox still didn't reliably stick.** The per-slot "Sell" permission field was being written as `sellTime{n}En`, guessed from naming convention — that field doesn't exist in the real API. A reporter's own raw settings dump showed the correct field is `sellTime{n}on`: `sellTime3on` was `true` for the one slot they'd manually ticked "Sell" on via the inverter screen. Separately, `SYSTEM_MODE_SETTING_KEYS` still listed the old `sellTime{n}En` name, so even once the field name was corrected, `async_write_setting()` didn't recognize it as part of the System Mode Timer settings group and sent it as a lone single-field payload instead of the full group payload the API appears to expect — likely why it kept silently failing to persist even at the point the correct field was already being written. Both are now fixed. (#21)

## [1.9.3] - 2026-09-26

### Fixed
- **Settings were still written to a parallel group's master twice per tick.** 1.9.2 redirected a parallel slave's writes to its master, but Tariff Manager and Virtual Slot Scheduler both still looped over every *configured* serial and called the write once per serial — for a parallel group, that meant the master received the same setting twice in quick succession (once via the redirected slave call, once via its own native call). A reporter confirmed a second write landing right behind the first was, on its own, enough to make the master intermittently reject/revert one of them — fresh Repairs kept appearing against the master's own serial even after 1.9.2. Added `write_target_serials` (the configured serials with a parallel slave already collapsed into its master) and switched both features' write loops to use it, so each setting is now written exactly once per distinct physical target. Non-parallel accounts are unaffected. (#21)

## [1.9.2] - 2026-09-25

### Fixed
- **Parallel-slave write redirect widened to all settings, not just battery.** 1.9.1's fix for chargeCurrent/dischargeCurrent corruption on parallel setups was scoped to battery settings only — a diagnostics dump had made System Mode Timer slot settings (`time1on`, `sellTime1`, `cap1`, etc.) look like they verified fine independently on each unit. That turned out to be a timing artifact: the reporter went on to write a slot's start time directly to the slave *on the Sunsynk portal itself*, bypassing this integration, and watched the portal silently revert it to the master's value 10-15 seconds later — well outside this integration's 2-second write-verification window, so the confirmation read caught the still-fresh (but doomed) value and reported success. Since the slave never independently keeps any setting, the redirect now applies to every setting written to a parallel slave, not a specific category. As a side effect this also roughly halves per-tick API writes on parallel accounts. (#21)

## [1.9.1] - 2026-09-21

Six fixes, all traced back to a single real-world report (#21) from a parallel/multi-inverter account running Sunsynk's Zero-Export/Limited to Home work mode — plus one from a single-inverter account (#20). Bundled together since several only became visible once the earlier ones in the chain were fixed.

### Fixed
- **Virtual Slot Scheduler never enabled the per-slot "Sell" permission.** Sunsynk added a per-slot checkbox (`sellTime{n}En`) specifically so battery discharge can be sold to the grid while System Work Mode is Zero-Export/Limited to Home, without switching to Selling First. This integration already exposed it as a manual switch (since 1.8.0), but the scheduler's own slot-arming logic never wrote it — a virtual discharge slot (or a live Tariff Manager price override) could have `on`, `cap` and sell-power all correctly set and still export nothing. Now enabled automatically whenever a discharge slot is armed, disabled otherwise. (#21)
- **Sunsynk's 30-minute time-slot boundary wasn't validated.** Confirmed via a reporter's own test — the Sunsynk portal silently rejected a slot saved with 22:45/22:50, but accepted 20:30/21:00 without complaint; a non-half-hour value isn't rejected by the settings-write API either, it's just silently ignored by the inverter. Neither `sunsynk.set_virtual_slot` nor the manual "Time Slot N Start" text entities enforced this. Both now require `:00`/`:30` minute values, rejected immediately with a clear error instead of silently never taking effect. (#21)
- **Settings-write API responses weren't always exactly `"Success"`.** A parallel-inverter setup's API acknowledged a write with `"send command success:{}"` instead — a message that means success but isn't an exact string match. The strict equality check treated it as a failure even though the write had gone through. Now matches "success" case-insensitively. (#21)
- **Tariff Manager's "Price Quality" sensor only ever showed the import side's status.** With a separate export price entity configured, a stale/missing export sensor could silently block discharging with nothing in the visible state pointing at why. Now falls back to reporting the export side whenever import is fine but export isn't. (#21)
- **Write-verification could false-positive on relayed/parallel setups.** The fail-safe added in 1.9.0 read a setting straight back with no delay at all; on an account where the write is relayed to the physical inverter asynchronously, that consistently raced ahead of the propagation and raised a false Repair for practically every write. Added a short wait before the read-back. (#21)
- **Battery settings written to a parallel slave could corrupt the group.** chargeCurrent/dischargeCurrent written independently to both units in a parallel pair could race against the portal's own master→slave propagation and corrupt both. Battery settings targeting a parallel slave now redirect to that group's master instead (detected via the already-polled `equipMode` field); non-parallel accounts are unaffected. Time-slot settings are untouched — those verified correctly written per-unit independently. (#21)
- **"Cannot read plant info" when setting Manual Energy Price, on a single-inverter account.** The plant-info endpoint requires a `lan` query parameter that wasn't being sent — the API rejects the call outright rather than 404ing. Added `lan=en`, matching the other endpoints that already need it. (#20)

## [1.9.0] - 2026-09-02

### Added
- **Generator Power / Micro Inverter Power sensors.** None of the endpoints previously polled (pv/grid/battery/load/output) report generator port power — only the `/flow` endpoint does (the same data behind the flow diagram in the Sunsynk Connect app). Adds two diagnostic sensors sourced from it, each created only when the inverter actually reports something wired into that port: **Generator Power** (`genPower`, shown when the API reports `existsGen`) and **Micro Inverter Power** (`minPower`, shown when `existsMin`). A micro-inverter physically wired into the generator port (e.g. to keep it working in Island mode) can surface under either flag depending on the setup, so both are exposed independently rather than guessing. Confirmed against a real SolarEdge-as-microinverter setup — tracked the vendor app's own reading correctly. Wired into the auto-generated dashboard's new Diagnostics → Generator card. (#17)
- **Separate import/export price entities for Tariff Manager.** Adds an optional **Export/Sell Price Sensor** alongside the existing price sensor (now labelled **Import/Buy Price Sensor**). Leave it blank and nothing changes — both charging and discharging keep reading the one sensor, exactly as before. Set it, and cheap-rate charging keeps reading the import price while expensive-rate discharging switches to the export price — for tariffs like Octopus Intelligent Go (import) + Outgoing (export) where the two rates aren't linked. Price-data quality (availability/staleness) is now tracked per entity rather than shared, so a problem with one sensor only pauses its own side. (#16)
- **Write verification fail-safe.** Every settings write is now followed by a fresh, uncached read of that setting straight from the Sunsynk API. If the value read back doesn't match what was sent, a Home Assistant Repair is raised under Settings → System → Repairs naming the inverter and setting, clearing automatically once a later write to that setting is confirmed. Guards against the inverter or dongle silently rejecting a write (out of range, briefly offline) that previously looked successful from a 200 response alone.

## [1.8.3] - 2026-09-02

### Fixed
- **PV Max Limit reported as % instead of W.** `pvMaxLimit` is a Watt value — confirmed against a real portal screenshot showing "Inverter Power Limiter (500~7500W)" for the same setting a user's HA instance displayed as "4000 %". Was originally modeled as a 0-100 percentage; fixed to match Zero Export Power and Solar Max Sell Power, the other two power-limit settings. (#19)
- **"No plant found for inverter X" when writing Manual Energy Price.** `async_write_plant_price()` only ever read the plant ID from the coordinator's cache, which can be empty even on an account that genuinely has a plant linked (e.g. right after a reload, or if a previous background refresh's best-effort plant fetch failed) — with no way to recover short of a refresh happening to succeed on its own. Now falls back to one fresh inverter-info fetch before concluding there's truly no plant, mirroring the same cache-miss-refetch pattern already used for regular settings writes. (#20)

### Changed
- **Battery SOH formula reverted to lifetime charge/discharge totals.** `correctCap / capacity` (introduced in 1.8.0 as the safer alternative after the counter-based formula produced 139% on one diagnostics dump) has now been checked against two real accounts and read a flat 100% on both — including a battery a few years old with genuine, measurable wear. It never reflected actual degradation on any hardware seen so far. Reverted to `100 - (etotalChg - etotalDischg) / etotalDischg * 100`, which correctly read ~95% on that same aged battery — but the output is now clamped to 0-100%, so an account whose counters have drifted (the original 139% case) shows a plausible number instead of a meaningless one, rather than reintroducing that failure mode unguarded. Still a best-effort estimate, not a certified BMS health reading. (#14)

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
