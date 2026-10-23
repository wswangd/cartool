# STRUOFVIEW DBC 草稿自测结果

## 文件

- 输入：`E:\实验室测试\FAI\STRUOFVIEW\STRUOFVIEW 2022-10-26-1.txt`
- 起点：`SUBJECT ENGINE 18FF513D;`，不包含前面的 COMMAND 部分。
- 输出：`STRUOFVIEW_2022-10-26_ENGINE_DRAFT.dbc`，GBK/CP936 编码。
- 统计：37 条扩展帧报文，原文 135 个信号，导出 132 个，暂不导出 3 个。

## 用户确认的保守转换规则

- 明确列出多个字节时，按字节列表确定长度；只给一个起始字节时，按声明类型确定长度。
- 越界和相互重叠的定义暂不导出，不猜测修正位置。
- 假设字节编号从 0 开始、Intel 小端、报文长度 8 字节。这些并非原文明确声明，仍需用协议或实车数据确认。
- `sbyte`、`sword` 保留为有符号数；没有类型的 C_CAL1、C_CAL2 根据单字节位置推定为 8 位。
- 保留原文显示范围，部分范围超出类型实际可表示值，在 conversion_audit.md 中明确列出，不据此捏造比例或偏移。
- 信号名中的括号单位提取到 DBC Unit，完整原名与中文描述保留在注释中。

## CANdb++ 3.1 实际界面自测

- 已在本机 Vector CANdb++ 3.1 Editor 打开生成的 DBC，没有导入错误弹窗。
- Messages 页面显示 **37 Message(s)**，Signals 页面显示 **132 Signal(s)**，与生成统计一致。
- 在 Signals 页面核对了 Intel、Signed/Unsigned、系数、偏移和中文注释。
- 已执行菜单 **File → Consistency Check**。
- 结果：**0 Error、0 Warning、9 Info**。这 9 项均为 `Signal >= 8/16 bits not on 1/2-byte limit`，属于双字节对齐建议，不是越界或重叠错误。
- 没有为消除 Info 改动原文给定的位置，也没有通过界面保存覆盖生成文件。

### CANdb++ 的 9 条 Info

1. CAL9.T3_supplier
2. CAL9.T3_EOBD_supplier
3. RUNTIME_1.WUC
4. RUNTIME_1.OBD_C
5. SENSOR4.NOxD
6. SENSOR4.NOxU
7. WORK1_DOSER.prd_doser
8. SLAVE_Doser2.DCU_Integral
9. SLAVE_Doser1.FCC_Integral

## 自动校验

- cantools 严格加载通过，所有导出的信号均在 8 字节范围内且不重叠。
- 导出再读入后，ID、扩展帧标志、信号数量、起始位、位长、单位、有符号性、系数和偏移一致。
- 每条报文以全零、全 FF 及 30 组固定种子的随机字节测试，共 **4,224 次信号解码比对**。
- 参考解码直接使用 `int.from_bytes` 和 `物理值 = 原始值 × 系数 + 偏移`，与 cantools 结果一致。
- 原始值重新编码后再解码，所有信号值一致。

## 暂不导出的 3 个信号

| 信号 | 原定义问题 |
| --- | --- |
| SLAVE_Doser1.FCC_PRESS | 字节 7 起始的 word 需要字节 8，超出 8 字节报文 |
| Vehicle_PARA3.Turb_Pressure(kPa) | 字节 0 起始的 word 占用 0、1，与下一信号重叠 |
| Vehicle_PARA3.InterCool_Temp | 字节 1 的 sbyte，与前一信号重叠；为避免擅自选择，冲突双方都暂不导出 |

## 结论与限制

该文件在以上保守规则下可正常导入 CANdb++，结构与数值换算测试通过。
这是待协议确认的草稿，不等同于已经实车验证的最终协议：特别需要确认字节序、3 个未导出信号，以及 byte-list 与 dword 不一致的几处长度。
完整异常和推断见 `conversion_audit.md`；转换脚本为 `convert_struofview.py`。
