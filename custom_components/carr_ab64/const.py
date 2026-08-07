"""Constants for the Carrier-Toshiba AB64 (Modbus) integration.

Register map and error code table are transcribed from
`references/ac-modbus-ab64-reference.md` (sections 5 and 6) — do not duplicate the
full tables as comments elsewhere, look them up there when needed.
"""
from __future__ import annotations

DOMAIN = "carr_ab64"

CONF_UNIT_ID = "unit_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ENABLE_ADVANCED = "enable_advanced_telemetry"

# --- Basic block registers (0-11), Holding Registers / FC03 only — FC04 is not
# supported by this device even for read-only fields (empirical, bring-up log only,
# the vendor manual never mentions Modbus function codes at all). ---
REG_ON_OFF = 0
REG_MODE = 1
REG_FAN = 2
REG_SWING = 3
REG_SETPOINT = 4
REG_ERROR = 11
REG_SW_VERSION = 50

BASIC_BLOCK_START = 0
BASIC_BLOCK_COUNT = 12

# --- Advanced telemetry blocks (opt-in only, see options flow) ---
# Addresses inside these blocks that aren't listed below (4015, 4016, 4018, 4019,
# 4414, 4416) are read as part of the block but discarded — they have no matching
# field in the vendor manual.
ADV_INDOOR_START = 4012
ADV_INDOOR_COUNT = 9
ADV_INDOOR_FIELDS: dict[int, str] = {
    4012: "indoor_temp",
    4013: "indoor_coil_temp_tcj",
    4014: "indoor_coil_temp_tc2",
    4017: "indoor_fan_rpm",
    4020: "filter_sign_timer",
}

ADV_OUTDOOR_START = 4410
ADV_OUTDOOR_COUNT = 9
ADV_OUTDOOR_FIELDS: dict[int, str] = {
    4410: "outdoor_evaporator_temp_te",
    4411: "outdoor_temp_to",
    4412: "outdoor_discharge_temp_td",
    4413: "outdoor_suction_temp_ts",
    4415: "compressor_current",
    4417: "compressor_rpm",
    4418: "lowest_fan_rpm",
}

# Which advanced fields are temperatures and therefore need signed decoding
# (16-bit two's complement) — rpm/current/timer fields never go negative.
# The reference doc (section 5) does not state signedness or a scaling factor for
# any advanced register; this is an inference, not a vendor-confirmed fact — flagged
# in the integration-core-fix done report for hardware verification.
ADV_TEMPERATURE_FIELDS: frozenset[str] = frozenset(
    {
        "indoor_temp",
        "indoor_coil_temp_tcj",
        "indoor_coil_temp_tc2",
        "outdoor_evaporator_temp_te",
        "outdoor_temp_to",
        "outdoor_discharge_temp_td",
        "outdoor_suction_temp_ts",
    }
)
# Sentinel for "no value" on advanced registers — same 0xFFFF pattern as register 11.
ADV_NO_VALUE = 0xFFFF

# HA's own const module only defines REVOLUTIONS_PER_MINUTE, not a per-second variant,
# so this one is ours. Needed for compressor_rpm specifically: a real ramp test
# (idle -> high load) measured 23-58 on that register while the discharge pipe (TD)
# read ~80°C the whole time — at that heat output the compressor cannot be turning at
# 23-58 RPM (that would mean it's nearly stopped), so the raw value has to be
# revolutions per *second*, not per minute. compressor_current is presented as a raw
# unscaled number instead (see ADV_SENSOR_META in sensor.py) rather than getting a
# similar unit guess, since current has no equivalent physical tell to confirm a scale
# factor against.
REVOLUTIONS_PER_SECOND = "rps"

# --- Value maps (register 0-3) ---
MODE_AUTO = 0
MODE_HEAT = 1
MODE_DRY = 2
MODE_FAN = 3
MODE_COOL = 4

FAN_AUTO = 0
FAN_LOW = 1
FAN_MEDIUM = 2
FAN_HIGH = 3

