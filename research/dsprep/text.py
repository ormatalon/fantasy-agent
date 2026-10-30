"""Free-text features.

Methods (per column; pass a list to combine, e.g. method=["stats", "tfidf"]):
  stats      length, word count, digit / uppercase / punctuation share, missing flag
  tfidf      TF-IDF (word or char n-grams) reduced with TruncatedSVD to ``svd_components`` dense columns
             (default 50). Raw TF-IDF (svd_components=None) is wide: as dense columns it is refused above
             ``max_dense_cells`` (rows x vocabulary, default 20M) - use ``sparse_tfidf()`` instead, which
             returns a scipy sparse matrix for linear models in the modeling step.
  keywords   0/1 flag per keyword or regex (keywords={"urgent": r"\\burgent\\b", ...})
  embedding  sentence embeddings (needs sentence-transformers; model_name param), optional PCA
  native     cleaned text kept as one string column for CatBoost ``text_features``

Per-column params:
  lowercase (True), strip_html (True), max_features (5000), ngram_range ((1, 2)),
  analyzer ("word" | "char_wb"), min_df (2), svd_components (50; None = raw TF-IDF),
  max_dense_cells (20_000_000),
  keywords (dict or list), model_name ("all-MiniLM-L6-v2"), embedding_pca (None or int),
  batch_size (64), language-agnostic by default (no stop words); pass stop_words="english" if wanted.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

from .base import BaseColumnProcessor

_HTML = re.compile(r"<[^>]+>")
_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)


def clean_text(s: pd.Series, lowercase=True, strip_html=True) -> pd.Series:
    x = s.astype("object").where(s.notna(), "").map(str)
    if strip_html:
        x = x.map(lambda t: _HTML.sub(" ", t))
    if lowercase:
        x = x.str.lower()
    return x.map(lambda t: " ".join(t.split()))


class TextProcessor(BaseColumnProcessor):
    default_method = "stats"
    valid_methods = ("stats", "tfidf", "keywords", "embedding", "native")

    def __init__(self, spec=None, keep_original=False, method="stats", lowercase=True, strip_html=True,
                 max_features=5000, ngram_range=(1, 2), analyzer="word", min_df=2, stop_words=None,
                 svd_components=50, max_dense_cells=20_000_000, keywords=None,
                 model_name="all-MiniLM-L6-v2", embedding_pca=None, batch_size=64, random_state=42):
        super().__init__(spec=spec, keep_original=keep_original)
        self.method = method
        self.lowercase = lowercase
        self.strip_html = strip_html
        self.max_features = max_features
        self.ngram_range = ngram_range
        self.analyzer = analyzer
        self.min_df = min_df
        self.stop_words = stop_words
        self.svd_components = svd_components
        self.max_dense_cells = max_dense_cells
        self.keywords = keywords
        self.model_name = model_name
        self.embedding_pca = embedding_pca
        self.batch_size = batch_size
        self.random_state = random_state

    def get_text_features(self) -> list[str]:
        """Output columns holding raw (cleaned) text - pass these to CatBoost ``text_features``."""
        self._check_fitted()
        return [col for col, st in self.state_.items() if "native" in st["methods"]]

    def sparse_tfidf(self, X: pd.DataFrame, col: str):
        """Raw TF-IDF of ``col`` as a scipy sparse matrix (rows of X, fitted vocabulary).
        For linear models on text; combine with the dense features using scipy.sparse.hstack."""
        self._check_fitted()
        st = self.state_[col]
        if "tfidf" not in st:
            raise ValueError(f"'{col}' was not fitted with method 'tfidf'")
        p = st["params"]
        return st["tfidf"].transform(clean_text(X[col], p.get("lowercase", True), p.get("strip_html", True)))

    def _fit_column(self, s, y, p):
        col = s.name
        methods = p["method"] if isinstance(p["method"], (list, tuple)) else [p["method"]]
        st: dict = {"methods": methods}
        x = clean_text(s, p.get("lowercase", True), p.get("strip_html", True))
        if "tfidf" in methods:
            vec = TfidfVectorizer(max_features=p.get("max_features", 5000),
                                  ngram_range=tuple(p.get("ngram_range", (1, 2))),
                                  analyzer=p.get("analyzer", "word"), min_df=p.get("min_df", 2),
                                  stop_words=p.get("stop_words"), sublinear_tf=True)
            M = vec.fit_transform(x)
            st["tfidf"] = vec
            k = p.get("svd_components")
            if k:
                k = min(int(k), M.shape[1] - 1) if M.shape[1] > 1 else 1
                svd = TruncatedSVD(n_components=k, random_state=p.get("random_state", 42)).fit(M)
                st["svd"] = svd
                self._log(col, "tfidf+svd", f"vocab {M.shape[1]} -> {k} components, "
                                            f"explained var {svd.explained_variance_ratio_.sum():.1%}")
            else:
                cells = M.shape[0] * M.shape[1]
                if cells > p.get("max_dense_cells", 20_000_000):
                    raise MemoryError(
                        f"'{col}': raw TF-IDF would be {M.shape[0]:,} x {M.shape[1]:,} dense cells. Set "
                        f"svd_components (e.g. 50), lower max_features, or keep the text out of the frame and "
                        f"use TextProcessor.sparse_tfidf() in the modeling step.")
                self._log(col, "tfidf", f"raw vocabulary: {M.shape[1]} dense columns")
        if "keywords" in methods:
            kw = p.get("keywords") or {}
            if isinstance(kw, (list, tuple)):
                kw = {re.sub(r"\W+", "_", k): rf"\b{re.escape(k.lower())}\b" for k in kw}
            st["keywords"] = {k: re.compile(v, flags=re.IGNORECASE) for k, v in kw.items()}
            self._log(col, "keywords", f"{len(kw)} flags")
        if "embedding" in methods:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise ImportError("method='embedding' needs `pip install sentence-transformers`") from e
            model = SentenceTransformer(p.get("model_name", "all-MiniLM-L6-v2"))
            st["embedder"] = model
            if p.get("embedding_pca"):
                E = model.encode(x.tolist(), batch_size=p.get("batch_size", 64), show_progress_bar=False)
                st["pca"] = PCA(n_components=int(p["embedding_pca"]), random_state=p.get("random_state", 42)).fit(E)
            self._log(col, "embedding", p.get("model_name", "all-MiniLM-L6-v2"))
        if "stats" in methods:
            self._log(col, "stats", "len, words, digit/upper/punct share, missing")
        if "native" in methods:
            self._log(col, "native", "cleaned text kept for CatBoost text_features")
        return st

    def _transform_column(self, s, st):
        p = st["params"]
        col = s.name
        x = clean_text(s, p.get("lowercase", True), p.get("strip_html", True))
        raw = s.astype("object").where(s.notna(), "").map(str)
        parts = []
        m = st["methods"]
        if "native" in m:
            parts.append(pd.DataFrame({col: x}, index=s.index))
        if "stats" in m:
            n_chars = raw.str.len()
            denom = n_chars.replace(0, np.nan)
            parts.append(pd.DataFrame({
                self._name(col, "missing"): s.isna().astype("int8"),
                self._name(col, "n_chars"): n_chars.astype(float),
                self._name(col, "n_words"): raw.str.split().str.len().fillna(0).astype(float),
                self._name(col, "digit_share"): (raw.str.count(r"\d") / denom).fillna(0),
                self._name(col, "upper_share"): (raw.str.count(r"[A-Z]") / denom).fillna(0),
                self._name(col, "punct_share"): (raw.map(lambda t: len(_PUNCT.findall(t))) / denom).fillna(0),
            }, index=s.index))
        if "tfidf" in m:
            M = st["tfidf"].transform(x)
            if "svd" in st:
                Z = st["svd"].transform(M)
                parts.append(pd.DataFrame(Z, index=s.index,
                                          columns=[self._name(col, f"svd{i}") for i in range(Z.shape[1])]))
            else:
                vocab = st["tfidf"].get_feature_names_out()
                parts.append(pd.DataFrame(M.toarray(), index=s.index,
                                          columns=[self._name(col, f"tfidf_{w}") for w in vocab]))
        if "keywords" in m:
            parts.append(pd.DataFrame({self._name(col, f"kw_{k}"): x.map(lambda t, r=r: int(bool(r.search(t))))
                                       for k, r in st["keywords"].items()}, index=s.index))
        if "embedding" in m:
            E = st["embedder"].encode(x.tolist(), batch_size=p.get("batch_size", 64), show_progress_bar=False)
            if "pca" in st:
                E = st["pca"].transform(E)
            parts.append(pd.DataFrame(E, index=s.index, columns=[self._name(col, f"emb{i}") for i in range(E.shape[1])]))
        return pd.concat(parts, axis=1)
