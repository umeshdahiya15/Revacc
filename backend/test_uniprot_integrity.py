from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app.tools import uniprot
from app.tools import runner as runner_mod


class _Response:
    def __init__(self, *, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = {"etag": "test-etag", "content-length": str(len(text))}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params or {}))
        return self.responses.pop(0)


class UniProtIntegrityTest(unittest.TestCase):
    def setUp(self):
        uniprot._cache.clear()
        uniprot._metadata_cache.clear()
        uniprot._latest_metadata_key.clear()

    def test_reference_proteome_query_excludes_taxon_wide_extra_entry(self):
        """The fixture models 2 taxon hits but only 1 reference-proteome hit."""
        metadata = {"results": [{"id": "UP000000821", "taxonomy": {"taxonId": 208435}, "proteomeType": "Reference proteome", "proteinCount": 1, "modified": "2025-12-05"}]}
        fasta = ">tr|P1|PROT_A Protein A\nMKT\n"
        client = _Client([_Response(payload=metadata), _Response(text=fasta)])
        with patch.object(uniprot.httpx, "AsyncClient", return_value=client), patch.object(uniprot, "_DISK_CACHE_DIR", tempfile.mkdtemp()):
            records = asyncio.run(uniprot.fetch_proteome(208435, reviewed_only=False, force_refresh=True))
        self.assertEqual(len(records), 1)
        self.assertEqual(client.calls[1][1]["query"], "proteome:UP000000821")
        self.assertNotIn("taxonomy_id:208435", client.calls[1][1]["query"])
        self.assertEqual(uniprot.fetch_metadata(208435, reviewed_only=False)["query"], "proteome:UP000000821")

    def test_reference_proteome_runner_timeout_allows_slow_stream(self):
        self.assertGreaterEqual(runner_mod.runner_timeout("1-1"), uniprot.REQUEST_TIMEOUT)

    def test_reviewed_only_is_separate_cache_contract(self):
        metadata = {"results": [{"id": "UP1", "taxonomy": {"taxonId": 7}, "proteomeType": "Reference proteome", "proteinCount": 1}]}
        all_fasta = ">tr|P1|PROT_A Protein A\nMKT\n"
        reviewed_fasta = ">sp|P1|PROT_A Protein A\nMKT\n"
        client = _Client([_Response(payload=metadata), _Response(text=all_fasta), _Response(payload=metadata), _Response(text=reviewed_fasta)])
        with patch.object(uniprot.httpx, "AsyncClient", return_value=client), patch.object(uniprot, "_DISK_CACHE_DIR", tempfile.mkdtemp()):
            asyncio.run(uniprot.fetch_proteome(7, reviewed_only=False, force_refresh=True))
            records = asyncio.run(uniprot.fetch_proteome(7, reviewed_only=True, force_refresh=True))
        self.assertTrue(records[0].reviewed)
        queries = [call[1]["query"] for call in client.calls if call[0].endswith("/stream")]
        self.assertEqual(queries, ["proteome:UP1", "proteome:UP1 AND reviewed:true"])

    def test_duplicate_accessions_are_removed_without_count_correction(self):
        text = ">tr|P1|PROT Protein\nMKT\n>sp|P1|PROT Protein\nMKT\n>tr|P2|OTHER Other\nMKK\n"
        records = uniprot.parse_fasta(text)
        self.assertEqual([r.uniprot_id for r in records], ["P1", "P2"])
        self.assertTrue(records[0].reviewed)


    def test_default_fetch_uses_complete_reference_stream_and_exposes_counts(self):
        metadata = {
            "results": [{
                "id": "UP-COMPLETE",
                "taxonomy": {"taxonId": "208435"},
                "proteomeType": "Reference proteome",
                "components": [{"proteinCount": 2}],
            }]
        }
        fasta = ">tr|P1|PROT_A Protein A\nMKT\n>tr|P2|PROT_B Protein B\nMKK\n"
        client = _Client([_Response(payload=metadata), _Response(text=fasta)])
        with patch.object(uniprot.httpx, "AsyncClient", return_value=client), patch.object(uniprot, "_DISK_CACHE_DIR", tempfile.mkdtemp()):
            records = asyncio.run(uniprot.fetch_proteome(208435, force_refresh=True))
        self.assertEqual(len(records), 2)
        self.assertEqual(client.calls[1][1]["query"], "proteome:UP-COMPLETE")
        provenance = uniprot.fetch_metadata(208435, reviewed_only=False)
        self.assertIsNotNone(provenance)
        self.assertEqual(provenance["cacheType"], "real")
        self.assertEqual(provenance["referenceProteomeProteinCount"], 2)
        self.assertEqual(provenance["returnedRecordCount"], 2)
        self.assertTrue(provenance["countMatchesProteomeMetadata"])

    def test_reference_count_mismatch_is_rejected(self):
        metadata = {"results": [{
            "id": "UP-MISMATCH",
            "taxonomy": {"taxonId": 208435},
            "proteomeType": "Reference proteome",
            "proteinCount": 2,
        }]}
        client = _Client([_Response(payload=metadata)] + [_Response(text=">tr|P1|PROT_A Protein A\nMKT\n") for _ in range(uniprot.MAX_RETRIES)])
        with patch.object(uniprot.httpx, "AsyncClient", return_value=client), patch.object(uniprot, "_DISK_CACHE_DIR", tempfile.mkdtemp()), patch.object(uniprot.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(uniprot.fetch_proteome(208435, force_refresh=True))
        self.assertIn("count mismatch", str(raised.exception))
        self.assertIsNone(uniprot.fetch_metadata(208435, reviewed_only=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