SWING_OFF = 0
SWING_ON = 10  # not 1 — confirmed in reference section 5

TEMP_MIN = 16
TEMP_MAX = 32
TEMP_STEP = 1

# --- AB Bus link fault (register 11 == 65535) ---
# This is a distinct fault class: the AB64 itself has lost its AB Bus link to the AC
# (hardware-level), separate from both normal AC error codes and a Modbus timeout.
AB_BUS_ERROR_VALUE = 65535
# 12s AB64-internal AC-comms timeout + 5s LED warm-up on power-up = up to ~17s of
# legitimate transient 65535 readings (reference sections 4 and 8). Debounce past
# that before surfacing a "problem" state, or every HA restart/reload looks like a
# hardware failure.
AB_BUS_FAULT_DEBOUNCE_SECONDS = 20

# --- Defaults ---
# 502 is the Modbus TCP standard port and the default homeassistant.components.modbus
# itself uses. This reverses the earlier "don't default the port" guidance in
# references/ac-modbus-ab64-reference.md (decision reversed by the user 2026-08-04):
# the empty NumberSelector box was rendering as 0, which min=1 always rejects, so the
# old "no default" behavior guaranteed a submit error rather than avoiding one. The
# risk this was meant to avoid (e.g. an Elfin EW11 actually defaulting to 8899) is now
# covered by a warning in the port field's data_description instead of an empty field.
DEFAULT_PORT = 502
# 5s default (2026-08-05, was 30s): measured 115 ms per register-block read, 2
# blocks/poll by default (basic block + the indoor advanced block, which is now
# always read for current_temperature) — 4 AB64 units sharing one gateway at 5s each
# comes to 18.4% of the RS-485 bus's time (27.6% with advanced telemetry on for all
# 4), comfortably under saturation. 30s made changes made from a remote/panel (not
# issued through HA) take up to 30s to show up on the card, slower than the bus math
# actually requires. Must stay greater than POST_WRITE_REFRESH_DELAY below — if it
# ever drops to <= that value, _async_schedule_post_write_refresh() returns
# immediately every time and the 0.1.7 follow-up-read feature goes silently dead.
DEFAULT_SCAN_INTERVAL = 5
# Lowered from 10 (2026-08-05): after a climate command, users want the card to
# reflect a value actually read back rather than an optimistic one we just wrote —
# the fix is to poll faster, not to fake the state. 1s is only safe together with
# MAX_CONSECUTIVE_READ_FAILURES below: a single dropped RS-485 frame at this
# interval is common and must not flap every entity to unavailable.
MIN_SCAN_INTERVAL = 1
# Consecutive failed basic-block reads the coordinator tolerates — holding onto the
# last known self.data — before it raises UpdateFailed. Exists specifically to make
# MIN_SCAN_INTERVAL = 1 usable: without this, one missed frame at a 1s interval would
# make every entity flicker unavailable far more often than at the old 10s floor.
MAX_CONSECUTIVE_READ_FAILURES = 3

# Per-advanced-block backoff (2026-08-05): the indoor advanced block (4012+) is now
# read on every poll regardless of the advanced-telemetry option, to give
# climate.current_temperature a value from first install. That turns a register that
# doesn't exist on some AC model/firmware from an opt-in risk the user chose into a
# default-on one everyone is exposed to — and a register that simply never responds
# (as opposed to answering with a Modbus exception or the 0xFFFF sentinel, both of
# which degrade instantly) costs a full read timeout every single poll, forever. At
# MIN_SCAN_INTERVAL = 1s that's not a degraded feature, it's a broken integration.
# UNSUPPORTED_BLOCK_FAILURES bounds that cost: after this many consecutive failures on
# one advanced block, stop reading it and wait before trying again — separate from
# MAX_CONSECUTIVE_READ_FAILURES above, which is a basic-block-only, UpdateFailed-only
# mechanism and must not be affected by this.
UNSUPPORTED_BLOCK_FAILURES = 3
# Ladder, not a flat delay (decision reversed 2026-08-05, replacing a flat 600s): at
# MIN_SCAN_INTERVAL = 1s, a block trips (hits UNSUPPORTED_BLOCK_FAILURES) after only
# 3-18 seconds of real elapsed time depending on whether failures are timeouts or fast
# error responses. A flat 600s pause is the wrong punishment for the far more common
# case — a transient RS-485 hiccup — since the hardware may already be answering again
# 20 seconds later, but current_temperature would still be gone from the card for the
# next 10 minutes. Climbing 60s -> 300s -> 600s recovers fast from a brief stumble
# while a block that's genuinely unsupported by this AC model/firmware still lands on
# the same 600s ceiling as before (no extra bus cost in the case the flat delay was
# originally sized for) — it just takes two failed retries to get there instead of
# zero. Indexed by a per-block retry step counter (see AB64Coordinator._block_retry_step)
# that only advances on failure and resets to 0 on any success.
UNSUPPORTED_BLOCK_RETRY_LADDER = (60, 300, 600)

