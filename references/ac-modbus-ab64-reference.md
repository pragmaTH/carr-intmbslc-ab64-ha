# Carrier-Toshiba AB64 Modbus Interface — Integration Reference

> เอกสารอ้างอิงสำหรับสร้าง HA integration ควบคุมแอร์ผ่าน Modbus (คนละ repo จาก TVO OT Server Room) — สรุปจาก live bring-up session วันที่ 2026-07-31 ที่ทดสอบเชื่อมต่อกล่อง AB64 จริงจนอ่านค่าได้สำเร็จ ไม่ใช่เอกสารสถานะของระบบ TVO (TVO ยังควบคุม AC ผ่าน Toshiba cloud ตามเดิม ดู `logic/status.md` และ `project_ac_rs485_migration` memory)

**Model:** CARR-INTMBSLC-AB64 (built by Intesis, รุ่น INMBSTOS001R000) — compatible with Digital Inverter & VRF line
**ยืนยันการเชื่อมต่อสำเร็จจริง:** 2026-07-31, IP `192.168.14.149:502`, unit-id `2`, `9600 8N2`
**อ่านค่าจริงได้:** `OnOff=1 Mode=0(Auto) FanSpeed=2(Med) Swing=0 SetTemp=22°C ErrorCode=0`

---

## 1. Architecture

ห่วงโซ่ 4 ชั้นระหว่าง HA integration กับ compressor แต่ละจุดพังเงียบได้อิสระต่อกัน — นี่คือเรื่องราวทั้งหมดของการ bring-up วันนี้:

```
AC indoor unit (control board)
      │  AB Bus (proprietary)
      ▼
AB64 box (Toshiba ↔ Modbus translator)
      │  RS-485, Modbus RTU
      ▼
TCP↔RTU gateway (เช่น Elfin EW11)
      │  Ethernet, Modbus TCP
      ▼
HA integration (Modbus TCP client)
```

AB64 เป็นตัวแปลโปรโตคอล ไม่ใช่ Modbus-native device — ฝั่งหนึ่งคุยด้วย **AB Bus** ที่เป็น proprietary ของแอร์ อีกฝั่งคุยด้วย Modbus RTU และ**รับไฟจากฝั่ง AB Bus (จากแอร์) ไม่ใช่จากฝั่ง Modbus** ต่อได้สูงสุด 63 กล่องต่อ Modbus network เดียวกัน

> **ยืนยันจาก user manual (Intesis INMBSTOS001R000, r2.7 EN) — ไม่ใช่แค่คำโฆษณา:** diagram หน้า 2 ของคู่มือแสดงชัดว่า **1 กล่อง AB64 ต่อกับ AC indoor unit เดียวเสมอ (1:1)** ส่วน "สูงสุด 63 กล่องต่อ network" คือการเอาหลายกล่อง (หลาย AC คนละเครื่อง) มาแชร์ RS-485 bus/gateway เดียวกัน แยกกันด้วย unit-id (0–63) เท่านั้น — **นี่คือ topology มาตรฐานที่ vendor ออกแบบไว้ให้ใช้งานจริง ไม่ใช่ edge case** ผลต่อ integration: ห้ามผูก 1 Modbus connection ต่อ 1 config entry แบบตายตัว — ต้อง share connection/lock กันข้าม config entry ถ้า host:port ตรงกัน ไม่งั้น 2 AC ผ่าน gateway เดียวกันจะชนกันบน bus จริง

## 2. Wiring — terminal 2 ชุด อย่าสลับ

จุดที่พาหลงทางนานที่สุดในการ bring-up วันนี้: กล่องมี terminal 2 ชุดแยกกันคนละหน้าที่ และชื่อคล้ายกันจนสับสนง่าย

| Terminal (silkscreen) | ต่อไปที่ | โปรโตคอล |
|---|---|---|
| `A B — AC UNIT` | แผงวงจรแอร์ | AB Bus proprietary — **ไม่ใช่ Modbus** |
| `MODBUS RS-485 — B A G` | Modbus TCP↔RTU gateway | Modbus RTU — จุดที่ต้องต่อ master |

