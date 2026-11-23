#!/usr/bin/env python3
"""Turn raw 10-K HTML into clean, sectioned text with tables kept as Markdown.

Run after download_sec.py:
    python scripts/parse_10k.py              # all filings
    python scripts/parse_10k.py --ticker AAPL  # one company

For each filing, writes to data/processed/<TICKER>/:
    <name>.json   metadata + a list of sections (Item 1, 1A, 7, 8, ...) for chunking
    <name>.md     the same text as one readable file, for checking the output by eye
and a summary of every filing to data/processed/parse_report.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString

ROOT = Path(__file__).resolve().parent.parent

ITEM_TITLES = {
    "1": "Business", "1A": "Risk Factors", "1B": "Unresolved Staff Comments", "1C": "Cybersecurity",
    "2": "Properties", "3": "Legal Proceedings", "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",
    "6": "[Reserved]",
    "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
    "9A": "Controls and Procedures", "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance", "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters",
    "13": "Certain Relationships and Related Transactions, and Director Independence",
    "14": "Principal Accountant Fees and Services", "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

PART_III_IV = {"10", "11", "12", "13", "14", "15", "16"}
FINANCIAL_TITLE = "Financial section (MD&A and financial statements placed after Part IV)"
FIN_HEADING_RE = re.compile(
    r"^(management['’]s discussion and analysis|consolidated (statements?|balance sheets?)\b"
    r"|report of independent registered public accounting firm|financial (section|table of contents)"
    r"|index to (consolidated )?financial statements)", re.IGNORECASE)

BLOCK_TAGS = ["p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"]
HIDDEN_HEADER_RE = re.compile(r"<ix:header>.*?</ix:header>", re.IGNORECASE | re.DOTALL)
HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
NUMERIC_RE = re.compile(r"^[\$\(\-—–]*\s*[\d,]*\.?\d+\s*%?\)?%?$|^[—–-]+$")
ITEM_RE = re.compile(
    r"^(?:part\s+[ivx]+\W+)?item\s*(\d{1,2}[abc]?)\s*(?:[\.:\-—–]|\s)\s*(.{0,150})$", re.IGNORECASE)
DATE_RE = re.compile(
    r"^((january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2},?\s+)?(19|20)\d{2}$", re.IGNORECASE)
JUNK_LINE_RE = re.compile(
    r"^(\d{1,3}|[ivx]{1,5}|page \d+|table of contents|index|.*form 10-k\s*\|?\s*\d{1,3})$", re.IGNORECASE)


def norm(text: str) -> str:
    text = text.replace("\xa0", " ").replace("​", "").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_number(cell: str) -> bool:
    return bool(NUMERIC_RE.match(cell.replace(" ", "")))


def is_column_header(cell: str) -> bool:
    """Year/date column labels such as '2025' or 'September 27, 2025'."""
    return bool(DATE_RE.match(cell)) or is_number(cell)


def merge_cells(cells: list[str]) -> list[str]:
    """SEC tables split '$', the number and ')' or '%' into separate cells; glue them back."""
    out: list[str] = []
    prefix = ""
    for c in cells:
        if c in ("$", "(", "$(", "($"):
            prefix += c
            continue
        if c in (")", "%", ")%", "%)") and out:
            out[-1] += c
            continue
        out.append(prefix + c)
        prefix = ""
    if prefix:
        out.append(prefix)
    return out


def table_to_markdown(table) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [norm(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
        cells = [re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", c)) for c in cells]
        cells = merge_cells([c for c in cells if c])
        if cells:
            rows.append(cells)
    if not rows:
        return ""

    width = max(len(r) for r in rows)
    has_numbers = any(is_number(c) for r in rows for c in r)
    # Layout tables (bullets, footnotes, two-column text) are prose, not data.
    if width == 1 or (width <= 2 and not has_numbers):
        return "\n".join(" ".join(r) for r in rows)

    grid = []
    for r in rows:
        if len(r) < width:
            if all(is_column_header(c) for c in r):
                r = [""] * (width - len(r)) + r          # header/number rows: right-align
            else:
                r = r[:1] + [""] * (width - len(r)) + r[1:]  # label stays left, numbers right
        grid.append([c.replace("|", "\\|") for c in r])

    lines = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
    return "\n".join(lines)


def html_to_text(html: str) -> tuple[str, int]:
    html = HIDDEN_HEADER_RE.sub("", html)  # hidden XBRL metadata: often most of the file
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "head", "title"]):
        tag.decompose()
    for tag in soup.find_all(style=HIDDEN_STYLE_RE):
        tag.decompose()

    n_tables = 0
    for table in soup.find_all("table"):
        if table.find_parent("table"):  # nested tables are handled by their outer table
            continue
        md = table_to_markdown(table)
        if md.startswith("|"):
            n_tables += 1
        table.replace_with(NavigableString(f"\n\n{md}\n\n"))

    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_after(NavigableString("\n"))

    lines = []
    for raw in soup.get_text("").split("\n"):
        line = raw if raw.lstrip().startswith("|") else norm(raw)
        line = line.strip()
        if JUNK_LINE_RE.match(line):
            continue
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text, n_tables


def split_sections(text: str) -> list[dict]:
    """Split on 'Item N.' headings. The table of contents repeats every heading, so for each
    item keep the occurrence followed by the most text."""
    lines = text.split("\n")
    hits = []
    for i, line in enumerate(lines):
        candidate = norm(line.replace("|", " ")) if line.startswith("|") else line
        if len(candidate) > 200:
            continue
        m = ITEM_RE.match(candidate)
        if m and m.group(1).upper() in ITEM_TITLES:
            hits.append((i, m.group(1).upper()))

    if not hits:
        return [{"item": "full", "title": "Full document", "text": text}]

    sized = []  # (line index, item, length of text until the next heading)
    for n, (i, key) in enumerate(hits):
        end = hits[n + 1][0] if n + 1 < len(hits) else len(lines)
        sized.append((i, key, sum(len(l) for l in lines[i + 1:end])))

    # Walk items in their official order; each must come after the previous one,
    # which skips table-of-contents entries for short items like "Item 6. [Reserved]".
    starts, last = [], -1
    for key in ITEM_TITLES:
        options = [(size, i) for i, k, size in sized if k == key and i > last]
        if options:
            _, i = max(options)
            starts.append((i, key))
            last = i
    if not starts:
        return [{"item": "full", "title": "Full document", "text": text}]
    sections = []
    pre = "\n".join(lines[:starts[0][0]]).strip()
    if pre:
        sections.append({"item": "cover", "title": "Cover page and table of contents", "text": pre})
    for n, (i, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        body = "\n".join(lines[i + 1:end]).strip()
        sections.append({"item": key, "title": ITEM_TITLES[key], "text": body})
    return split_financial_section(sections)


def split_financial_section(sections: list[dict]) -> list[dict]:
    """Some companies (JPM, XOM, CVX, NVDA...) place MD&A and the financial statements after
    Part IV instead of under Items 7 and 8. Part III/IV items are normally short, so when one
    is huge, move everything from the first MD&A / financial-statement heading into its own section."""
    out = []
    for s in sections:
        if s["item"] in PART_III_IV and len(s["text"]) > 50_000:
            body_lines = s["text"].split("\n")
            offset, cut = 0, None
            for n, line in enumerate(body_lines):
                if offset > 1000 and len(line) < 120 and FIN_HEADING_RE.match(line.strip("| ")):
                    cut = n
                    break
                offset += len(line) + 1
            if cut is not None:
                out.append({**s, "text": "\n".join(body_lines[:cut]).strip()})
                out.append({"item": "financial", "title": FINANCIAL_TITLE,
                            "text": "\n".join(body_lines[cut:]).strip()})
                continue
        out.append(s)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--ticker", help="Only parse this company")
    ap.add_argument("--force", action="store_true", help="Re-parse filings that were already processed")
    args = ap.parse_args()
    data = Path(args.data)

    records = [json.loads(l) for l in (data / "manifest.jsonl").read_text().splitlines() if l.strip()]
    if args.ticker:
        records = [r for r in records if r["ticker"] == args.ticker.upper()]

    report = []
    for n, rec in enumerate(sorted(records, key=lambda r: r["path"]), 1):
        src = data / rec["path"]
        out_dir = data / "processed" / rec["ticker"]
        out_json = out_dir / (src.stem + ".json")
        if out_json.exists() and not args.force:
            doc = json.loads(out_json.read_text())
            report.append(doc["stats"])
            continue

        t0 = time.time()
        html = src.read_text(encoding="utf-8", errors="replace")
        text, n_tables = html_to_text(html)
        sections = split_sections(text)
        stats = {
            "file": rec["path"], "ticker": rec["ticker"], "period": rec["period_of_report"],
            "raw_mb": round(len(html) / 1e6, 1), "clean_mb": round(len(text) / 1e6, 2),
            "tables": n_tables, "sections": len(sections),
            "items_found": " ".join(s["item"] for s in sections if s["item"] not in ("cover", "full")),
            "has_financial_statements": any(s["item"] in ("8", "financial") and len(s["text"]) > 15_000
                                            for s in sections),
            "seconds": round(time.time() - t0, 1),
        }
        doc = {**{k: rec[k] for k in ("ticker", "company", "cik", "accession", "filing_date",
                                      "period_of_report", "url")},
               "source_path": rec["path"], "stats": stats, "sections": sections}

        out_dir.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(doc, ensure_ascii=False))
        md = [f"# {rec['company']} 10-K, period ending {rec['period_of_report']}\n"]
        for s in sections:
            heading = s["title"] if s["item"] in ("cover", "full", "financial") else f"Item {s['item']}. {s['title']}"
            md.append(f"\n## {heading}\n\n{s['text']}\n")
        (out_dir / (src.stem + ".md")).write_text("".join(md), encoding="utf-8")

        report.append(stats)
        print(f"[{n}/{len(records)}] {rec['path']}: {stats['raw_mb']} MB -> {stats['clean_mb']} MB, "
              f"{n_tables} tables, items: {stats['items_found'] or 'none found'} ({stats['seconds']}s)")

    if report:
        report_path = data / "processed" / "parse_report.csv"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(report[0].keys()))
            w.writeheader()
            w.writerows(report)
        print(f"\nParsed {len(report)} filings. Summary: {report_path}")


if __name__ == "__main__":
    main()
