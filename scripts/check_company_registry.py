from __future__ import annotations

import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


REQUIRED_FIELDS = ("id", "company_name", "registered_address", "is_non_public")

INVISIBLES = "".join(chr(cp) for cp in (0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060, 0x00A0))


def _fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def _strip_invisibles(text: str) -> str:
    return text.strip().strip(INVISIBLES).strip()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8")


def _extract_companies_from_prompt(prompt_text: str) -> list[dict[str, str]]:
    match = re.search(r"```json\s*(.*?)```", prompt_text, re.DOTALL)
    if not match:
        raise ValueError("prompt 中未找到 ```json ... ``` 代码块")
    companies = json.loads(match.group(1).strip())
    if not isinstance(companies, list):
        raise ValueError("prompt 公司列表不是数组")
    return companies


def _extract_batch_id_from_prompt(prompt_text: str, fallback: str) -> str:
    match = re.search(r"批次\s*ID\s*[：:]\s*(\S+)", prompt_text)
    if match:
        return match.group(1).strip()
    return fallback


def _parse_strict_response(response: str) -> tuple[dict | None, str]:
    """Per prompt contract: response must itself be a JSON object — no fences,
    no preamble/epilogue, first char `{`, last char `}`, JSON.parse-clean."""
    if not isinstance(response, str):
        return None, "response 不是字符串"
    s = _strip_invisibles(response)
    if not s:
        return None, "response 去除空白后为空"
    if "```" in s:
        return None, "response 含有 Markdown 代码围栏（```）"
    if not s.startswith("{"):
        return None, f"response 首字符不是 {{（实际 {s[:1]!r}，预览 head={s[:80]!r}）"
    if not s.endswith("}"):
        return None, f"response 末字符不是 }}（实际 {s[-1:]!r}，预览 tail={s[-80:]!r}）"
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as exc:
        return None, f"response 无法被 JSON.parse 解析：{exc}"
    if not isinstance(parsed, dict):
        return None, f"response 顶层不是对象（实际 {type(parsed).__name__}）"
    return parsed, ""


def _check_item_types(index: int, item: dict) -> str:
    cid = item["id"]
    if not isinstance(cid, str) or not cid:
        return f"items[{index}].id 必须是非空字符串（实际 {type(cid).__name__}={cid!r}）"

    company_name = item["company_name"]
    if not isinstance(company_name, str) or not company_name:
        return (
            f"items[{index}].company_name 必须是非空字符串"
            f"（实际 {type(company_name).__name__}={company_name!r}）"
        )

    addr = item["registered_address"]
    if addr is not None:
        if not isinstance(addr, str):
            return (
                f"items[{index}].registered_address 必须是字符串或 null"
                f"（实际 {type(addr).__name__}={addr!r}）"
            )
        if not addr:
            return (
                f"items[{index}].registered_address 为空字符串；模板要求未知时填 null，"
                f"不要使用空字符串"
            )

    is_non_public = item["is_non_public"]
    if is_non_public is not None and not isinstance(is_non_public, bool):
        return (
            f"items[{index}].is_non_public 必须是布尔或 null"
            f"（实际 {type(is_non_public).__name__}={is_non_public!r}）"
        )

    return ""


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        return _fail("用法：check_company_registry.py <output_json_path> <prompt_path>")

    output_path = Path(argv[1])
    prompt_path = Path(argv[2])

    try:
        output_data = json.loads(output_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _fail(f"输出 JSON 不存在：{output_path}")
    except json.JSONDecodeError as exc:
        return _fail(f"输出 JSON 解析失败：{exc}")

    response = output_data.get("response")
    if not isinstance(response, str) or not response.strip():
        return _fail("输出 JSON 缺少非空字符串字段 response")

    try:
        prompt_text = _read_text(prompt_path)
    except OSError as exc:
        return _fail(f"无法读取 prompt：{exc}")

    try:
        companies = _extract_companies_from_prompt(prompt_text)
    except (ValueError, json.JSONDecodeError) as exc:
        return _fail(f"无法解析 prompt 中的公司列表：{exc}")

    expected_batch_id = _extract_batch_id_from_prompt(prompt_text, prompt_path.stem)

    parsed, err = _parse_strict_response(response)
    if parsed is None:
        return _fail(err)

    actual_batch_id = parsed.get("batch_id")
    if not isinstance(actual_batch_id, str):
        return _fail(
            f"response.batch_id 缺失或不是字符串（实际 {type(actual_batch_id).__name__}={actual_batch_id!r}）"
        )
    if actual_batch_id != expected_batch_id:
        return _fail(
            f"response.batch_id 不匹配：期望 {expected_batch_id!r}，实际 {actual_batch_id!r}"
        )

    items = parsed.get("items")
    if not isinstance(items, list):
        keys = list(parsed.keys())
        return _fail(
            f"response.items 缺失或不是数组（已解析对象的键={keys}）"
        )

    if len(items) != len(companies):
        return _fail(
            f"items 数量不匹配：输入 {len(companies)} 条，输出 {len(items)} 条"
        )

    input_map: dict[str, str] = {}
    for index, company in enumerate(companies):
        if not isinstance(company, dict):
            return _fail(f"prompt companies[{index}] 不是对象")
        cid = company.get("id")
        name = company.get("name")
        if cid is None or name is None:
            return _fail(f"prompt companies[{index}] 缺少 id/name")
        input_map[str(cid)] = str(name)

    output_map: dict[str, dict] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            return _fail(f"items[{index}] 不是对象")
        missing = [field for field in REQUIRED_FIELDS if field not in item]
        if missing:
            return _fail(f"items[{index}] 缺少字段 {missing}")
        type_error = _check_item_types(index, item)
        if type_error:
            return _fail(type_error)
        item_id = item["id"]
        if item_id in output_map:
            return _fail(f"items 中存在重复 id：{item_id}")
        output_map[item_id] = item

    missing_ids = sorted(set(input_map) - set(output_map))
    if missing_ids:
        return _fail(f"输出缺少输入中的 id：{missing_ids}")

    extra_ids = sorted(set(output_map) - set(input_map))
    if extra_ids:
        return _fail(f"输出含有输入中没有的 id：{extra_ids}")

    mismatched: list[str] = []
    for cid, expected_name in input_map.items():
        actual = output_map[cid]["company_name"]
        if actual != expected_name:
            mismatched.append(f"id={cid} 输入={expected_name!r} 输出={actual!r}")
    if mismatched:
        return _fail("公司名称与输入不匹配：" + "; ".join(mismatched))

    print(f"OK 校验通过：{len(items)} 条记录与输入完全匹配，batch_id={actual_batch_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
