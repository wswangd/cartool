"""报文列表导入导出、日志导出与配置持久化。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .frame import (SEND_TYPE_OPTIONS, TRACE_HEADERS, CanFrame, FrameFormatError,
                    SendItem, TraceRecord, format_data, parse_data, parse_id)

APP_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = APP_DIR / "settings.json"
FILE_VERSION = 1

LIST_CSV_HEADERS = ("启用", "备注", "ID(hex)", "帧格式", "帧类型", "发送类型",
                    "长度", "数据(hex)", "周期(ms)", "次数")


class StorageError(Exception):
    """文件读写或格式错误，消息可直接展示给用户。"""


# ------------------------------------------------------------ 报文列表 JSON

def _item_to_dict(item: SendItem) -> dict:
    frame = item.frame
    return {
        "enabled": item.enabled, "note": item.note,
        "id": f"{frame.frame_id:X}", "extended": frame.extended,
        "remote": frame.remote, "send_type": frame.send_type,
        "dlc": frame.dlc, "data": format_data(frame.data),
        "period_ms": item.period_ms, "repeat": item.repeat,
    }


def _dict_to_item(raw: dict, uid: int) -> SendItem:
    frame = CanFrame(
        frame_id=parse_id(str(raw.get("id", "0"))),
        data=parse_data(str(raw.get("data", ""))),
        dlc=int(raw.get("dlc", -1)),
        extended=bool(raw.get("extended", False)),
        remote=bool(raw.get("remote", False)),
        send_type=int(raw.get("send_type", 0)),
    )
    return SendItem(
        uid=uid, frame=frame, enabled=bool(raw.get("enabled", True)),
        note=str(raw.get("note", "")), period_ms=int(raw.get("period_ms", 0) or 0),
        repeat=int(raw.get("repeat", 0) or 0),
    )


def save_list_json(path: Path, items: list[SendItem]) -> None:
    payload = {"version": FILE_VERSION,
               "messages": [_item_to_dict(item) for item in items]}
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def load_list_json(path: Path) -> list[dict]:
    try:
        payload = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise StorageError(f"JSON 解析失败：{exc}") from exc
    messages = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(messages, list):
        raise StorageError("文件里找不到 messages 数组")
    return messages


# ------------------------------------------------------------- 报文列表 CSV

def save_list_csv(path: Path, items: list[SendItem]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(LIST_CSV_HEADERS)
        for item in items:
            frame = item.frame
            writer.writerow([
                1 if item.enabled else 0, item.note, f"{frame.frame_id:X}",
                frame.format_text, frame.type_text, frame.send_type_text,
                frame.dlc, format_data(frame.data), item.period_ms,
                item.repeat if item.repeat > 0 else "无限",
            ])


def load_list_csv(path: Path) -> list[dict]:
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise StorageError("文件是空的")
    header = [cell.strip() for cell in rows[0]]
    if "ID(hex)" not in header:
        raise StorageError("CSV 缺少表头行，应包含：" + ",".join(LIST_CSV_HEADERS))
    index = {name: header.index(name) for name in LIST_CSV_HEADERS if name in header}

    def cell(row: list[str], name: str, default: str = "") -> str:
        pos = index.get(name)
        return row[pos].strip() if pos is not None and pos < len(row) else default

    messages = []
    for line_no, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        try:
            messages.append({
                "enabled": cell(row, "启用", "1") not in ("0", "否", "false", "False"),
                "note": cell(row, "备注"),
                "id": cell(row, "ID(hex)", "0"),
                "extended": cell(row, "帧格式", "标准帧") == "扩展帧",
                "remote": cell(row, "帧类型", "数据帧") == "远程帧",
                "send_type": _send_type_value(cell(row, "发送类型", "正常发送")),
                "dlc": int(cell(row, "长度") or -1),
                "data": cell(row, "数据(hex)"),
                "period_ms": int(float(cell(row, "周期(ms)", "0") or 0)),
                "repeat": _repeat_value(cell(row, "次数", "0")),
            })
        except (ValueError, FrameFormatError) as exc:
            raise StorageError(f"第 {line_no} 行格式错误：{exc}") from exc
    return messages


def _send_type_value(text: str) -> int:
    text = text.strip()
    if text in SEND_TYPE_OPTIONS:
        return SEND_TYPE_OPTIONS.index(text)
    return int(text) if text else 0


def _repeat_value(text: str) -> int:
    text = text.strip()
    return 0 if text in ("", "无限", "0") else int(float(text))


# --------------------------------------------------------------- 统一入口

def load_list(path: Path) -> list[dict]:
    path = Path(path)
    text = _read_text(path).lstrip()
    if path.suffix.lower() == ".json" or text.startswith("{"):
        return load_list_json(path)
    if path.suffix.lower() in (".csv", ".txt"):
        return load_list_csv(path)
    raise StorageError(f"不支持的文件类型 {path.suffix}，请使用 .json 或 .csv")


def load_items(path: Path) -> list[SendItem]:
    items = []
    for uid, raw in enumerate(load_list(path), start=1):
        try:
            items.append(_dict_to_item(raw, uid))
        except (FrameFormatError, ValueError, TypeError) as exc:
            raise StorageError(f"报文条目无效：{exc}") from exc
    return items


def save_list(path: Path, items: list[SendItem]) -> None:
    path = Path(path)
    if path.suffix.lower() == ".json":
        save_list_json(path, items)
    else:
        save_list_csv(path, items)


# ---------------------------------------------------------------- 日志导出

def export_trace_csv(path: Path, records: list[TraceRecord]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(TRACE_HEADERS)
        for record in records:
            frame = record.frame
            writer.writerow([
                record.seq, record.time_text, record.direction,
                frame.format_text, "错误帧" if record.error else frame.type_text,
                f"{frame.frame_id:X}", frame.dlc, format_data(frame.payload),
                record.dev_timestamp or "",
            ])


# ---------------------------------------------------------------- 配置持久化

def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_settings(settings: dict) -> None:
    try:
        _write_text(SETTINGS_PATH,
                    json.dumps(settings, ensure_ascii=False, indent=2))
    except OSError:
        pass


def _write_text(path: Path, text: str) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"写入 {path} 失败：{exc}") from exc


def _read_text(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        raise StorageError(f"文件不存在：{path}")
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise StorageError(f"读取 {path} 失败：{exc}") from exc
    raise StorageError(f"无法识别 {path} 的文本编码")