# Post-write follow-up refresh (2026-08-05): measured on real hardware that a
# command takes ~1.7s to be reflected back in the AB64's registers (setpoint 1.71s;
# mode 1.76s / 1.65s — three consistent measurements at a 1s poll interval), so the
# immediate async_refresh() right after a write (see AB64Coordinator.async_write)
# always reads back the pre-write value. Without a follow-up, the next read that
# could show the settled value is whatever the regular poll interval leaves the user
# waiting for — up to the full DEFAULT_SCAN_INTERVAL (5s) even though the hardware
# was ready after about 1 second. This schedules exactly one extra read this far
# after a write, once, to close that gap — see async_write for why it's still a real
# read and not optimistic state.
POST_WRITE_REFRESH_DELAY = 2
# Full DIP address space, 64 values starting at 1 (not 0): the vendor's SW1+SW2
# table is 1-based (unit-id = SW2 * 16 + SW1 + 1 — confirmed against the table's own
# worked examples, e.g. SW2=0,SW1=0 -> 1 and SW2=3,SW1=15 -> 64). This corrects
# references/ac-modbus-ab64-reference.md:54, which had the lower end wrong — a
# transcription error, not a hardware quirk; address 0 was never valid.
DEFAULT_UNIT_ID_SCAN_RANGE = range(1, 65)

