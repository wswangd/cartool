"""Apply the reviewed frame_disparameter layout and validate every emitted signal."""

import argparse
import hashlib
import math
import random
import re
from pathlib import Path

import cantools
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion


def field(name, first, size, expression, scale=1, offset=0, unit="", signed=False, big=False):
    return dict(name=name, first=first, size=size, expression=expression,
                scale=scale, offset=offset, unit=unit, signed=signed, big=big)


LAYOUTS = {
    3: ("FRAME_PARA_CAL4", "CAL4", [
        field("v_t3eobd_Avalue_low16", 0, 2, "v_t3eobd_Avalue"),
        field("v_t3_Avalue_low16", 2, 2, "v_t3_Avalue"),
        field("supply_t1e_modfiy_flag", 4, 1, "supply_t1e_modfiy_flag"),
        field("supply_t1e_uqs_modify_raw", 6, 1, "supply_t1e_uqs_modfiy_value"),
        field("supply_t1e_uqs_modify_ee_raw", 7, 1, "supply_t1e_uqs_modfiy_value_ee")]),
    4: ("FRAME_PARA_CAL5", "CAL5", [
        field("Inj_Prd_0", 0, 4, "Inj.Prd[0]"),
        field("Inj_Prd_1", 4, 4, "Inj.Prd[1]")]),
    5: ("FRAME_PARA_CAL6", "CAL6", [
        field(name, index, 1, name) for index, name in enumerate([
            "v_inj_short1", "v_inj_short2", "v_sup_short1", "v_sup_short2",
            "v_cvv_short", "v_isv_short", "v_ht1_short", "v_ht2_short"])]),
    6: ("FRAME_PARA_CAL7", "CAL7", [
        field("EGTU_AD", 0, 2, "d_egtu_ave"), field("EGTD_AD", 2, 2, "d_egtd_ave"),
        field("AD_capture_3", 4, 2, "AD_capture(3)"),
        field("bsp_get_egtd_raw", 6, 2, "bsp_get_egtd()")]),
    7: ("FRAME_PARA_CAL8", "CAL8", [
        field("v_inj_blocking_nb", 1, 1, "v_inj_blocking_nb"),
        field("v_cal_pump_err", 2, 1, "v_cal_pump_err"),
        field("v_inj_err_blocking2", 3, 1, "v_inj_err_blocking2"),
        field("inj_lock_count_0", 4, 1, "inj_get_lock_count(0)"),
        field("inj_lock_count_1", 5, 1, "inj_get_lock_count(1)"),
        field("inj_empty_count_0", 6, 1, "inj_get_empty_count(0)"),
        field("inj_empty_count_1", 7, 1, "inj_get_empty_count(1)")]),
    8: ("FRAME_PARA_CAL9", "CAL9", [
        field("T1_supplier", 0, 2, "bsp_pump_get_t1(1)"),
        field("T3_supplier", 3, 2, "bsp_pump_get_t3(1)"),
        field("T3_EOBD_supplier", 5, 2, "bsp_pump_get_t3_eobd(1)"),
        field("Pumpstate_supplier", 7, 1, "Inj.diager[1]")]),
    11: ("FRAME_WORK1_DOSER", "WORK1_DOSER", [
        field("DCU_MODE", 0, 1, "Scr.mode"), field("DOSER_State", 1, 1, "Scr.pump_sta"),
        field("prd_supplier_low24", 3, 3, "bsp_pump_get_prd(1)"),
        field("supply_t1e_modfiy_value", 6, 2, "supply_t1e_modfiy_value")]),
    12: ("FRAME_WORK2_DOSER", "WORK2_DOSER", [
        field("T1_selected_pump", 0, 2, "Scr.module == 0 ? bsp_pump_get_t1(1) : bsp_pump_get_t1(0)"),
        field("T3_selected_pump", 2, 2, "Scr.module == 0 ? bsp_pump_get_t3(1) : bsp_pump_get_t3(0)"),
        field("Pumpstate_doser", 4, 1, "Scr.pump_sta"),
        field("T3_EOBD_selected_pump", 6, 2, "Scr.module == 0 ? bsp_pump_get_t3_eobd(1) : bsp_pump_get_t3_eobd(0)")]),
    14: ("FRAME_SENSOR1", "SENSOR1", [
        field("Vbat_raw_0", 0, 1, "bsp_inj_vbat()"),
        field("Vbat_raw_1", 1, 1, "bsp_inj_vbat()"),
        field("TDEF", 2, 1, "Scr.tdef", offset=-40, unit="degC"),
        field("LDEF", 4, 1, "Scr.ldef", scale=0.4, unit="%"),
        field("CDEF", 6, 1, "Scr.cdef", scale=0.25, unit="%")]),
    15: ("FRAME_SENSOR2", "SENSOR2", [
        field("PRESS_AD", 0, 1, "v_press_ad"),
        field("supply_dc_voltage_level", 1, 1, "supply_dc_voltage_level"),
        field("PRESS_Value", 2, 2, "v_press", unit="kPa"),
        field("LDEF_received", 4, 1, "_STRU_AT1T1I.Byte[0]", scale=0.4, unit="%"),
        field("NH3_Load_low16", 6, 2, "SCRNH3LD_mNH3LdCurr", big=True)]),
    17: ("FRAME_SENSOR4", "SENSOR4", [
        field("HEAT2_OPN_AD_low8", 0, 1, "HEAT2_OPN_AD"),
        field("NOxD_LowByte", 2, 1, "Scr.nox2 & 0xFF"),
        field("NOxD_HighByte", 4, 1, "Scr.nox2 >> 8"),
        field("NOxU", 5, 2, "Scr.nox1", unit="ppm")]),
    20: ("FRAME_ENGINE", "ENGINE", [
        field("N", 0, 2, "Vehicle.RPM", unit="r/min"),
        field("Torque", 2, 1, "Vehicle.Torque_Percent", unit="%"),
        field("ENGINE_n", 3, 1, "Vehicle.RPM_Percent", unit="%"),
        field("Vspeed", 4, 2, "Vehicle.Speed", unit="km/h"),
        field("QinjLperH", 6, 2, "Vehicle.QinjLperH", scale=0.05, unit="L/h")]),
    22: ("FRAME_PARA1_MASTER", "Vehicle_PARA1", [
        field("APP_R", 0, 1, "Vehicle.Pedal", scale=0.4, unit="%"),
        field("Fuel", 1, 1, "Vehicle.Qinj >> 6", scale=0.125, unit="km/L"),
        field("BaroPre", 2, 1, "Vehicle.Air_Press", scale=0.5, unit="kPa"),
        field("AMT", 3, 1, "Vehicle.Air_Temp", signed=True, unit="degC"),
        field("Fuel_T", 4, 1, "Vehicle.Fuel_Temp", signed=True, unit="degC"),
        field("Oil_T", 5, 1, "Vehicle.Oil_Temp", signed=True, unit="degC"),
        field("Qexh", 6, 2, "Vehicle.Gas_Flow", scale=0.2, unit="kg/h")]),
    23: ("FRAME_PARA2_MASTER", "Vehicle_PARA2", [
        field("Trip_Distance", 0, 4, "Vehicle.Trip_Distance_L | (Vehicle.Trip_Distance_H << 16)", scale=0.125, unit="km"),
        field("Total_Vehicle_Distance", 4, 4, "Vehicle.Total_Distance_L | (Vehicle.Total_Distance_H << 16)", scale=0.125, unit="km")]),
    24: ("FRAME_PARA3_MASTER", "Vehicle_PARA3", [
        field("Turb_Pressure_low8", 0, 1, "Vehicle.Turb_Press", unit="kPa"),
        field("InterCool_Temp", 1, 1, "Vehicle.Intercooler_Temp", signed=True, unit="degC"),
        field("Cool_Temp", 2, 1, "SCRTRL_Cool_Temp", signed=True, unit="degC"),
        field("APP_FAI_CVN", 3, 4, "APP_FAI_CVN", big=True)]),
    25: ("FRAME_PARA4_MASTER", "Vehicle_PARA4", [
        field(name, index * 2, 2, "Vehicle." + name, signed=True, unit="degC")
        for index, name in enumerate(["DOC_EGTU", "DPF_EGTU", "SCR_EGTU", "SCR_EGTD"])]),
    26: ("FRAME_PARA5_MASTER", "Vehicle_PARA5", [
        field("T3_EOBD_DOSER_ST", 0, 2, "bsp_pump_get_T3_EOBD_ST(0)"),
        field("T3_EOBD_SUPPLY_ST", 2, 2, "bsp_pump_get_T3_EOBD_ST(1)"),
        field("NOX1_TIMEOUT", 4, 1, "_STRU_NOX1.COMMON.TimeOut_200ms / 5", unit="s"),
        field("NOX2_TIMEOUT", 5, 1, "_STRU_NOX2.COMMON.TimeOut_200ms / 5", unit="s"),
        field("ECU_TIMEOUT", 6, 1, "_STRU_ECU.COMMON.TimeOut_200ms / 5", unit="s"),
        field("SCR_THAWING", 7, 1, "Scr.thawing")]),
    27: ("FRAME_PARA6_MASTER", "Vehicle_PARA6", [
        field("Qexh_PARA6", 0, 2, "Vehicle.Gas_Flow", scale=0.2, unit="kg/h"),
        field("Firmware_Version", 2, 2, "VERSION"),
        field("CWV_OpenTime_low16", 4, 2, "Scr.cwv_open_time"),
        field("CWV_CloseTime_low16", 6, 2, "Scr.cwv_close_time")]),
    28: ("FRAME_PARA7_MASTER", "Vehicle_PARA7", [
        field("CWV_STATE", 0, 1, "SCR_CWV_CTL"),
        field("HEAT_SATE", 1, 1, "SCR_HOSE_HEATER_CTL"),
        field("CWV_OPEN_TIME", 2, 2, "SCRTRL_CWV_OpenTime"),
        field("Thawing_State", 4, 1, "Scr.thawing"),
        field("inj_err_thawing_0", 5, 1, "v_inj_err_thawing[0]"),
        field("inj_err_thawing_1", 6, 1, "v_inj_err_thawing[1]")]),
    29: ("FRAME_PARA8_MASTER", "PARA8_MASTER", [
        field("ACT_sta", 0, 1, "Scr.inj.Byte"),
        field("off_to_purge_flag", 1, 1, "v_off_to_purge_flag"),
        field("v_sw_set", 6, 1, "v_sw_set")]),
    30: ("FRAME_FAI_DTC", "FAI_DTC", [
        field(f"DTC_{index}", index, 1, f"v_SPNX_forMIL[{index}][v_sw_set]") for index in range(8)]),
    31: ("FRAME_FAI_FLASH", "FAI_FLASH", [
        field("Flash_Marker_A0", 0, 1, "0xA0"), field("Flash_Marker_DA", 1, 1, "0xDA"),
        field("Flash_Address", 2, 2, "v_dataview_addr"),
        *[field(f"Flash_Data_{index}", index + 4, 1, f"bsp_read_data_eeprom(v_wt + {index})") for index in range(4)]]),
    32: ("RUNTIME_1", "RUNTIME_1", [
        field("DC", 0, 3, "Scr.dc"), field("WUC", 3, 2, "Scr.wuc"),
        field("OBD_C", 5, 2, "Scr.obdc")]),
    34: ("RUNTIME_3", "RUNTIME_3", [
        field("nb_DOSER", 0, 4, "bsp_pump_get_counter(0)"),
        field("nb_Supplier", 4, 4, "bsp_pump_get_counter(1)")]),
    35: ("RUNTIME_4", "RUNTIME_4", [
        field("Ave_T3_Doser", 0, 2, "Ave_T3_Doser"),
        field("Ave_T3_EOBD_Doser", 2, 2, "Ave_T3_EOBD_Doser"),
        field("Ave_T3_Doser_Diff", 4, 2, "Ave_T3_Doser_Diff"),
        field("Num_T3_Doser", 6, 1, "Num_T3_Doser"),
        field("Num_T3_EOBD_Doser", 7, 1, "Num_T3_EOBD_Doser")]),
    36: ("RUNTIME_5", "RUNTIME_5", [
        field("Sum_T3_Doser", 0, 4, "Sum_T3_Doser"),
        field("Sum_T3_EOBD_Doser", 4, 4, "Sum_T3_EOBD_Doser")]),
    39: ("RUNTIME_8", "RUNTIME_8", [field("Test_RunTime", 0, 4, "Test.run_time_tmp", unit="s")]),
}
for case, channel in ((40, 0), (41, 4), (42, 8)):
    LAYOUTS[case] = (f"SCREFF{case - 39}", f"SCREFF{case - 39}", [
        field(f"ADC_Channel_{channel + index}", index * 2, 2, f"AD_capture({channel + index})")
        for index in range(4)])


