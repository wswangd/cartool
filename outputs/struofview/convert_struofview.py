"""Convert the signal section to a conservative, explicitly audited DBC draft."""

import argparse
import math
import random
import re
from pathlib import Path

import cantools
from cantools.database.can import Database, Message, Node, Signal
from cantools.database.conversion import BaseConversion


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
DECLARATION = re.compile(
    r"^(\S+)\s+([0-7]+)\s+(y=x(?:[*/]" + NUMBER + r")?(?:[+-](?:\d+(?:\.\d*)?|\.\d+))?)"
    r"\s*(byte|sbyte|word|sword|dword)?\s*\(([^)]*)\)\s*(.*)$", re.S)
TYPE_BITS = {"byte": 8, "sbyte": 8, "word": 16, "sword": 16, "dword": 32}


def convert(source_path: Path, output_dir: Path):
    source = source_path.read_bytes().decode("gb18030")
    marker = "SUBJECT ENGINE 18FF513D;"
    source = source[source.index(marker):]
    issues = []
    omitted = []
    messages = []
    used_names = set()
    count = 0
    statements = [statement.strip() for statement in source.split(";") if statement.strip()]
    groups = []
    current = None
    for statement in statements:
        if statement.startswith("SUBJECT "):
            match = re.fullmatch(r"SUBJECT\s+(\S+)\s+([0-9A-Fa-f]+)", statement)
            if not match:
                raise ValueError(f"Invalid SUBJECT: {statement}")
            current = (match[1], int(match[2], 16), [])
            groups.append(current)
        elif statement == "END":
            current = None
        elif current is not None:
            current[2].append(statement)
        else:
            raise ValueError(f"Unexpected statement: {statement}")

    for message_name, frame_id, declarations in groups:
        candidates = []
        for declaration in declarations:
            count += 1
            match = DECLARATION.fullmatch(declaration)
            if not match:
                raise ValueError(f"Unrecognized signal in {message_name}: {declaration}")
            original_name, positions, formula, type_name, limits, description = match.groups()
            reference = f"{message_name}.{original_name}"
            digits = [int(digit) for digit in positions]
            if digits != list(range(digits[0], digits[0] + len(digits))):
                raise ValueError(f"Non-contiguous byte positions: {reference}: {positions}")
            if type_name is None:
                issues.append(f"{reference}: missing type; inferred {len(digits) * 8} bits from byte list.")
            length = len(digits) * 8 if len(digits) > 1 or type_name is None else TYPE_BITS[type_name]
            if type_name and len(digits) > 1 and TYPE_BITS[type_name] != length:
                issues.append(f"{reference}: byte list {positions} => {length} bits, overrides {type_name}.")
            factor_match = re.fullmatch(r"y=x(?:([*/])(" + NUMBER + r"))?([+-](?:\d+(?:\.\d*)?|\.\d+))?", formula)
            operation, operand, shift = factor_match.groups()
            factor = 1.0 if operand is None else float(operand)
            if operation == "/":
                factor = 1.0 / factor
            offset = float(shift) if shift else 0.0
            bounds = re.fullmatch(r"\s*(" + NUMBER + r")\s*[,，]\s*(" + NUMBER + r")\s*", limits)
            if bounds is None:
                bounds = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", limits)
            if bounds is None:
                raise ValueError(f"Invalid range: {reference}: {limits}")
            minimum, maximum = map(float, bounds.groups())
            unit_match = re.fullmatch(r"([^()]+)\(([^()]*)\)(.*)", original_name)
            unit = unit_match[2] if unit_match else ""
            base_name = unit_match[1] + unit_match[3] if unit_match else original_name
            name = re.sub(r"[^A-Za-z0-9_]", "_", base_name)
            if not re.match(r"[A-Za-z_]", name):
                name = "S_" + name
            if name.casefold() in used_names:
                name = f"{message_name}_{name}"
            if len(name) > 32:
                name = name[:25] + f"_{count:06d}"
            if name.casefold() in used_names:
                raise ValueError(f"Duplicate generated name: {name}")
            used_names.add(name.casefold())
            signed = type_name in ("sbyte", "sword")
            raw_minimum = -(1 << (length - 1)) if signed else 0
            raw_maximum = (1 << (length - int(signed))) - 1
            physical_bounds = sorted((raw_minimum * factor + offset, raw_maximum * factor + offset))
            if minimum < physical_bounds[0] or maximum > physical_bounds[1]:
                issues.append(f"{reference}: declared range [{minimum:g},{maximum:g}] exceeds representable "
                              f"[{physical_bounds[0]:g},{physical_bounds[1]:g}]; retained as source display range.")
            choices = {int(value): label.strip() for value, label in
                       re.findall(r"^\s*(\d+):([^\n]+)", description, flags=re.M)} or None
            comment = (f"Original: {original_name}; bytes={positions}; type={type_name or 'inferred'}; {formula}. "
                       "ASSUMED Intel byte order; byte-list length takes precedence. " + description)
            signal = Signal(name=name, start=digits[0] * 8, length=length, byte_order="little_endian",
                            is_signed=signed, conversion=BaseConversion.factory(factor, offset, choices),
                            minimum=minimum, maximum=maximum, unit=unit, comment=comment, receivers=["Monitor"])
            candidates.append((reference, signal))
        invalid = {}
        for reference, signal in candidates:
            if signal.start + signal.length > 64:
                invalid[reference] = "extends beyond the assumed 8-byte CAN payload"
        for index, (reference, signal) in enumerate(candidates):
            for other_reference, other in candidates[index + 1:]:
                if max(signal.start, other.start) < min(signal.start + signal.length, other.start + other.length):
                    invalid[reference] = f"overlaps {other_reference}"
                    invalid[other_reference] = f"overlaps {reference}"
        for reference, reason in invalid.items():
            omitted.append(f"{reference}: {reason}")
        signals = [signal for reference, signal in candidates if reference not in invalid]
        messages.append(Message(frame_id=frame_id, name=message_name, length=8, signals=signals,
                                is_extended_frame=True, senders=["ECU"],
                                comment="DRAFT conversion; assumed 8-byte payload and Intel byte order; verify against ECU captures.",
                                strict=True))

    database = Database(messages=messages, nodes=[Node("ECU"), Node("Monitor")], strict=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    dbc_path = output_dir / "STRUOFVIEW_2022-10-26_ENGINE_DRAFT.dbc"
    cantools.database.dump_file(database, str(dbc_path), database_format="dbc", encoding="gbk")
    loaded = cantools.database.load_file(str(dbc_path), encoding="gbk", strict=True)
    random_source = random.Random(20221026)
    checked = 0
    for original, message in zip(database.messages, loaded.messages):
        assert original.frame_id == message.frame_id and message.is_extended_frame
        assert len(original.signals) == len(message.signals)
        for expected, actual in zip(original.signals, message.signals):
            assert (expected.name, expected.start, expected.length, expected.unit or "", expected.is_signed,
                    expected.scale, expected.offset) == (actual.name, actual.start, actual.length,
                    actual.unit or "", actual.is_signed, actual.scale, actual.offset), actual.name
        for payload in [bytes(8), bytes([255]) * 8] + [random_source.randbytes(8) for _ in range(30)]:
            decoded = message.decode(payload, decode_choices=False)
            raw_values = {}
            for signal in message.signals:
                chunk = payload[signal.start // 8:(signal.start + signal.length) // 8]
                raw = int.from_bytes(chunk, "little", signed=signal.is_signed)
                expected_value = raw * signal.scale + signal.offset
                assert math.isclose(decoded[signal.name], expected_value, abs_tol=1e-8, rel_tol=1e-10)
                raw_values[signal.name] = raw
                checked += 1
            encoded = message.encode(raw_values, scaling=False, strict=False)
            assert message.decode(encoded, decode_choices=False) == decoded

    report = ["# STRUOFVIEW → DBC conversion audit", "", f"Source: {source_path}",
              f"Start marker: `{marker}`", f"Messages: {len(messages)}", f"Source signals: {count}",
              f"Exported signals: {sum(len(message.signals) for message in messages)}",
              f"Omitted signals: {len(omitted)}", f"Independent physical decode checks: {checked}", "",
              "## Status", "DRAFT: syntax, strict bounds/non-overlap, metadata round-trip and independent numeric decoding passed.",
              "This does not establish ECU protocol correctness. No live ECU data was provided.", "",
              "## Assumptions", "- 8-byte extended CAN frames, zero-based byte positions, Intel/little-endian byte order.",
              "- Explicit multi-byte lists define signal width; otherwise the declared integer type defines width.",
              "- Signedness follows sbyte/sword. Missing types use the byte-list width.",
              "- Only explicit name-parenthesis units and explicit multiline value enumerations are extracted.",
              "- Source display ranges are retained even when wider than the representable physical range.",
              "- Names are DBC-safe and globally unique; original names and descriptions remain in signal comments.",
              "- No undocumented cycle time, offset, unit or extra signal has been invented.",
              "- Output encoding is GBK/CP936 for compatibility with Chinese CANdb++ 3.1.", "",
              "## Omitted definitions requiring confirmation", *[f"- {issue}" for issue in omitted], "",
              "## Warnings requiring protocol review", *[f"- {issue}" for issue in issues], ""]
    (output_dir / "conversion_audit.md").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report[:11]))
    print(dbc_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    arguments = parser.parse_args()
    convert(arguments.source, arguments.output_dir)
