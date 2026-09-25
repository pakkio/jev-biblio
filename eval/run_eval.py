# /// script
# requires-python = ">=3.10"
# dependencies = ["typesafe-sdk", "python-dotenv"]
# ///
"""Misura quanto le stelle del verificatore concordano con le etichette di eval/dataset.jsonl.

Uso:
    uv run eval/run_eval.py [--only apollo11,curie] [--workers 3]

Stampa accuratezza esatta, entro una stella, accordo buono/non buono (>= 3 stelle),
errore medio, matrice di confusione, tempi e costi; salva i dettagli in eval/results.json.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import main  # noqa: E402


def run_item(item):
    start = time.perf_counter()
    result = json.loads(main.run_bibliography_verifier(item["article"], item["bibliography"]))
    return item, result, time.perf_counter() - start


def main_eval():
    parser = argparse.ArgumentParser(description="Valutazione del verificatore sul dataset etichettato")
    parser.add_argument("--only", help="prefissi degli id da includere, separati da virgola")
    parser.add_argument("--workers", type=int, default=3, help="articoli verificati in parallelo")
    args = parser.parse_args()

    items = [json.loads(line) for line in open(HERE / "dataset.jsonl")]
    if args.only:
        prefixes = tuple(args.only.split(","))
        items = [i for i in items if i["id"].startswith(prefixes)]

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        outcomes = list(pool.map(run_item, items))

    rows, confusion = [], [[0] * 5 for _ in range(5)]
    for item, result, seconds in outcomes:
        if "error" in result:
            print(f"ERRORE {item['id']}: {result['error']}")
            continue
        expected, got = item["expected_stars"], result["rating_stars"]
        confusion[expected - 1][got - 1] += 1
        rows.append({
            "id": item["id"], "expected": expected, "got": got, "value": result["rating_value"],
            "confidence": result["rating_confidence"], "counts": result["claim_counts"],
            "seconds": round(seconds, 1), "cents": result["metrics"]["total_cents"],
        })

    print(f"\n{'articolo':22} {'atteso':>6} {'ottenuto':>8} {'valore':>6} {'conf':>5} {'tempo':>6} {'costo':>8}  affermazioni")
    for r in sorted(rows, key=lambda r: r["id"]):
        mark = "✓" if r["got"] == r["expected"] else ("≈" if abs(r["got"] - r["expected"]) == 1 else "✗")
        counts = ", ".join(f"{n} {v.lower()}" for v, n in r["counts"].items())
        print(f"{r['id']:22} {r['expected']:>6} {r['got']:>7}{mark} {r['value']:>6.2f} {r['confidence']:>5.0%} "
              f"{r['seconds']:>5.1f}s {r['cents']:>7.3f}¢  {counts}")

    n = len(rows)
    if not n:
        return
    exact = sum(r["got"] == r["expected"] for r in rows) / n
    within = sum(abs(r["got"] - r["expected"]) <= 1 for r in rows) / n
    binary = sum((r["got"] >= 3) == (r["expected"] >= 3) for r in rows) / n
    mae = sum(abs(r["value"] - r["expected"]) for r in rows) / n
    print(f"\nArticoli: {n}")
    print(f"Stelle esatte:            {exact:.0%}")
    print(f"Entro una stella:         {within:.0%}")
    print(f"Buono/non buono corretto: {binary:.0%}")
    print(f"Errore medio (valore):    {mae:.2f} stelle")
    print(f"Tempo medio:              {sum(r['seconds'] for r in rows) / n:.1f} s")
    print(f"Costo totale:             {sum(r['cents'] for r in rows):.3f} ¢ ({sum(r['cents'] for r in rows) / n:.3f} ¢ ad articolo)")
    print("\nMatrice di confusione (righe = atteso, colonne = ottenuto):")
    print("       " + " ".join(f"{s}★".rjust(4) for s in range(1, 6)))
    for i, line in enumerate(confusion):
        if any(line):
            print(f"  {i + 1}★   " + " ".join(str(v).rjust(4) for v in line))

    with open(HERE / "results.json", "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main_eval()
