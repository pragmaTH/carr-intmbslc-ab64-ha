# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This repo now contains a real Home Assistant custom integration at `custom_components/carr_ab64/` (HACS custom-repository distribution, public under `pragmaTH/carr-intmbslc-ab64-ha`), plus everything learned during a live bring-up session (2026-07-31, **historical** — see below) connecting to a real Carrier-Toshiba AB64 Modbus interface (Intesis INMBSTOS001R000) that the integration was built from.

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

Each link can fail independently and silently. The AB64 is a protocol translator, not a Modbus-native device — one side speaks the AC's proprietary AB Bus, the other speaks Modbus RTU. It is powered from the AB Bus side (from the AC), not from the Modbus side. Unit-id address space is 1–64 (corrected 2026-08-05 — see the DIP-switch bullet below; it used to be documented as 0–63, which was off by one). **"Up to 64 boxes per Modbus network" is a count of valid addresses, not a vendor-stated device maximum** — see the qualifier in the topology bullet below (M-3, review flagged 2026-08-05).

Confirmed-working configuration from this bring-up: IP `192.168.14.149:502`, unit-id `2`, `9600 8N2`.

## Non-obvious facts that must carry into any integration built from this reference

**Historical context, still load-bearing**: these facts come from the 2026-07-31 bring-up session referenced above. They are no longer forward-looking guidance for "a future integration" — they are the reasons behind specific decisions already implemented in `custom_components/carr_ab64/` (e.g. shared hub/refcount in `hub.py`, the 65535 debounce in `coordinator.py`, FC03-only in `hub.py`). Treat them as design-rationale documentation for the existing code, not just a checklist for someone starting fresh.

These are documented in full (with the reasoning/evidence behind each) in `references/ac-modbus-ab64-reference.md` — read it before implementing anything that touches this device. Highlights:

