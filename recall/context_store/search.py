"""Search layer — Model2Vec embeddings and hybrid retrieval."""

import struct
from pathlib import Path
from typing import Optional

_model = None
_model_load_attempted = False


def _get_model():
    """Lazy-load the Model2Vec model. Returns None if unavailable."""
    global _model, _model_load_attempted
    if _model is not None:
        return _model
    if _model_load_attempted:
        return None
    _model_load_attempted = True
    try:
        from model2vec import StaticModel
        _model = StaticModel.from_pretrained("minishlab/potion-retrieval-32M")
        return _model
    except Exception:
        return None


def embed_text(text: str) -> Optional[bytes]:
    """Embed text using Model2Vec. Returns packed float bytes or None if model unavailable."""
    model = _get_model()
    if model is None:
        return None
    embedding = model.encode(text)
    # model.encode returns a numpy array — convert to list of floats
    vec = embedding.tolist()
    if isinstance(vec[0], list):
        vec = vec[0]
    return struct.pack(f"{len(vec)}f", *vec)


def embed_texts(texts: list[str]) -> list[Optional[bytes]]:
    """Batch embed multiple texts. Returns list of packed bytes or None per text."""
    model = _get_model()
    if model is None:
        return [None] * len(texts)
    embeddings = model.encode(texts)
    results = []
    for emb in embeddings:
        vec = emb.tolist()
        results.append(struct.pack(f"{len(vec)}f", *vec))
    return results


def is_model_available() -> bool:
    """Check if the embedding model is available."""
    return _get_model() is not None
