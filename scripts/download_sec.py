#!/usr/bin/env python3
"""Download recent 10-K filings and XBRL company facts from SEC EDGAR.

Usage:
    python scripts/download_sec.py --user-agent "Daniel Li your_email@rutgers.edu"

Writes:
    data/raw/<TICKER>/<TICKER>_10-K_<period>.htm   the 10-K documents (your RAG corpus)
    data/xbrl/<TICKER>.json                        structured financials (ground truth for evals)
    data/manifest.jsonl                            one line of metadata per downloaded filing

Safe to re-run: files that already exist are skipped.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
MIN_INTERVAL = 0.15  # seconds between requests; SEC allows at most 10 per second


class Edgar:
    """Small polite HTTP client: required User-Agent, rate limiting, retries."""

    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._last = 0.0

    def get(self, url: str) -> requests.Response:
        for attempt in range(4):
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            resp = self.session.get(url, timeout=60)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * 2**attempt)
                continue
            resp.raise_for_status()
        resp.raise_for_status()
        return resp


def to_rows(columns: dict) -> list[dict]:
    """EDGAR returns filings as parallel lists; turn them into one dict per filing."""
    keys = list(columns.keys())
    return [dict(zip(keys, vals)) for vals in zip(*columns.values())]


def load_tickers(path: Path) -> list[tuple[str, int | None]]:
    """Each line: TICKER, optionally followed by a CIK to override SEC's ticker lookup."""
    tickers = []
    for line in path.read_text().splitlines():
        parts = line.split("#")[0].split()
        if parts:
            tickers.append((parts[0].upper(), int(parts[1]) if len(parts) > 1 else None))
    return tickers


def find_10ks(edgar: Edgar, cik: int, wanted: int) -> tuple[str, list[dict]]:
    sub = edgar.get(SUBMISSIONS_URL.format(cik=cik)).json()
    tenks = [r for r in to_rows(sub["filings"]["recent"]) if r["form"] == "10-K"]
    # Big banks file thousands of forms a year, so older 10-Ks can sit in extra pages.
    for page in sub["filings"].get("files", []):
        if len(tenks) >= wanted:
            break
        older = edgar.get(SUBMISSIONS_PAGE_URL.format(name=page["name"])).json()
        tenks += [r for r in to_rows(older) if r["form"] == "10-K"]
    return sub.get("name", ""), tenks[:wanted]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-agent", required=True,
                    help='Your name and email, e.g. "Daniel Li dl123@rutgers.edu". SEC requires this.')
    ap.add_argument("--tickers", default=str(ROOT / "tickers.txt"), help="File with one ticker per line")
    ap.add_argument("--per-company", type=int, default=3, help="How many recent 10-Ks per company (default 3)")
    ap.add_argument("--out", default=str(ROOT / "data"), help="Output folder (default ./data)")
    args = ap.parse_args()

    if "@" not in args.user_agent:
        sys.exit('--user-agent must include an email address, e.g. "Daniel Li dl123@rutgers.edu"')

    out = Path(args.out)
    raw_dir, xbrl_dir = out / "raw", out / "xbrl"
    raw_dir.mkdir(parents=True, exist_ok=True)
    xbrl_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.jsonl"

    manifest: dict[str, dict] = {}
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                manifest[rec["accession"]] = rec

    edgar = Edgar(args.user_agent)
    print("Fetching SEC ticker list...")
    ticker_map = {
        v["ticker"].upper(): (int(v["cik_str"]), v["title"])
        for v in edgar.get(TICKER_MAP_URL).json().values()
    }

    tickers = load_tickers(Path(args.tickers))
    failures = []
    for i, (ticker, cik_override) in enumerate(tickers, 1):
        if cik_override is not None:
            cik = cik_override
        elif ticker in ticker_map:
            cik = ticker_map[ticker][0]
        else:
            print(f"[{i}/{len(tickers)}] {ticker}: not in SEC ticker list, skipping")
            failures.append(ticker)
            continue

        try:
            company, tenks = find_10ks(edgar, cik, args.per_company)
        except requests.RequestException as e:
            print(f"[{i}/{len(tickers)}] {ticker}: could not list filings: {e}")
            failures.append(ticker)
            continue
        print(f"[{i}/{len(tickers)}] {ticker} ({company}, CIK {cik})")
        if not tenks:
            print("    no 10-K filings found for this CIK; check the ticker or add a CIK override")
            failures.append(ticker)

        for f in tenks:
            acc = f["accessionNumber"]
            period = f.get("reportDate") or f["filingDate"]
            suffix = Path(f["primaryDocument"]).suffix or ".htm"
            dest = raw_dir / ticker / f"{ticker}_10-K_{period}{suffix}"
            url = DOC_URL.format(cik=cik, acc=acc.replace("-", ""), doc=f["primaryDocument"])
            if not dest.exists():
                try:
                    resp = edgar.get(url)
                except requests.RequestException as e:
                    print(f"    10-K {period}: download failed ({e})")
                    failures.append(f"{ticker} {period}")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(resp.content)
                print(f"    10-K for period {period}: {len(resp.content) / 1e6:.1f} MB")
            else:
                print(f"    10-K for period {period}: already downloaded")
            manifest[acc] = {
                "ticker": ticker,
                "company": company,
                "cik": cik,
                "form": "10-K",
                "accession": acc,
                "filing_date": f["filingDate"],
                "period_of_report": period,
                "url": url,
                "path": str(dest.relative_to(out)),
            }

        facts_path = xbrl_dir / f"{ticker}.json"
        if not facts_path.exists():
            try:
                facts_path.write_bytes(edgar.get(FACTS_URL.format(cik=cik)).content)
                print("    XBRL company facts saved")
            except requests.RequestException as e:
                print(f"    XBRL facts failed ({e})")
                failures.append(f"{ticker} xbrl")

        # Save after every company so an interrupted run can resume.
        manifest_path.write_text("".join(json.dumps(r) + "\n" for r in manifest.values()))

    print(f"\nDone: {len(manifest)} filings listed in {manifest_path}")
    if failures:
        print("Problems (re-run to retry):", ", ".join(failures))


if __name__ == "__main__":
    main()
