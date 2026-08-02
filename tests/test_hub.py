"""Hub/connection sharing tests — the most important layer of this integration.

RS-485 behind one TCP<->RTU gateway means every config entry pointing at the same
(host, port) MUST share one AsyncModbusTcpClient and one lock, even though each
entry only talks to its own unit_id. See CLAUDE.md "Architecture" + "Non-obvious
facts" for why this is the vendor-standard topology, not an edge case.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pymodbus.exceptions import ConnectionException

from custom_components.carr_ab64.hub import AB64ReadError, AB64WriteError, async_acquire_hub, async_release_hub

from tests.conftest import TEST_HOST, TEST_PORT, TEST_UNIT_ID, seed_happy_path


async def test_two_entries_same_host_port_share_one_hub(hass, fake_clients):
    """Case 1: two entries, same (host, port), different unit_id -> identical AB64Hub."""
    hub_a = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)
    hub_b = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)

    assert hub_a is hub_b
    assert hub_a._client is hub_b._client
    assert hub_a._lock is hub_b._lock
    assert hub_a.refcount == 2


async def test_different_host_port_gets_different_hub(hass, fake_clients):
    hub_a = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)
    hub_b = await async_acquire_hub(hass, TEST_HOST, TEST_PORT + 1)

    assert hub_a is not hub_b


async def test_refcount_unload_one_entry_keeps_client_open(hass, fake_clients):
    """Case 2: unload entry 1 of 2 -> client NOT closed; unload both -> closed exactly once."""
    await async_acquire_hub(hass, TEST_HOST, TEST_PORT)  # entry 1
    await async_acquire_hub(hass, TEST_HOST, TEST_PORT)  # entry 2
    client = fake_clients[(TEST_HOST, TEST_PORT)]
    assert client.connected is True

    await async_release_hub(hass, TEST_HOST, TEST_PORT)  # entry 1 unloads
    assert client.close_calls == 0
    assert client.connected is True

    await async_release_hub(hass, TEST_HOST, TEST_PORT)  # entry 2 unloads
    assert client.close_calls == 1
    assert client.connected is False


async def test_refcount_does_not_go_negative_on_extra_release(hass, fake_clients):
    await async_acquire_hub(hass, TEST_HOST, TEST_PORT)
    await async_release_hub(hass, TEST_HOST, TEST_PORT)
    client = fake_clients[(TEST_HOST, TEST_PORT)]
    assert client.close_calls == 1

    # Releasing again (no hub left for this key) must be a no-op, not raise/double-close.
    await async_release_hub(hass, TEST_HOST, TEST_PORT)
    assert client.close_calls == 1


async def test_acquire_hub_raises_and_rolls_back_on_connect_failure(hass, fake_clients):
    from tests.conftest import FakeModbusClient

    client = FakeModbusClient(TEST_HOST, TEST_PORT)
    client.fail_connect = True
    fake_clients[(TEST_HOST, TEST_PORT)] = client

    with pytest.raises(ConnectionException):
        await async_acquire_hub(hass, TEST_HOST, TEST_PORT)

    domain_data = hass.data.get("carr_ab64", {})
    assert (TEST_HOST, TEST_PORT) not in domain_data.get("hubs", {})


async def test_lock_serializes_concurrent_read_and_write(hass, fake_clients):
    """Case 3: lock covers both read and write — no interleaving on the shared bus."""
    hub = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)
    client = fake_clients[(TEST_HOST, TEST_PORT)]
    client.op_delay = 0.02  # widen the window so a missing lock would visibly interleave

    await asyncio.gather(
        hub.async_read_holding(2, 0, 1),
        hub.async_write_register(2, 4, 22),
    )

    # Every "<op>_start" must be immediately followed by its own "<op>_end" before
    # any other op's start appears — i.e. operations ran back-to-back, never interleaved.
    log = client.op_log
    assert len(log) == 4
    for i in range(0, len(log), 2):
        start, end = log[i], log[i + 1]
        assert start.split(":")[0].endswith("_start")
        assert end.split(":")[0].endswith("_end")
        assert start.split(":", 1)[1] == end.split(":", 1)[1]


async def test_no_code_path_calls_read_input_registers():
    """Case 4: FC04 (Input Registers) is not supported by the real AB64 hardware —
    structural guarantee that nothing in the integration calls it, empirical-only
    per CLAUDE.md (the vendor manual never documents Modbus function codes)."""
    integration_dir = Path(__file__).parent.parent / "custom_components" / "carr_ab64"
    offenders = []
    for path in integration_dir.glob("*.py"):
        if re.search(r"read_input_registers", path.read_text()):
            offenders.append(str(path))
    assert offenders == []


# --- P1: pymodbus unit-id kwarg shim (hub.py::AB64Hub._resolve_unit_kwarg) -------
#
# pymodbus renamed its unit-id kwarg slave= -> device_id= in 3.10.0. HA core
# 2025.10.1 pins pymodbus==3.11.2 (device_id=); this repo's dev machine happens to
# have 3.8.6 installed (slave=). Both client "shapes" must work without editing
# hub.py. These stubs mimic just the method signatures of each shape — not full
# FakeModbusClient — because the point here is exactly what inspect.signature()
# sees on the real client, which depends on the *shape* of the method, not its body.


class _StubDeviceIdOnlyClient:
    """Mimics pymodbus>=3.10 AsyncModbusTcpClient: device_id= kwarg only."""

    def __init__(self) -> None:
        self.connected = True
        self.calls: list[dict] = []

    async def read_holding_registers(self, address: int, *, count: int = 1, device_id: int = 1):
        from types import SimpleNamespace

        self.calls.append({"op": "read", "address": address, "count": count, "device_id": device_id})
        return SimpleNamespace(registers=[0] * count, isError=lambda: False)

    async def write_register(self, address: int, value: int, *, device_id: int = 1):
        from types import SimpleNamespace

        self.calls.append({"op": "write", "address": address, "value": value, "device_id": device_id})
        return SimpleNamespace(isError=lambda: False)


class _StubSlaveOnlyClient:
    """Mimics pymodbus<3.10 AsyncModbusTcpClient: slave= kwarg only."""

    def __init__(self) -> None:
        self.connected = True
        self.calls: list[dict] = []

    async def read_holding_registers(self, address: int, *, count: int = 1, slave: int = 1):
        from types import SimpleNamespace

        self.calls.append({"op": "read", "address": address, "count": count, "slave": slave})
        return SimpleNamespace(registers=[0] * count, isError=lambda: False)

    async def write_register(self, address: int, value: int, *, slave: int = 1):
        from types import SimpleNamespace

        self.calls.append({"op": "write", "address": address, "value": value, "slave": slave})
        return SimpleNamespace(isError=lambda: False)


class _StubNoUnitKwargClient:
    """Mimics a hypothetical future pymodbus with no unit-id kwarg at all — must
    fail loudly rather than silently send the wrong (or no) kwarg."""

    def __init__(self) -> None:
        self.connected = True

    async def read_holding_registers(self, address: int, *, count: int = 1):
        raise AssertionError("should never be called — resolution must fail first")


async def test_kwarg_shim_resolves_device_id_and_value_reaches_client(hass, fake_clients):
    """P1 case 1: device_id-only client (pymodbus 3.10+ shape) -> shim resolves to
    'device_id' AND the real unit_id value is what actually reaches the client call."""
    hub = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)
    stub = _StubDeviceIdOnlyClient()
    hub._client = stub
    hub._unit_kwarg = None

    await hub.async_read_holding(7, 0, 1)
    await hub.async_write_register(7, 4, 22)

    assert hub._unit_kwarg == "device_id"
    read_call = next(c for c in stub.calls if c["op"] == "read")
    write_call = next(c for c in stub.calls if c["op"] == "write")
    assert read_call["device_id"] == 7
    assert write_call["device_id"] == 7
    assert write_call["value"] == 22


async def test_kwarg_shim_resolves_slave(hass, fake_clients):
    """P1 case 2: slave-only client (pymodbus <3.10 shape, matches this dev
    machine's installed 3.8.6) -> shim resolves to 'slave'."""
    hub = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)
    stub = _StubSlaveOnlyClient()
    hub._client = stub
    hub._unit_kwarg = None

    await hub.async_read_holding(9, 0, 1)

    assert hub._unit_kwarg == "slave"
    assert stub.calls[0]["slave"] == 9