- **Only FC03 (Holding Registers) works.** FC04 (Input Registers) is not supported, even for read-only fields. Don't offer it as an option. This is empirical-only — the vendor's user manual (Intesis INMBSTOS001R000 r2.7 EN) never mentions Modbus function codes at all, so this fact rests on the bring-up log, not on documentation.
- **Multiple AB64 boxes sharing one gateway is the vendor's standard topology, not an edge case.** The manual's own wiring diagram shows one AB64 wired 1:1 to exactly one AC indoor unit; multiple such 1:1 pairs can share one RS-485 bus/gateway, distinguished by unit-id (confirmed **1–64**, 64 slots, from the full SW1+SW2 DIP table — see the corrected DIP-switch bullet below). **"Up to 64 boxes" is derived by counting valid unit-id addresses, not a line the manual states directly as a device-count maximum** — corrected 2026-08-05 (review `unitstep-review.md` M-3) after the same mistake was caught in the 63→64 off-by-one fix: the old "63 boxes" figure was *also* just an address-space count, so simply computing it correctly (64) doesn't turn it into vendor-confirmed spec. The manual may have a separate device-count limit driven by RS-485 electrical loading (standard RS-485 typically supports 32 unit loads per segment without a repeater) or poll bandwidth — neither has been checked. The bus-math table in `README.md`'s poll-interval option already shows a shared bus getting congested well before 64 boxes, so don't repeat "64" as if it were a tested or vendor-endorsed ceiling. Any integration MUST share the underlying Modbus connection/lock across config entries that target the same host:port — a per-config-entry connection breaks the moment a user has two AC units on one gateway.
- **Register 11 == 65535 can be transiently true for up to ~17 seconds after power-up or an AC-side blip** (5s LED warm-up + a documented 12-second internal AB64→AC comms timeout) **and this is not a permanent fault.** Debounce before surfacing it as a persistent problem/binary_sensor state, or every HA restart will look like a hardware failure.
- **DIP switch → unit-id is a 1-based formula, not a direct reading.** `unit-id = SW2 × 16 + SW1 + 1`. Verified against the vendor's full SW1/SW2 table (sent by the user 2026-08-05): (0,0)→1, (0,1)→2, (1,0)→17, (3,B)→60 — the table is 1-based, so the true address space is **1–64**, not 0–63. **Corrected 2026-08-05**: this bullet used to say "don't trust the DIP-switch address" because the unit-id that worked in the 2026-07-31 bring-up (`2`) didn't match what the switches appeared to show (`1`) — that framing was wrong. The switches were read correctly (confirmed twice); the team simply forgot the `+1`: switches (SW2=0, SW1=1) → `0×16 + 1 + 1 = 2`, exactly the working unit-id. The lesson is "always run the DIP reading through the formula," not "don't trust the switches." Scanning a unit-id range and confirming against a real register read is still worth keeping in the config flow — as a safety net for anyone who fat-fingers the formula (as this team did), not because the hardware/DIP reading is unreliable.
- **DIP switches only take effect on power-up.** Changing SW1/SW2 (address) or SW3 (baudrate) requires a full power cycle of the AB64 box, not just re-polling.
- **The AB64's two terminal blocks are easy to cross.** `A B — AC UNIT` goes to the AC control board (AB Bus, not Modbus); `MODBUS RS-485 — B A G` goes to the TCP↔RTU gateway. Note the gateway-side terminal is silkscreened `B A G` left-to-right, not the more intuitive `A B G` — compare printed letters, not position. Wrong wiring here produces total silence at every layer (TCP connects fine, gateway forwards fine) with no distinguishing symptom.
- **Register 11 == 65535 is a distinct fault class.** It means the AB64 itself has lost its AB Bus link to the AC (hardware-level), separate from both normal AC error codes and a Modbus timeout. Surface it as "gateway can't reach the unit," not as a generic AC error.
- **Separate "TCP connect failed" from "TCP connected but Modbus timed out."** These point to different layers (network/gateway config vs. addressing/wiring) — collapsing both into a generic "unavailable" state defeats debugging.
- **Serialize all register access through one coordinator/connection.** This is RS-485 behind a single TCP↔RTU gateway; concurrent requests from multiple entities will collide or serialize unpredictably. Don't create one Modbus client per entity.
- **Port defaults vary per gateway — the EW11 used in this bring-up actually defaulted to 8899, not 502.** That fact is still true and is why the config flow's port field warns about it directly. **However, as of 2026-08-04 the user decided the config flow *should* default the port field to `502`** (the Modbus TCP standard, and the same default `homeassistant/components/modbus` itself uses) — this reverses the original guidance here, which said not to default it to 502 at all. Reasoning: leaving the field with no default rendered as `0` on the frontend, which is wrong 100% of the time, strictly worse than a default that's sometimes wrong. The EW11/8899 risk is mitigated by keeping the 8899 warning in the port field's `data_description` (guarded by a test that fails if it's ever removed) rather than by refusing to default the field. IP, port, and unit-id remain three separate required fields — that part of the original guidance still holds.
- **The gateway's TX/RX byte counters (Status page) are the fastest diagnostic tool** for bisecting where a request dies: network-side received bytes, serial-side sent bytes, serial-side received bytes — check in that order.
- **AB64 has two independent status LEDs**: blue = Modbus frame received with correct address; green = AB Bus link to the AC unit. They indicate different link segments — don't conflate them when troubleshooting.
- **Decision 2026-08-05 — config flow UX + polling, from real-world v0.1.2 usage feedback:**
  - **No auto-pick of unit-id, ever** — even when a scan finds exactly one responding unit-id, the config flow still shows a confirmation step rather than silently using it. The previous behavior (silent auto-pick on a single match) meant three different flows depending on scan results, which users could not predict.
  - unit-id lives in the *first* step alongside name/host/port, not a separate step — leaving it blank still triggers a scan.
  - The scan itself is kept (it's not being removed) — see the corrected DIP-switch bullet above for why: it's a safety net for formula mistakes, not because DIP hardware is unreliable.
  - `MIN_SCAN_INTERVAL` lowered from 10s to 1s (default poll interval is still 30s — this only changes the floor a user can configure). This came from a user reporting climate-card updates taking ~10s after issuing a command; polling faster is the fix, paired with tolerating a few consecutive failed reads (see next bullet) so a faster poll doesn't make transient RS-485 hiccups visibly flicker every entity.
  - **Do not add optimistic state** (showing the just-issued command's value immediately instead of what was actually read back), under any circumstance, even if it looks like an obvious UX improvement. Reasoning, stated directly by the user: people buy this integration specifically because they want a value that reflects what the hardware actually reports — showing the commanded value instead of the read-back value defeats the reason they installed it. If a future change proposes optimistic state to "fix" perceived latency, that is re-introducing something the user explicitly rejected — poll faster / tolerate more misses instead.

The full register map (basic R/W registers 0–4, status registers, advanced indoor/outdoor telemetry at 4012+/4410+) and the complete error code table (register 11, categorized C/E/F/H/L/P codes) are in the reference doc — don't duplicate them here, look them up there when needed.
