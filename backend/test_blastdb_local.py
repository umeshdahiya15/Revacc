"""Tests for blastdb_local — local BLAST database provisioning and parsing.

The DB build and search functions are exercised with a tiny synthetic FASTA
and ``makeblastdb``/``blastp`` when NCBI BLAST+ is installed.  Provisioning
downloads (efetch / UniProt) are mocked so the suite runs offline.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, ".")

from app.tools import blastdb_local  # noqa: E402

HAVE_BLAST = shutil.which("blastp") is not None and shutil.which("makeblastdb") is not None


class TestProvisioning(unittest.TestCase):
    def test_availability_reports_missing(self):
        with patch.object(blastdb_local.shutil, "which", return_value=None):
            check = blastdb_local.availability()
            self.assertFalse(check.available)
            self.assertIn("blastp", check.message)

    @patch.object(blastdb_local, "_download")
    def test_ensure_deg_fasta_writes_file(self, mock_download):
        with tempfile.TemporaryDirectory() as tmp:
            fake_fasta = ">WP_000138202.1 test\nMTENEQLFWNRVLELSR\n"
            mock_download.return_value = fake_fasta

            genes = [
                type("G", (), {"gi": 446060347})(),
                type("G", (), {"gi": 446503278})(),
                type("G", (), {"gi": 446162340})(),
            ]
            with (
                patch("app.tools.deg.fetch_essential_genes", new=AsyncMock(return_value=genes)),
                patch.object(blastdb_local, "CACHE_DIR", tmp),
            ):
                path = asyncio.run(blastdb_local.ensure_deg_fasta())
            self.assertEqual(path, str(Path(tmp) / "deg_essential.fasta"))
            self.assertIn(">WP_000138202.1", Path(path).read_text())

    @patch.object(blastdb_local, "_download")
    def test_ensure_human_fasta_writes_file(self, mock_download):
        with tempfile.TemporaryDirectory() as tmp:
            mock_download.return_value = ">sp|P0DP23|CALM1_HUMAN test\nMADQLTEEQIAEFKEAFSLFDKDGDG\n"
            with patch.object(blastdb_local, "CACHE_DIR", tmp):
                path = asyncio.run(blastdb_local.ensure_human_fasta())
            self.assertEqual(
                path,
                str(Path(tmp) / "human_reviewed.fasta"),
            )


@unittest.skipUnless(HAVE_BLAST, "NCBI BLAST+ not installed")
class TestLocalSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.seq = "MTENEQLFWNRVLELSRSQIAPAAYEFFVLEARLLKIEHQTAVITLDNIEMKKLFWEQNLGPVILTAGFE"
        fasta = cls.tmp.name + "/tiny.fasta"
        with open(fasta, "w") as out:
            out.write(f">ref1\n{cls.seq}\n")
        cls.db = cls.tmp.name + "/tiny_db"
        subprocess = __import__("subprocess")
        subprocess.run(["makeblastdb", "-in", fasta, "-dbtype", "prot", "-out", cls.db], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_blastp_sync_self_hit(self):
        queries = [("q1", self.seq)]
        with patch.object(blastdb_local, "CACHE_DIR", self.tmp.name):
            results = blastdb_local.blastp_sync(queries, "tiny_db", expect=1e-5, hitlist_size=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].query_def, "q1")
        self.assertTrue(results[0].hits)
        hit = results[0].hits[0]
        self.assertGreaterEqual(hit.identity / hit.align_length, 0.95)
        self.assertLessEqual(hit.e_value, 1e-5)

    def test_blastp_sync_no_hit_returns_empty(self):
        with patch.object(blastdb_local, "CACHE_DIR", self.tmp.name):
            results = blastdb_local.blastp_sync([("q1", "M" * 40)], "tiny_db", expect=1e-20, hitlist_size=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].hits, [])

    def test_blastp_missing_db_raises(self):
        from app.tools.blastdb_local import LocalBlastError

        with self.assertRaises(LocalBlastError):
            blastdb_local.blastp_sync([("q1", "M" * 40)], "does_not_exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)