"""
Standalone Modbus TCP test script for the Carrier-Toshiba AB64 Modbus
Interface (RS485-to-AB-bus gateway on the AC unit), reached via a Modbus
TCP<->RTU gateway (e.g. Elfin EW11). No Home Assistant dependency --
just pymodbus. Meant to be run from WSL2 to confirm the AC is reachable
over the network before building/testing the real HA integration.

See ./ac-modbus-ab64-reference.md for the full protocol writeup this
is based on (wiring, DIP switches, register map, error codes, gotchas).

Setup (in WSL2):
    pip install pymodbus

Run:
    python ac-modbus-ab64-test.py --host 192.168.14.149 --port 502 --unit-id 2

If the connection hangs/times out in WSL2 but works fine from Windows,
check WSL2's networking mode -- "mirrored" networking mode (Windows 11,
recent WSL2) is required to reliably reach devices on the same LAN;
older NAT mode can be flaky for this.
"""

import argparse
import sys
import time
from datetime import datetime

from pymodbus.client import ModbusTcpClient
from pymodbus import FramerType

# Basic registers (Holding Registers, FC03 -- this device has no FC04 support):
#   0  On/Off        0=off, 1=on
#   1  Mode           0=Auto, 1=Heat, 2=Dry, 3=Fan, 4=Cool
#   2  Fan speed      0=Auto, 1=Low, 2=Med, 3=High
#   3  Swing          0=off, 10=on
#   4  Temp setpoint  16-32 C
#   5-10  N/A
#   11 Error code     65535 = AB64<->AC link down (not a normal AC error)
BASIC_FIELD_NAMES = ["OnOff", "Mode", "FanSpeed", "Swing", "SetTemp", None, None, None, None, None, None, "ErrorCode"]


def parse_args():
    p = argparse.ArgumentParser(description="Poll a Carrier-Toshiba AB64 Modbus interface over Modbus TCP.")
    p.add_argument("--host", required=True, help="Gateway IP address, e.g. 192.168.14.149")
    p.add_argument("--port", type=int, default=502, help="Gateway TCP port (default: 502 -- verify per gateway, not always the default)")
    p.add_argument("--unit-id", type=int, default=1, help="Modbus slave/unit id (default: 1 -- don't trust a DIP-switch reading, confirm empirically)")
    p.add_argument("--interval", type=float, default=5.0, help="Seconds between polls (default: 5)")
    p.add_argument("--timeout", type=float, default=3.0, help="Per-request timeout in seconds (default: 3)")
    p.add_argument("--once", action="store_true", help="Send a single request and exit instead of looping")
    return p.parse_args()


def decode(registers):
    return "  ".join(f"{name}={val}" for name, val in zip(BASIC_FIELD_NAMES, registers) if name)


def main():
    args = parse_args()
    client = ModbusTcpClient(args.host, port=args.port, framer=FramerType.SOCKET, timeout=args.timeout, retries=1)

    print(f"Target: {args.host}:{args.port}  unit_id={args.unit_id}")
    print(f"Reading registers 0-11 every {args.interval}s" + (" (single run)" if args.once else " (Ctrl+C to stop)"))
    print()

    if not client.connect():
        print(f"FAILED to open TCP connection to {args.host}:{args.port}")
        sys.exit(1)

    try:
        while True:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                result = client.read_holding_registers(0, count=12, slave=args.unit_id)
                if result is None or result.isError():
                    print(f"[{ts}] ERROR: {result}")
                else:
                    print(f"[{ts}] OK  raw={result.registers}  {decode(result.registers)}")
            except Exception as exc:
                print(f"[{ts}] EXCEPTION: {exc}")

            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
