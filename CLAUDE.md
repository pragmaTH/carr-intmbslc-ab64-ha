# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This repo now contains a real Home Assistant custom integration at `custom_components/carr_ab64/` (HACS custom-repository distribution, public under `PragmaTH/carr-intmbslc-ab64-ha`), plus everything learned during a live bring-up session (2026-07-31, **historical** — see below) connecting to a real Carrier-Toshiba AB64 Modbus interface (Intesis INMBSTOS001R000) that the integration was built from.

### Repo layout

```
custom_components/carr_ab64/   the integration itself (config_flow, coordinator, hub, entities)
tests/                         pytest suite (mocks pymodbus's AsyncModbusTcpClient only; everything
                                above that boundary — hub/coordinator/entities/config_flow — runs as
                                real code, not mocks)
references/                    the original bring-up reference doc + standalone diagnostic script
README.md / info.md / hacs.json  end-user-facing docs (HACS store page + GitHub README)
```

### Running the integration test suite

If the dev machine's default Python is newer than 3.13 (e.g. 3.14), it can't install `homeassistant==2025.10.1` directly — a transitive dependency (`pydantic-core`) has no prebuilt wheel for it and fails to build from source. Use a **Python 3.13** virtualenv instead — this is verified working, not theoretical (the whole suite passes this way):

```bash
uv venv --python 3.13 .venv-test
uv pip install --python .venv-test/bin/python -r requirements_test.txt
.venv-test/bin/python -m pytest -q
```

