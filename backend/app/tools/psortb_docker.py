"""Real PSORTb 3.0 subcellular localization via the Brinkman Lab Docker image.

No mock, no heuristic: sequences are written to a FASTA, PSORTb is executed
inside the ``brinkmanlab/psortb_commandline`` container (gram-positive mode for
S. agalactiae), and the terse output is parsed into per-protein localizations.

The Docker VM (Colima) mounts the host ``$HOME``; the work directory therefore
MUST live under ``$HOME`` so the container can read the input FASTA and write
its result back to the host. Results are cached by sequence SHA-256 so reruns
skip proteins already localized.
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

PSORTB_IMAGE = os.environ.get("PSORTB_IMAGE", "brinkmanlab/psortb_commandline:1.0.2")
# Work + cache dirs MUST be under $HOME (Colima mounts $HOME into the Docker VM).
_BASE = os.environ.get("MEV_PSORTB_DIR", os.path.expanduser("~/.mev-psortb"))
WORK_DIR = os.path.join(_BASE, "work")
CACHE_DIR = os.path.join(_BASE, "cache")

# PSORTb localization classes considered surface-exposed for vaccine targeting
# (the paper restricts to cell wall, extracellular, cell membrane, secreted).
SURFACE_LOCALIZATIONS = {
    "Cellwall",
    "CellWall",
    "Cellwall/Extracellular",
    "Cellwall/Membrane",
    "Extracellular",
    "CytoplasmicMembrane",
    "OuterMembrane",
    "Outer membrane",
    "Lipoprotein",
    "Cell-wall-anchored",
    "Wall-anchored",
}

# Per-invocation batch size (proteins per docker run) to bound memory/runtime.
BATCH_SIZE = int(os.environ.get("PSORTB_BATCH_SIZE", "250"))


class PSORTbUnavailable(RuntimeError):
    """Docker or the PSORTb image is not available."""


def _seq_key(sequence: str) -> str:
    return hashlib.sha256(sequence.strip().encode("utf-8")).hexdigest()


def _cache_file() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, "psortb_grampos.json")


def _load_cache() -> dict[str, dict]:
    try:
        with open(_cache_file(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    path = _cache_file()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", PSORTB_IMAGE],
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def require_available() -> None:
    if not docker_available():
        raise PSORTbUnavailable(
            f"Docker or the PSORTb image ({PSORTB_IMAGE}) is not available. "
            "Start the Docker daemon and `docker pull` the image."
        )


def _gram_flag(gram: str) -> str:
    return {"positive": "-p", "negative": "-n", "archaea": "-a"}.get(gram, "-p")


def _parse_terse(text: str) -> dict[str, tuple[str, float]]:
    """Parse PSORTb terse output: ``SeqID<TAB>Localization<TAB>Score``."""
    out: dict[str, tuple[str, float]] = {}
    for line in text.splitlines():
        line = line.rstrip("\n")
        if not line or line.startswith("SeqID"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            parts = line.split()
        if len(parts) < 2:
            continue
        seqid = parts[0].strip()
        localization = parts[1].strip()
        try:
            score = float(parts[2]) if len(parts) > 2 else 0.0
        except ValueError:
            score = 0.0
        out[seqid] = (localization, score)
    return out


def _run_batch_sync(seqs_by_id: dict[str, str], gram: str) -> dict[str, tuple[str, float]]:
    """Run PSORTb on one batch (id -> sequence). Returns id -> (loc, score)."""
    os.makedirs(WORK_DIR, exist_ok=True)
    stamp = f"{int(time.time()*1000)}_{os.getpid()}"
    in_name = f"in_{stamp}.fasta"
    out_name = f"out_{stamp}.txt"
    in_path = os.path.join(WORK_DIR, in_name)
    out_path = os.path.join(WORK_DIR, out_name)

    with open(in_path, "w", encoding="utf-8") as fh:
        for sid, seq in seqs_by_id.items():
            clean = "".join(seq.split()).upper()
            fh.write(f">{sid}\n{clean}\n")

    # PSORTb writes its result to /tmp/results/<stamp>_psortb_gram*.txt inside
    # the container; copy it onto the mounted volume so the host can read it.
    script = (
        f"/usr/local/psortb/bin/psort {_gram_flag(gram)} --output terse "
        f"-i /data/{in_name} >/dev/null 2>&1; "
        f"cp /tmp/results/*_psortb_*.txt /data/{out_name} 2>/dev/null; true"
    )
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{WORK_DIR}:/data",
        "--entrypoint", "/bin/bash",
        PSORTB_IMAGE, "-c", script,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    except subprocess.TimeoutExpired as exc:
        raise PSORTbUnavailable(f"PSORTb batch timed out: {exc}") from exc

    result: dict[str, tuple[str, float]] = {}
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
            result = _parse_terse(fh.read())
    # Clean up work files (keep dir).
    for p in (in_path, out_path):
        try:
            os.remove(p)
        except OSError:
            pass
    return result


def localize_sync(sequences: list[str], *, gram: str = "positive") -> list[dict]:
    """Localize each sequence (by list order). Returns a list of dicts:
    ``{"localization": str, "score": float, "cached": bool}`` aligned to input.
    Uses a persistent sequence-keyed cache; only uncached sequences hit Docker.
    """
    require_available()
    cache = _load_cache()

    # Map each unique uncached sequence to a batch id.
    uncached: dict[str, str] = {}  # seq_key -> sequence
    for seq in sequences:
        if not seq:
            continue
        k = _seq_key(seq)
        if k not in cache and k not in uncached:
            uncached[k] = seq

    if uncached:
        keys = list(uncached.keys())
        chunks = [keys[i : i + BATCH_SIZE] for i in range(0, len(keys), BATCH_SIZE)]

        def _process_chunk(chunk_keys: list[str]) -> dict[str, dict]:
            id_to_key = {f"c{j}": chunk_keys[j] for j in range(len(chunk_keys))}
            seqs_by_id = {sid: uncached[k] for sid, k in id_to_key.items()}
            parsed = _run_batch_sync(seqs_by_id, gram)
            out: dict[str, dict] = {}
            for sid, k in id_to_key.items():
                loc, score = parsed.get(sid, ("Unknown", 0.0))
                out[k] = {"localization": loc, "score": score}
            return out

        # Run PSORTb containers concurrently (bounded) to cut wall-clock time.
        from concurrent.futures import ThreadPoolExecutor

        max_workers = int(os.environ.get("PSORTB_CONCURRENCY", "3"))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for chunk_result in pool.map(_process_chunk, chunks):
                cache.update(chunk_result)
                _save_cache(cache)

    results: list[dict] = []
    for seq in sequences:
        if not seq:
            results.append({"localization": "Unknown", "score": 0.0, "cached": True})
            continue
        entry = cache.get(_seq_key(seq)) or {"localization": "Unknown", "score": 0.0}
        results.append({
            "localization": entry.get("localization", "Unknown"),
            "score": entry.get("score", 0.0),
            "cached": True,
        })
    return results


async def localize(sequences: list[str], *, gram: str = "positive") -> list[dict]:
    """Async wrapper running the blocking Docker calls in a thread."""
    return await asyncio.get_running_loop().run_in_executor(
        None, lambda: localize_sync(sequences, gram=gram)
    )


def is_surface(localization: str) -> bool:
    """Return whether a PSORTb label is a surface-accessible category."""
    value = " ".join(str(localization).replace("_", " ").split()).strip().lower()
    compact = value.replace("-", " ")
    return compact in {
        "cellwall", "cell wall", "cell wall anchored", "cellwall anchored",
        "cellwall/extracellular", "cell wall/extracellular",
        "cellwall/membrane", "cell wall/membrane", "extracellular",
        "cytoplasmicmembrane", "cytoplasmic membrane", "outermembrane",
        "outer membrane", "lipoprotein", "cell-wall-anchored", "wall-anchored",
        "wall anchored", "secreted",
    }
