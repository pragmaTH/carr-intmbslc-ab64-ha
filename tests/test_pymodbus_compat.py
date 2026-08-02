"""P1 item 5: verify the real, installed pymodbus package (not the FakeModbusClient
used everywhere else in this suite) still matches every low-level assumption
hub.py makes about it.

Every other test file mocks at the AsyncModbusTcpClient boundary, which is correct
for testing our own logic — but it means those tests would keep passing even if a
pymodbus upgrade silently broke one of these assumptions. This file is the one
place that talks to the real pymodbus.client.AsyncModbusTcpClient class.

manifest.json pins `pymodbus>=3.8.6,<4` specifically because pymodbus renamed its
unit-id kwarg slave= -> device_id= in 3.10.0, and HA core 2025.10.1 itself pins
pymodbus==3.11.2 (a custom integration shares the HA python env, so whatever HA
core resolves is what we actually get at runtime) while this repo's dev machine
happens to have 3.8.6 installed — see AB64Hub._resolve_unit_kwarg() in hub.py.
"""
from __future__ import annotations

import inspect

import pymodbus
import pytest
from pymodbus import FramerType
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException

from custom_components.carr_ab64.hub import AB64Hub


@pytest.fixture(autouse=True)
def mock_modbus_client():
    """Shadow conftest's autouse fixture of the same name: this file specifically
    needs the REAL pymodbus AsyncModbusTcpClient, not FakeModbusClient, so it must
    NOT be patched here."""
    yield


def test_installed_pymodbus_version_note(capsys):
    """Not an assertion — just surfaces which pymodbus this run actually exercised,
    since requirements_test.txt intentionally leaves the exact version to pip's
    resolver within manifest.json's >=3.8.6,<4 range."""
    print(f"\n[test_pymodbus_compat] installed pymodbus version: {pymodbus.__version__}")


def test_framer_type_socket_exists():
    assert FramerType.SOCKET is not None


def test_async_client_init_accepts_timeout_and_retries():
    sig = inspect.signature(AsyncModbusTcpClient.__init__)
    assert "timeout" in sig.parameters
    assert "retries" in sig.parameters


def test_connection_exception_is_a_modbus_exception():
    """hub.py relies on this: a dropped connection raised *during* a read/write
    must be catchable by the same `except ModbusException` branch that wraps
    AB64ReadError/AB64WriteError, not slip through uncaught."""
    assert issubclass(ConnectionException, ModbusException)


def test_close_is_not_a_coroutine_function():
    """hub.py calls `self._client.close()` without awaiting it — this must stay
    synchronous across pymodbus versions or that call silently does nothing
    (a bare unawaited coroutine) instead of actually closing the socket."""
    assert not inspect.iscoroutinefunction(AsyncModbusTcpClient.close)


def test_read_and_write_expose_a_known_unit_id_kwarg():
    """Whatever pymodbus version is installed, its client methods must expose one
    of the kwarg names hub.py's _resolve_unit_kwarg() knows how to look for."""
    read_params = inspect.signature(AsyncModbusTcpClient.read_holding_registers).parameters
    write_params = inspect.signature(AsyncModbusTcpClient.write_register).parameters
    known = {"device_id", "slave", "unit"}
    assert known & set(read_params), f"no known unit kwarg in {list(read_params)}"
    assert known & set(write_params), f"no known unit kwarg in {list(write_params)}"


async def test_real_ab64hub_resolves_unit_kwarg_against_real_client():
    """End-to-end: construct a *real* AB64Hub (real AsyncModbusTcpClient inside,
    not FakeModbusClient) against whatever pymodbus is actually installed, and
    confirm _resolve_unit_kwarg() succeeds without raising. This is the strongest
    test in this file — it exercises the exact code path hub.py runs in production,
    not a hand-written stub of what we believe the signature looks like.

    Manually re-verified against a standalone `pip install pymodbus==3.11.2`
    (separate from this suite's requirements_test.txt env, since HA 2025.10.1 pins
    that exact version) during this test round: resolved to 'device_id', matching
    the P1 fix's expectation. See done/qa-core-fix.md for that transcript.
    """
    hub = AB64Hub("192.0.2.1", 502)
    assert hub._client.__class__ is AsyncModbusTcpClient  # not FakeModbusClient
    kwarg = hub._resolve_unit_kwarg()
    assert kwarg in {"device_id", "slave", "unit"}
