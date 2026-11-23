#!/usr/bin/env python3
"""Build a numeric Q&A eval set from SEC XBRL data, with answers that are known to be correct.

Run after download_sec.py:
    python scripts/make_eval.py

Writes data/eval/numeric_qa.jsonl. Each question is answerable from one of the downloaded
10-Ks (the source filing is recorded), and the answer is the official XBRL value.
"""
import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# label -> XBRL concepts to try, in priority order (companies tag the same idea differently)
METRICS = {
    "total revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "net income": ["NetIncomeLoss"],
    "operating income": ["OperatingIncomeLoss"],
    "research and development expense": ["ResearchAndDevelopmentExpense"],
    "diluted earnings per share": ["EarningsPerShareDiluted"],
    "total assets": ["Assets"],
    "cash and cash equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
    "total stockholders' equity": ["StockholdersEquity"],
}


def parse(d: str) -> date:
    return date.fromisoformat(d)


def human_date(d: str) -> str:
    x = parse(d)
    return f"{x.strftime('%B')} {x.day}, {x.year}"


def human_value(val: float, unit: str) -> str:
    if unit == "USD/shares":
        return f"${val:,.2f} per share"
    if unit == "USD":
        sign = "-" if val < 0 else ""
        v = abs(val)
        if v >= 1e9:
            return f"{sign}${v / 1e9:,.2f} billion"
        if v >= 1e6:
            return f"{sign}${v / 1e6:,.1f} million"
        return f"{sign}${v:,.0f}"
    return f"{val:,} {unit}"


def facts_for(us_gaap: dict, concept: str):
    units = us_gaap.get(concept, {}).get("units", {})
    for unit in ("USD", "USD/shares"):
        for fact in units.get(unit, []):
            yield unit, fact


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(ROOT / "data"))
    args = ap.parse_args()
    data = Path(args.data)

    filings_by_ticker = defaultdict(dict)  # ticker -> accession -> manifest record
    for line in (data / "manifest.jsonl").read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            filings_by_ticker[rec["ticker"]][rec["accession"]] = rec

    questions = []
    for ticker, filings in sorted(filings_by_ticker.items()):
        facts_path = data / "xbrl" / f"{ticker}.json"
        if not facts_path.exists():
            print(f"{ticker}: no XBRL file, skipping")
            continue
        us_gaap = json.loads(facts_path.read_text()).get("facts", {}).get("us-gaap", {})
        company = next(iter(filings.values()))["company"]

        for label, concepts in METRICS.items():
            chosen = {}  # period end -> best fact for this metric
            for concept in concepts:
                for unit, f in facts_for(us_gaap, concept):
                    if f.get("form") != "10-K" or f.get("accn") not in filings:
                        continue
                    start = f.get("start")
                    if start is not None:
                        days = (parse(f["end"]) - parse(start)).days
                        if not 350 <= days <= 380:  # keep full fiscal years, drop quarters
                            continue
                    end = f["end"]
                    source = filings[f["accn"]]
                    # Prefer the filing whose own reporting period is this one (not a prior-year comparison column)
                    is_primary = source["period_of_report"] == end
                    current = chosen.get(end)
                    if current is None or (is_primary and not current["primary"]):
                        chosen[end] = {"concept": concept, "unit": unit, "fact": f, "source": source,
                                       "primary": is_primary}
                if chosen:
                    break  # found this metric under a higher-priority concept; don't mix concepts

            for end, c in sorted(chosen.items()):
                f, unit, source = c["fact"], c["unit"], c["source"]
                if f.get("start") is not None:
                    q = f"What was {company}'s {label} for the fiscal year ended {human_date(end)}?"
                else:
                    q = f"What was {company}'s {label} as of {human_date(end)}?"
                questions.append({
                    "id": f"{ticker}-{c['concept']}-{end}",
                    "ticker": ticker,
                    "company": company,
                    "question": q,
                    "answer_value": f["val"],
                    "unit": unit,
                    "answer_text": human_value(f["val"], unit),
                    "metric": label,
                    "xbrl_concept": c["concept"],
                    "period_start": f.get("start"),
                    "period_end": end,
                    "source_accession": source["accession"],
                    "source_path": source["path"],
                })

    out = data / "eval" / "numeric_qa.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(q) + "\n" for q in questions))
    print(f"Wrote {len(questions)} questions for {len(filings_by_ticker)} companies to {out}")


if __name__ == "__main__":
    main()