> **Gotcha — ลำดับตัวอักษร:** terminal ฝั่ง Modbus พิมพ์ `B A G` เรียงซ้าย-ขวา ไม่ใช่ `A B G` แบบที่คุ้นเคย ต้องเทียบตัวอักษรที่พิมพ์บนบอร์ดจริง ไม่ใช่นับตำแหน่ง — ถ้าสลับขั้ว จะไม่มี symptom อะไรที่ TCP layer เลย (connect ได้ปกติ, gateway ยัง forward byte ออกไปได้ปกติ) มีแต่ความเงียบทั้งหมด

สาย run ได้ไกลสุด 500 m. `G` คือ ground/common reference ของ differential pair — ควรต่อไปที่ 0V ของ gateway ถ้าทำได้ (ใน deployment นี้สุดท้ายใช้งานได้แม้ไม่ได้ต่อ G — ถือเป็น "ใช้ได้ทั้งที่ไม่ต่อ" ไม่ใช่คำแนะนำให้ข้าม)

## 3. DIP switch configuration

- **SW1 + SW2** = ตั้ง slave address รวมกัน 0–63
- **SW3** = ตั้ง baudrate (2400 / 4800 / 9600 / 19200 / 38400 / 57600 / 76800 / 115200)

> **Gotcha — DIP switch อ่านค่าครั้งเดียวตอนจ่ายไฟ:** เปลี่ยน SW1/SW2/SW3 แล้วต้อง**ตัดไฟจริงแล้วจ่ายใหม่**ถึงจะมีผล ปรับสวิตช์ตอนไฟยังอยู่ไม่มีผลอะไรจนกว่าจะ boot รอบถัดไป

> **Gotcha — อย่าเชื่อการอ่าน DIP switch จาก manual/สายตา:** unit-id ที่ใช้งานได้จริงในรอบนี้คือ **2** ทั้งที่อ่าน DIP switch ได้ (ทั้งจากคำบอกเล่าและจากรูปถ่าย) ว่าตรงกับ address 1 — การอ่าน slide switch ตัวเล็กๆ ผิดพลาดได้ง่ายมาก ควรถือว่า address จาก DIP เป็นแค่ hypothesis แล้วยืนยันด้วยการ scan unit-id ช่วงเล็กๆ กับการอ่าน register จริง ไม่ใช่อ่านสวิตช์ซ้ำให้ละเอียดขึ้น

> **ยืนยันจาก user manual:** ตาราง SW1+SW2 เต็มหน้า (หน้า 3) ยืนยัน address space คือ **0–63 เต็ม (64 ค่า)** ตรงกับที่บอกไว้ข้อ "สูงสุด 63 กล่อง" ด้านบน — ใช้เป็น default range ตอน scan unit-id ใน config_flow ได้เลย (ไม่ต้องเดาช่วงเอง เช่น 1–10)

## 4. TCP↔RTU gateway configuration checklist

ไม่ว่าจะใช้ gateway ยี่ห้อไหน (รอบนี้ใช้ Elfin EW11) มี 4 จุดที่ต้องตรงกัน:

| Setting | ค่าที่ต้องเป็น | ทำไมถึงมีปัญหาบ่อย |
|---|---|---|
| Serial baud/format | ตรงกับ AB64 SW3 เป๊ะ | ไม่ตรง = เงียบสนิท ไม่มี error โผล่ที่ไหนเลย |
| Serial protocol mode | "Modbus" (protocol conversion) | ถ้ายังเป็น transparent/passthrough จะไม่แปลง MBAP↔RTU framing เลย |
| TCP local port | ดูจากหน้า config ของ gateway เอง | ไม่จำเป็นต้องเป็น 502 เสมอไป — ตัวที่ใช้จริงรอบนี้ default เป็น 8899 |
| Route / binding | TCP channel ต้องผูกกับ UART | gateway หลาย channel อาจมี TCP server ที่ไม่ได้ผูกกับ serial port เลย |

### วิธี diagnose ที่เร็วที่สุด: byte/frame counter ของ gateway

gateway ส่วนใหญ่มี TX/RX counter แยกฝั่ง network กับฝั่ง serial ในหน้า Status — ใช้ bisect ปัญหาได้เร็วกว่าลองสลับสายมั่วๆ มาก:

