# Carrier-Toshiba AB64 (Modbus) for Home Assistant

[![Validate](https://github.com/PragmaTH/carr-intmbslc-ab64-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/PragmaTH/carr-intmbslc-ab64-ha/actions/workflows/validate.yml)
[![Test](https://github.com/PragmaTH/carr-intmbslc-ab64-ha/actions/workflows/test.yml/badge.svg)](https://github.com/PragmaTH/carr-intmbslc-ab64-ha/actions/workflows/test.yml)

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
2. Repository: `https://github.com/PragmaTH/carr-intmbslc-ab64-ha`, Category: **Integration**.
3. Find "Carrier-Toshiba AB64 (Modbus)" in HACS and **Download**.
4. **Restart Home Assistant.**
5. Settings → Devices & Services → **Add Integration** → search "Carrier-Toshiba AB64" — or use this button (same "already set up My Home Assistant" caveat as above):

   [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=carr_ab64)

## Configuration

The config flow has two steps:

1. **Connect to AB64 gateway** — enter a **device name** (used as the entry title and, at creation time, feeds the entity IDs generated for this device — you can rename the friendly name later, but the entity IDs themselves won't change), the gateway **host/IP**, and the **port**. Port has no default — check your gateway's own configuration page; an Elfin EW11 commonly defaults to `8899`, not the Modbus-standard `502`.
2. **Find the unit-id** — leave it blank to scan unit-ids **1–63** and read register 0 on each, or enter a specific unit-id (including `0`) to verify it directly. **Don't trust a DIP-switch reading by eye** — always let the integration confirm the address against a real register read.
   - A full scan can take up to **about a minute**. There's no progress bar while it runs, so it will look idle for a while — let it finish, or enter the unit-id manually if you already know it.
   - If more than one unit-id responds on the gateway, you'll get a **Multiple units found** step to pick the right one (unit-ids already used by another config entry on the same gateway are filtered out of that list automatically). If you're adding a second (or third...) AC unit on a gateway you've already set up, and *every* unit-id that responds turns out to already belong to another entry, you'll get a distinct message telling you so — that's expected and doesn't mean anything is wired wrong; it just means there's nothing new on this gateway to add yet (e.g. a new AB64 box hasn't been wired in, or is still using the same address as one you've already configured).
   - **Unit-id `0` is the Modbus broadcast address, so a full scan skips it** — a real AB64 never answers a read addressed to `0`. You can still type `0` in manually; it will simply fail verification. If your AB64 is genuinely set to address `0` on its DIP switches, change SW1/SW2 to a non-zero address and **power-cycle the AB64** (DIP switches only take effect on power-up, not on a live re-scan).

### Options

- **Poll interval** — default 30s, minimum 10s (entering a lower value is rejected with an explicit error, not silently clamped). The bus may be shared with other AB64 boxes on the same RS-485 line, so polling too fast adds contention for everyone on it.
- **Enable advanced telemetry sensors** — **off by default**. Turns on ~12 extra sensors (indoor/outdoor coil, discharge, suction temperatures, fan/compressor RPM, compressor current, filter timer) read from a separate register block. Some of these registers may not exist on every model/firmware — if so, that specific sensor will simply go `unavailable` rather than breaking the rest of the integration.

### Multiple AC units on one gateway

Add the integration again — **one indoor unit per config entry**. If a second entry points at the same host:port as an existing one, it automatically **shares the underlying Modbus connection** with it; you don't need to configure anything for this. This is the vendor's normal topology (up to 63 AB64 boxes on one RS-485 bus), not an edge case.

### Reconfigure

To change IP, port, or unit-id after setup, use the integration's **Reconfigure** button — don't remove and re-add it. The new values are re-verified with a real register read before being saved, and are checked against every other config entry to avoid two entries ending up on the same host:port:unit-id.

## Entities

| Entity | Notes |
|---|---|
| `climate.<name>` | On/off, HVAC mode, fan speed, swing, target temperature. |
| `sensor.<name>_error_code` | Raw AC error/status code, with `remote_code`, `description`, and `category` attributes decoded from the vendor's error table. |
| `sensor.<name>_*` (advanced, opt-in, 12 sensors) | Indoor/outdoor temperatures, fan/compressor RPM, compressor current, filter timer — only created at all if the **Enable advanced telemetry sensors** option above is turned on. Of those 12, the 7 temperature sensors are enabled by default; the other 5 (indoor/compressor/lowest-fan RPM, compressor current, filter timer) are created but start **disabled in the entity registry** — a separate switch from the option itself, so you can turn on just the ones you want from that entity's settings without recreating the config entry. |
| `binary_sensor.<name>_ab_bus_link` | **Problem** class. Means "the AB64 box has lost its internal AB Bus link to the AC unit" — a hardware-level fault on the AB64↔AC side, *not* the same thing as Home Assistant losing its Modbus connection to the gateway. Debounced ~20 seconds internally so a normal HA restart or a brief AC-side blip doesn't flip it on. |

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
- Advanced telemetry registers may not exist on every model/firmware — if a given register isn't supported, that specific sensor goes `unavailable` rather than affecting the rest of the integration.

## Requirements

Home Assistant **2025.10.1** or newer.

## License

[MIT](LICENSE)
