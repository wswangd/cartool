# 按STRUOFVIEW文本修订

日期：2026-10-29

目标：`STRUOFVIEW_2022-10-26_ENGINE_DRAFT.before_frame_disparameter.dbc`

重新解析SUBJECT ENGINE之后的全部定义；逐项核对起始位、位宽、符号、系数和偏移，与所指定DBC均一致。
因此保留文本中的原公式，不套用上一版C源码的布局与系数。
共37条报文、132个信号；补充26处单位。
单位依据文本的信号名或明确描述补充；温度使用ASCII degC避免编码乱码。
未知单位留空，例如泵T1/T3、AD采样值；不从变量名猜测单位。
FCC_PRESS越界、Turb_Pressure与InterCool_Temp重叠仍按此前保守规则不导出。
字节列表与类型冲突时继续以多字节列表为准；单起始字节继续按声明类型展开。
严格加载和落盘重载通过；重新生成时通过4224次独立数值解码检查。
主DBC（按frame_disparameter修订的44报文版本）未改动。
原始文件另存为 `.before_text_refinement.dbc`；详细原文冲突见text_definition/conversion_audit.md。

## 单位补充

- Vehicle_PARA1.APP_R: %
- Vehicle_PARA1.BaroPre: kPa
- Vehicle_PARA1.AMT: degC
- Vehicle_PARA1.Fuel_T: degC
- Vehicle_PARA1.Oil_T: degC
- Vehicle_PARA2.Trip_Distance: km
- Vehicle_PARA2.Total_Vehicle_Distance: km
- Vehicle_PARA3.Cool_Temp: degC
- Vehicle_PARA4.DOC_EGTU: degC
- Vehicle_PARA4.DPF_EGTU: degC
- Vehicle_PARA4.SCR_EGTU: degC
- Vehicle_PARA4.SCR_EGTD: degC
- Vehicle_PARA7.CWV_OPEN_TIME: s
- RUNTIME_2.ENG_RT_S: s
- RUNTIME_2.KEYON_S: s
- CAL1.SCRT_dmEG: kg/s
- CAL1.SCRT_tCatUs: degC
- CAL1.SCRT_tCatDs: degC
- UQS1.L_LDEF: %
- UQS1.L_TDEF: degC
- UQS2.C_TDEF: degC
- UQS2.C_CDEF: %
- UQS3.L_ELDEF: %
- UQS3.L_ETDEF: degC
- UQS4.L_DLDEF: %
- UQS4.L_DTDEF: degC
