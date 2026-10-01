"""Source connectors. A connector turns an external source into ``NewDocument`` items; the
rest of the pipeline is the same for every source.

Implemented here: local files listed in a manifest, and rows of a SQL table. Connectors
for hosted services (SharePoint, Confluence, live mailboxes) would implement the same
function shape, but need that organisation's credentials.
"""

from __future__ import annotations

import email
import email.policy
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from pramana.knowledge.service import NewDocument
from pramana.shared.types import Sensitivity


def manifest_documents(data_dir: Path, manifest: Path) -> Iterator[tuple[NewDocument, dict[str, Any]]]:
    """Files described by a JSON-lines manifest (path, title, department, sensitivity, …)."""
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        path = data_dir / row["path"]
        data = path.read_bytes()
        title = row.get("title") or ""
        if not title and path.suffix.lower() == ".eml":
            subject = str(email.message_from_bytes(data, policy=email.policy.default).get("Subject", "")).strip()
            title = f"Email: {subject}" if subject else f"Email {path.stem}"
        effective = row.get("effective_date")
        yield (
            NewDocument(
                data=data, filename=path.name, title=title or path.stem,
                department=row["department"], sensitivity=Sensitivity.parse(row["sensitivity"]),
                region=row.get("region", "global"), source_type=row.get("source_type", "file"),
                collection=row.get("collection", "default"),
                effective_date=date.fromisoformat(effective) if effective else None,
                version=str(row.get("version", "1")), source_uri=row.get("source_uri", ""),
                meta={"license": row.get("license", ""), "manifest_path": row["path"]},
            ),
            row,
        )


EMPLOYEE_TEMPLATE = """# Employee record: {full_name} ({employee_id})

Classification: RESTRICTED. Source: HR database, table employees.

## Role

{full_name} is a {title} (level {level}) in the {department} department, based in the {region_upper} region.
Employee ID: {employee_id}. Hire date: {hire_date}. Manager: {manager}.

## Contact

Work email: {email}. Phone: {phone}.

## Compensation

Base salary: ${base_salary_usd:,} per year. Bonus target: {bonus_target_pct}% of base salary.

## Performance

Latest performance rating: {performance_rating}.

## Payroll identifiers

National ID: {national_id}. Bank account number {bank_account}.
"""


def sql_employee_documents(database_url: str, *, source_name: str = "hr") -> Iterator[NewDocument]:
    """One document per row of the ``employees`` table: structured data made retrievable.

    The same pattern works for any table: a query and a text template per row.
    """
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = [dict(r._mapping) for r in connection.execute(text("SELECT * FROM employees ORDER BY employee_id"))]
    finally:
        engine.dispose()
    names = {r["employee_id"]: r["full_name"] for r in rows}
    for row in rows:
        manager = names.get(row["manager_id"], "none (reports to the board)")
        body = EMPLOYEE_TEMPLATE.format(**row, manager=manager, region_upper=str(row["region"]).upper())
        yield NewDocument(
            data=body.encode("utf-8"), filename=f"employee-{row['employee_id']}.md",
            title=f"Employee record: {row['full_name']} ({row['employee_id']})",
            department="hr", sensitivity=Sensitivity.RESTRICTED, region="global",
            source_type="sql", collection="hr-directory",
            source_uri=f"sql://{source_name}/employees/{row['employee_id']}",
            meta={"employee_id": row["employee_id"], "full_name": row["full_name"]},
        )
