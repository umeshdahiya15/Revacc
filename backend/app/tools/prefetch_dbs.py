"""Build reference BLAST databases at image build time.

Downloading and indexing DEG10, the reviewed human proteome, and the VFDB
core dataset during ``docker build`` removes runtime network dependence for
pipeline steps 2-1 (essential proteins), 3-3 (virulence factors), and 3-4
(human homology). Each database is built once into the cache directories
declared by ``MEV_BLAST_DB_CACHE`` and ``MEV_VFDB_CACHE``.

Run from the image build (WORKDIR /app):

    PYTHONPATH=/app python3 -m app.tools.prefetch_dbs
"""
from __future__ import annotations

import asyncio
import sys

from . import blastdb_local, vfdb


async def _main() -> int:
    deg_db = await blastdb_local.ensure_deg10_db()
    print(f"[prefetch] DEG10 database ready: {deg_db}")

    human_db = await blastdb_local.ensure_human_db()
    print(f"[prefetch] human proteome database ready: {human_db}")

    vfdb_fasta = await asyncio.to_thread(vfdb.ensure_vfdb_fasta)
    vfdb_db = await asyncio.to_thread(vfdb._build_db, vfdb_fasta, vfdb.VFDB_CACHE_DIR)
    print(f"[prefetch] VFDB database ready: {vfdb_db}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