def read_cases(source):
    cleaned = re.sub(r'/\*.*?\*/|//[^\n]*', lambda match: "\n" * match[0].count("\n"), source, flags=re.S)
    body = cleaned.split("byte frame_disparameter(byte id)", 1)[1].split("byte frame_ldef_avr", 1)[0]
    return {int(match[1]): match[2] for match in re.finditer(
        r"case\s+(\d+)\s*:(.*?)(?=case\s+\d+\s*:|\Z)", body, re.S)}


def make_signal(spec, case):
    bits = spec["size"] * 8
    low = -(1 << (bits - 1)) if spec["signed"] else 0
    high = (1 << (bits - int(spec["signed"]))) - 1
    comment = f'frame_disparameter case {case}: {spec["expression"]}; byte {spec["first"]}..{spec["first"] + spec["size"] - 1}.'
    if not spec["unit"]:
        comment += " Raw/internal value; physical unit not established."
    if case == 17 and spec["name"].startswith("NOxD_"):
        comment += " NOxD_ppm = NOxD_LowByte + 256 * NOxD_HighByte; byte 3 is zero."
    return Signal(spec["name"], spec["first"] * 8 + (7 if spec["big"] else 0), bits,
                  byte_order="big_endian" if spec["big"] else "little_endian",
                  is_signed=spec["signed"], conversion=BaseConversion.factory(spec["scale"], spec["offset"]),
                  minimum=low * spec["scale"] + spec["offset"],
                  maximum=high * spec["scale"] + spec["offset"], unit=spec["unit"],
                  receivers=["Monitor"], comment=comment)


