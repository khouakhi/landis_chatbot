"""Check that retrieval finds the passage that answers each test question.

Each case names a phrase that must appear in a passage that answers the question.
A case passes when any passage containing the phrase is in the top results.
Run it from the code folder after the database changes:

    python -m apps.admin.eval_retrieval
    python -m apps.admin.eval_retrieval --top-k 8 --verbose

Add a case whenever a question is answered badly, so the fix stays in place.
"""

from __future__ import annotations

import argparse
import sys

from landis import rag, store_pg
from landis.embeddings import get_client

# (question, phrase that must appear in the passage that answers it)
CASES: list[tuple[str, str]] = [
    ("how many units Soilscapes define?", "27 soil map units"),
    ("how many soilscape classes are there in England and Wales?", "27 soil map units"),
    ("how many reference soil groups are in the World Reference Base?", "32 Reference Soil Groups"),
    ("how many soil associations are there?", "296 geographic soil associations"),
    ("how many soil series are mapped on the national soil map?", "297 soil series"),
    ("how many map units does the soil association map have?", "297 map units"),
    ("what is the area of the Denchworth series?", "2,801 km"),
    ("what is the area of the Dowels association?", "95 km2"),
    ("what is the Soilscapes classification for drainage?", "Soilscape class"),
    ("what is a soil series?", "soil series"),
    ("what is the difference between a soil series and an association?", "association"),
    ("tell me about the Denchworth series", "Denchworth series"),
    ("how is the WRB classification used?", "World Reference Base"),
    ("what is association 1013a?", "1013a"),
    ("which soils are most common in Wales?", "Wales"),
]


def gold_chunk_ids(phrase: str) -> set[str]:
    rows = store_pg.get_connection().execute(
        "SELECT chunk_id FROM chunks WHERE position(%s in text) > 0", (phrase,)
    ).fetchall()
    return {row["chunk_id"] for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval check for LandIS questions")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--verbose", action="store_true", help="Show the passages found")
    args = parser.parse_args()

    client = get_client()
    passed = 0
    for question, phrase in CASES:
        gold = gold_chunk_ids(phrase)
        sources, _ = rag.retrieve(client, question, args.top_k, [])
        found = [source.chunk_id for source in sources]
        hit = any(chunk_id in gold for chunk_id in found)
        passed += hit
        print(f"{'PASS' if hit else 'FAIL'}  {question}")
        if args.verbose or not hit:
            print(f"      expected a passage with: {phrase!r}")
            print(f"      found: {', '.join(found)}")
    print(f"\nRecall at {args.top_k}: {passed} of {len(CASES)} questions")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