# --- Error code table (register 11), reference section 6 ---
# key = decimal code, value = (remote_code, description, category).
# 65535 is intentionally NOT included here — it is a distinct AB Bus link fault
# surfaced via binary_sensor (see AB_BUS_ERROR_VALUE above), not a normal AC error.
ERROR_CODES: dict[int, tuple[str, str, str]] = {
    # Central Controller issues
    33: ("C01", "Duplicated setting of control address", "central_controller"),
    34: ("C02", "Central control number of units mis-matched", "central_controller"),
    35: ("C03", "Incorrect wiring of central control", "central_controller"),
    36: ("C04", "Incorrect connection of central control", "central_controller"),
    37: (
        "C05",
        "System Controller fault, error transmitting comms signal, i/door or "
        "o/door unit not working, wiring fault",
        "central_controller",
    ),
    38: (
        "C06",
        "System Controller fault, error receiving comms signal, CN1 not "
        "connected correctly",
        "central_controller",
    ),
    44: ("C12", "Batch alarm by local controller", "central_controller"),
    48: ("C16", "Transmission error from adaptor to unit", "central_controller"),
    49: ("C17", "Reception error to adaptor from unit", "central_controller"),
    50: ("C18", "Duplicate central address in adaptor", "central_controller"),
    51: ("C19", "Duplicate adaptor address", "central_controller"),
    52: ("C20", "Mix of PAC & GHP type units on adaptor", "central_controller"),
    53: ("C21", "Memory fault in adaptor", "central_controller"),
    54: ("C22", "Incorrect address setting in adaptor", "central_controller"),
    55: ("C23", "Host terminal software failure", "central_controller"),
    56: ("C24", "Host terminal hardware failure", "central_controller"),
    57: ("C25", "Host terminal processing failure", "central_controller"),
    58: ("C26", "Host terminal communication failure", "central_controller"),
    60: ("C28", "Reception error of S-DDC from host terminal", "central_controller"),
    61: ("C29", "Initialization failure of S-DDC", "central_controller"),
    63: ("C31", "Configuration change detected by adaptor", "central_controller"),
    # Addressing & communication problems
    65: (
        "E01",
        "Remote detecting error from indoor unit; address not set / "
        "auto-address failed",
        "addressing_comms",
    ),
    66: ("E02", "Remote detecting error from indoor unit", "addressing_comms"),
    67: ("E03", "Indoor unit detecting error from remote", "addressing_comms"),
    68: ("E04", "Indoor units connected less than qty set", "addressing_comms"),
    69: (
        "E05",
        "Indoor unit error from outdoor, sending comms signal",
        "addressing_comms",
    ),
    70: (
        "E06",
        "Outdoor unit error from indoor, receiving comms signal",
        "addressing_comms",
    ),
    71: (
        "E07",
        "Outdoor unit error from indoor, sending comms signal",
        "addressing_comms",
    ),
    72: ("E08", "Indoor address duplicated", "addressing_comms"),
    73: (
        "E09",
        "Remote address duplicated / IR wireless not disabled",
        "addressing_comms",
    ),
    74: ("E10", "Error from 'option' plug, sending comms signal", "addressing_comms"),
    75: (
        "E11",
        "Error from 'option' plug, receiving comms signal",
        "addressing_comms",
    ),
    76: (
        "E12",
        "Auto address connector CN100 shorted during auto-addressing",
        "addressing_comms",
    ),
    77: ("E13", "Indoor unit failed to send signal to remote", "addressing_comms"),
    78: ("E14", "Duplication of master indoor units", "addressing_comms"),
    79: ("E15", "Indoor units connected fewer than number set", "addressing_comms"),
    80: ("E16", "Indoor units connected more than number set", "addressing_comms"),
    81: ("E17", "Group wiring: main not sending to subs", "addressing_comms"),
    82: ("E18", "Group wiring: main not receiving from subs", "addressing_comms"),
    83: ("E19", "Outdoor header units quantity error", "addressing_comms"),
    84: ("E20", "No indoor units connected", "addressing_comms"),
    87: ("E23", "Sending error between outdoor units", "addressing_comms"),
    88: ("E24", "Error on sub outdoor unit", "addressing_comms"),
    89: ("E25", "Error on outdoor unit address setting", "addressing_comms"),
    90: (
        "E26",
        "Main/sub outdoor unit quantity mismatch on PCB",
        "addressing_comms",
    ),
    92: ("E28", "Follower outdoor unit error", "addressing_comms"),
    93: (
        "E29",
        "Sub outdoor unit not receiving comms for main",
        "addressing_comms",
    ),
    95: (
        "E31",
        "Comms failure with MDC — if persists after power cycle, replace PCB",
        "addressing_comms",
    ),
    # Sensor faults
    97: ("F01", "Indoor Heat Exch inlet temp sensor failure (E1)", "sensor_fault"),
    98: ("F02", "Indoor Heat Exch freeze temp sensor failure (E2)", "sensor_fault"),
    99: ("F03", "Indoor Heat Exch outlet temp sensor failure (E3)", "sensor_fault"),
    100: (
        "F04",
        "Outdoor discharge temp sensor failure (TD/DISCH1)",
        "sensor_fault",
    ),
    101: ("F05", "Outdoor discharge temp sensor failure (DISCH2)", "sensor_fault"),
    102: (
        "F06",
        "Outdoor Heat Exch temp sensor failure (C1/EXG1)",
        "sensor_fault",
    ),
    103: (
        "F07",
        "Outdoor Heat Exch temp sensor failure (C2/EXL1)",
        "sensor_fault",
    ),
    104: ("F08", "Outdoor air temp sensor failure (TO)", "sensor_fault"),
    106: ("F10", "Indoor inlet temp sensor failure", "sensor_fault"),
    107: ("F11", "Indoor outlet temp sensor failure", "sensor_fault"),
    108: ("F12", "Outdoor intake sensor failure (TS)", "sensor_fault"),
    109: ("F13", "GHP cooling water temp sensor failure", "sensor_fault"),
    111: ("F15", "Outdoor temp sensor misconnection (TE1, TL)", "sensor_fault"),
    112: ("F16", "Outdoor high pressure sensor failure", "sensor_fault"),
    113: ("F17", "GHP cooling water temp sensor fault", "sensor_fault"),
    114: ("F18", "GHP exhaust gas temp sensor fault", "sensor_fault"),
    116: ("F20", "GHP clutch coil temp fault", "sensor_fault"),
    119: ("F23", "Outdoor Heat Exch temp sensor failure (EXG2)", "sensor_fault"),
    120: ("F24", "Outdoor Heat Exch temp sensor failure (EXL2)", "sensor_fault"),
    125: ("F29", "Indoor EEPROM error", "sensor_fault"),
    126: ("F30", "Clock function (RTC) fault", "sensor_fault"),
    127: ("F31", "Outdoor EEPROM error", "sensor_fault"),
    # Compressor issues
    129: ("H01", "Over current (Comp1)", "compressor"),
    130: ("H02", "Locked rotor current detected (Comp1)", "compressor"),
    131: ("H03", "No current detected (Comp1)", "compressor"),
    132: ("H04", "Comp-1 case thermo operation", "compressor"),
    133: ("H05", "Discharge temp not detected (Comp1)", "compressor"),
    134: ("H06", "Low pressure trip", "compressor"),
    135: ("H07", "Low oil level", "compressor"),
    136: ("H08", "Oil sensor fault (Comp1)", "compressor"),
    139: ("H11", "Over current (Comp2)", "compressor"),
    140: ("H12", "Locked rotor current detected (Comp2)", "compressor"),
    141: ("H13", "No current detected (Comp2)", "compressor"),
    142: ("H14", "Comp-2 case thermo operation", "compressor"),
    143: ("H15", "Discharge temp not detected (Comp2)", "compressor"),
    144: (
        "H16",
        "Oil level detection / magnet switch / overcurrent relay error",
        "compressor",
    ),
    149: ("H21", "Over current (Comp3)", "compressor"),
    150: ("H22", "Locked rotor current detected (Comp3)", "compressor"),
    151: ("H23", "No current detected (Comp3)", "compressor"),
    153: ("H25", "Discharge temp not detected (Comp3)", "compressor"),
    155: ("H27", "Oil sensor fault (Comp2)", "compressor"),
    156: ("H28", "Oil sensor connection failure", "compressor"),
    159: ("H31", "IPM trip (current on temperature)", "compressor"),
    # Incorrect settings
    193: ("L01", "Indoor unit group setting error", "incorrect_settings"),
    194: (
        "L02",
        "Indoor/outdoor unit type/model mismatched",
        "incorrect_settings",
    ),
    195: (
        "L03",
        "Duplication of main indoor unit address in group",
        "incorrect_settings",
    ),
    196: (
        "L04",
        "Duplication of outdoor unit system address",
        "incorrect_settings",
    ),
    197: (
        "L05",
        "2+ controllers set 'priority' — shown on priority controllers",
        "incorrect_settings",
    ),
    198: (
        "L06",
        "2+ controllers set 'priority' — shown on non-priority controllers",
        "incorrect_settings",
    ),
    199: (
        "L07",
        "Group wiring connected on an individual indoor unit",
        "incorrect_settings",
    ),
    200: ("L08", "Indoor unit address/group not set", "incorrect_settings"),
    201: ("L09", "Indoor unit capacity code not set", "incorrect_settings"),
    202: ("L10", "Outdoor unit capacity code not set", "incorrect_settings"),
    203: ("L11", "Group control wiring incorrect", "incorrect_settings"),
    205: ("L13", "Indoor unit type setting error, capacity", "incorrect_settings"),
    207: ("L15", "Indoor unit pairing fault", "incorrect_settings"),
    208: ("L16", "Water heat exch. unit setting failure", "incorrect_settings"),
    209: (
        "L17",
        "Mismatch of outdoor unit with different refrigerant",
        "incorrect_settings",
    ),
    210: ("L18", "4-way valve failure", "incorrect_settings"),
    211: (
        "L19",
        "Water heat exch. unit duplicated address",
        "incorrect_settings",
    ),
    212: ("L20", "Duplicated central control addresses", "incorrect_settings"),
    213: ("L21", "Gas type setup failure", "incorrect_settings"),
    220: (
        "L28",
        "Maximum number of outdoor units exceeded",
        "incorrect_settings",
    ),
    221: ("L29", "No. of IPDU error", "incorrect_settings"),
    222: ("L30", "Auxiliary interlock in indoor unit", "incorrect_settings"),
    223: ("L31", "IC error", "incorrect_settings"),
    # Indoor / outdoor unit problems
    225: ("P01", "Indoor fault, fan motor thermal overload", "indoor_outdoor_problem"),
    226: (
        "P02",
        "Outdoor fault, compressor motor thermal overload / over-under voltage",
        "indoor_outdoor_problem",
    ),
    227: (
        "P03",
        "Compressor discharge temp >111°C (Comp1); low ref gas / valve / pipework",
        "indoor_outdoor_problem",
    ),
    228: ("P04", "Outdoor fault, high pressure trip", "indoor_outdoor_problem"),
    229: (
        "P05",
        "Outdoor fault, open phase on power supply",
        "indoor_outdoor_problem",
    ),
    231: ("P07", "Heat sink overheat error", "indoor_outdoor_problem"),
    233: (
        "P09",
        "Indoor fault, ceiling panel incorrectly wired",
        "indoor_outdoor_problem",
    ),
    234: (
        "P10",
        "Indoor fault, condensate float switch opened",
        "indoor_outdoor_problem",
    ),
    235: (
        "P11",
        "GHP water heat exch. low temp (frost protection)",
        "indoor_outdoor_problem",
    ),
    236: ("P12", "Indoor fault, fan DC motor fault", "indoor_outdoor_problem"),
    237: (
        "P13",
        "Outdoor liquid back detection error",
        "indoor_outdoor_problem",
    ),
    238: (
        "P14",
        "Input from leak detector (if fitted)",
        "indoor_outdoor_problem",
    ),
    239: (
        "P15",
        "Refrigerant loss — high discharge temp, EEV wide open, low current draw",
        "indoor_outdoor_problem",
    ),
    240: (
        "P16",
        "Outdoor fault, open phase on compressor power supply",
        "indoor_outdoor_problem",
    ),
    241: (
        "P17",
        "Compressor discharge temp >111°C (Comp2)",
        "indoor_outdoor_problem",
    ),
    242: ("P18", "Outdoor fault, by-pass valve failure", "indoor_outdoor_problem"),
    243: ("P19", "4-way valve failure", "indoor_outdoor_problem"),
    244: (
        "P20",
        "Ref gas high temp/pressure, heat exch. temp high C2 55-60°C",
        "indoor_outdoor_problem",
    ),
    246: (
        "P22",
        "Outdoor fan motor fault / blade jammed",
        "indoor_outdoor_problem",
    ),
    250: (
        "P26",
        "Compressor overcurrent / inverter failure",
        "indoor_outdoor_problem",
    ),
    252: ("P29", "Inverter circuit fault — MDC fault", "indoor_outdoor_problem"),
    253: (
        "P30",
        "System controller detected fault on sub indoor unit",
        "indoor_outdoor_problem",
    ),
    255: (
        "P31",
        "Simultaneous operation multi-control fault / group controller fault",
        "indoor_outdoor_problem",
    ),
}
