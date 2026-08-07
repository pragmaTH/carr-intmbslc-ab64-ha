## Carrier-Toshiba AB64 (Modbus)

Control a Carrier-Toshiba air conditioner fitted with an **AB64** interface box (Intesis INMBSTOS001R000) over Modbus TCP, via a TCP↔RTU gateway (e.g. Elfin EW11).

Gives you a `climate` entity (on/off, mode, fan speed, swing, setpoint, and current room temperature — available from installation, no options needed), a standalone room-temperature sensor for the same reading, an error-code sensor with decoded descriptions, an AB Bus link-health binary sensor, and — opt-in — 11 more advanced telemetry sensors (indoor/outdoor coil, discharge, suction temperatures, fan/compressor speed, compressor current, filter timer).

**Prerequisites**: an AB64 box wired to your indoor unit, a Modbus TCP↔RTU gateway on your network, and Home Assistant 2025.10.1+.

Multiple AC units on the same gateway are supported — add one config entry per indoor unit; entries on the same gateway automatically share the underlying connection.

The setup screens and entity names are available in **English and Thai**; entity IDs stay English in every language regardless — that's Home Assistant's own behavior for non-Latin-script languages.

See the [README](https://github.com/pragmaTH/carr-intmbslc-ab64-ha#readme) for full installation, configuration, and troubleshooting instructions.
