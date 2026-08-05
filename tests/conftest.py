"""Shared fixtures for carr_ab64 tests.

Mocks pymodbus at the `AsyncModbusTcpClient` boundary only — everything above that
(AB64Hub, AB64Coordinator, entities, config_flow) runs as real code. This is
deliberate: the whole point of this integration is the hub/coordinator logic
(shared connection, refcount, debounce), and mocking above that boundary would
just be testing the mock.

Never use the real bring-up device's LAN IP here — use the TEST-NET-1 placeholder
192.0.2.1 (RFC 5737) instead.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pymodbus.exceptions import ModbusException

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Required by pytest-homeassistant-custom-component to load custom_components/."""


TEST_HOST = "192.0.2.1"
TEST_PORT = 502
TEST_UNIT_ID = 2
TEST_NAME = "Living Room AC"

# Happy-path values from the bring-up session (see CLAUDE.md):
# OnOff=1 Mode=0(Auto) FanSpeed=2(Med) Swing=0(Off) SetTemp=22 ErrorCode=0
# Registers 5-10 are reserved/unused in the basic block.
HAPPY_BASIC_REGS = [1, 0, 2, 0, 22, 0, 0, 0, 0, 0, 0, 0]


class FakeModbusClient:
    """Stand-in for pymodbus's AsyncModbusTcpClient.

    One instance per (host, port) — mirrors the real AsyncModbusTcpClient lifecycle
    that AB64Hub wraps. Registers are stored per unit_id so one fake can serve
    multiple unit_ids on the same simulated RS-485 bus, like the real vendor topology.
    """

    def __init__(self, host: str | None = None, port: int | None = None, **_kwargs: Any) -> None:
        self.host = host
        self.port = port
        self.connected = False
        self.fail_connect = False
        self.fail_write = False
        self.connect_calls = 0
        self.close_calls = 0
        self.registers: dict[int, dict[int, int]] = {}
        self.fail_read_addresses: dict[int, set[int]] = {}
        self.fail_read_block_addresses: dict[int, dict[int, set[int]]] = {}
        # Distinct from fail_read_addresses/fail_write: those simulate a raised
        # ModbusException (transport-level timeout/no-response). These simulate a
        # *successful* Modbus transaction that comes back as an exception response
        # (e.g. IllegalDataAddress) — result.isError() is True but no exception is
        # raised. On real AB64 hardware this is reportedly MORE common than a
        # timeout (e.g. an advanced register that doesn't exist on a given unit),
        # and hub.py has a dedicated isError() branch (hub.py:116,136) that was
        # never exercised by any test until this fixture could produce it.
        self.error_response_read_addresses: dict[int, set[int]] = {}
        self.error_response_write_addresses: dict[int, set[int]] = {}
        # Real hardware: only unit_ids with an actual AB64 box on the bus respond;
        # everything else times out. Off by default so hub/coordinator tests that
        # don't care about scanning semantics can read any unit_id and get zeros.
        # config_flow scan tests turn this on to get realistic "no response" behavior.
        self.only_respond_to_known_units = False
        self.known_units: set[int] = set()
        self.read_log: list[tuple[int, int, int]] = []
        self.write_log: list[tuple[int, int, int]] = []
        # Sequence of "<op>_start:<slave>:<address>" / "<op>_end:..." markers used to
        # prove the AB64Hub lock actually serializes concurrent read/write calls.
        self.op_log: list[str] = []
        self.op_delay: float = 0.0
        # Per-unit override of op_delay, for simulating one slow/hung candidate on
        # the bus during a scan while others still respond quickly.
        self.delay_per_unit: dict[int, float] = {}
        # Simulates the physical connection dropping partway through a sequence of
        # reads (e.g. between config_flow's scan phase and its label-building
        # phase, both of which now share one AB64Hub/connection — qa-unitstep-m1):
        # after exactly this many total read_holding_registers calls, `connected`
        # flips False, so AB64Hub.async_read_holding's own `if not
        # self._client.connected` check raises a real ConnectionException on the
        # *next* call — not a fabricated one. None (default) never disconnects.
        self.disconnect_after_n_reads: int | None = None
        self._read_count = 0

    def set_registers(self, unit_id: int, start: int, values: list[int]) -> None:
        self.known_units.add(unit_id)
        regs = self.registers.setdefault(unit_id, {})
        for offset, value in enumerate(values):
            regs[start + offset] = value

    def fail_read_at(self, unit_id: int, *addresses: int) -> None:
        self.fail_read_addresses.setdefault(unit_id, set()).update(addresses)

    def clear_fail_read_at(self, unit_id: int) -> None:
        """Undo fail_read_at for this unit_id — lets a test simulate "back to
        healthy" mid-run (e.g. proving a consecutive-failure counter resets on
        success rather than only ever climbing)."""
        self.fail_read_addresses.pop(unit_id, None)

    def fail_read_block_at(self, unit_id: int, address: int, count: int) -> None:
        """Like fail_read_at, but scoped to one exact (address, count) pair —
        e.g. failing only a multi-register block read at an address without
        also failing a single-register probe at that same starting address
        (config_flow's scan probe and its unit-label read both start at
        BASIC_BLOCK_START/REG_ON_OFF == 0, differing only in count)."""
        self.fail_read_block_addresses.setdefault(unit_id, {}).setdefault(address, set()).add(count)

    def error_response_at_read(self, unit_id: int, *addresses: int) -> None:
        """Next read(s) at these addresses succeed at the transport level but come
        back as a Modbus exception response (isError() == True), e.g. IllegalDataAddress."""
        self.error_response_read_addresses.setdefault(unit_id, set()).update(addresses)

    def error_response_at_write(self, unit_id: int, *addresses: int) -> None:
        self.error_response_write_addresses.setdefault(unit_id, set()).update(addresses)

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connected = not self.fail_connect

    def close(self) -> None:
        self.close_calls += 1
        self.connected = False

    async def read_holding_registers(self, address: int, *, count: int = 1, slave: int = 1):
        self._read_count += 1
        if self._read_count == self.disconnect_after_n_reads:
            # Flip after this call returns/raises — visible to AB64Hub's connected
            # check on the *next* call, not this one.
            self.connected = False
        self.read_log.append((slave, address, count))
        self.op_log.append(f"read_start:{slave}:{address}")
        delay = self.delay_per_unit.get(slave, self.op_delay)
        if delay:
            import asyncio

            await asyncio.sleep(delay)
        unknown_unit = self.only_respond_to_known_units and slave not in self.known_units
        block_failure = count in self.fail_read_block_addresses.get(slave, {}).get(address, set())
        if unknown_unit or address in self.fail_read_addresses.get(slave, set()) or block_failure:
            self.op_log.append(f"read_end:{slave}:{address}")
            raise ModbusException(f"simulated read failure at {address} (unit {slave})")
        if address in self.error_response_read_addresses.get(slave, set()):
            self.op_log.append(f"read_end:{slave}:{address}")
            # No `registers` attribute — a real pymodbus ExceptionResponse doesn't
            # have one either. hub.py must check isError() before ever touching
            # .registers; keeping this fake strict means a future change that
            # swaps that order gets caught here as an AttributeError, not silently
            # passed by a mock that's more forgiving than the real client.
            return SimpleNamespace(isError=lambda: True)
        regs = self.registers.get(slave, {})
        values = [regs.get(address + i, 0) for i in range(count)]
        self.op_log.append(f"read_end:{slave}:{address}")
        return SimpleNamespace(registers=values, isError=lambda: False)

    async def write_register(self, address: int, value: int, *, slave: int = 1):
        self.write_log.append((slave, address, value))
        self.op_log.append(f"write_start:{slave}:{address}")
        if self.op_delay:
            import asyncio

            await asyncio.sleep(self.op_delay)
        if self.fail_write:
            self.op_log.append(f"write_end:{slave}:{address}")
            raise ModbusException(f"simulated write failure at {address}")
        if address in self.error_response_write_addresses.get(slave, set()):
            self.op_log.append(f"write_end:{slave}:{address}")
            return SimpleNamespace(isError=lambda: True)
        regs = self.registers.setdefault(slave, {})
        regs[address] = value
        self.op_log.append(f"write_end:{slave}:{address}")
        return SimpleNamespace(isError=lambda: False)

    async def read_input_registers(self, *args: Any, **kwargs: Any):
        # FC04 is not supported by the real AB64 — this fake deliberately has no
        # working implementation so any accidental call fails loudly (case 4).
        raise AssertionError(
            "read_input_registers (FC04) was called — AB64 only supports FC03 "
            "(holding registers). See CLAUDE.md / reference section on function codes."
        )


