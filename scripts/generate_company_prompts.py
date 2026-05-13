from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "companies.csv"
DEFAULT_TEMPLATE = PROJECT_ROOT / "prompt_templates" / "company_registry_lookup.md"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "prompts"

REQUIRED_COLUMNS = ("id", "name")
REQUIRED_PLACEHOLDERS = ("{{BATCH_ID}}", "{{COMPANIES_JSON}}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate DeepSeek prompt files from data/companies.csv."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"CSV file with id,name columns. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help=f"Prompt template file. Default: {DEFAULT_TEMPLATE}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for generated .prompt files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of companies per prompt file. Default: 10",
    )
    parser.add_argument(
        "--prefix",
        default="company_registry_lookup",
        help="Generated prompt filename prefix. Default: company_registry_lookup",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many input rows before batching. Default: 0",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Generate prompts for at most this many rows. Useful for testing.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing prompt files with the same names.",
    )
    parser.add_argument(
        "--drop-incomplete",
        action="store_true",
        help="Drop the final batch if it has fewer than --batch-size rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print a summary without writing files.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="With --dry-run, print the first rendered prompt.",
    )
    return parser.parse_args()


def read_companies(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {csv_path}")

        missing = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"CSV is missing required columns {missing}; found {reader.fieldnames}"
            )

        rows: list[dict[str, str]] = []
        for line_number, row in enumerate(reader, start=2):
            company_id = (row.get("id") or "").strip()
            name = (row.get("name") or "").strip()
            if not company_id or not name:
                raise ValueError(
                    f"Blank id/name at CSV line {line_number}: id={company_id!r}, name={name!r}"
                )
            rows.append({"id": company_id, "name": name})

    return rows


def chunked(rows: list[dict[str, str]], batch_size: int) -> Iterable[list[dict[str, str]]]:
    for start in range(0, len(rows), batch_size):
        yield rows[start : start + batch_size]


def load_template(template_path: Path) -> str:
    template = template_path.read_text(encoding="utf-8")
    missing = [
        placeholder
        for placeholder in REQUIRED_PLACEHOLDERS
        if placeholder not in template
    ]
    if missing:
        raise ValueError(f"Template is missing required placeholders: {missing}")
    return template


def render_prompt(
    template: str,
    *,
    batch_id: str,
    companies: list[dict[str, str]],
) -> str:
    companies_json = json.dumps(companies, ensure_ascii=False, indent=2)
    replacements = {
        "{{BATCH_ID}}": batch_id,
        "{{COMPANIES_JSON}}": companies_json,
    }

    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def select_rows(
    rows: list[dict[str, str]], *, offset: int, limit: int | None
) -> list[dict[str, str]]:
    if offset < 0:
        raise ValueError("--offset must be greater than or equal to 0")
    if limit is not None and limit < 0:
        raise ValueError("--limit must be greater than or equal to 0")

    selected = rows[offset:]
    if limit is not None:
        selected = selected[:limit]
    return selected


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")

    rows = select_rows(
        read_companies(args.input),
        offset=args.offset,
        limit=args.limit,
    )
    batches = list(chunked(rows, args.batch_size))
    if args.drop_incomplete and batches and len(batches[-1]) < args.batch_size:
        batches = batches[:-1]

    total_batches = len(batches)
    template = load_template(args.template)

    if args.dry_run:
        print(
            f"Validated {len(rows)} rows into {total_batches} prompt batches "
            f"(batch size: {args.batch_size})."
        )
        if args.preview and batches:
            batch_id = f"{args.prefix}_0001"
            print()
            print(
                render_prompt(
                    template,
                    batch_id=batch_id,
                    companies=batches[0],
                )
            )
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    digits = max(4, len(str(total_batches)))
    for batch_index, companies in enumerate(batches, start=1):
        batch_id = f"{args.prefix}_{batch_index:0{digits}d}"
        output_path = args.output_dir / f"{batch_id}.prompt"
        if output_path.exists() and not args.overwrite:
            skipped += 1
            continue

        output_path.write_text(
            render_prompt(
                template,
                batch_id=batch_id,
                companies=companies,
            ),
            encoding="utf-8",
        )
        written += 1

    expected_rows = sum(len(batch) for batch in batches)
    print(
        f"Generated {written} prompt files, skipped {skipped} existing files, "
        f"covering {expected_rows} rows."
    )
    print(f"Output directory: {args.output_dir}")
    if total_batches and len(batches[-1]) < args.batch_size:
        print(
            "Note: the final prompt has "
            f"{len(batches[-1])} rows because the input count is not divisible by "
            f"{args.batch_size}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
