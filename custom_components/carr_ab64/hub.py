"""Shared Modbus TCP hub for AB64 boxes, keyed by (host, port).

Multiple AB64 boxes sharing one TCP<->RTU gateway is the vendor's standard
topology (up to 64 boxes per RS-485 bus, matching the 1-64 SW1+SW2 address space —
see DEFAULT_UNIT_ID_SCAN_RANGE in const.py). RS-485 can only carry one frame at a
time, so every config entry pointing at the same (host, port) MUST share a single
`AsyncModbusTcpClient` and a single `asyncio.Lock` covering both reads and writes,
even though each entry only talks to its own unit_id.
"""
from __future__ import annotations

import asyncio
import inspect
import logging

from pymodbus import FramerType
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MODBUS_TIMEOUT = 3
# Retries=1 (pymodbus default is 3) is intentional: this box answers fast when it's
# going to answer at all, and a busy shared RS-485 bus (multiple AB64 boxes on one
# gateway) benefits more from failing a probe quickly than from retrying into a
# collision. Applies to both unit-id scanning and normal polling.
MODBUS_RETRIES = 1

# pymodbus renamed its unit-id kwarg `slave=` -> `device_id=` in 3.10.0 (the same
# method signatures otherwise). HA core 2025.10.1's built-in `modbus` integration
# pins `pymodbus==3.11.2` (device_id=); this repo's dev machine has 3.8.6 (slave=)
# installed. Both must work without editing this file, since custom integrations
# share the HA Python env with core's pinned pymodbus. Resolved once per client via
# inspect.signature() and cached, instead of a try/except TypeError scattered across
# every call site.
_UNIT_KWARG_CANDIDATES = ("device_id", "slave", "unit")

_HUBS_KEY = "hubs"
_HUBS_LOCK_KEY = "hubs_lock"


class AB64ReadError(Exception):
    """Register read failed after a successful TCP connection (timeout / error response).

    Distinct from `ConnectionException` (TCP connect itself failed) so callers can
    surface "register read timeout" separately from "cannot connect".
    """


class AB64WriteError(Exception):
    """Register write failed after a successful TCP connection."""


class AB64Hub:
    """Singleton Modbus TCP client + lock shared by every config entry on one (host, port)."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.refcount = 0
        self._client = AsyncModbusTcpClient(
            host,
            port=port,
            framer=FramerType.SOCKET,
            timeout=MODBUS_TIMEOUT,
            retries=MODBUS_RETRIES,
        )
        self._lock = asyncio.Lock()
        self._unit_kwarg: str | None = None

    def _resolve_unit_kwarg(self) -> str:
        """Detect, once, which kwarg this installed pymodbus version's client uses
        for the unit/device id, and cache it for every read/write on this hub."""
        if self._unit_kwarg is None:
            params = inspect.signature(self._client.read_holding_registers).parameters
            for name in _UNIT_KWARG_CANDIDATES:
                if name in params:
                    self._unit_kwarg = name
                    break
            else:
                raise RuntimeError(
                    "Installed pymodbus client's read_holding_registers() exposes "
                    f"none of {_UNIT_KWARG_CANDIDATES} as a keyword argument — "
                    "pymodbus's API has changed in a way this integration doesn't "
                    "know how to handle yet."
                )
        return self._unit_kwarg

    async def async_connect(self) -> None:
        if self._client.connected:
            return
        await self._client.connect()
        if not self._client.connected:
            raise ConnectionException(f"Unable to connect to {self.host}:{self.port}")

    async def async_close(self) -> None:
        self._client.close()

    async def async_read_holding(self, unit_id: int, address: int, count: int) -> list[int]:
        """Read `count` holding registers starting at `address` (FC03 only — FC04 is unsupported)."""
        async with self._lock:
            if not self._client.connected:
                raise ConnectionException(f"Not connected to {self.host}:{self.port}")
            try:
                result = await self._client.read_holding_registers(
                    address, count=count, **{self._resolve_unit_kwarg(): unit_id}
                )
            except ModbusException as err:
                raise AB64ReadError(
                    f"Register read timeout/error at {self.host}:{self.port} unit "
                    f"{unit_id}, address {address}: {err}"
                ) from err
            if result.isError():
                raise AB64ReadError(
                    f"Register read timeout/error at {self.host}:{self.port} unit "
                    f"{unit_id}, address {address}: {result}"
                )
            return list(result.registers)

    async def async_write_register(self, unit_id: int, address: int, value: int) -> None:
        async with self._lock:
            if not self._client.connected:
                raise ConnectionException(f"Not connected to {self.host}:{self.port}")
            try:
                result = await self._client.write_register(
                    address, value, **{self._resolve_unit_kwarg(): unit_id}
                )
            except ModbusException as err:
                raise AB64WriteError(
                    f"Register write failed at {self.host}:{self.port} unit "
                    f"{unit_id}, address {address}: {err}"
                ) from err
            if result.isError():
                raise AB64WriteError(
                    f"Register write failed at {self.host}:{self.port} unit "
                    f"{unit_id}, address {address}: {result}"
                )


async def async_acquire_hub(hass: HomeAssistant, host: str, port: int) -> AB64Hub:
    """Get (creating if needed) the shared hub for (host, port) and bump its refcount.

    Connects (or reconnects) under the same creation lock so two entries racing to
    set up on the same gateway can't create two clients for one (host, port).
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    hubs: dict[tuple[str, int], AB64Hub] = domain_data.setdefault(_HUBS_KEY, {})
    creation_lock: asyncio.Lock = domain_data.setdefault(_HUBS_LOCK_KEY, asyncio.Lock())

    key = (host, port)
    async with creation_lock:
        hub = hubs.get(key)
        if hub is None:
            hub = AB64Hub(host, port)
            hubs[key] = hub
        try:
            await hub.async_connect()
        except ConnectionException:
            if hub.refcount == 0:
                hubs.pop(key, None)
            raise
        hub.refcount += 1
        return hub


async def async_release_hub(hass: HomeAssistant, host: str, port: int) -> None:
    """Drop a reference to the shared hub for (host, port); close it only at refcount 0."""
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        return
    hubs: dict[tuple[str, int], AB64Hub] = domain_data.get(_HUBS_KEY, {})
    creation_lock: asyncio.Lock | None = domain_data.get(_HUBS_LOCK_KEY)
    if creation_lock is None:
        return

    key = (host, port)
    async with creation_lock:
        hub = hubs.get(key)
        if hub is None:
            return
        hub.refcount -= 1
        if hub.refcount <= 0:
            hubs.pop(key, None)
            await hub.async_close()