(`requirements_test.txt` pins `pycares<5` — see the comment above that line in the file for why: `aiodns==3.5.0`, pinned by `homeassistant==2025.10.1`, breaks on newer pycares. Test-env-only, doesn't affect the shipped integration.)

This runs entirely locally, on the exact pinned stack — don't wait for CI to find out whether it works.

### Running hassfest locally

CI's `validate.yml` runs `hassfest` (via `home-assistant/actions/hassfest@master`) and HACS validation. `hassfest` isn't a pip package, but it *can* be run locally — verified working end-to-end (`Integrations: 1, Invalid integrations: 0`), not just theoretical — by sparse-checking-out the relevant scripts from `home-assistant/core` at the tag matching this project's pinned HA version (`2025.10.1`):

```bash
# separate scratch venv with ruff (hassfest shells out to it) — capture the absolute
# path *before* cd'ing elsewhere below, or every reference to it after that breaks
uv venv --python 3.13 .venv-hassfest
VENV="$PWD/.venv-hassfest"
uv pip install --python "$VENV/bin/python" -r requirements_test.txt ruff

# sparse-checkout just the scripts hassfest needs, at the pinned tag
git init /tmp/core-hassfest && cd /tmp/core-hassfest
git remote add origin https://github.com/home-assistant/core.git
git sparse-checkout init --cone
git sparse-checkout set script pyproject.toml
git fetch --depth 1 origin 2025.10.1
git checkout FETCH_HEAD

rm -rf homeassistant  # only present with some checkout methods; shadows the pip-installed package if it is

# hassfest's docker.py imports turbojpeg transitively (via go2rtc -> camera -> img_util)
# even though it's irrelevant to this integration — stub it rather than installing the
# native lib:
cat > "$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')/turbojpeg.py" << 'EOF'
class TurboJPEG:
    def __init__(self, *a, **k): raise NotImplementedError("stub — not for runtime use")
EOF

PATH="$VENV/bin:$PATH" "$VENV/bin/python" -m script.hassfest \
    --action validate --integration-path <this-repo>/custom_components/carr_ab64
```

Do this before pushing, not after — it catches manifest/translation/config-flow issues (e.g. `manifest.json` keys not being in hassfest's required sort order) that CI would otherwise be the first to surface.

## Running the test script

```bash
pip install pymodbus
python references/ac-modbus-ab64-test.py --host 192.168.14.149 --port 502 --unit-id 2
```

- `--once` sends a single read and exits instead of polling in a loop.
- `--interval` / `--timeout` control poll cadence and per-request timeout.
- This script has no Home Assistant dependency — it's meant to confirm the AC is reachable over the network (from WSL2 or elsewhere) before wiring up the real integration.
- If the connection hangs in WSL2 but works from Windows, WSL2 networking mode is the likely cause: "mirrored" mode is required to reliably reach LAN devices; NAT mode can be flaky.

## Architecture: the 4-layer chain

```
AC indoor unit (control board)
      │  AB Bus (proprietary)
      ▼
AB64 box (Toshiba ↔ Modbus translator)
      │  RS-485, Modbus RTU
      ▼
TCP↔RTU gateway (e.g. Elfin EW11)
      │  Ethernet, Modbus TCP
      ▼
HA integration (Modbus TCP client)
```

Each link can fail independently and silently. The AB64 is a protocol translator, not a Modbus-native device — one side speaks the AC's proprietary AB Bus, the other speaks Modbus RTU. It is powered from the AB Bus side (from the AC), not from the Modbus side. Up to 63 boxes per Modbus network.

Confirmed-working configuration from this bring-up: IP `192.168.14.149:502`, unit-id `2`, `9600 8N2`.

## Non-obvious facts that must carry into any integration built from this reference

**Historical context, still load-bearing**: these facts come from the 2026-07-31 bring-up session referenced above. They are no longer forward-looking guidance for "a future integration" — they are the reasons behind specific decisions already implemented in `custom_components/carr_ab64/` (e.g. shared hub/refcount in `hub.py`, the 65535 debounce in `coordinator.py`, FC03-only in `hub.py`). Treat them as design-rationale documentation for the existing code, not just a checklist for someone starting fresh.

These are documented in full (with the reasoning/evidence behind each) in `references/ac-modbus-ab64-reference.md` — read it before implementing anything that touches this device. Highlights:

- **Only FC03 (Holding Registers) works.** FC04 (Input Registers) is not supported, even for read-only fields. Don't offer it as an option. This is empirical-only — the vendor's user manual (Intesis INMBSTOS001R000 r2.7 EN) never mentions Modbus function codes at all, so this fact rests on the bring-up log, not on documentation.
- **Multiple AB64 boxes sharing one gateway is the vendor's standard topology, not an edge case.** The manual's own wiring diagram shows one AB64 wired 1:1 to exactly one AC indoor unit; "up to 63 boxes per network" means multiple such 1:1 pairs sharing one RS-485 bus/gateway, distinguished only by unit-id (confirmed 0–63, 64 slots, from the full SW1+SW2 DIP table). Any integration MUST share the underlying Modbus connection/lock across config entries that target the same host:port — a per-config-entry connection breaks the moment a user has two AC units on one gateway.
- **Register 11 == 65535 can be transiently true for up to ~17 seconds after power-up or an AC-side blip** (5s LED warm-up + a documented 12-second internal AB64→AC comms timeout) **and this is not a permanent fault.** Debounce before surfacing it as a persistent problem/binary_sensor state, or every HA restart will look like a hardware failure.
- **Don't trust the DIP-switch address.** The unit-id that actually worked in this session (`2`) did not match what the DIP switches appeared to show (`1`), confirmed by two independent readings. Any integration should scan a small unit-id range and confirm against a real register read rather than relying on visual DIP inspection.
- **DIP switches only take effect on power-up.** Changing SW1/SW2 (address) or SW3 (baudrate) requires a full power cycle of the AB64 box, not just re-polling.
- **The AB64's two terminal blocks are easy to cross.** `A B — AC UNIT` goes to the AC control board (AB Bus, not Modbus); `MODBUS RS-485 — B A G` goes to the TCP↔RTU gateway. Note the gateway-side terminal is silkscreened `B A G` left-to-right, not the more intuitive `A B G` — compare printed letters, not position. Wrong wiring here produces total silence at every layer (TCP connects fine, gateway forwards fine) with no distinguishing symptom.
- **Register 11 == 65535 is a distinct fault class.** It means the AB64 itself has lost its AB Bus link to the AC (hardware-level), separate from both normal AC error codes and a Modbus timeout. Surface it as "gateway can't reach the unit," not as a generic AC error.
- **Separate "TCP connect failed" from "TCP connected but Modbus timed out."** These point to different layers (network/gateway config vs. addressing/wiring) — collapsing both into a generic "unavailable" state defeats debugging.
- **Serialize all register access through one coordinator/connection.** This is RS-485 behind a single TCP↔RTU gateway; concurrent requests from multiple entities will collide or serialize unpredictably. Don't create one Modbus client per entity.
- **Don't default the config flow's port to 502.** Verify per gateway — the EW11 used in this bring-up actually defaulted to 8899. IP, port, and unit-id should be three separate required fields.
- **The gateway's TX/RX byte counters (Status page) are the fastest diagnostic tool** for bisecting where a request dies: network-side received bytes, serial-side sent bytes, serial-side received bytes — check in that order.
- **AB64 has two independent status LEDs**: blue = Modbus frame received with correct address; green = AB Bus link to the AC unit. They indicate different link segments — don't conflate them when troubleshooting.

The full register map (basic R/W registers 0–4, status registers, advanced indoor/outdoor telemetry at 4012+/4410+) and the complete error code table (register 11, categorized C/E/F/H/L/P codes) are in the reference doc — don't duplicate them here, look them up there when needed.