| อาการ | หมายความว่า |
|---|---|
| Received Bytes ฝั่ง network เป็น 0 ตลอด | request จาก client ไปไม่ถึง gateway เลย — เช็ค IP/port/firewall หรือ channel ที่ยิงผิด |
| ฝั่ง network รับได้ แต่ Sent Bytes ฝั่ง serial เป็น 0 | gateway ไม่ forward ออก — เช็ค protocol mode / route binding |
| Sent Bytes ฝั่ง serial ขยับ แต่ Received Bytes ฝั่ง serial เป็น 0 | byte ออกไปถึงสายจริงแล้ว ปัญหาอยู่ปลายทาง: wiring, address, หรือตัว AB64 เอง |

> **ยืนยันจริงจาก session นี้:** byte counter พิสูจน์ว่า gateway forward ทุก request ออกไปทาง RS-485 ถูกต้องมาหลายชั่วโมง ขณะที่ AB64 เงียบสนิท — ซึ่งสุดท้ายชี้ไปที่ปัญหา address ไม่ใช่ gateway

### LED บนตัว AB64 เอง — ตัวชี้วัดทาง physical layer

- **LED สีน้ำเงิน (Modbus status)** — กระพริบ 100ms เฉพาะตอนได้รับคำสั่งที่ถูกต้องและ address ตรง ถ้าไม่กระพริบเลยแม้ส่งไปหลายสิบครั้ง แปลว่า frame ไม่ถูกรับเลย (baud/address/wiring ผิด ไม่ใช่ปัญหาที่ client)
- **LED สีเขียว (AC Unit status)** — 100ms ON/1900ms OFF = คุยกับแอร์ได้ปกติ, 500ms/500ms = คุยกับแอร์ไม่ได้ — คนละเส้นทางกับ Modbus โดยสิ้นเชิง (ฝั่ง AB Bus ไปแอร์) อย่าเอามาปนกัน

> **Gotcha ใหม่จาก user manual — 12 วินาทีคือ internal timeout ของ AB64 เอง:** "หากอุปกรณ์ไม่ได้รับข้อมูลจากแอร์คอนดิชั่นเป็นเวลามากกว่า 12 วินาที จะถือว่าการสื่อสารขัดข้อง" (หน้า 4) — บวกกับตอน power-up LED ทั้งคู่ติดค้าง 5 วินาทีก่อนเริ่มคุยกับแอร์ รวมแล้ว **register 11 อาจค้างที่ 65535 ได้นานถึง ~17 วินาทีหลัง power-up หรือหลัง AC-side สะดุดชั่วคราว โดยไม่ใช่ hardware fault ถาวร** ผลต่อ integration: `binary_sensor` ที่ผูกกับ reg11==65535 ควร debounce (เช่น ต้องเห็นค่าเดิมต่อเนื่องเกิน ~20 วินาที หรือหลาย poll cycle ติดกัน) ก่อน flag เป็น "problem" จริง ไม่งั้นจะ false-positive ทุกครั้งที่ HA restart/reload entry

## 5. Register map — ทั้งหมดเป็น Holding Registers (FC03)

Input Registers (FC04) **ไม่รองรับ** — การอ่านทุกจุดใน integration ควรใช้ FC03 แม้แต่ register ที่เป็น read-only

> **หมายเหตุ provenance:** ข้อสรุปนี้มาจาก**การทดลองจริงตอน bring-up เท่านั้น** — user manual (Intesis INMBSTOS001R000 r2.7 EN) **ไม่มี section ไหนพูดถึง Modbus function code ที่รองรับเลย** ถ้ามีคน challenge fact นี้ทีหลัง ให้ยึด evidence จาก bring-up log ไม่ใช่จากคู่มือ (คู่มือไม่ยืนยันและไม่ปฏิเสธ)

### Basic registers

