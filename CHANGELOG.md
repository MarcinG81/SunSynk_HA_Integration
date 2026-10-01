# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
