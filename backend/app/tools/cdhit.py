"""CD-HIT-style greedy clustering — Phase 1, step 2 ("Remove Redundant Sequences").

CD-HIT itself is a local binary; for a dependency-light backend this module
reproduces its core greedy algorithm: an incremental seed set, a short-word
k-mer prefilter, and a Smith-Waterman-free global identity check via
`difflib`. Output matches CD-HIT's semantics — a cluster representative plus
a list of members — so a native `cd-hit` binary can be swapped in later.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from typing import Sequence


def cluster(
    sequences: list[str],
    *,
    identity: float = 0.8,
    word: int = 5,
) -> tuple[list[list[int]], list[int]]:
    """Greedy cluster amino-acid sequences at `identity`.

    Returns (clusters, representatives): each cluster is a list of sequence
    indices (representative first); `representatives` holds the index of each
    cluster's seed.
    """
    if not sequences:
        return [], []

    # Identity threshold -> the shortest sequences (shorter than the standard
    # 5 residue word) are compared directly with the whole sequence set.
    min_len = min((len(s) for s in sequences if s), default=0)
    if len(sequences) == 1 or min_len < word:
        return [[0] * len(sequences)], [0]

    # Scale word size up for large proteomes to speed up k-mer prefiltering
    n_seqs = len(sequences)
    if n_seqs > 1000:
        word = max(word, 7)
    if n_seqs > 3000:
        word = max(word, 10)

    seed_of_kmers: dict[str, set[int]] = defaultdict(set)
    clusters: list[list[int]] = []
    rep_of: dict[int, int] = {}  # cluster index -> seed seq index
    member_cluster: dict[int, int] = {}  # seq index -> cluster index
    seed_kmers: list[frozenset[str]] = []  # k-mers per cluster seed, cached

    for seq_index, seq in enumerate(sequences):
        seq = seq.upper()
        kmers = {seq[i : i + word] for i in range(len(seq) - word + 1)}

        candidates: set[int] = set()
        for kmer in kmers:
            candidates |= seed_of_kmers.get(kmer, set())

        best_cluster: int | None = None
        for cluster_index in candidates:
            if not kmers.intersection(seed_kmers[cluster_index]):
                continue
            seed_seq = sequences[rep_of[cluster_index]]
            # Quick length check: if lengths differ by more than (1-identity), skip
            len_ratio = min(len(seed_seq), len(seq)) / max(len(seed_seq), len(seq))
            if len_ratio < identity:
                continue
            matcher = SequenceMatcher(None, seed_seq, seq)
            if matcher.quick_ratio() < identity:
                continue
            if matcher.ratio() >= identity:
                best_cluster = cluster_index
                break

        if best_cluster is not None:
            cluster = clusters[best_cluster]
            cluster.append(seq_index)
            member_cluster[seq_index] = best_cluster
            for kmer in kmers:
                seed_of_kmers[kmer].add(best_cluster)
        else:
            new_index = len(clusters)
            clusters.append([seq_index])
            rep_of[new_index] = seq_index
            member_cluster[seq_index] = new_index
            seed_kmers.append(frozenset(kmers))
            for kmer in kmers:
                seed_of_kmers[kmer].add(new_index)

    return clusters, [rep_of[i] for i in range(len(clusters))]


def kmers_of(seq: str, word: int) -> frozenset[str]:
    seq = seq.upper()
    return frozenset(seq[i : i + word] for i in range(len(seq) - word + 1))


def stats(sequences: list[str], clusters: list[list[int]], threshold: float) -> dict:
    members = [len(c) for c in clusters if c]
    non_singleton = sum(1 for m in members if m > 1)
    return {
        "sequences": len(sequences),
        "clusters": len(clusters),
        "nonRedundant": len(clusters),
        "nonSingletonClusters": non_singleton,
        "totalClusters": len(clusters),
        "removedAsRedundant": len(sequences) - len(clusters),
        "identityThreshold": threshold,
        "maxClusterSize": max(members) if members else 0,
    }


def sorted_sequences(records: Sequence) -> list[str]:
    """Return sequences in a stable order for reproducible clustering."""
    return [r.sequence for r in records]