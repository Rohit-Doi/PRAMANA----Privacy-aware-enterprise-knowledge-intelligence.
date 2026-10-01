"""Download the free public corpora used by the demo tenant and the evaluation suites.

No account or API key is needed. Every step is idempotent: existing files are kept.
A step that fails is reported and skipped; nothing is ever invented to fill a gap.

    python scripts/fetch_data.py                 # everything
    python scripts/fetch_data.py --only handbook regulations

Outputs
    data/raw/handbook/<department>/*.md      GitLab Handbook pages (attribution kept)
    data/raw/regulations/*.pdf               laws and standards (public law / public domain)
    data/raw/contracts/*.txt                 CUAD commercial contracts (CC BY 4.0)
    data/raw/emails/*.eml                    Enron corpus sample (public record)
    data/raw/manifest.jsonl                  one row per document: department, sensitivity, …
    data/eval/ragtruth_*.jsonl, halueval_qa.jsonl   detector benchmarks (not ingested)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
EVAL = ROOT / "data" / "eval"
USER_AGENT = "Mozilla/5.0"


@dataclass
class ManifestRow:
    path: str
    title: str
    source_type: str
    collection: str
    department: str
    sensitivity: str
    region: str = "global"
    effective_date: str | None = None
    version: str = "1"
    source_uri: str = ""
    license: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# GitLab Handbook
# ─────────────────────────────────────────────────────────────────────────────
HANDBOOK_REPO = "https://gitlab.com/gitlab-com/content-sites/handbook.git"
HANDBOOK_SECTIONS: dict[str, tuple[str, str, int]] = {
    # handbook folder: (department, sensitivity, max pages)
    "people-group": ("hr", "INTERNAL", 45),
    "people-policies": ("hr", "INTERNAL", 20),
    "total-rewards": ("hr", "INTERNAL", 20),
    "hiring": ("hr", "INTERNAL", 15),
    "labor-and-employment-notices": ("hr", "INTERNAL", 5),
    "security": ("security", "INTERNAL", 90),
    "finance": ("finance", "INTERNAL", 60),
    "legal": ("legal", "INTERNAL", 50),
    "engineering": ("engineering", "INTERNAL", 45),
    "product": ("engineering", "INTERNAL", 15),
    "support": ("engineering", "INTERNAL", 10),
    "it": ("it", "INTERNAL", 25),
    "business-technology": ("it", "INTERNAL", 15),
    "marketing": ("marketing", "PUBLIC", 35),
    "sales": ("sales", "INTERNAL", 35),
    "company": ("company", "PUBLIC", 20),
    "values": ("company", "PUBLIC", 8),
    "communication": ("company", "PUBLIC", 12),
}
POLICY_HINTS = (
    "policy", "policies", "procedure", "standard", "guideline", "process", "compliance",
    "security", "access", "incident", "expense", "leave", "benefit", "onboarding", "offboarding",
    "privacy", "data", "risk", "audit", "travel", "remote", "password", "approval", "contract",
    "retention", "classification", "vulnerability", "code-of-conduct", "handbook", "training",
)
FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
TITLE_LINE = re.compile(r"^title:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)  # noqa: S603 - fixed argv, no shell


def _slug(text: str, limit: int = 80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "untitled"


def fetch_handbook(rows: list[ManifestRow]) -> None:
    repo = RAW / "_handbook_repo"
    out = RAW / "handbook"
    if not (repo / ".git").exists():
        print("handbook: cloning (sparse, no history) …")
        _run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", HANDBOOK_REPO, str(repo)])
        _run(
            ["git", "sparse-checkout", "set", *[f"content/handbook/{s}" for s in HANDBOOK_SECTIONS]],
            cwd=repo,
        )
    commit = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

    kept = 0
    for section, (department, sensitivity, cap) in HANDBOOK_SECTIONS.items():
        section_dir = repo / "content" / "handbook" / section
        if not section_dir.is_dir():
            print(f"handbook: section missing upstream: {section}")
            continue
        candidates: list[tuple[int, int, str, Path, str]] = []
        for md in sorted(section_dir.rglob("*.md")):
            text = md.read_text(encoding="utf-8", errors="replace")
            body = FRONT_MATTER.sub("", text, count=1)
            if not 1_500 <= len(body) <= 40_000:
                continue
            fm = FRONT_MATTER.match(text)
            title_match = TITLE_LINE.search(fm.group(1)) if fm else None
            rel = md.relative_to(section_dir)
            title = title_match.group(1).strip() if title_match else rel.parent.name or section
            haystack = (str(rel) + " " + title).lower()
            score = sum(1 for hint in POLICY_HINTS if hint in haystack)
            depth = len(rel.parts)
            candidates.append((-score, depth, str(rel), md, title))
        candidates.sort()
        for _, _, rel_str, md, title in candidates[:cap]:
            rel = Path(rel_str)
            name = _slug(section + "-" + "-".join(rel.with_suffix("").parts)) + ".md"
            target = out / department / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(md, target)
            url_path = "/".join(rel.parent.parts) if rel.name == "_index.md" else "/".join(rel.with_suffix("").parts)
            rows.append(
                ManifestRow(
                    path=str(target.relative_to(ROOT / "data")).replace("\\", "/"),
                    title=title,
                    source_type="handbook",
                    collection="handbook",
                    department=department,
                    sensitivity=sensitivity,
                    source_uri=f"https://handbook.gitlab.com/handbook/{section}/{url_path}".rstrip("/"),
                    version=commit,
                    license="GitLab Handbook, see upstream LICENSE; attribution: GitLab B.V.",
                )
            )
            kept += 1
    print(f"handbook: {kept} pages selected (commit {commit})")


# ─────────────────────────────────────────────────────────────────────────────
# Regulations and standards
# ─────────────────────────────────────────────────────────────────────────────
REGULATIONS = [
    # file name, title, url, effective date, licence note
    ("gdpr-regulation-eu-2016-679.pdf", "General Data Protection Regulation (EU) 2016/679",
     "http://publications.europa.eu/resource/cellar/3e485e15-11bd-11e6-ba9a-01aa75ed71a1.0006.01/DOC_1",
     "2018-05-25", "EU law; reuse permitted (Commission Decision 2011/833/EU)"),
    ("hipaa-45-cfr-part-164.pdf", "HIPAA Security and Privacy Rules (45 CFR Part 164)",
     "https://www.govinfo.gov/content/pkg/CFR-2023-title45-vol2/pdf/CFR-2023-title45-vol2-part164.pdf",
     "2023-10-01", "US federal regulation; public domain"),
    ("sarbanes-oxley-act-2002.pdf", "Sarbanes-Oxley Act of 2002 (Public Law 107-204)",
     "https://www.govinfo.gov/content/pkg/PLAW-107publ204/pdf/PLAW-107publ204.pdf",
     "2002-07-30", "US public law; public domain"),
    ("nist-csf-2.0.pdf", "NIST Cybersecurity Framework 2.0",
     "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf",
     "2024-02-26", "NIST publication; public domain"),
    ("nist-ai-rmf-1.0.pdf", "NIST AI Risk Management Framework 1.0",
     "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
     "2023-01-26", "NIST publication; public domain"),
    ("nist-sp-800-171r3.pdf", "NIST SP 800-171 Rev. 3: Protecting Controlled Unclassified Information",
     "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-171r3.pdf",
     "2024-05-14", "NIST publication; public domain"),
    ("india-dpdp-act-2023.pdf", "Digital Personal Data Protection Act, 2023 (India)",
     "https://prsindia.org/files/bills_acts/acts_parliament/2023/Digital_Personal_Data_Protection_Act,_2023.pdf",
     "2023-08-11", "Indian Act of Parliament; government publication"),
]


def fetch_regulations(rows: list[ManifestRow]) -> None:
    out = RAW / "regulations"
    out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": USER_AGENT}) as client:
        for filename, title, url, effective, licence in REGULATIONS:
            target = out / filename
            if not target.exists():
                try:
                    response = client.get(url)
                    if response.status_code != 200 or not response.content.startswith(b"%PDF"):
                        print(f"regulations: SKIPPED {filename} (HTTP {response.status_code}, not a PDF)")
                        continue
                    target.write_bytes(response.content)
                except httpx.HTTPError as exc:
                    print(f"regulations: SKIPPED {filename} ({exc!r})")
                    continue
            rows.append(
                ManifestRow(
                    path=str(target.relative_to(ROOT / "data")).replace("\\", "/"),
                    title=title, source_type="regulation", collection="regulations",
                    department="compliance", sensitivity="PUBLIC", effective_date=effective,
                    source_uri=url, license=licence,
                )
            )
            print(f"regulations: ok {filename} ({target.stat().st_size // 1024} KB)")


# ─────────────────────────────────────────────────────────────────────────────
# CUAD contracts
# ─────────────────────────────────────────────────────────────────────────────
def _parquet_urls(dataset: str) -> dict[str, dict[str, list[str]]]:
    response = httpx.get(
        f"https://huggingface.co/api/datasets/{dataset}/parquet",
        timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


CUAD_JSON_URL = "https://huggingface.co/datasets/theatticusproject/cuad/resolve/main/CUAD_v1/CUAD_v1.json"


def fetch_contracts(rows: list[ManifestRow], limit: int = 50) -> None:
    out = RAW / "contracts"
    out.mkdir(parents=True, exist_ok=True)
    existing = sorted(out.glob("*.txt"))
    if len(existing) < limit:
        # CUAD ships one SQuAD-format file; each entry holds the full text of one contract.
        response = httpx.get(CUAD_JSON_URL, timeout=300, follow_redirects=True, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        seen: dict[str, str] = {}
        for entry in response.json()["data"]:
            title = entry["title"]
            context = entry["paragraphs"][0]["context"]
            if title not in seen and 8_000 <= len(context) <= 60_000:
                seen[title] = context
        for title in sorted(seen)[:limit]:
            target = out / (_slug(title, 100) + ".txt")
            if not target.exists():
                target.write_text(seen[title], encoding="utf-8")
        existing = sorted(out.glob("*.txt"))
    for target in existing[:limit]:
        pretty = re.sub(r"[_\-]+", " ", target.stem).title()[:140]
        rows.append(
            ManifestRow(
                path=str(target.relative_to(ROOT / "data")).replace("\\", "/"),
                title=f"Contract: {pretty}", source_type="contract", collection="contracts",
                department="legal", sensitivity="CONFIDENTIAL",
                source_uri="https://huggingface.co/datasets/theatticusproject/cuad",
                license="CUAD v1, CC BY 4.0, The Atticus Project",
            )
        )
    print(f"contracts: {min(len(existing), limit)} contracts")


# ─────────────────────────────────────────────────────────────────────────────
# Enron emails
# ─────────────────────────────────────────────────────────────────────────────
def _header(value: str) -> str:
    """Email header values must be a single line."""
    return re.sub(r"\s+", " ", value).strip()[:900]


def fetch_emails(rows: list[ManifestRow], limit: int = 1_200) -> None:
    out = RAW / "emails"
    out.mkdir(parents=True, exist_ok=True)
    existing = sorted(out.glob("*.eml"))
    if len(existing) < limit:
        from datasets import load_dataset

        stream = load_dataset("corbt/enron-emails", split="train", streaming=True)
        written = len(existing)
        seen_bodies: set[str] = set()
        for record in stream:
            if written >= limit:
                break
            body = (record.get("body") or "").strip()
            subject = (record.get("subject") or "").strip()
            if not subject or not 400 <= len(body) <= 4_000:
                continue
            if body.count("\n>") > 3 or "-----Original Message-----" in body:
                continue  # skip long quoted threads; they duplicate other messages
            digest = hashlib.sha256(body.encode()).hexdigest()
            if digest in seen_bodies:
                continue
            seen_bodies.add(digest)

            message = EmailMessage()
            message["Subject"] = _header(subject)
            message["From"] = _header(str(record.get("from") or ""))
            recipients = record.get("to") or []
            if isinstance(recipients, list):
                recipients = ", ".join(str(r) for r in recipients[:20])
            message["To"] = _header(str(recipients))
            when = record.get("date")
            if when is not None:
                try:
                    message["Date"] = format_datetime(when)
                except (TypeError, ValueError):
                    message["Date"] = str(when)
            message.set_content(body)
            (out / f"enron-{digest[:16]}.eml").write_bytes(message.as_bytes())
            written += 1
        existing = sorted(out.glob("*.eml"))
    for target in existing[:limit]:
        rows.append(
            ManifestRow(
                path=str(target.relative_to(ROOT / "data")).replace("\\", "/"),
                title="", source_type="email", collection="emails",
                department="operations", sensitivity="CONFIDENTIAL",
                source_uri="https://huggingface.co/datasets/corbt/enron-emails",
                license="Enron corpus, public record (FERC release)",
            )
        )
    print(f"emails: {min(len(existing), limit)} messages")


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation benchmarks (never ingested as knowledge)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_eval() -> None:
    import pandas as pd

    EVAL.mkdir(parents=True, exist_ok=True)

    ragtruth_done = list(EVAL.glob("ragtruth_*.jsonl"))
    if not ragtruth_done:
        urls = _parquet_urls("wandb/RAGTruth-processed")
        config = next(iter(urls.values()))
        for split, split_urls in config.items():
            frames = [pd.read_parquet(url) for url in split_urls]
            frame = pd.concat(frames, ignore_index=True)
            target = EVAL / f"ragtruth_{split}.jsonl"
            frame.to_json(target, orient="records", lines=True, force_ascii=False)
            print(f"eval: ragtruth {split}: {len(frame)} rows, columns {list(frame.columns)}")
    else:
        print(f"eval: ragtruth already present ({len(ragtruth_done)} files)")

    halueval = EVAL / "halueval_qa.jsonl"
    if not halueval.exists():
        urls = _parquet_urls("pminervini/HaluEval")
        config_name = "qa_samples" if "qa_samples" in urls else next(iter(urls))
        config = urls[config_name]
        frames = [pd.read_parquet(url) for split_urls in config.values() for url in split_urls]
        frame = pd.concat(frames, ignore_index=True)
        frame.to_json(halueval, orient="records", lines=True, force_ascii=False)
        print(f"eval: halueval {config_name}: {len(frame)} rows, columns {list(frame.columns)}")
    else:
        print("eval: halueval already present")


# ─────────────────────────────────────────────────────────────────────────────
STEPS = ("handbook", "regulations", "contracts", "emails", "eval")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", choices=STEPS, default=list(STEPS))
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / "manifest.jsonl"
    previous: dict[str, list[dict[str, object]]] = {}
    if manifest_path.exists():
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            previous.setdefault(row["collection"], []).append(row)

    collected: dict[str, list[ManifestRow]] = {}
    failures = 0
    runners = {
        "handbook": fetch_handbook, "regulations": fetch_regulations,
        "contracts": fetch_contracts, "emails": fetch_emails,
    }
    for step in args.only:
        try:
            if step == "eval":
                fetch_eval()
            else:
                rows: list[ManifestRow] = []
                runners[step](rows)
                collected[step] = rows
        except Exception as exc:  # report and continue with the other corpora
            failures += 1
            print(f"{step}: FAILED: {exc!r}")

    merged: list[dict[str, object]] = []
    for collection in ("handbook", "regulations", "contracts", "emails"):
        if collection in collected:
            merged.extend(asdict(row) for row in collected[collection])
        else:
            merged.extend(previous.get(collection, []))
    manifest_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged), encoding="utf-8"
    )
    print(f"manifest: {len(merged)} documents -> {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
