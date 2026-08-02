## Carrier-Toshiba AB64 (Modbus)

Control a Carrier-Toshiba air conditioner fitted with an **AB64** interface box (Intesis INMBSTOS001R000) over Modbus TCP, via a TCP↔RTU gateway (e.g. Elfin EW11).

Gives you a `climate` entity (on/off, mode, fan speed, swing, setpoint), an error-code sensor with decoded descriptions, an AB Bus link-health binary sensor, and optional opt-in advanced telemetry (indoor/outdoor temperatures, fan/compressor RPM, compressor current).

**Prerequisites**: an AB64 box wired to your indoor unit, a Modbus TCP↔RTU gateway on your network, and Home Assistant 2025.10.1+.

Multiple AC units on the same gateway are supported — add one config entry per indoor unit; entries on the same gateway automatically share the underlying connection.

See the [README](https://github.com/PragmaTH/carr-intmbslc-ab64-ha#readme) for full installation, configuration, and troubleshooting instructions.
