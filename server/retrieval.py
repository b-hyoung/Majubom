"""Pure BM25/RRF ranking shared by live search and reproducible evaluation.

Cormack, Clarke & Buettcher (SIGIR 2009), doi:10.1145/1571941.1572114.
RRF scores are ranking scores, never probabilities or clinical confidence.
"""
import math
import re

from rank_bm25 import BM25Okapi


def tokenize(text):
    return re.findall(r"[\w]+", str(text).lower(), flags=re.UNICODE)


def sparse_scores(query, documents):
    corpus = [tokenize(text) for text in documents]
    if not any(corpus):
        return [0.0] * len(corpus)
    return [float(score) for score in BM25Okapi(corpus).get_scores(tokenize(query))]


def fuse_rankings(vector_ids, lexical_scores, k_rrf=60):
    """Ignore nonpositive lexical matches; equal BM25 scores receive equal rank.

    Without this abstention a no-match query acquires an arbitrary second
    ranking based solely on insertion order, corrupting dense retrieval.
    """
    if not isinstance(k_rrf, int) or k_rrf < 1:
        raise ValueError("k_rrf must be a positive integer")
    dense_rank = {cid: rank for rank, cid in enumerate(dict.fromkeys(vector_ids), 1)}
    positive = sorted(((cid, float(score)) for cid, score in lexical_scores.items()
                       if math.isfinite(float(score)) and float(score) > 0), key=lambda p: (-p[1], p[0]))
    sparse_rank = {}
    previous, rank = None, 0
    for position, (cid, score) in enumerate(positive, 1):
        if previous is None or score != previous:
            rank = position
        sparse_rank[cid], previous = rank, score
    scores = {cid: (1/(k_rrf+dense_rank[cid]) if cid in dense_rank else 0) +
                   (1/(k_rrf+sparse_rank[cid]) if cid in sparse_rank else 0)
              for cid in dense_rank.keys() | sparse_rank.keys()}
    order = sorted(scores, key=lambda cid: (-scores[cid], dense_rank.get(cid, math.inf), cid))
    return order, scores, dense_rank, sparse_rank