def validate(database, reviewed):
    generator = random.Random(20261028)
    checks = 0
    for frame_id, case in reviewed.items():
        message = database.get_message_by_frame_id(frame_id)
        for payload in [bytes(8), bytes([255]) * 8, bytes(range(8))] + [generator.randbytes(8) for _ in range(64)]:
            decoded = message.decode(payload, decode_choices=False)
            for spec in LAYOUTS[case][2]:
                raw = int.from_bytes(payload[spec["first"]:spec["first"] + spec["size"]],
                                     "big" if spec["big"] else "little", signed=spec["signed"])
                expected = raw * spec["scale"] + spec["offset"]
                assert math.isclose(decoded[spec["name"]], expected, abs_tol=1e-8)
                checks += 1
            encoded = message.encode(message.decode(payload, scaling=False, decode_choices=False), scaling=False)
            for spec in LAYOUTS[case][2]:
                first, end = spec["first"], spec["first"] + spec["size"]
                assert encoded[first:end] == payload[first:end]
    sensor = database.get_message_by_name("SENSOR4").decode(bytes([21, 0, 0x34, 0, 0x12, 0x78, 0x56, 0]))
    assert sensor["NOxD_LowByte"] + 256 * sensor["NOxD_HighByte"] == 0x1234
    assert sensor["NOxU"] == 0x5678
    assert database.get_message_by_name("SENSOR2").decode(bytes([0, 0, 0, 0, 0, 0, 0x12, 0x34]))["NH3_Load_low16"] == 0x1234
    assert database.get_message_by_name("Vehicle_PARA3").decode(bytes([0, 0, 0, 0x12, 0x34, 0x56, 0x78, 0]))["APP_FAI_CVN"] == 0x12345678
    return checks


