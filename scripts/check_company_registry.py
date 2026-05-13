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


def _fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


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


def _extract_response_object(response_text: str) -> dict:
    text = response_text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1).strip())

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError("无法从 response 中解析 JSON 对象")


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

    try:
        parsed = _extract_response_object(response)
    except (ValueError, json.JSONDecodeError) as exc:
        return _fail(f"无法解析 response 中的 JSON：{exc}")

    if not isinstance(parsed, dict):
        return _fail("response 顶层不是 JSON 对象")

    items = parsed.get("items")
    if not isinstance(items, list):
        return _fail("response.items 缺失或不是数组")

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
        item_id = str(item["id"])
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
        actual = str(output_map[cid].get("company_name", ""))
        if actual != expected_name:
            mismatched.append(f"id={cid} 输入={expected_name!r} 输出={actual!r}")
    if mismatched:
        return _fail("公司名称与输入不匹配：" + "; ".join(mismatched))

    print(f"OK 校验通过：{len(items)} 条记录与输入完全匹配")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