| Addr | Access | Field | Values |
|---|---|---|---|
| 0 | R/W | On/Off | 0 = off, 1 = on |
| 1 | R/W | Mode | 0 Auto · 1 Heat · 2 Dry · 3 Fan · 4 Cool |
| 2 | R/W | Fan speed | 0 Auto · 1 Low · 2 Med · 3 High |
| 3 | R/W | Swing | 0 = off, 10 = on |
| 4 | R/W | Setpoint temp | 16–32 °C |
| 5–10 | R | N/A | — |
| 11 | R | Error code | ดูตารางข้อ 6 |
| 50 | R | Software version | — |

### Advanced — indoor unit status

| Addr | Access | Field |
|---|---|---|
| 4012 | R | Indoor temp (TA) |
| 4013 | R | Indoor fan coil temp (TCJ) |
| 4014 | R | Indoor fan coil temp (TC2) |
| 4017 | R | Indoor fan revolutions |
| 4020 | R | Filter sign timer |

### Advanced — outdoor unit status

| Addr | Access | Field |
|---|---|---|
| 4410 | R | TE — evaporator temp |
| 4411 | R | TO — outdoor temp |
| 4412 | R | TD — discharge temp |
| 4413 | R | TS — suction temp |
| 4415 | R | Compressor current |
| 4417 | R | Compressor revolutions |
| 4418 | R | Lowest-fan revolutions |

## 6. Error code table (register 11)

ตารางเต็มจาก manual ของ AB64 จัดกลุ่มตาม category — ใช้เป็น source สำหรับ mapping ค่าใน HA `sensor`/`problem entity`

<details>
<summary>Central Controller issues (14 codes)</summary>

| Dec | Hex | Remote | Description |
|---|---|---|---|
| 33 | 21 | C01 | Duplicated setting of control address |
| 34 | 22 | C02 | Central control number of units mis-matched |
| 35 | 23 | C03 | Incorrect wiring of central control |
| 36 | 24 | C04 | Incorrect connection of central control |
| 37 | 25 | C05 | System Controller fault, error transmitting comms signal, i/door or o/door unit not working, wiring fault |
| 38 | 26 | C06 | System Controller fault, error receiving comms signal, CN1 not connected correctly |
| 44 | 2C | C12 | Batch alarm by local controller |
| 48 | 30 | C16 | Transmission error from adaptor to unit |
| 49 | 31 | C17 | Reception error to adaptor from unit |
| 50 | 32 | C18 | Duplicate central address in adaptor |
| 51 | 33 | C19 | Duplicate adaptor address |
| 52 | 34 | C20 | Mix of PAC & GHP type units on adaptor |
| 53 | 35 | C21 | Memory fault in adaptor |
| 54 | 36 | C22 | Incorrect address setting in adaptor |
| 55 | 37 | C23 | Host terminal software failure |
| 56 | 38 | C24 | Host terminal hardware failure |
| 57 | 39 | C25 | Host terminal processing failure |
| 58 | 3A | C26 | Host terminal communication failure |
| 60 | 3C | C28 | Reception error of S-DDC from host terminal |
| 61 | 3D | C29 | Initialization failure of S-DDC |
| 63 | 3F | C31 | Configuration change detected by adaptor |

</details>

<details>
<summary>Addressing & communication problems (18 codes)</summary>

| Dec | Hex | Remote | Description |
|---|---|---|---|
| 65 | 41 | E01 | Remote detecting error from indoor unit; address not set / auto-address failed |
| 66 | 42 | E02 | Remote detecting error from indoor unit |
| 67 | 43 | E03 | Indoor unit detecting error from remote |
| 68 | 44 | E04 | Indoor units connected less than qty set |
| 69 | 45 | E05 | Indoor unit error from outdoor, sending comms signal |
| 70 | 46 | E06 | Outdoor unit error from indoor, receiving comms signal |
| 71 | 47 | E07 | Outdoor unit error from indoor, sending comms signal |
| 72 | 48 | E08 | Indoor address duplicated |
| 73 | 49 | E09 | Remote address duplicated / IR wireless not disabled |
| 74 | 4A | E10 | Error from 'option' plug, sending comms signal |
| 75 | 4B | E11 | Error from 'option' plug, receiving comms signal |
| 76 | 4C | E12 | Auto address connector CN100 shorted during auto-addressing |
| 77 | 4D | E13 | Indoor unit failed to send signal to remote |
| 78 | 4E | E14 | Duplication of master indoor units |
| 79 | 4F | E15 | Indoor units connected fewer than number set |
| 80 | 50 | E16 | Indoor units connected more than number set |
| 81 | 51 | E17 | Group wiring: main not sending to subs |
| 82 | 52 | E18 | Group wiring: main not receiving from subs |
| 83 | 53 | E19 | Outdoor header units quantity error |
| 84 | 54 | E20 | No indoor units connected |
| 87 | 57 | E23 | Sending error between outdoor units |
| 88 | 58 | E24 | Error on sub outdoor unit |
| 89 | 59 | E25 | Error on outdoor unit address setting |
| 90 | 5A | E26 | Main/sub outdoor unit quantity mismatch on PCB |
| 92 | 5C | E28 | Follower outdoor unit error |
| 93 | 5D | E29 | Sub outdoor unit not receiving comms for main |
| 95 | 5F | E31 | Comms failure with MDC — if persists after power cycle, replace PCB |

