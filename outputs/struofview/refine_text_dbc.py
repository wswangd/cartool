"""Regenerate the selected text-based draft and complete explicit description units."""

from pathlib import Path

import cantools

from convert_struofview import convert


directory = Path(__file__).resolve().parent
source = Path(r"E:\实验室测试\FAI\STRUOFVIEW\STRUOFVIEW 2022-10-26-1.txt")
target = directory / "STRUOFVIEW_2022-10-26_ENGINE_DRAFT.before_frame_disparameter.dbc"
generated = directory / "text_definition"
convert(source, generated)
database = cantools.database.load_file(str(generated / "STRUOFVIEW_2022-10-26_ENGINE_DRAFT.dbc"),
                                      encoding="gbk", strict=True)
units = {
    "Vehicle_PARA1": {"APP_R": "%", "BaroPre": "kPa", "AMT": "degC", "Fuel_T": "degC", "Oil_T": "degC"},
    "Vehicle_PARA2": {"Trip_Distance": "km", "Total_Vehicle_Distance": "km"},
    "Vehicle_PARA3": {"Cool_Temp": "degC"},
    "Vehicle_PARA4": {name: "degC" for name in ("DOC_EGTU", "DPF_EGTU", "SCR_EGTU", "SCR_EGTD")},
    "Vehicle_PARA7": {"CWV_OPEN_TIME": "s"},
    "RUNTIME_2": {"ENG_RT_S": "s", "KEYON_S": "s"},
    "CAL1": {"SCRT_dmEG": "kg/s", "SCRT_tCatUs": "degC", "SCRT_tCatDs": "degC"},
    "UQS1": {"L_LDEF": "%", "L_TDEF": "degC"},
    "UQS2": {"C_TDEF": "degC", "C_CDEF": "%"},
    "UQS3": {"L_ELDEF": "%", "L_ETDEF": "degC"},
    "UQS4": {"L_DLDEF": "%", "L_DTDEF": "degC"},
}
changes = []
for message in database.messages:
    for signal in message.signals:
        unit = units.get(message.name, {}).get(signal.name)
        if unit and not signal.unit:
            signal.unit = unit
            changes.append(f"- {message.name}.{signal.name}: {unit}")
original = cantools.database.load_file(str(target), encoding="gbk", strict=True)
for message in database.messages:
    previous = original.get_message_by_frame_id(message.frame_id)
    for signal in message.signals:
        old = previous.get_signal_by_name(signal.name)
        assert (signal.start, signal.length, signal.is_signed, signal.scale, signal.offset) == (
            old.start, old.length, old.is_signed, old.scale, old.offset)
backup = target.with_suffix(".before_text_refinement.dbc")
if not backup.exists():
    backup.write_bytes(target.read_bytes())
text = database.as_dbc_string()
text = "\r\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\r\n"
cantools.database.load_string(text, database_format="dbc", strict=True)
target.write_bytes(text.encode("gbk"))
reloaded = cantools.database.load_file(str(target), encoding="gbk", strict=True)
report = ["# 按STRUOFVIEW文本修订", "", "日期：2026-10-29", "",
          f"目标：`{target.name}`", "",
          "重新解析SUBJECT ENGINE之后的全部定义；逐项核对起始位、位宽、符号、系数和偏移，与所指定DBC均一致。",
          "因此保留文本中的原公式，不套用上一版C源码的布局与系数。",
          f"共{len(reloaded.messages)}条报文、{sum(len(message.signals) for message in reloaded.messages)}个信号；补充{len(changes)}处单位。",
          "单位依据文本的信号名或明确描述补充；温度使用ASCII degC避免编码乱码。",
          "未知单位留空，例如泵T1/T3、AD采样值；不从变量名猜测单位。",
          "FCC_PRESS越界、Turb_Pressure与InterCool_Temp重叠仍按此前保守规则不导出。",
          "字节列表与类型冲突时继续以多字节列表为准；单起始字节继续按声明类型展开。",
          "严格加载和落盘重载通过；重新生成时通过4224次独立数值解码检查。",
          "主DBC（按frame_disparameter修订的44报文版本）未改动。",
          "原始文件另存为 `.before_text_refinement.dbc`；详细原文冲突见text_definition/conversion_audit.md。",
          "", "## 单位补充", "", *changes]
(directory / "text_definition_修订说明.md").write_text("\n".join(report) + "\n", encoding="utf-8")
print(f"Updated {target.name}: {len(changes)} units")
