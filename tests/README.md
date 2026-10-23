# 测试文件

此目录集中存放测试脚本、测试数据、数据生成脚本和调试输出。
正式配置 `settings.json`、`signals.json`、`experiments.json` 保留在项目根目录。

从项目根目录运行指定测试，例如：

```powershell
python -m unittest tests.test_list_checkbox tests.test_list_sequence tests.test_list_csv_order tests.test_list_delete_editors tests.test_scrollbar_menu tests.test_send_lifecycle tests.test_measurement
```

生成测试数据：

```powershell
python -m tests.gen_test_csv
python -m tests.gen_wltc_csv
python -m tests.verify_wltc
python -m tests.test_csv_export
```

生成的数据保存在此目录，不写入项目根目录。

注意：`test_import_export.py` 是旧版手工验证脚本，会调用正式配置保存逻辑，
可能覆盖项目的 `signals.json`，且与当前接口存在兼容性问题。
不要直接运行全目录测试发现，也不要在未备份正式配置时运行此脚本。
`test_signal_card_layout.py` 此前存在字体尺寸断言失败，与本次文件整理无关。