</details>

<details>
<summary>Sensor faults (23 codes)</summary>

| Dec | Hex | Remote | Description |
|---|---|---|---|
| 97 | 61 | F01 | Indoor Heat Exch inlet temp sensor failure (E1) |
| 98 | 62 | F02 | Indoor Heat Exch freeze temp sensor failure (E2) |
| 99 | 63 | F03 | Indoor Heat Exch outlet temp sensor failure (E3) |
| 100 | 64 | F04 | Outdoor discharge temp sensor failure (TD/DISCH1) |
| 101 | 65 | F05 | Outdoor discharge temp sensor failure (DISCH2) |
| 102 | 66 | F06 | Outdoor Heat Exch temp sensor failure (C1/EXG1) |
| 103 | 67 | F07 | Outdoor Heat Exch temp sensor failure (C2/EXL1) |
| 104 | 68 | F08 | Outdoor air temp sensor failure (TO) |
| 106 | 6A | F10 | Indoor inlet temp sensor failure |
| 107 | 6B | F11 | Indoor outlet temp sensor failure |
| 108 | 6C | F12 | Outdoor intake sensor failure (TS) |
| 109 | 6D | F13 | GHP cooling water temp sensor failure |
| 111 | 6F | F15 | Outdoor temp sensor misconnection (TE1, TL) |
| 112 | 70 | F16 | Outdoor high pressure sensor failure |
| 113 | 71 | F17 | GHP cooling water temp sensor fault |
| 114 | 72 | F18 | GHP exhaust gas temp sensor fault |
| 116 | 74 | F20 | GHP clutch coil temp fault |
| 119 | 77 | F23 | Outdoor Heat Exch temp sensor failure (EXG2) |
| 120 | 78 | F24 | Outdoor Heat Exch temp sensor failure (EXL2) |
| 125 | 7D | F29 | Indoor EEPROM error |
| 126 | 7E | F30 | Clock function (RTC) fault |
| 127 | 7F | F31 | Outdoor EEPROM error |

</details>

<details>
<summary>Compressor issues (18 codes)</summary>

| Dec | Hex | Remote | Description |
|---|---|---|---|
| 129 | 81 | H01 | Over current (Comp1) |
| 130 | 82 | H02 | Locked rotor current detected (Comp1) |
| 131 | 83 | H03 | No current detected (Comp1) |
| 132 | 84 | H04 | Comp-1 case thermo operation |
| 133 | 85 | H05 | Discharge temp not detected (Comp1) |
| 134 | 86 | H06 | Low pressure trip |
| 135 | 87 | H07 | Low oil level |
| 136 | 88 | H08 | Oil sensor fault (Comp1) |
| 139 | 8B | H11 | Over current (Comp2) |
| 140 | 8C | H12 | Locked rotor current detected (Comp2) |
| 141 | 8D | H13 | No current detected (Comp2) |
| 142 | 8E | H14 | Comp-2 case thermo operation |
| 143 | 8F | H15 | Discharge temp not detected (Comp2) |
| 144 | 90 | H16 | Oil level detection / magnet switch / overcurrent relay error |
| 149 | 95 | H21 | Over current (Comp3) |
| 150 | 96 | H22 | Locked rotor current detected (Comp3) |
| 151 | 97 | H23 | No current detected (Comp3) |
| 153 | 99 | H25 | Discharge temp not detected (Comp3) |
| 155 | 9B | H27 | Oil sensor fault (Comp2) |
| 156 | 9C | H28 | Oil sensor connection failure |
| 159 | 9F | H31 | IPM trip (current on temperature) |

