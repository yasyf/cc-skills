from __future__ import annotations

import json
import re

SCALAR_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


class YamlError(ValueError):
    pass


def unquoted(text: str, opens_anywhere: bool = True):
    quote, escaped = None, False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"" and (opens_anywhere or index == 0):
            quote = char
        else:
            yield index, char


def strip_comment(line: str) -> str:
    for index, char in unquoted(line):
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index].rstrip()
    return line.rstrip()


def split_flow(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for index, char in unquoted(text):
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    return [*parts, tail] if tail or parts else []


def scalar(text: str):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return [scalar(part) for part in split_flow(text[1:-1])]
    if text.startswith("{") and text.endswith("}"):
        return dict(pair(part) for part in split_flow(text[1:-1]))
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return json.loads(text)
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~", ""):
        return None
    if SCALAR_NUMBER.match(text):
        return float(text) if "." in text else int(text)
    return text


def key_split(text: str) -> tuple[str, str] | None:
    for index, char in unquoted(text, opens_anywhere=False):
        if char == ":" and (index + 1 == len(text) or text[index + 1] == " "):
            return text[:index].strip().strip("'\""), text[index + 1 :].strip()
    return None


def pair(text: str) -> tuple[str, object]:
    if not (split := key_split(text)):
        raise YamlError(f"expected key: value in {text!r}")
    return split[0], scalar(split[1])


def tokens(text: str) -> list[tuple[int, str, int]]:
    out = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw)
        if line.strip():
            out.append((len(line) - len(line.lstrip(" ")), line.strip(), number))
    return out


def loads(text: str):
    lines = tokens(text)
    if not lines:
        return {}
    value, index = block(lines, 0, lines[0][0], text.splitlines())
    if index != len(lines):
        raise YamlError(f"unexpected indentation at line {lines[index][2]}")
    return value


def literal(lines: list[tuple[int, str, int]], index: int, parent: int, raw: list[str]) -> tuple[str, int]:
    end, indent, body = lines[index - 1][2], None, []
    while end < len(raw):
        line = raw[end]
        if line.strip():
            lead = len(line) - len(line.lstrip(" "))
            if lead <= parent or (indent is not None and lead < indent):
                break
            indent = lead if indent is None else indent
        body.append(line)
        end += 1
    while body and not body[-1].strip():
        body.pop()
    while index < len(lines) and lines[index][2] <= end:
        index += 1
    return ("\n".join(line[indent:] for line in body) + "\n" if body else ""), index


def value_after(rest: str, lines, index: int, indent: int, raw: list[str]):
    if rest in ("|", "|-", ">"):
        text, index = literal(lines, index, indent, raw)
        return (text.rstrip("\n") if rest == "|-" else text), index
    if rest:
        return scalar(rest), index
    if index < len(lines) and lines[index][0] > indent:
        return block(lines, index, lines[index][0], raw)
    if index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
        return block(lines, index, indent, raw)
    return None, index


def block(lines, index: int, indent: int, raw: list[str]):
    if lines[index][1].startswith("-"):
        items = []
        while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("-"):
            rest = lines[index][1][1:].strip()
            index += 1
            if not rest:
                value, index = block(lines, index, lines[index][0], raw) if index < len(lines) and lines[index][0] > indent else (None, index)
                items.append(value)
            elif (split := key_split(rest)) and not rest.startswith(("[", "{", "'", '"')):
                inner = indent + 2
                item = {}
                key, after = split
                item[key], index = value_after(after, lines, index, inner, raw)
                if index < len(lines) and lines[index][0] > indent:
                    more, index = block(lines, index, lines[index][0], raw)
                    if not isinstance(more, dict):
                        raise YamlError(f"expected mapping continuation near line {lines[index - 1][2]}")
                    item.update(more)
                items.append(item)
            else:
                items.append(scalar(rest))
        return items, index
    mapping: dict = {}
    while index < len(lines) and lines[index][0] == indent:
        text = lines[index][1]
        if text.startswith("- "):
            raise YamlError(f"mixed list and mapping at line {lines[index][2]}")
        if not (split := key_split(text)):
            raise YamlError(f"expected key: value at line {lines[index][2]}")
        key, rest = split
        index += 1
        mapping[key], index = value_after(rest, lines, index, indent, raw)
    if index < len(lines) and lines[index][0] > indent:
        raise YamlError(f"unexpected indentation at line {lines[index][2]}")
    return mapping, index
