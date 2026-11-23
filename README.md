# SEC 10-K RAG Assistant

Question answering over SEC 10-K filings, with an eval set whose answers come from the SEC's own XBRL financial data.

## Get the data

```bash
cd ~/Sandbox/sec-rag
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# SEC requires a real name and email in the User-Agent
python scripts/download_sec.py --user-agent "Daniel Li your_email@rutgers.edu"
python scripts/make_eval.py
python scripts/parse_10k.py   # clean text + Markdown tables, split into 10-K Items
```

Defaults: the 20 companies in `tickers.txt`, the 3 most recent 10-Ks each (about 60 filings, a few hundred MB).
Change the list or use `--per-company N` to adjust. Re-running skips files already downloaded.

## What you get

| Path | Contents |
|---|---|
| `data/raw/<TICKER>/*.htm` | The 10-K documents: the corpus to chunk and embed |
| `data/xbrl/<TICKER>.json` | SEC "company facts": every tagged financial number |
| `data/manifest.jsonl` | Company, period, filing date, accession number and URL for each 10-K |
| `data/eval/numeric_qa.jsonl` | Generated questions with exact answers and the source filing |
| `data/processed/<TICKER>/*.json` | Parsed filings: sections (Item 1A, 7, 8...) with clean text and Markdown tables |
| `data/processed/<TICKER>/*.md` | The same, as one readable file per filing |
| `data/processed/parse_report.csv` | Per-filing stats: size, tables, sections found |

## Data cleaning

`parse_10k.py` turns 285 MB of raw 10-K HTML (60 filings) into 31 MB of clean text with 8,600+ tables:

- **Hidden XBRL metadata removed.** Bank filings are up to 38 MB raw; most of that is invisible `ix:header` data.
- **Tables kept as Markdown.** SEC tables split `$`, the number and `)` into separate cells; these are merged
  back so `$ | 355,533` becomes `$355,533` and negative values stay as `(321)`. Layout-only tables become plain text.
- **Split into 10-K Items.** Every Item heading also appears in the table of contents, so the parser walks
  Items in their official order and keeps the occurrence followed by the most text.
- **Non-standard layouts handled.** JPMorgan, Exxon, Chevron, NVIDIA, Netflix and Disney put MD&A and
  financial statements after Part IV instead of under Items 7 and 8; these are detected and split into
  their own `financial` section. All 60 filings end up with labeled financial statements.

## Roadmap

- [x] Parse 10-K HTML into clean text, keeping tables intact
- [ ] Baseline: fixed-size chunks, embeddings, Chroma, top-k retrieval, cited answers
- [ ] Eval harness: retrieval hit rate (right filing?), numeric accuracy (within 1%)
- [ ] Improvements, one at a time: table-aware chunking, metadata filters (ticker/year), hybrid BM25, reranker
- [ ] Hand-written questions for risk factors and year-over-year comparisons
- [ ] FastAPI + Streamlit demo, deployed

## Results

| Change | Retrieval hit rate | Numeric accuracy |
|---|---|---|
| Baseline | | |
