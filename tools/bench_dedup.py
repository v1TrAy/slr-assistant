# -*- coding: utf-8 -*-
"""
tools/bench_dedup.py
---------------------
Reproducible dedup benchmark (synthetic, seeded).

    python tools/bench_dedup.py 2000 5000 20000

For each N it builds N unique pseudo-titles (8-14 words drawn from a
Zipf-like vocabulary, so frequent words such as "of"/"the"/"learning"
create the crowded blocks real corpora have), stages them through
ImportBatch, then stages N/10 planted duplicates (case/punctuation change,
leading article, or a one-letter typo) and prints:

    * wall time to stage everything
    * recall of the planted duplicates
    * false positives (originals wrongly flagged)
"""

import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import slr_core  # noqa: E402

COMMON = ("of the in for and a on with using based analysis study approach model "
          "learning data system systems method methods review network performance "
          "evaluation framework design application applications effect effects").split()


def make_vocab(rng, size=5000):
    letters = "abcdefghijklmnopqrstuvwxyz"
    words = set()
    while len(words) < size:
        words.add("".join(rng.choice(letters) for _ in range(rng.randint(4, 11))))
    return sorted(words)


def make_title(rng, vocab):
    n = rng.randint(8, 14)
    out = []
    for _ in range(n):
        if rng.random() < 0.35:
            out.append(rng.choice(COMMON))
        else:
            # Zipf-ish: low indices are much more frequent.
            out.append(vocab[int(len(vocab) * rng.random() ** 3)])
    return " ".join(out).capitalize()


def mutate(rng, title):
    kind = rng.choice(["case", "article", "typo", "punct"])
    if kind == "case":
        return title.upper()
    if kind == "article":
        return "The " + title[0].lower() + title[1:]
    if kind == "punct":
        return title.replace(" ", ": ", 1) + "."
    i = rng.randrange(len(title))
    return title[:i] + ("x" if title[i] != "x" else "y") + title[i + 1:]


def rec(title):
    return {"title": title, "authors": "", "year": "", "journal": "", "doi": "", "database": "Bench"}


def run(n):
    rng = random.Random(42)
    vocab = make_vocab(rng)
    titles, seen = [], set()
    while len(titles) < n:
        t = make_title(rng, vocab)
        if t.lower() not in seen:
            seen.add(t.lower())
            titles.append(t)
    planted_src = rng.sample(range(n), n // 10)
    planted = [mutate(rng, titles[i]) for i in planted_src]

    batch = slr_core.ImportBatch([])
    t0 = time.perf_counter()
    batch.add_records([rec(t) for t in titles])
    t_orig = time.perf_counter() - t0
    fp = sum(1 for r in batch.records if r.is_dup == "Yes")

    t1 = time.perf_counter()
    batch.add_records([rec(t) for t in planted])
    t_dup = time.perf_counter() - t1
    found = sum(1 for r in batch.records[n:] if r.is_dup == "Yes")
    print("N={:>6}  stage originals {:7.2f}s  stage dups {:6.2f}s  total {:7.2f}s  "
          "recall {}/{} ({:.1%})  false+ {}".format(
              n, t_orig, t_dup, t_orig + t_dup, found, len(planted),
              found / max(1, len(planted)), fp))


if __name__ == "__main__":
    sizes = [int(a) for a in sys.argv[1:]] or [1000, 3000]
    for s in sizes:
        run(s)
