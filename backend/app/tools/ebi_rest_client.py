"""EBI Job Dispatcher REST client — signal peptide & TM topology prediction.

Thin wrapper over the EBI JDispatcher REST API (the same backend that powers
the web UI).  The generic ``submit -> status poll -> result fetch`` flow is
implemented once and parameterised by tool id, so any EBI tool (Phobius,
InterProScan, …) can reuse it.  Phobius is the tool actually wired into the
Phase 2 pipeline (verified live end-to-end).

The ``result`` payload Phobius returns is an EMBOSS feature table:

    ID   EMBOSS_001
    FT   SIGNAL      1      24
    FT   TRANSMEM   45      67       POTENTIAL.
    FT   DOMAIN      1      74       NON CYTOPLASMIC.
    //

``parse_phobius_out`` turns that into structured regions and
``classify_phobius`` buckets a protein as secreted / membrane / intracellular.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

EBI_REST_BASE = "https://www.ebi.ac.uk/Tools/services/rest"
USER_AGENT = "mev-pipeline/0.1 (multi-epitope vaccine pipeline; contact@example.org)"
REQUEST_TIMEOUT = 90.0
MAX_RETRIES = 3
# Transient 5xx server errors (EBI overload spikes) get a much longer,
# jittered backoff so a short outage does not kill an in-flight step.
MAX_SERVER_RETRIES = 6
_RETRYABLE = (429, 500, 502, 503, 504)
_SERVER_RETRYABLE = (500, 502, 503, 504)
_RETRY_BACKOFF = (2.0, 5.0)
_SERVER_ERROR_BACKOFF = (5.0, 10.0, 20.0, 40.0, 60.0)
_DEFAULT_EMAIL = "mev-pipeline@example.com"

_FT_RE = re.compile(r"^\s*FT\s+(SIGNAL|TRANSMEM|DOMAIN)\s+(\d+)\s+(\d+)(?:\s+(.*))?$")

# PSORTb emits these names (with minor spelling/case differences between
# releases) in its terse output.  Keep the parser permissive, but classify
# only documented localization labels; unknown output is never a surface call.
_PSORTB_SURFACE_LABELS = {
    "cellwall",
    "cellwallextracellular",
    "cellwallmembrane",
    "cellwallanchored",
    "wallanchored",
    "extracellular",
    "secreted",
    "cytoplasmicmembrane",
    "innerchromosomalregion",
    "outermembrane",
    "lipoprotein",
}


def _psortb_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def classify_psortb(localization: str) -> str:
    """Return a stable PSORTb localization category.

    PSORTb 3/6 uses labels such as ``OuterMembrane``, ``Lipoprotein`` and
    ``CellWall``; some builds insert spaces or hyphens.  The canonical value
    is intentionally separate from the raw label retained in parsed output.
    """
    token = _psortb_token(localization)
    if token == "outermembrane":
        return "outer_membrane"
    if token == "lipoprotein":
        return "lipoprotein"
    if token in {"cellwallanchored", "wallanchored"}:
        return "cell_wall_anchored"
    if token in {"cellwall", "cellwallextracellular", "cellwallmembrane"}:
        return "cell_wall"
    if token == "extracellular":
        return "extracellular"
    if token == "secreted":
        return "secreted"
    if token == "cytoplasmicmembrane":
        return "cytoplasmic_membrane"
    if token in {"cytoplasmic", "cytosol", "cytoplasm"}:
        return "intracellular"
    return "unknown"


def is_psortb_surface(localization: str) -> bool:
    """Whether a PSORTb localization is surface-accessible for this pipeline."""
    return classify_psortb(localization) in {
        "outer_membrane",
        "lipoprotein",
        "cell_wall_anchored",
        "cell_wall",
        "extracellular",
        "secreted",
        "cytoplasmic_membrane",
    }


def parse_psortb_out(raw: str) -> dict[str, dict]:
    """Parse PSORTb terse output into ``sequence_id -> localization`` records.

    Supported forms include the tabular ``SeqID<TAB>Localization<TAB>Score``
    output and whitespace-delimited output where labels contain spaces. Header,
    warning, and unparseable lines are ignored. No localization is inferred
    from a missing row: callers receive ``Unknown`` for that sequence.
    """
    parsed: dict[str, dict] = {}
    if not raw:
        return parsed

    known_labels = (
        "Cell-wall-anchored", "Cell Wall Anchored", "Wall anchored",
        "CellWall/Extracellular", "CellWall/Membrane",
        "CytoplasmicMembrane", "Outer membrane", "OuterMembrane",
        "Lipoprotein", "Extracellular", "CellWall", "Secreted",
        "Cytoplasmic",
    )
    label_re = re.compile(
        r"(?<![A-Za-z])(" + "|".join(re.escape(label) for label in known_labels) + r")(?![A-Za-z])",
        re.IGNORECASE,
    )
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.lower().startswith(("seqid", "sequence")):
            continue
        fields = line.split("\t")
        if len(fields) == 1:
            whitespace_fields = re.split(r"\s+", line)
            if len(whitespace_fields) < 2:
                continue
            seq_id = whitespace_fields[0].strip()
            remainder = " ".join(whitespace_fields[1:])
        else:
            seq_id = fields[0].strip() if fields else ""
            remainder = " ".join(field.strip() for field in fields[1:] if field.strip())
        if not seq_id:
            continue
        match = label_re.search(remainder)
        if match:
            localization = match.group(1)
        else:
            tokens = re.split(r"\s+", remainder)
            if not tokens:
                continue
            localization = tokens[0]
        score = 0.0
        for token in reversed(re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", remainder)):
            try:
                score = float(token)
                break
            except ValueError:
                continue
        parsed[seq_id] = {
            "localization": localization,
            "category": classify_psortb(localization),
            "score": score,
        }
    return parsed


# Descriptive alias used by callers that refer to the command-line output.
parse_psortb_output = parse_psortb_out


class PSORTbClientError(RuntimeError):
    """Raised when a configured PSORTb executable cannot produce results."""


def find_psortb_binary(configured: str | None = None) -> str | None:
    """Find a local PSORTb executable without guessing scientific results.

    ``PSORTB_BIN`` (or an explicit argument) takes precedence, followed by
    conventional executable names. A configured path must exist and be
    executable; a missing configuration is treated as normal unavailability.
    """
    configured = configured or os.environ.get("PSORTB_BIN") or os.environ.get("PSORTB_PATH")
    candidates = [configured] if configured else ["psortb", "psortb.pl", "psort"]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


class PSORTbClient:
    """Run a locally installed PSORTb command-line binary on FASTA input."""

    def __init__(self, binary: str | None = None, *, timeout: float = 7200.0) -> None:
        self.binary = binary or find_psortb_binary()
        self.timeout = timeout
        if not self.binary:
            raise PSORTbClientError("No PSORTb executable was found on PATH or in PSORTB_BIN.")

    def localize_sync(self, sequences: list[str], *, gram: str = "positive") -> list[dict]:
        if not self.binary:
            raise PSORTbClientError("No PSORTb executable is configured.")
        gram_flag = {"positive": "-p", "negative": "-n", "archaea": "-a"}.get(gram, "-p")
        with tempfile.TemporaryDirectory(prefix="mev-psortb-") as work_dir:
            fasta = Path(work_dir) / "input.fasta"
            with fasta.open("w", encoding="utf-8") as handle:
                for index, sequence in enumerate(sequences):
                    handle.write(f">seq_{index}\n{''.join(sequence.split()).upper()}\n")
            command = [self.binary, "-i", str(fasta), gram_flag, "--output", "terse"]
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=True, timeout=self.timeout, check=False
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise PSORTbClientError(f"PSORTb execution failed: {exc}") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "unknown error").strip()
                raise PSORTbClientError(f"PSORTb exited with code {completed.returncode}: {detail[:300]}")
            parsed = parse_psortb_out(completed.stdout)
        return [
            {
                "localization": (parsed.get(f"seq_{index}") or {}).get("localization", "Unknown"),
                "category": (parsed.get(f"seq_{index}") or {}).get("category", "unknown"),
                "score": (parsed.get(f"seq_{index}") or {}).get("score", 0.0),
                "cached": False,
            }
            for index, _sequence in enumerate(sequences)
        ]

    async def localize(self, sequences: list[str], *, gram: str = "positive") -> list[dict]:
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: self.localize_sync(sequences, gram=gram)
        )


# Alternate capitalization kept for integrations that spell the tool in all caps.
PSORTBClient = PSORTbClient


class EBIRestClientError(RuntimeError):
    """Raised when an EBI job cannot be submitted or its result fetched."""


@dataclass
class EBIResult:
    job_id: str
    tool: str
    status: str
    raw_text: str = ""


def parse_phobius_out(raw: str) -> dict:
    """Parse an EMBOSS feature-table into signal / TM / domain regions."""
    parsed: dict = {
        "signal_peptide": None,
        "transmembrane_regions": [],
        "domains": [],
    }
    if not raw:
        return parsed
    for line in raw.splitlines():
        match = _FT_RE.match(line)
        if not match:
            continue
        key, start, end, description = match.group(1), int(match.group(2)), int(match.group(3)), (match.group(4) or "").strip()
        if key == "SIGNAL":
            parsed["signal_peptide"] = {"start": start, "end": end}
        elif key == "TRANSMEM":
            parsed["transmembrane_regions"].append({"start": start, "end": end, "description": description})
        elif key == "DOMAIN":
            parsed["domains"].append({"start": start, "end": end, "description": description})
    return parsed


def classify_phobius(parsed: dict) -> str:
    """Bucket a protein by its Phobius features.

    - ``secreted``: signal peptide, no TM helices
    - ``membrane``: one or more TM helices (signal optional)
    - ``intracellular``: neither signal peptide nor TM helix
    """
    if parsed.get("transmembrane_regions"):
        return "membrane"
    if parsed.get("signal_peptide"):
        return "secreted"
    return "intracellular"


class EBIRestClient:
    """Submit a sequence to an EBI tool and fetch its finished result.

    Usage::

        client = EBIRestClient(email="me@example.com")
        result = await client.run("phobius", "MKTAYIAKQRQISFVKSHFSRQ...")
        await client.close()
    """

    def __init__(
        self,
        *,
        email: str = _DEFAULT_EMAIL,
        poll_interval: float = 5.0,
        poll_timeout: float = 600.0,
    ) -> None:
        self.email = email
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    @staticmethod
    def _text(response: httpx.Response) -> str:
        """Read ``response.text``, tolerating test doubles that expose it as a callable."""
        text = getattr(response, "text", "")
        return text() if callable(text) else (text or "")

    @staticmethod
    def _jittered(seconds: float) -> float:
        """Add +/-25% jitter so N retrying workers don't sync up."""
        return seconds * random.uniform(0.75, 1.25)

    async def _post(self, url: str, data: dict[str, str]) -> httpx.Response:
        client = await self._http()
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + MAX_SERVER_RETRIES):
            try:
                response = await client.post(url, data=data)
                status_code = getattr(response, "status_code", None)
                if isinstance(status_code, int) and status_code in _RETRYABLE:
                    if status_code in _SERVER_RETRYABLE and attempt < MAX_RETRIES + MAX_SERVER_RETRIES - 1:
                        backoff = self._jittered(_SERVER_ERROR_BACKOFF[min(attempt, len(_SERVER_ERROR_BACKOFF) - 1)])
                    elif attempt < MAX_RETRIES + MAX_SERVER_RETRIES - 1:
                        backoff = self._jittered(_RETRY_BACKOFF[0])
                    else:
                        backoff = 0.0
                    if backoff:
                        await asyncio.sleep(backoff)
                    continue
                response.raise_for_status()
                return response
            except Exception as exc:  # noqa: BLE001 - surface any transport failure
                last_error = exc
                if attempt < MAX_RETRIES + MAX_SERVER_RETRIES - 1:
                    await asyncio.sleep(self._jittered(_RETRY_BACKOFF[attempt % len(_RETRY_BACKOFF)]))
        raise EBIRestClientError(f"EBI JDispatcher unreachable: {last_error}")

    async def _get(self, url: str) -> str:
        client = await self._http()
        last_error: Exception | None = None
        attempts = MAX_RETRIES + MAX_SERVER_RETRIES
        for attempt in range(attempts):
            try:
                response = await client.get(url)
                status_code = getattr(response, "status_code", None)
                if isinstance(status_code, int) and status_code in _RETRYABLE:
                    if attempt >= attempts - 1:
                        response.raise_for_status()
                    backoff = (
                        self._jittered(_SERVER_ERROR_BACKOFF[min(attempt, len(_SERVER_ERROR_BACKOFF) - 1)])
                        if status_code in _SERVER_RETRYABLE
                        else self._jittered(_RETRY_BACKOFF[0])
                    )
                    await asyncio.sleep(backoff)
                    continue
                response.raise_for_status()
                return self._text(response)
            except Exception as exc:  # noqa: BLE001 - surface any transport failure
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(
                        self._jittered(_RETRY_BACKOFF[attempt % len(_RETRY_BACKOFF)])
                    )
        raise EBIRestClientError(f"EBI JDispatcher result fetch failed: {last_error}")

    async def submit(self, tool: str, sequence: str) -> str:
        """Submit a sequence and return the job identifier."""
        response = await self._post(
            f"{EBI_REST_BASE}/{tool}/run/",
            {"email": self.email, "tool": tool, "sequence": sequence},
        )
        job_id = self._text(response).strip()
        if not job_id:
            raise EBIRestClientError("EBI JDispatcher returned an empty job identifier.")
        return job_id

    async def poll_status(self, tool: str, job_id: str) -> str:
        started = asyncio.get_running_loop().time()
        status = "RUNNING"
        while status not in ("FINISHED", "ERROR", "NOT_FOUND"):
            await asyncio.sleep(self.poll_interval)
            status = (await self._get(f"{EBI_REST_BASE}/{tool}/status/{job_id}")).strip()
            if asyncio.get_running_loop().time() - started > self.poll_timeout:
                status = "TIMEOUT"
                break
        return status

    async def fetch_result(self, tool: str, job_id: str, result_type: str = "out") -> str:
        return await self._get(f"{EBI_REST_BASE}/{tool}/result/{job_id}/{result_type}")

    async def run(self, tool: str, sequence: str, result_type: str = "out") -> EBIResult:
        """Submit, poll to completion and fetch the chosen result type."""
        job_id = await self.submit(tool, sequence)
        status = await self.poll_status(tool, job_id)
        raw_text = ""
        if status == "FINISHED":
            try:
                raw_text = await self.fetch_result(tool, job_id, result_type)
            except EBIRestClientError:
                raw_text = ""  # keep the FINISHED record; caller decides on empty text
        return EBIResult(job_id=job_id, tool=tool, status=status, raw_text=raw_text)

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def parse_phobius_out(raw: str) -> dict:
        """Parse an EMBOSS feature-table into signal / TM / domain regions."""
        return parse_phobius_out(raw)

    @staticmethod
    def classify_phobius(parsed: dict) -> str:
        """Bucket a protein by its Phobius features."""
        return classify_phobius(parsed)