</details>

<details>
<summary>Incorrect settings (21 codes)</summary>

| Dec | Hex | Remote | Description |
|---|---|---|---|
| 193 | C1 | L01 | Indoor unit group setting error |
| 194 | C2 | L02 | Indoor/outdoor unit type/model mismatched |
| 195 | C3 | L03 | Duplication of main indoor unit address in group |
| 196 | C4 | L04 | Duplication of outdoor unit system address |
| 197 | C5 | L05 | 2+ controllers set 'priority' — shown on priority controllers |
| 198 | C6 | L06 | 2+ controllers set 'priority' — shown on non-priority controllers |
| 199 | C7 | L07 | Group wiring connected on an individual indoor unit |
| 200 | C8 | L08 | Indoor unit address/group not set |
| 201 | C9 | L09 | Indoor unit capacity code not set |
| 202 | CA | L10 | Outdoor unit capacity code not set |
| 203 | CB | L11 | Group control wiring incorrect |
| 205 | CD | L13 | Indoor unit type setting error, capacity |
| 207 | CF | L15 | Indoor unit pairing fault |
| 208 | D0 | L16 | Water heat exch. unit setting failure |
| 209 | D1 | L17 | Mismatch of outdoor unit with different refrigerant |
| 210 | D2 | L18 | 4-way valve failure |
| 211 | D3 | L19 | Water heat exch. unit duplicated address |
| 212 | D4 | L20 | Duplicated central control addresses |
| 213 | D5 | L21 | Gas type setup failure |
| 220 | DC | L28 | Maximum number of outdoor units exceeded |
| 221 | DD | L29 | No. of IPDU error |
| 222 | DE | L30 | Auxiliary interlock in indoor unit |
| 223 | DF | L31 | IC error |

</details>

<details>
<summary>Indoor / outdoor unit problems (24 codes)</summary>

| Dec | Hex | Remote | Description |
|---|---|---|---|
| 225 | E1 | P01 | Indoor fault, fan motor thermal overload |
| 226 | E2 | P02 | Outdoor fault, compressor motor thermal overload / over-under voltage |
| 227 | E3 | P03 | Compressor discharge temp >111°C (Comp1); low ref gas / valve / pipework |
| 228 | E4 | P04 | Outdoor fault, high pressure trip |
| 229 | E5 | P05 | Outdoor fault, open phase on power supply |
| 231 | E7 | P07 | Heat sink overheat error |
| 233 | E9 | P09 | Indoor fault, ceiling panel incorrectly wired |
| 234 | EA | P10 | Indoor fault, condensate float switch opened |
| 235 | EB | P11 | GHP water heat exch. low temp (frost protection) |
| 236 | EC | P12 | Indoor fault, fan DC motor fault |
| 237 | ED | P13 | Outdoor liquid back detection error |
| 238 | EE | P14 | Input from leak detector (if fitted) |
| 239 | EF | P15 | Refrigerant loss — high discharge temp, EEV wide open, low current draw |
| 240 | F0 | P16 | Outdoor fault, open phase on compressor power supply |
| 241 | F1 | P17 | Compressor discharge temp >111°C (Comp2) |
| 242 | F2 | P18 | Outdoor fault, by-pass valve failure |
| 243 | F3 | P19 | 4-way valve failure |
| 244 | F4 | P20 | Ref gas high temp/pressure, heat exch. temp high C2 55–60°C |
| 246 | F6 | P22 | Outdoor fan motor fault / blade jammed |
| 250 | FA | P26 | Compressor overcurrent / inverter failure |
| 252 | FC | P29 | Inverter circuit fault — MDC fault |
| 253 | FD | P30 | System controller detected fault on sub indoor unit |
| 255 | FF | P31 | Simultaneous operation multi-control fault / group controller fault |
| 65535 | — | N/A | Communication error between the AB64 interface and the AC unit itself (AB Bus link down) |

