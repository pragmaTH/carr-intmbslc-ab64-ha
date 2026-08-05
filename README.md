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

- **Poll interval** — default 30s, minimum **1s** (entering a lower value is rejected with an explicit error, not silently clamped). Before lowering it, do the bus math: a single basic-block read takes roughly 60–80ms including turnaround at `9600 8N2`, so at a 1s interval one AC unit uses about **8%** of the bus's time; with advanced telemetry sensors also enabled (2 extra blocks — indoor and outdoor telemetry, 3 blocks total per poll) that rises to roughly **20–25%**. If you have **multiple AB64 boxes sharing the same RS-485 line**, their poll traffic all shares that same bus — twelve units all polling at 1s would add up to roughly **96%**, i.e. the bus effectively saturated. Lower the interval gradually and watch for read failures rather than jumping straight to 1s on a shared bus.
- **Enable advanced telemetry sensors** — **off by default**. Turns on ~12 extra sensors (indoor/outdoor coil, discharge, suction temperatures, fan/compressor RPM, compressor current, filter timer) read from a separate register block. Some of these registers may not exist on every model/firmware — if so, the sensors in that register block go `unavailable` together rather than breaking the rest of the integration.

### Multiple AC units on one gateway

Add the integration again — **one indoor unit per config entry**. If a second entry points at the same host:port as an existing one, it automatically **shares the underlying Modbus connection** with it; you don't need to configure anything for this. This is the vendor's normal topology (multiple AB64 boxes sharing one RS-485 bus), not an edge case — the unit-id address space supports up to **64** boxes (see the DIP-switch formula above), but that's a count of valid addresses, not a vendor-stated maximum device count. It isn't a bandwidth ceiling either: the poll-interval bus math above already shows a shared bus getting congested well before 64 boxes, and standard RS-485 has its own electrical unit-load limits (typically 32 without a repeater) that this integration hasn't independently verified against this device. Treat 64 as "how many addresses exist," not "how many boxes you should actually put on one bus."

### Reconfigure

To change IP, port, or unit-id after setup, use the integration's **Reconfigure** button — don't remove and re-add it. The new values are re-verified with a real register read before being saved, and are checked against every other config entry to avoid two entries ending up on the same host:port:unit-id.

## Entities

| Entity | Notes |
|---|---|
| `climate.<name>` | On/off, HVAC mode, fan speed, swing, target temperature. |
| `sensor.<name>_error_code` | Raw AC error/status code, with `remote_code`, `description`, and `category` attributes decoded from the vendor's error table. |
| `sensor.<name>_*` (advanced, opt-in, 12 sensors) | Indoor/outdoor temperatures, fan/compressor RPM, compressor current, filter timer — only created at all if the **Enable advanced telemetry sensors** option above is turned on. Of those 12, the 7 temperature sensors are enabled by default; the other 5 (indoor/compressor/lowest-fan RPM, compressor current, filter timer) are created but start **disabled in the entity registry** — a separate switch from the option itself, so you can turn on just the ones you want from that entity's settings without recreating the config entry. |
| `binary_sensor.<name>_ab_bus_link` | **Problem** class. Means "the AB64 box has lost its internal AB Bus link to the AC unit" — a hardware-level fault on the AB64↔AC side, *not* the same thing as Home Assistant losing its Modbus connection to the gateway. Debounced ~20 seconds internally so a normal HA restart or a brief AC-side blip doesn't flip it on. |

## Entity availability, and why values aren't instant

`unavailable` doesn't mean the same thing everywhere in this integration — there are three distinct levels:

1. **The basic register block (on/off, mode, fan, swing, setpoint) fails to read 3 times in a row** — every entity for that device goes `unavailable` together. A single missed read (1 or 2 failures) does **not** do this; the entities keep showing their last known value rather than flickering on every transient RS-485 hiccup.
2. **An advanced telemetry block fails to read** — only the sensors fed by that specific register block go `unavailable`; every other entity (basic block, other advanced blocks) keeps working normally.
3. **Register 11 reads back `65535`** — this is **not** an `unavailable` state at all. It surfaces instead as the `binary_sensor.<name>_ab_bus_link` entity turning "Problem" after its own ~20-second debounce. It means something different from the first two cases: the AB64 itself has lost its link to the AC unit, not Home Assistant losing its connection to the AB64/gateway.

**There is no optimistic state.** Every value shown by this integration is a value actually read back from the hardware — never the value you just commanded. After changing mode, fan speed, or setpoint from the climate card, it can take **several seconds** (roughly ~10s observed, not a documented vendor spec) before the card reflects the new value, because the integration is waiting for the AB64 to sync with the AC over its internal AB Bus link, not because the integration itself is slow. This is a deliberate choice, not a bug: showing the commanded value instead of what the hardware actually reports would defeat the reason people choose a direct Modbus integration over a cloud-based one in the first place. If the ~10s feels too slow, lower the **Poll interval** option (see above) rather than expecting the card to update instantly.

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
- **Advanced telemetry scaling has not been verified against real hardware.** Negative readings (e.g. evaporator temperature during defrost) are decoded as signed (two's complement) values, but neither that signedness assumption nor the raw-to-°C scaling itself is vendor-confirmed — both are inferences from the register map. If you can compare a live advanced-temperature reading against a known-good thermometer, feedback is welcome.
- **A register value of `0xFFFF` (65535) on any advanced telemetry field is treated as "no value"** and the corresponding sensor goes `unavailable`, rather than being shown as a number — this is the same sentinel convention the device uses for register 11 (see the `ab_bus_link` binary_sensor above).
- Advanced telemetry registers may not exist on every model/firmware — registers are read one block at a time, so if a block isn't supported, the sensors in that register block go `unavailable` together rather than affecting the rest of the integration.

## Requirements

Home Assistant **2025.10.1** or newer.

## License

[MIT](LICENSE)
