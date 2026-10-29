# frame_disparameter 修订核对记录

日期：2026-10-28

源文件：`E:\需求测试\26\吉利甲醇\代码\tmp_code\DCU_G6_AC7811_Master_QI_YN28_F1_24V_geely_9L_CAN1_500\User\app\scr_frame.c`
头文件：`E:\需求测试\26\吉利甲醇\代码\tmp_code\DCU_G6_AC7811_Master_QI_YN28_F1_24V_geely_9L_CAN1_500\User\app\scr_frame.h`
scr_frame.c SHA256：`1beeac25da96104b53254a16c733d132defc9ebd9eda114944d32c9be3e0e348`
scr_frame.h SHA256：`8531cde9da84956e8deef4c7175390fd797b6dea9f5ccfdceffb9e8871a45065`

原有 37 条报文，更新后 44 条、169 个信号。
已核对全部 30 个实际调用发送函数的 case；新增 7 条报文。
严格加载、边界/重叠检查、GBK落盘重载、8911 次独立字节解码及原始值回编码检查通过。
另核对 NOxD 分散字节、NH3 大端16位、CVN 大端32位的固定样例。
本次未调用 CANdb++ GUI；旧 CANdb 自测记录只针对原版草稿。

补充验证：`python -m unittest tests.test_frame_dbc` 的5项测试通过，
包括实际C打包布局的典型值、温度符号、大端CVN/氨存储、不连续NOx字节，
以及CANTool信号测量面板导入全部44条报文和169个信号。

## 报文范围

| case | CAN ID | 报文 | 信号数 | 操作 |
|---|---|---|---|---|
| 3 | 0x18FF443D | CAL4 | 5 | 更新 |
| 4 | 0x18FF453D | CAL5 | 2 | 更新 |
| 5 | 0x18FF463D | CAL6 | 8 | 更新 |
| 6 | 0x18FF473D | CAL7 | 4 | 更新 |
| 7 | 0x18FF483D | CAL8 | 7 | 更新 |
| 8 | 0x18FF493D | CAL9 | 4 | 更新 |
| 11 | 0x18FF4A3D | WORK1_DOSER | 4 | 更新 |
| 12 | 0x18FF4B3D | WORK2_DOSER | 4 | 更新 |
| 14 | 0x18FF4E3D | SENSOR1 | 5 | 更新 |
| 15 | 0x18FF4F3D | SENSOR2 | 5 | 更新 |
| 17 | 0x18FF623D | SENSOR4 | 4 | 更新 |
| 20 | 0x18FF513D | ENGINE | 5 | 更新 |
| 22 | 0x18FF533D | Vehicle_PARA1 | 7 | 更新 |
| 23 | 0x18FF543D | Vehicle_PARA2 | 2 | 更新 |
| 24 | 0x18FF553D | Vehicle_PARA3 | 4 | 更新 |
| 25 | 0x18FF573D | Vehicle_PARA4 | 4 | 更新 |
| 26 | 0x18FF583D | Vehicle_PARA5 | 6 | 更新 |
| 27 | 0x18FF593D | Vehicle_PARA6 | 4 | 更新 |
| 28 | 0x18FF603D | Vehicle_PARA7 | 6 | 更新 |
| 29 | 0x18FF613D | PARA8_MASTER | 3 | 更新 |
| 30 | 0x18FF563D | FAI_DTC | 8 | 更新 |
| 31 | 0x18FFA03D | FAI_FLASH | 7 | 新增 |
| 32 | 0x18FF653D | RUNTIME_1 | 3 | 更新 |
| 34 | 0x18FF673D | RUNTIME_3 | 2 | 更新 |
| 35 | 0x18FF683D | RUNTIME_4 | 5 | 新增 |
| 36 | 0x18FF693D | RUNTIME_5 | 2 | 新增 |
| 39 | 0x18FF6C3D | RUNTIME_8 | 1 | 新增 |
| 40 | 0x18FF6D3D | SCREFF1 | 4 | 新增 |
| 41 | 0x18FF6E3D | SCREFF2 | 4 | 新增 |
| 42 | 0x18FF6F3D | SCREFF3 | 4 | 新增 |

## 关键说明

- 源地址保留原 DBC 的 0x3D；代码 frame_init 默认也为0x3D，但可由 BYPTR_M0_RESERVE1 改写。其他地址需重新生成。
- 仅解析实际代码，排除注释内的 case 43～52；空 case 不新增报文。
- CAL4/CAL5/CAL6/CAL7/CAL8 等已改变用途，移除不再对应实际字节的旧信号、枚举和单位。
- WORK2 byte4 才是 Scr.pump_sta，byte5 为零。T1/T3通道依 Scr.module 选择；因此使用 selected_pump 名称。
- SENSOR1 CDEF 只有byte6（8位），byte7为零。两个电池字段均为 bsp_inj_vbat 原始返回值，不再套用未证实的0.15/-0.02换算。
- SENSOR2 PRESS_AD是byte0（8位），byte1为供电档位；NH3值为byte6高、byte7低，只能恢复原uint32值的低16位。
- SENSOR4 NOxD低字节在byte2、高字节在byte4，byte3为零。标准DBC不能直接表达中间跳字节：拆为NOxD_LowByte和NOxD_HighByte，ppm = Low + 256*High；不得误设byte3～4为连续16位。NOxU在byte5～6。
- Vehicle_PARA3 增压压力只发送word变量低8位，超过255 kPa的信息无法恢复；CVN为byte3～6大端。
- ENGINE转速/车速在接收处理时已换算为整数，不重复应用原始CAN分辨率；QinjLperH仍为原始计数，按scr_app.h的0.05 L/h换算。
- Vehicle_PARA1 Fuel为Qinj右移6位再截断8位，按1/512*64=0.125 km/L解码；高位丢失无法由DBC恢复。
- 温度接收代码已减偏移，车辆温度按有符号整数解释；单字节仅保留低8位，超出-128～127的温度存在歧义。SCR浮点温度经int转换后只发送低16位，不按IEEE浮点解码。
- CAL4两个char修正值保留raw字节，char是否有符号依工具链设置，未擅自假定。
- 未查明的内部值/泵计时单位留空、scale=1，并在信号注释标注Raw。范围为传输位宽可表示范围，并非传感器有效量程或故障值定义。
- 未继承可能过时的枚举；常量/未赋值填充字节一般不定义成信号。FAI_FLASH保留A0/DA标记和4个原始数据字节。
- FAI_FLASH还受v_dataview_link/v_dataview_ctrl条件限制；函数支持某case不代表固件调度一定会发送。

## 保留但未由本函数核对的旧报文

- `ENGINE_BOSCH` / `0x0CF00400`
- `SLAVE_Doser1` / `0x18F02400`
- `SLAVE_Doser1_1` / `0x0CF02400`
- `SLAVE_Doser2` / `0x18F0233D`
- `NOX_U` / `0x18F00E51`
- `NOX_D` / `0x18F00F52`
- `RUNTIME_2` / `0x18FF663D`
- `CAL1` / `0x18FF403D`
- `CAL2` / `0x18FF413D`
- `CAL3` / `0x18FF433D`
- `UQS1` / `0x18FE56A3`
- `UQS2` / `0x18FD9BA3`
- `UQS3` / `0x18FE5600`
- `UQS4` / `0x18FE563D`

原始备份：`STRUOFVIEW_2022-10-26_ENGINE_DRAFT.before_frame_disparameter.dbc`。旧conversion_audit.md和CANdb_自测结果.md是原草稿历史记录。