@pytest.fixture
def fake_clients() -> dict[tuple[str, int], FakeModbusClient]:
    """Registry of FakeModbusClient instances keyed by (host, port).

    Pre-seed `fake_clients[(host, port)] = FakeModbusClient(...)` before triggering
    hub creation to control register values / failure modes; otherwise one is
    auto-created (all registers default to 0, connect succeeds) on first use.
    """
    return {}


@pytest.fixture(autouse=True)
def mock_modbus_client(monkeypatch: pytest.MonkeyPatch, fake_clients: dict) -> dict:
    def _factory(host: str, port: int | None = None, **kwargs: Any) -> FakeModbusClient:
        key = (host, port)
        if key not in fake_clients:
            fake_clients[key] = FakeModbusClient(host, port)
        return fake_clients[key]

    monkeypatch.setattr("custom_components.carr_ab64.hub.AsyncModbusTcpClient", _factory)
    return fake_clients


def seed_happy_path(
    fake_clients: dict[tuple[str, int], FakeModbusClient],
    *,
    host: str = TEST_HOST,
    port: int = TEST_PORT,
    unit_id: int = TEST_UNIT_ID,
    basic_regs: list[int] | None = None,
) -> FakeModbusClient:
    """Create (or reuse) the fake client for (host, port) with happy-path registers."""
    key = (host, port)
    client = fake_clients.setdefault(key, FakeModbusClient(host, port))
    client.set_registers(unit_id, 0, basic_regs or list(HAPPY_BASIC_REGS))
    return client
