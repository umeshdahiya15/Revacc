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
import random
import re
from dataclasses import dataclass

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
