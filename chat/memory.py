"""Conversation memory: what the assistant knows when it answers.

The strategy is the layered context used by production assistants (ChatGPT
projects, Claude Projects, most RAG agents). No single technique is enough:

1. **Session brief** (structured state, always included) -- patient context,
   every uploaded file with its detected type, and the latest analysis:
   status, triage, findings, impression. Small, exact, never retrieved.
2. **Rolling summary** (summary-buffer memory) -- when the verbatim history
   outgrows its budget, the oldest turns are folded into an LLM-written
   summary that is carried forward. Long chats keep their thread without the
   prompt growing without bound.
3. **Recent turns** (window memory) -- the last few exchanges verbatim, so
   "what about the second one?" resolves.
4. **Retrieved evidence** (RAG over the session's own material) -- document
   pages, per-image reads, findings, screening results and older turns are
   chunked and indexed. Each question pulls the top-k by **hybrid search**:
   BM25 for exact terms (drug names, lab values, "CRP") fused with dense
   embeddings for paraphrase ("inflammation markers"), combined by
   Reciprocal Rank Fusion. Every chunk carries a source ref the answer cites.

Each layer has a character budget (``config.CHAT``), so the assembled prompt
stays within the model's window however long the session gets.

Dense embeddings are optional: without ``sentence-transformers`` (or with
``MAOS_CHAT_EMBEDDINGS=off``) retrieval is BM25-only and still works.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from config import CHAT
from core.logging_utils import get_logger

_log = get_logger("chat-memory")

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the "
    "this to was were will with what which who how does do did there their".split()
)


# British -> American spelling stems. Lab reports and clinicians mix both, and
# an exact-term index treats "haemoglobin" and "hemoglobin" as unrelated --
# which once produced a confident "no haemoglobin value in the report" when
# the report said "Hemoglobin 11.8 g/dL".
_SPELLING = (
    ("haem", "hem"), ("anaem", "anem"), ("leukaem", "leukem"), ("septicaem", "septicem"),
    ("glycaem", "glycem"), ("oedem", "edem"), ("oesophag", "esophag"), ("oestr", "estr"),
    ("paed", "ped"), ("orthopaed", "orthoped"), ("haemat", "hemat"), ("faec", "fec"),
)

# Abbreviation <-> term equivalence, applied to queries and documents alike so
# "Hb", "haemoglobin" and "hemoglobin" all meet on the same tokens.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "hb": ("hemoglobin",), "hgb": ("hemoglobin",), "hemoglobin": ("hb",),
    "wbc": ("white", "leukocyte"), "leukocyte": ("wbc",), "leucocyte": ("wbc", "leukocyte"),
    "rbc": ("red", "erythrocyte"), "plt": ("platelet",), "platelet": ("plt",), "platelets": ("plt", "platelet"),
    "crp": ("c", "reactive", "protein"), "esr": ("sedimentation",),
    "na": ("sodium",), "sodium": ("na",), "k": ("potassium",), "potassium": ("k",),
    "cr": ("creatinine",), "creat": ("creatinine",), "creatinine": ("cr",),
    "bun": ("urea",), "urea": ("bun",), "alt": ("alanine",), "ast": ("aspartate",),
    "alp": ("phosphatase",), "bili": ("bilirubin",), "inr": ("prothrombin",),
    "hba1c": ("glycated", "a1c"), "tsh": ("thyroid",), "egfr": ("filtration",),
    "cxr": ("chest", "xray"), "xray": ("radiograph",), "ct": ("tomography",),
    "mri": ("magnetic", "resonance"), "uti": ("urinary", "infection"),
}


def _normalise(token: str) -> str:
    for british, american in _SPELLING:
        if token.startswith(british):
            return american + token[len(british):]
    return token


def tokenize(text: str, *, expand: bool = True) -> list[str]:
    tokens = [_normalise(t) for t in _TOKEN.findall(text.lower().replace("x-ray", "xray")) if t not in _STOP]
    if not expand:
        return tokens
    out = list(tokens)
    for token in tokens:
        out.extend(_SYNONYMS.get(token, ()))
    return out


@dataclass
class Chunk:
    id: str
    kind: str  # document | image_read | document_read | finding | report | screening | turn
    source: str  # human-readable ref, e.g. "labs.pdf#page=2"
    text: str
    run: int = 0  # analysis run that produced it (0 = not analysis-derived)


class MemoryIndex:
    """Persistent chunk store with hybrid BM25 + dense retrieval."""

    # Analysis-derived kinds are replaced wholesale by each new analysis run,
    # because a re-analysis of the session supersedes the previous read.
    ANALYSIS_KINDS = frozenset({"image_read", "document_read", "finding", "report", "screening"})

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.chunks: list[Chunk] = []
        self._embeddings: dict[str, list[float]] = {}
        self._load()

    # -------------------------------------------------------------- storage
    @property
    def _chunks_path(self) -> Path:
        return self.directory / "chunks.jsonl"

    @property
    def _embeddings_path(self) -> Path:
        return self.directory / "embeddings.json"

    def _load(self) -> None:
        if self._chunks_path.exists():
            for line in self._chunks_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        self.chunks.append(Chunk(**json.loads(line)))
                    except (TypeError, ValueError):
                        continue
        if self._embeddings_path.exists():
            try:
                self._embeddings = json.loads(self._embeddings_path.read_text(encoding="utf-8"))
            except ValueError:
                self._embeddings = {}

    def _persist(self) -> None:
        tmp = self._chunks_path.with_suffix(".tmp")
        tmp.write_text(
            "".join(json.dumps(asdict(c), ensure_ascii=False) + "\n" for c in self.chunks),
            encoding="utf-8",
        )
        tmp.replace(self._chunks_path)
        live = {c.id for c in self.chunks}
        self._embeddings = {k: v for k, v in self._embeddings.items() if k in live}
        self._embeddings_path.write_text(json.dumps(self._embeddings), encoding="utf-8")

    # ------------------------------------------------------------- mutation
    def add(self, chunks: list[Chunk]) -> int:
        """Add chunks, skipping exact duplicates by id. Returns how many were new."""
        with self._lock:
            known = {c.id for c in self.chunks}
            fresh = [c for c in chunks if c.id not in known and c.text.strip()]
            self.chunks.extend(fresh)
            if fresh:
                self._persist()
            return len(fresh)

    def replace_analysis(self, chunks: list[Chunk]) -> int:
        """Drop previous analysis-derived chunks, then add these."""
        with self._lock:
            self.chunks = [c for c in self.chunks if c.kind not in self.ANALYSIS_KINDS]
            self._persist()
        return self.add(chunks)

    # ------------------------------------------------------------ retrieval
    def search(self, query: str, k: int | None = None, *, exclude_kinds: set[str] | None = None) -> list[Chunk]:
        k = k or CHAT.retrieve_k
        with self._lock:
            pool = [c for c in self.chunks if not exclude_kinds or c.kind not in exclude_kinds]
        if not pool or not query.strip():
            return []
        rankings = [_bm25_rank(query, pool)]
        dense = self._dense_rank(query, pool)
        if dense:
            rankings.append(dense)
        fused = _rrf(rankings)
        by_id = {c.id: c for c in pool}
        return [by_id[i] for i in fused[:k]]

    def _dense_rank(self, query: str, pool: list[Chunk]) -> list[str]:
        model = _embedder()
        if model is None:
            return []
        try:
            missing = [c for c in pool if c.id not in self._embeddings]
            if missing:
                vectors = model.encode([c.text[:2000] for c in missing], normalize_embeddings=True)
                with self._lock:
                    for chunk, vector in zip(missing, vectors):
                        self._embeddings[chunk.id] = [round(float(x), 5) for x in vector]
                    self._embeddings_path.write_text(json.dumps(self._embeddings), encoding="utf-8")
            q = model.encode([query], normalize_embeddings=True)[0]
            scored = []
            for chunk in pool:
                vector = self._embeddings.get(chunk.id)
                if vector:
                    scored.append((sum(a * b for a, b in zip(q, vector)), chunk.id))
            scored.sort(reverse=True)
            return [chunk_id for score, chunk_id in scored if score > 0.15]
        except Exception as exc:  # noqa: BLE001 - dense is an enhancement
            _log.warning("dense retrieval unavailable: %s", exc)
            return []


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------
def _bm25_rank(query: str, pool: list[Chunk], k1: float = 1.5, b: float = 0.75) -> list[str]:
    docs = [tokenize(c.source + " " + c.text) for c in pool]
    if not docs:
        return []
    avgdl = sum(len(d) for d in docs) / len(docs) or 1.0
    df: Counter = Counter()
    for doc in docs:
        df.update(set(doc))
    n = len(docs)
    terms = tokenize(query)
    scored = []
    for chunk, doc in zip(pool, docs):
        tf = Counter(doc)
        score = 0.0
        for term in terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(doc) / avgdl))
        if score > 0:
            scored.append((score, chunk.id))
    scored.sort(reverse=True)
    return [chunk_id for _, chunk_id in scored]


def _rrf(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion: robust to the two scorers' different scales."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


_EMBEDDER = None
_EMBEDDER_FAILED = False
_EMBEDDER_LOCK = threading.Lock()


_EMBEDDER_THREAD: threading.Thread | None = None


def _embedder():
    """Return the dense embedder if it is ready; never block waiting for it.

    The model (~90 MB) is downloaded and loaded on a background daemon thread
    the first time it is wanted. Until it is ready -- or forever, if the
    download stalls or there is no network -- retrieval is BM25-only. An
    answer must never wait on a model download: a stalled Hub transfer once
    hung the chat indefinitely at exactly this point.
    """
    global _EMBEDDER_THREAD
    if CHAT.embeddings == "off" or _EMBEDDER_FAILED:
        return None
    if _EMBEDDER is not None:
        return _EMBEDDER
    with _EMBEDDER_LOCK:
        if _EMBEDDER_THREAD is None:
            _EMBEDDER_THREAD = threading.Thread(target=_warm_embedder, name="maos-embedder", daemon=True)
            _EMBEDDER_THREAD.start()
    return None


def warm_up() -> None:
    """Start loading the embedder early (e.g. when a session opens)."""
    _embedder()


def _warm_embedder() -> None:
    global _EMBEDDER, _EMBEDDER_FAILED
    try:
        from sentence_transformers import SentenceTransformer

        try:
            model = SentenceTransformer(CHAT.embedding_model, device="cpu", local_files_only=True)
        except Exception:  # noqa: BLE001 - not cached yet: download it
            model = SentenceTransformer(CHAT.embedding_model, device="cpu")
        _EMBEDDER = model
        _log.info("dense embeddings ready (%s)", CHAT.embedding_model)
    except Exception as exc:  # noqa: BLE001 - fall back to BM25 only
        _log.info("dense embeddings disabled (%s); using BM25 only", exc)
        _EMBEDDER_FAILED = True


# --------------------------------------------------------------------------
# Chunking helpers
# --------------------------------------------------------------------------
def split_text(text: str, size: int = 1500, overlap: int = 200) -> list[str]:
    """Split on paragraph boundaries into ~size-char pieces with overlap."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    pieces, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text):
        if len(current) + len(paragraph) + 2 <= size:
            current = f"{current}\n\n{paragraph}" if current else paragraph
            continue
        if current:
            pieces.append(current)
        while len(paragraph) > size:
            pieces.append(paragraph[:size])
            paragraph = paragraph[size - overlap:]
        current = (pieces[-1][-overlap:] + "\n\n" + paragraph) if pieces and overlap else paragraph
    if current:
        pieces.append(current)
    return pieces


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n[...truncated...]"
