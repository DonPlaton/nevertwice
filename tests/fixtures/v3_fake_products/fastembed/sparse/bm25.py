"""The fake fastembed's Bm25 (see fastembed/__init__.py): fastembed 0.8.1's names and its offline acceptance rule."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

#: fastembed 0.8.1 bm25.py:25-43, as data
supported_languages = ["arabic", "danish", "dutch", "english", "finnish", "french", "german", "greek", "hungarian",
                       "italian", "norwegian", "portuguese", "romanian", "russian", "spanish", "swedish", "tamil",
                       "turkish"]
#: bm25.py:46-57: the one description, its model_file and additional files
supported_bm25_models = [SimpleNamespace(model="Qdrant/bm25", model_file="mock.file",
                                         additional_files=[f"{lang}.txt" for lang in supported_languages])]
_OFFLINE = {"1", "TRUE", "YES", "ON"}


def define_cache_dir(cache_dir: str | None = None) -> Path:
    """common/utils.py:58-69: the argument, else FASTEMBED_CACHE_PATH, else <tempdir>/fastembed_cache."""
    return Path(cache_dir or os.environ.get("FASTEMBED_CACHE_PATH") or Path(tempfile.gettempdir()) / "fastembed_cache")


class Bm25:
    def __init__(self, model_name: str, cache_dir: str | None = None, language: str = "english", **_kw) -> None:
        if language not in supported_languages:
            raise ValueError(f"{language} language is not supported")
        desc = next((d for d in supported_bm25_models if d.model == model_name), None)
        if desc is None:
            raise ValueError(f"Model {model_name} is not supported in Bm25.")
        self.model_name, self.language = model_name, language
        self.cache_dir = str(define_cache_dir(cache_dir))
        if os.environ.get("HF_HUB_OFFLINE", "").strip().upper() not in _OFFLINE:
            raise ValueError(f"Could not download model {model_name}: the fake fastembed has no network "
                             f"(HF_HUB_OFFLINE is not set)")
        repo = Path(self.cache_dir) / ("models--" + model_name.replace("/", "--"))
        try:
            ref = (repo / "refs" / "main").read_text(encoding="utf-8")      # huggingface_hub: read(), never stripped
        except OSError:
            ref = None
        snap = repo / "snapshots" / ref if ref else None
        try:
            found = snap is not None and (snap / desc.model_file).exists() and all(
                (snap / f).exists() for f in desc.additional_files)
        except (OSError, ValueError):               # a ref with a newline is no directory name on Windows
            found = False
        if not found:
            raise ValueError(f"Could not find model in cache_dir {self.cache_dir} with local_files_only=True")
        self._model_dir = snap
        words = (snap / f"{language}.txt").read_text(encoding="utf-8").splitlines()
        self.stopwords = set(words)