def update(source_path, header_path, dbc_path, address):
    source = source_path.read_text(encoding="gb18030")
    header = header_path.read_text(encoding="gb18030")
    cases = read_cases(source)
    active = {case for case, body in cases.items() if re.search(r"bsp_can[12]_sendframe\(", body)}
    assert active == set(LAYOUTS), (active - set(LAYOUTS), set(LAYOUTS) - active)
    database = cantools.database.load_file(str(dbc_path), encoding="gbk", strict=True)
    original_count = len(database.messages)
    reviewed = {}
    rows = []
    for case, (macro, name, specs) in LAYOUTS.items():
        match = re.search(r"^#define\s+" + macro + r"\s+(0x[\da-fA-F]+)\s*\|\s*\(Model.idx\)", header, re.M)
        assert match, macro
        frame_id = int(match[1], 16) | address
        assert re.search(r"bsp_can[12]_sendframe\(" + macro + r",\s*ExtID,\s*_STRU_OUT\)", cases[case]), macro
        assignments = dict((int(match[1]), match[2].strip()) for match in re.finditer(
            r"_STRU_OUT\.Byte\[(\d+)\]\s*=\s*([^;]+);", cases[case]))
        occupied = set()
        for spec in specs:
            byte_range = set(range(spec["first"], spec["first"] + spec["size"]))
            assert not occupied & byte_range, (case, spec)
            assert byte_range <= set(assignments), (case, spec)
            occupied |= byte_range
        omitted = {position: expression for position, expression in assignments.items() if position not in occupied}
        assert all(re.fullmatch(r"0|0xff", value, flags=re.I) for value in omitted.values()), (case, omitted)
        existing = next((message for message in database.messages if message.frame_id == frame_id), None)
        message = Message(frame_id, existing.name if existing else name, 8,
                          [make_signal(spec, case) for spec in specs], is_extended_frame=True,
                          senders=["ECU"], comment=f"Verified against scr_frame.c frame_disparameter case {case}; {macro}; Model.idx=0x{address:02X}. Reserved/unassigned bytes are zero except explicit 0xFF fields.")
        if existing:
            database.messages[database.messages.index(existing)] = message
        else:
            database.messages.append(message)
        reviewed[frame_id] = case
        rows.append(f"| {case} | 0x{frame_id:08X} | {message.name} | {len(specs)} | {'更新' if existing else '新增'} |")
    retained = []
    for message in database.messages:
        if message.frame_id not in reviewed:
            prefix = "Legacy STRUOFVIEW definition; NOT verified by the supplied frame_disparameter implementation. "
            if not (message.comment or "").startswith(prefix):
                message.comment = prefix + (message.comment or "")
            retained.append(f"- `{message.name}` / `0x{message.frame_id:08X}`")
    database.refresh()
    text = database.as_dbc_string()
    text = "\r\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\r\n"
    checked = cantools.database.load_string(text, database_format="dbc", strict=True)
    checks = validate(checked, reviewed)
    encoded = text.encode("gbk")
    backup = dbc_path.with_suffix(".before_frame_disparameter.dbc")
    if not backup.exists():
        backup.write_bytes(dbc_path.read_bytes())
    dbc_path.write_bytes(encoded)
    reloaded = cantools.database.load_file(str(dbc_path), encoding="gbk", strict=True)
    validate(reloaded, reviewed)
    summary = ["# frame_disparameter 修订核对记录", "", "日期：2026-10-28", "",
               f"源文件：`{source_path}`", f"头文件：`{header_path}`",
               f"scr_frame.c SHA256：`{hashlib.sha256(source_path.read_bytes()).hexdigest()}`",
               f"scr_frame.h SHA256：`{hashlib.sha256(header_path.read_bytes()).hexdigest()}`", "",
               f"原有 {original_count} 条报文，更新后 {len(reloaded.messages)} 条、{sum(len(message.signals) for message in reloaded.messages)} 个信号。",
               f"已核对全部 {len(reviewed)} 个实际调用发送函数的 case；新增 {len(reloaded.messages) - original_count} 条报文。",
               f"严格加载、边界/重叠检查、GBK落盘重载、{checks} 次独立字节解码及原始值回编码检查通过。",
               "另核对 NOxD 分散字节、NH3 大端16位、CVN 大端32位的固定样例。",
               "本次未调用 CANdb++ GUI；旧 CANdb 自测记录只针对原版草稿。", "",
               "## 报文范围", "", "| case | CAN ID | 报文 | 信号数 | 操作 |", "|---|---|---|---|---|", *rows,
               "", "## 关键说明", "",
               "- 源地址保留原 DBC 的 0x3D；代码 frame_init 默认也为0x3D，但可由 BYPTR_M0_RESERVE1 改写。其他地址需重新生成。",
               "- 仅解析实际代码，排除注释内的 case 43～52；空 case 不新增报文。",
               "- CAL4/CAL5/CAL6/CAL7/CAL8 等已改变用途，移除不再对应实际字节的旧信号、枚举和单位。",
               "- WORK2 byte4 才是 Scr.pump_sta，byte5 为零。T1/T3通道依 Scr.module 选择；因此使用 selected_pump 名称。",
               "- SENSOR1 CDEF 只有byte6（8位），byte7为零。两个电池字段均为 bsp_inj_vbat 原始返回值，不再套用未证实的0.15/-0.02换算。",
               "- SENSOR2 PRESS_AD是byte0（8位），byte1为供电档位；NH3值为byte6高、byte7低，只能恢复原uint32值的低16位。",
               "- SENSOR4 NOxD低字节在byte2、高字节在byte4，byte3为零。标准DBC不能直接表达中间跳字节：拆为NOxD_LowByte和NOxD_HighByte，ppm = Low + 256*High；不得误设byte3～4为连续16位。NOxU在byte5～6。",
               "- Vehicle_PARA3 增压压力只发送word变量低8位，超过255 kPa的信息无法恢复；CVN为byte3～6大端。",
               "- ENGINE转速/车速在接收处理时已换算为整数，不重复应用原始CAN分辨率；QinjLperH仍为原始计数，按scr_app.h的0.05 L/h换算。",
               "- Vehicle_PARA1 Fuel为Qinj右移6位再截断8位，按1/512*64=0.125 km/L解码；高位丢失无法由DBC恢复。",
               "- 温度接收代码已减偏移，车辆温度按有符号整数解释；单字节仅保留低8位，超出-128～127的温度存在歧义。SCR浮点温度经int转换后只发送低16位，不按IEEE浮点解码。",
               "- CAL4两个char修正值保留raw字节，char是否有符号依工具链设置，未擅自假定。",
               "- 未查明的内部值/泵计时单位留空、scale=1，并在信号注释标注Raw。范围为传输位宽可表示范围，并非传感器有效量程或故障值定义。",
               "- 未继承可能过时的枚举；常量/未赋值填充字节一般不定义成信号。FAI_FLASH保留A0/DA标记和4个原始数据字节。",
               "- FAI_FLASH还受v_dataview_link/v_dataview_ctrl条件限制；函数支持某case不代表固件调度一定会发送。", "",
               "## 保留但未由本函数核对的旧报文", "", *retained, "",
               "原始备份：`" + backup.name + "`。旧conversion_audit.md和CANdb_自测结果.md是原草稿历史记录。"]
    dbc_path.with_name("frame_disparameter_修订说明.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"messages={len(reloaded.messages)}, signals={sum(len(message.signals) for message in reloaded.messages)}, active_cases={len(reviewed)}, checks={checks}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("header", type=Path)
    parser.add_argument("dbc", type=Path)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x3D)
    arguments = parser.parse_args()
    if not 0 <= arguments.address <= 255:
        parser.error("address must fit in one byte")
    update(arguments.source, arguments.header, arguments.dbc, arguments.address)