async def test_kwarg_shim_resolved_once_and_cached(hass, fake_clients):
    """P1 case 3: resolution happens exactly once, not re-inspected on every call."""
    hub = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)
    stub = _StubDeviceIdOnlyClient()
    hub._client = stub
    hub._unit_kwarg = None

    with patch("custom_components.carr_ab64.hub.inspect.signature", wraps=inspect.signature) as spy:
        await hub.async_read_holding(2, 0, 1)
        await hub.async_read_holding(2, 0, 1)
        await hub.async_write_register(2, 4, 22)

    assert spy.call_count == 1
    assert len(stub.calls) == 3


async def test_kwarg_shim_raises_runtime_error_when_no_kwarg_matches(hass, fake_clients):
    """P1 case 4: a client shape with none of (device_id, slave, unit) must fail
    loudly with a clear RuntimeError, not silently send a wrong/missing kwarg."""
    hub = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)
    stub = _StubNoUnitKwargClient()
    hub._client = stub
    hub._unit_kwarg = None

    with pytest.raises(RuntimeError, match="none of"):
        await hub.async_read_holding(2, 0, 1)


# --- m-QA1 group 1: result.isError() branches (hub.py's read/write both check it
# after a *successful* Modbus transaction — a raised ModbusException is a
# completely different path and was already covered; on real AB64 hardware an
# exception response like IllegalDataAddress is reportedly the MORE common of the
# two, per reviewer, and neither branch had ever been exercised by a test) --------


async def test_read_iserror_response_raises_ab64_read_error(hass, fake_clients):
    client = seed_happy_path(fake_clients)
    client.error_response_at_read(TEST_UNIT_ID, 0)
    hub = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)

    with pytest.raises(AB64ReadError):
        await hub.async_read_holding(TEST_UNIT_ID, 0, 1)


async def test_write_iserror_response_raises_ab64_write_error(hass, fake_clients):
    client = seed_happy_path(fake_clients)
    client.error_response_at_write(TEST_UNIT_ID, 4)
    hub = await async_acquire_hub(hass, TEST_HOST, TEST_PORT)

    with pytest.raises(AB64WriteError):
        await hub.async_write_register(TEST_UNIT_ID, 4, 22)