</details>

## 7. Reference implementation — ยืนยันแล้วว่าใช้งานได้จริง

```python
from pymodbus.client import ModbusTcpClient
from pymodbus import FramerType

client = ModbusTcpClient(
    "192.168.14.149",          # gateway IP
    port=502,                   # gateway's configured Modbus TCP port — เช็คต่อ deployment
    framer=FramerType.SOCKET,   # Modbus TCP/MBAP มาตรฐาน — ใช้ RTU เฉพาะ transparent bridge
    timeout=3,
    retries=1,
)
client.connect()

result = client.read_holding_registers(
    0,              # start register — basic block
    count=12,       # registers 0-11
    device_id=2,    # unit-id ที่ยืนยันแล้วจริง — ห้ามเดาจากการอ่าน DIP switch
)

if not result.isError():
    regs = result.registers
    # regs = [OnOff, Mode, FanSpeed, Swing, SetTemp, _, _, _, _, _, _, ErrorCode]
    on_off, mode, fan, swing, set_temp = regs[0], regs[1], regs[2], regs[3], regs[4]
    error_code = regs[11]

client.close()
```

การเขียนค่าใช้ register address ชุดเดียวกันผ่าน `write_register(address, value, device_id=...)` — register 0–4 คือชุดที่เขียนได้ (On/Off, Mode, Fan, Swing, Setpoint)

## 8. Bring-up gotchas ที่ควร encode เข้า integration

1. **อย่า hardcode unit-id จากการอ่าน DIP switch** — ควรมี setup step "scan หา unit-id" (ลองช่วงเล็กๆ เช่น 1–10 กับ register 0) แทนการเชื่อการอ่านสวิตช์ด้วยมือ — รอบนี้อ่านผิดทั้งที่ยืนยันซ้ำ 2 รอบ
2. **แยก connection failure ออกจาก register error ให้ชัด** — "TCP connect ได้แต่ Modbus timeout" กับ "TCP connect ไม่ได้เลย" ชี้ไปคนละ layer (gateway config vs network) ถ้ารวมเป็น "unavailable" เดียวกันจะพาคนไปผิดทางตอน debug เหมือนที่เกิดขึ้นจริงหลายชั่วโมงวันนี้
3. **Poll แบบระมัดระวัง** — นี่คือ RS-485 bus หลัง TCP↔RTU gateway ตัวเดียว (single-threaded) request พร้อมกันจากหลาย entity จะชนกันหรือถูก serialize — ควรรวมการอ่านทั้งหมดผ่าน coordinator/connection เดียว ไม่ใช่ 1 Modbus client ต่อ entity
4. **FC03 อย่างเดียว** — ไม่ต้องเสนอ Input Registers (FC04) เป็น option เพราะ device นี้ไม่รองรับ มีแต่จะเพิ่ม failed request รกๆ
5. **ปฏิบัติกับ register 11 = 65535 เป็นกรณีพิเศษ** — หมายความว่า AB64 เองขาดการเชื่อมต่อกับแอร์ผ่าน AB Bus (hardware-level fault) คนละแบบกับ error code อื่นๆ และคนละแบบกับ Modbus timeout ควรแสดงเป็น "gateway ต่อไม่ถึงตัวเครื่อง" ไม่ใช่ AC error ทั่วไป
6. **หน้า config ควรแยก field IP / port / unit-id เป็น 3 ช่องบังคับกรอกแยกกัน** — ตั้ง port default เป็น 502 อาจผิดสำหรับ gateway (เช่น EW11 ตัวนี้) ที่ default port เป็นค่าอื่น

---

*รวบรวมจาก live bring-up session, TVO OT Server Room project — 2026-07-31 อุปกรณ์ต้นทาง: Carrier-Toshiba AB64 Modbus Interface (Intesis INMBSTOS001R000) เชื่อมผ่าน Elfin EW11 Modbus TCP↔RTU gateway การตั้งค่าที่ยืนยันว่าใช้งานได้จริง: `192.168.14.149:502`, unit-id `2`, `9600 8N2`*
