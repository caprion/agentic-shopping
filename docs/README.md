# Documentation

This directory is the write-up that sits next to the code. It is meant to be read in order by someone
who wants to understand **what we built, why it is built that way, what the numbers say, and where the
real opportunities are**.

| # | Document | What it answers | Audience |
|---|---|---|---|
| 01 | [The product](01-the-product.md) | What Aria actually does, end to end, and what each surface is for | Anyone |
| 02 | [Architecture](02-architecture.md) | How the system is put together — retrieval, agent, guardrails, evaluation | Engineers |
| 03 | [Evaluation](03-evaluation.md) | What the system measures, what it scores, and where it is weak | Everyone (numbers first) |
| 04 | [Learnings](04-learnings.md) | What building it taught us — process, retrieval, grounding, data, eval | Everyone |
| 05 | [Moats](05-moats.md) | Where the defensible positions are in agentic commerce, and which we can take | Strategy |
| 06 | [Emerging markets](06-emerging-markets.md) | What changes when the shopper is voice-first, vernacular and price-sensitive | Strategy |

The long-form research dossiers that sit behind these live in [`../research`](../research):

| Dossier | Topic |
|---|---|
| [01](../research/01-amazon-ai-shopping.md) | Amazon's AI shopping surface today: what it does, where it breaks |
| [02](../research/02-competition-landscape.md) | The competitive landscape and the protocol layer (ACP, UCP, MCP) |
| [03](../research/03-open-datasets.md) | Every public dataset we evaluated, with verification status and licences |
| [04](../research/04-architecture.md) | The full reference architecture, DDL, technology choices, 10-day build order |
| [05](../research/05-india-emerging-markets.md) | Voice, vernacular, trust, COD and pincode logistics |

## How to run the demo

```bash
bash demo/scripts/fetch_data.sh          # public datasets → data/raw (≈16 GB)
python demo/ingest/build_db.py --stage all
python demo/ingest/build_embeddings.py   # incremental, resumable
python demo/run.py                       # → http://localhost:8000
```

## Reproducing the numbers

```bash
python -m app.eval --selftest            # quick
python demo/tests/run_all.py             # smoke tests
```

The full evaluation (retrieval metrics on the Amazon Shopping Queries gold set, constraint-parsing
accuracy, agent task success, groundedness, latency) is reproduced in
[03-evaluation.md](03-evaluation.md).
