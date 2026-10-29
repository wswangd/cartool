# STRUOFVIEW → DBC conversion audit

Historical audit of the original 37-message draft. On 2026-10-28 the main DBC was
updated against `scr_frame.c::frame_disparameter()`. See `frame_disparameter_修订说明.md`
for the current layout and checks; the original draft is preserved as
`STRUOFVIEW_2022-10-26_ENGINE_DRAFT.before_frame_disparameter.dbc`.

Source: E:\实验室测试\FAI\STRUOFVIEW\STRUOFVIEW 2022-10-26-1.txt
Start marker: `SUBJECT ENGINE 18FF513D;`
Messages: 37
Source signals: 135
Exported signals: 132
Omitted signals: 3
Independent physical decode checks: 4224

## Status
DRAFT: syntax, strict bounds/non-overlap, metadata round-trip and independent numeric decoding passed.
This does not establish ECU protocol correctness. No live ECU data was provided.

## Assumptions
- 8-byte extended CAN frames, zero-based byte positions, Intel/little-endian byte order.
- Explicit multi-byte lists define signal width; otherwise the declared integer type defines width.
- Signedness follows sbyte/sword. Missing types use the byte-list width.
- Only explicit name-parenthesis units and explicit multiline value enumerations are extracted.
- Source display ranges are retained even when wider than the representable physical range.
- Names are DBC-safe and globally unique; original names and descriptions remain in signal comments.
- No undocumented cycle time, offset, unit or extra signal has been invented.
- Output encoding is GBK/CP936 for compatibility with Chinese CANdb++ 3.1.

## Omitted definitions requiring confirmation
- SLAVE_Doser1.FCC_PRESS: extends beyond the assumed 8-byte CAN payload
- Vehicle_PARA3.Turb_Pressure(kPa): overlaps Vehicle_PARA3.InterCool_Temp
- Vehicle_PARA3.InterCool_Temp: overlaps Vehicle_PARA3.Turb_Pressure(kPa)

## Warnings requiring protocol review
- SLAVE_Doser1.DEF(g/h): declared range [0,2e+06] exceeds representable [0,19660.5]; retained as source display range.
- SLAVE_Doser1_1.DEF2(g/h): declared range [0,2e+06] exceeds representable [0,19660.5]; retained as source display range.
- SLAVE_Doser2.QDEF(g/h): declared range [0,2e+06] exceeds representable [0,19660.5]; retained as source display range.
- Vehicle_PARA1.Fuel: declared range [0,100] exceeds representable [0,31.875]; retained as source display range.
- Vehicle_PARA1.BaroPre: declared range [0,128] exceeds representable [0,127.5]; retained as source display range.
- Vehicle_PARA1.AMT: declared range [-128,128] exceeds representable [-128,127]; retained as source display range.
- Vehicle_PARA1.Fuel_T: declared range [-128,128] exceeds representable [-128,127]; retained as source display range.
- Vehicle_PARA1.Oil_T: declared range [-128,128] exceeds representable [-128,127]; retained as source display range.
- Vehicle_PARA3.InterCool_Temp: declared range [-40,220] exceeds representable [-128,127]; retained as source display range.
- PARA8_MASTER.ACT_sta: declared range [0,256] exceeds representable [0,255]; retained as source display range.
- PARA8_MASTER.st_DTC: declared range [0,256] exceeds representable [0,255]; retained as source display range.
- WORK1_DOSER.prd_doser: byte list 345 => 24 bits, overrides dword.
- WORK2_DOSER.fre_doser: declared range [0,256] exceeds representable [0,255]; retained as source display range.
- WORK2_DOSER.Pumpstate_doser: declared range [0,256] exceeds representable [0,255]; retained as source display range.
- SENSOR1.Vbat_V: declared range [0,50] exceeds representable [-0.02,38.23]; retained as source display range.
- NOX_U.NOx_u(ppm): byte list 01 => 16 bits, overrides dword.
- NOX_D.NOx_d(ppm): byte list 01 => 16 bits, overrides dword.
- RUNTIME_1.DC: byte list 012 => 24 bits, overrides dword.
- CAL9.fre_supplier: declared range [0,256] exceeds representable [0,255]; retained as source display range.
- CAL9.Pumpstate_supplier: declared range [0,256] exceeds representable [0,255]; retained as source display range.
- UQS2.C_CDEF: declared range [0,65] exceeds representable [0,63.75]; retained as source display range.
- UQS2.C_CAL1: missing type; inferred 8 bits from byte list.
- UQS2.C_CAL2: missing type; inferred 8 bits from byte list.
