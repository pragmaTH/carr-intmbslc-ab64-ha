# Carrier-Toshiba AB64 (Modbus) for Home Assistant

[![Validate](https://github.com/pragmaTH/carr-intmbslc-ab64-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/pragmaTH/carr-intmbslc-ab64-ha/actions/workflows/validate.yml)
[![Test](https://github.com/pragmaTH/carr-intmbslc-ab64-ha/actions/workflows/test.yml/badge.svg)](https://github.com/pragmaTH/carr-intmbslc-ab64-ha/actions/workflows/test.yml)

Home Assistant custom integration for Carrier-Toshiba air conditioners fitted with an **AB64** interface box (Intesis INMBSTOS001R000). The AB64 translates the AC's proprietary AB Bus protocol to Modbus RTU; a separate Modbus TCP↔RTU gateway (e.g. an Elfin EW11) puts that on your network for Home Assistant to talk to.

```
AC indoor unit → AB Bus → AB64 box → RS-485 (Modbus RTU) → TCP↔RTU gateway → Home Assistant
```

For the full hardware bring-up story, register map, and error code table behind this integration, see [`references/ac-modbus-ab64-reference.md`](references/ac-modbus-ab64-reference.md).

## Supported hardware / prerequisites

- A Carrier-Toshiba indoor unit wired to an **AB64** box (Intesis INMBSTOS001R000).
- A Modbus **TCP↔RTU gateway** bridging the AB64's RS-485 bus to your network (e.g. Elfin EW11).
- The gateway's serial settings must match the AB64's DIP-switch baud rate (SW3) — `9600 8N2` in the reference bring-up.
- Home Assistant **2025.10.1** or newer.

## Installation (HACS custom repository)

This integration is **not** in the default HACS store yet — install it as a custom repository.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pragmaTH&repository=carr-intmbslc-ab64-ha&category=integration)

The button above only works if you've already set up [My Home Assistant](https://www.home-assistant.io/integrations/my/) in this browser and already have HACS installed — if it doesn't do anything for you, or you'd rather see each step, follow the manual steps below (same result):

1. HACS → **⋮** (top right) → **Custom repositories**.
2. Repository: `https://github.com/pragmaTH/carr-intmbslc-ab64-ha`, Category: **Integration**.
3. Find "Carrier-Toshiba AB64 (Modbus)" in HACS and **Download**.
4. **Restart Home Assistant.**
5. Settings → Devices & Services → **Add Integration** → search "Carrier-Toshiba AB64" — or use this button (same "already set up My Home Assistant" caveat as above):

   [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=carr_ab64)

## Manual installation (without HACS)

Don't have HACS (e.g. a minimal Home Assistant Container/Core setup)? You can install this integration by hand instead:

1. Get the code — there's no tagged release yet, so grab it straight from the `main` branch: either `git clone https://github.com/pragmaTH/carr-intmbslc-ab64-ha` or use GitHub's **Code → Download ZIP** on the repo page and extract it.
2. Copy the **`custom_components/carr_ab64/` folder itself** (not the whole repo checkout) into your Home Assistant config directory, so you end up with `<config>/custom_components/carr_ab64/manifest.json` — **not** `<config>/custom_components/carr-intmbslc-ab64-ha/custom_components/carr_ab64/manifest.json` or similar. Copying the whole downloaded folder in one level too high is the single most common mistake here — if the integration doesn't show up after a restart, this is the first thing to check. Create `<config>/custom_components/` first if it doesn't exist yet.
3. **Restart Home Assistant.**
4. Settings → Devices & Services → **Add Integration** → search "Carrier-Toshiba AB64" (same as the HACS path above).

**Trade-off**: this bypasses HACS entirely, so you don't get update notifications or one-click updates — when a new version is released, you'll need to repeat steps 1–3 yourself, overwriting the old `custom_components/carr_ab64/` folder. If you have HACS available at all, the installation method above is less maintenance long-term.

## Configuration

The config flow is a **single step**: enter a **device name** (used as the entry title and, at creation time, feeds the entity IDs generated for this device — you can rename the friendly name later, but the entity IDs themselves won't change), the gateway **host/IP**, the **port** (defaults to `502`, the Modbus TCP standard, but that's not always correct — check your gateway's own configuration page, since an Elfin EW11 commonly defaults to `8899` instead), and an optional **unit-id**.

What happens next depends on whether you filled in unit-id:

1. **You entered a specific unit-id** — it's verified immediately with a real register read (register 0), and the entry is created. No extra step.
2. **You left unit-id blank** — the integration scans the full unit-id range **1–64** and reads register 0 on each responding address, then **always** shows a confirmation step listing whatever it found — even if only one unit-id responded, you still confirm it rather than it being picked for you silently. Each listed unit-id is labeled with the live state it read back (on/off, mode, setpoint) where that could be read, or just the bare unit-id number if reading state failed. Unit-ids already used by another config entry on the same gateway are filtered out of that list automatically — if you're adding a second (or third...) AC unit on a gateway you've already set up and *every* responding unit-id already belongs to another entry, you'll get a distinct message telling you so; that's expected and doesn't mean anything is wired wrong, it just means there's nothing new on this gateway to add yet.
   - A full scan can take up to **about a minute**. There's no progress bar while it runs, so it will look idle for a while — let it finish, or enter the unit-id manually instead if you already know it.

**To work out the unit-id from the AB64's DIP switches yourself**, don't read the switch positions as the unit-id directly — run them through the formula: `unit-id = SW2 × 16 + SW1 + 1` (the vendor's DIP table is 1-based, so the valid range is **1–64**, not 0–63). If you'd rather not do the math, leaving unit-id blank and letting the scan above confirm it against a real register read is the safer option — it also catches the case where you got the formula wrong.

### Options

- **Poll interval** — default 30s, minimum **1s** (entering a lower value is rejected with an explicit error, not silently clamped). Before lowering it, do the bus math — these numbers are **measured** (from real DEBUG-log timing on the reference hardware, 2026-08-05), not estimated: a single register-block read takes **~115ms** including turnaround at `9600 8N2`. By default this integration reads **2 blocks per poll** (the basic block, plus the indoor telemetry block for the room-temperature reading above — see below), so at a 1s interval one AC unit uses about **~23%** of the bus's time; with advanced telemetry sensors also enabled (3 blocks total per poll — basic + indoor + outdoor) that rises to roughly **~35%**. If you have **multiple AB64 boxes sharing the same RS-485 line**, their poll traffic all shares that same bus — at the default 2-block read, the bus is effectively saturated with only around **4 units** all polling at 1s. Lower the interval gradually and watch for read failures rather than jumping straight to 1s on a shared bus, especially if more than one AB64 shares the line.
- **Enable advanced telemetry sensors** — **off by default**. This option controls **entity creation, not register reads** — those are two different things: the indoor telemetry block (which includes the room-temperature register used by the climate card above) is **always read**, whether or not this option is on. **As of 0.1.7, the room temperature reading (register 4012, TA) is also created as its own `sensor.<name>_indoor_temperature_ta` entity unconditionally** — it's the one exception to this option gating entity creation. Reasoning: every AC needs a return-air thermistor to run its own control loop, so TA is the one register in this group confident to exist on every model/firmware (unlike the other 11, which stay opt-in because that isn't known yet); and since it's already read every poll anyway to feed `climate.current_temperature`, creating a standalone entity for it costs **zero extra bus time**. That means the same reading is available in **two places** — the `current_temperature` attribute on the `climate` entity, and this standalone sensor — use whichever fits: the sensor is the one to reach for if you want the reading on a history graph, a dashboard card, or in long-term statistics, since a climate entity's attributes don't get their own history the way a sensor entity does. Turning this option on additionally **creates the other 11 sensor entities** (indoor/outdoor coil, discharge, suction temperatures, fan/compressor telemetry, filter timer) fed from that same indoor block plus a second, outdoor telemetry block. Leaving it off means zero extra entities beyond the always-on room-temperature sensor above — not "entities that read zero," no entities at all.

  **If a register doesn't exist on your model**, the AB64 can respond in one of three ways: a Modbus exception, or the sentinel value `0xFFFF` (both of these are shown automatically as `unavailable` — see "Entity availability" below) — **or a plain `0`, which looks exactly like a real reading and can't be told apart from one automatically.** If any advanced sensor is stuck at `0` and never moves no matter how hard the AC is working, suspect that your model or firmware simply doesn't have that register, rather than trusting it as a real value. (Some models/firmware may be missing specific registers — see "Confirmation status of advanced telemetry values" below.)

### Multiple AC units on one gateway

Add the integration again — **one indoor unit per config entry**. If a second entry points at the same host:port as an existing one, it automatically **shares the underlying Modbus connection** with it; you don't need to configure anything for this. This is the vendor's normal topology (multiple AB64 boxes sharing one RS-485 bus), not an edge case. The unit-id address space is **1–64** (see the DIP-switch formula above), but that's just how many addresses exist — it's not a recommendation for how many boxes to actually put on one bus. For that, the number that actually matters is the poll-bandwidth math under **Poll interval** above: a shared bus gets saturated around **4 units** at a 1s poll interval. Use that to plan how many AB64 boxes make sense on one RS-485 line, not the address-space count.

### Reconfigure

To change IP, port, or unit-id after setup, use the integration's **Reconfigure** button — don't remove and re-add it. The new values are re-verified with a real register read before being saved, and are checked against every other config entry to avoid two entries ending up on the same host:port:unit-id.

## Entities

| Entity | Notes |
|---|---|
| `climate.<name>` | On/off, HVAC mode, fan speed, swing, target temperature, and **current (room) temperature** — shown as "Currently: XX °C" on the card. This reads register 4012 (TA, the AC's own return-air thermistor) and is available **from installation, with no options to enable**. The same reading is also created as its own `sensor.<name>_indoor_temperature_ta` entity below — see the note under "Enable advanced telemetry sensors" below for why this one field is on by default (as both a climate attribute and its own sensor entity) while the other 11 advanced sensors aren't. |
| `sensor.<name>_indoor_temperature_ta` | Room temperature — the same register 4012 (TA) reading shown on the climate card above, **created unconditionally, no option needed**, enabled by default. Exists as its own entity mainly so you can put it on a history graph, a dashboard card, or use Home Assistant's long-term statistics on it — a climate entity's attributes don't get their own history the way a sensor entity does. (The `_ta` suffix and full "temperature" spelling come from Home Assistant generating the entity ID from this entity's display name, "Indoor temperature (TA)" — not from the shorter `indoor_temp` name used internally in the integration's code.) |
| `sensor.<name>_error_code` | Raw AC error/status code, with `remote_code`, `description`, and `category` attributes decoded from the vendor's error table. |
| `sensor.<name>_*` (advanced, opt-in, 11 sensors) | Indoor/outdoor temperatures, fan/compressor speed, compressor current, filter timer — only created at all if the **Enable advanced telemetry sensors** option above is turned on (this doesn't include the always-on room-temperature sensor above, which isn't gated by this option). Of those 11, the 6 temperature sensors are enabled by default; the other 5 (indoor/compressor/lowest-fan speed, compressor current, filter timer) are created but start **disabled in the entity registry** — a separate switch from the option itself, so you can turn on just the ones you want from that entity's settings without recreating the config entry. |
| `binary_sensor.<name>_ab_bus_link` | **Problem** class. Means "the AB64 box has lost its internal AB Bus link to the AC unit" — a hardware-level fault on the AB64↔AC side, *not* the same thing as Home Assistant losing its Modbus connection to the gateway. Debounced ~20 seconds internally so a normal HA restart or a brief AC-side blip doesn't flip it on. |

## Entity availability, and why values aren't instant

`unavailable` doesn't mean the same thing everywhere in this integration — there are three distinct levels:

1. **The basic register block (on/off, mode, fan, swing, setpoint) fails to read 3 times in a row** — every entity for that device goes `unavailable` together. A single missed read (1 or 2 failures) does **not** do this; the entities keep showing their last known value rather than flickering on every transient RS-485 hiccup.
2. **An advanced telemetry block fails to read** — only the sensors fed by that specific register block go `unavailable`; every other entity (basic block, other advanced blocks) keeps working normally. This includes the indoor block that feeds the climate card's room temperature and the `sensor.<name>_indoor_temperature_ta` entity (see "Currently: XX °C" above) — since that block is now read by default, this can affect every installation, not just ones with advanced telemetry sensors turned on.

   **After 3 consecutive failed reads, that specific block is paused for a while** rather than retried on every single poll — you'll see it back off on a ladder: pause **1 minute**, retry once; still failing → pause **5 minutes**, retry once; still failing → pause **10 minutes** and stay at 10-minute retries from then on. As soon as a retry succeeds, it resets immediately back to normal polling — no cooldown on the way back up. This exists because a register that genuinely doesn't exist on your model would otherwise cost a ~6-second timeout on **every single poll, forever** — at the 1s poll floor this integration allows, that's not just annoying, it makes the integration effectively unusable. **How to recognize this happening**: a sensor (or the climate card's room temperature) goes `unavailable` for a stretch — up to several minutes — and then comes back on its own without you doing anything, rather than staying `unavailable` permanently or flickering on every poll. That pattern is this backoff working as intended, not a persistent fault.
3. **Register 11 reads back `65535`** — this is **not** an `unavailable` state at all. It surfaces instead as the `binary_sensor.<name>_ab_bus_link` entity turning "Problem" after its own ~20-second debounce. It means something different from the first two cases: the AB64 itself has lost its link to the AC unit, not Home Assistant losing its connection to the AB64/gateway.

**There is no optimistic state.** Every value shown by this integration is a value actually read back from the hardware — never the value you just commanded. After changing mode, fan speed, or setpoint from the climate card, there's a real hardware delay before the new value can be read back at all: **measured at ~1.3–1.4s** on the reference hardware (2026-08-05, poll interval 1s) — three back-to-back writes (setpoint 23→22, mode cool→fan_only, mode fan_only→cool) came back at 1.71s, 1.76s, and 1.65s respectively, consistently across all three, of which about 0.36s was round-trip overhead of the measurement tool itself rather than AB64/AC latency. (Previously this section said "~10s observed" — that was an imprecise guess, not a measurement; this replaces it.)

In practice, the delay you actually see on the card is that **~1–2s hardware delay, plus up to one more poll interval** — the coordinator's read immediately after the write happens before the AB64 has caught up, so it still gets the old value; the new value only shows up on the next scheduled poll. At the 30s default that wait dominates and makes the update feel much slower than the hardware actually is, even though the AB64 is ready again by around the 1-second mark.

**To close that gap without introducing optimistic state**, the coordinator schedules one extra read about **2 seconds** after every write, in addition to the immediate one — so on the 30s default you see the real, read-back value in about 2 seconds instead of waiting up to 30. This is still not optimistic state: what you see at that ~2s mark is a genuine register read, not the value you commanded — if the AC hadn't actually applied it yet for some reason, the card would show that instead. If your **Poll interval** is already **2 seconds or less**, this extra read doesn't happen — normal polling already covers it at that cadence, so there's nothing extra to add. If you're issuing several changes in a row and don't want to wait even the ~2s for each, lower the **Poll interval** option (see above) rather than expecting the card to update instantly.

## Confirmation status of advanced telemetry values

The advanced telemetry register map comes from the vendor manual, but the manual doesn't document everything about how to interpret every field (units, sign handling) — some of what this integration assumes is inferred rather than vendor-confirmed. Here's the state of each claim as of the first real-hardware verification pass (2026-08-05, against a single unit — see the ramp test below):

| Claim | Status |
|---|---|
| Temperature scale is 1:1 raw-to-°C | **Confirmed 2026-08-05** against a real unit via ramp test — every temperature field moved the correct direction as load changed. |
| TA (register 4012) is the real room temperature, not the setpoint | **Confirmed** — observed setpoint held at 18°C while TA read 21°C and drifted down toward 20°C over several minutes, tracking the room, not the setpoint. |
| Negative temperature readings use two's complement | **Not yet confirmed** — this only shows up when a value actually goes negative (e.g. evaporator temp during defrost/heating), which hasn't been observed yet. |
| Compressor current unit | **Unknown** — confirmed only that it is *not* a direct amps reading (see ramp test below for why). |
| Compressor speed unit | Believed to be **rps** (revolutions per second) — physics rules out the other plausible units, but there's no vendor documentation confirming it. |
| Filter-sign timer unit | **Unknown**. |

## Verifying advanced telemetry on your own hardware (ramp test)

If you have a different AC model and want to help confirm (or correct) the assumptions above, here's the test used to get the results in the table:

1. Turn on **Enable advanced telemetry sensors**, and temporarily lower the **Poll interval** option so you can watch values update quickly.
2. With the AC idle, note the compressor speed and compressor current sensor values.
3. Lower the setpoint 3–4°C from the current room temperature so the compressor ramps up, and wait 2–3 minutes.
4. Note the new values — speed and current should both move in the same direction (both up).
5. As a sanity check on the current reading: if you treat the raw number as amps directly, work out the implied power draw and compare it against your unit's rated capacity — if the implied efficiency (cooling output ÷ that power figure) comes out below 1, the raw number isn't amps.
6. Open an issue with your AC model and the numbers you got — every additional data point helps move a "believed" or "unknown" row above into "confirmed."

**Our reference result** (Carrier 38TGV0391A3 / 40TGV0391UP, 2026-08-05, idle → high load): compressor speed 23 → 58, compressor current 29 → 65, discharge temp (TD) 79 → 82°C, room temp (TA) 22 → 20°C as the room cooled.

## Troubleshooting

- **"Could not open a TCP connection to the gateway"** vs **"Connected, but the AB64 did not respond"** are reported as distinct errors on purpose — the first points at your network/gateway config, the second at wiring, baud rate, or a wrong unit-id.
- The AB64 has two status LEDs that mean different things: **blue** = a Modbus frame was received addressed to this box; **green** = the box's own AB Bus link to the AC is up. Don't conflate them when troubleshooting.
- The AB64's two terminal blocks are easy to cross: `A B — AC UNIT` goes to the AC control board, `MODBUS RS-485 — B A G` goes to your gateway — note the gateway-side block is silkscreened `B A G` left-to-right (not the more intuitive `A B G`), so compare the printed letters, not the position.
- Changing the AB64's DIP switches (address or baud rate) only takes effect after a **full power cycle** of the box — re-scanning without power-cycling will keep finding the old address.
- Scan finished but didn't find a unit you know is there? Enter the unit-id manually instead of relying on the scan — the scan uses a short per-address probe timeout, and a slow gateway can occasionally miss a response within it.
- For deeper diagnostics (gateway TX/RX byte counters, full register map, full error code table), see [`references/ac-modbus-ab64-reference.md`](references/ac-modbus-ab64-reference.md).

## Known limitations

- **v1 scope: one indoor unit per config entry.** No VRF group control — add a separate entry per indoor unit.
- **Only Modbus function code 03 (Holding Registers) is used.** This device does not answer FC04 (Input Registers), even for read-only fields. This is an **empirical finding from live bring-up testing, not something documented in the vendor manual** — the manual never mentions Modbus function codes at all.
- **English only** — no other translations are provided.
- **Not everything about advanced telemetry is vendor-confirmed** — see "Confirmation status of advanced telemetry values" above for exactly which claims are verified and which are still inferred.
- **A register value of `0xFFFF` (65535) on any advanced telemetry field is treated as "no value"** and the corresponding sensor goes `unavailable`, rather than being shown as a number — this is the same sentinel convention the device uses for register 11 (see the `ab_bus_link` binary_sensor above). A plain `0` is a separate, harder case — see the warning under "Enable advanced telemetry sensors" above.
- Advanced telemetry registers may not exist on every model/firmware — registers are read one block at a time, so if a block isn't supported, the sensors in that register block go `unavailable` together rather than affecting the rest of the integration.
- **Tested against a single real unit so far**: Carrier 38TGV0391A3 / 40TGV0391UP (3-phase). The vendor manual states the AB64 covers the **Digital Inverter & VRF** product line; everything in this repo beyond what the manual states has only been exercised against that one tested unit, not across the wider product line.

## Requirements

Home Assistant **2025.10.1** or newer.

## License

[MIT](LICENSE)
