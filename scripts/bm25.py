"""Compact BM25 index with memory-mapped postings (no sklearn at runtime)."""
import json
import math
import os
import pickle
import re
from array import array
from collections import Counter

import numpy as np

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
WORD = re.compile(r"[a-z0-9_]{2,}")

K1 = 1.2
B = 0.75


def tokenize(text):
    text = text.lower()
    toks = []
    for m in CJK_RUN.finditer(text):
        s = m.group()
        if len(s) == 1:
            toks.append(s)
        else:
            toks.extend(s[i:i + 2] for i in range(len(s) - 1))
    toks.extend(WORD.findall(text))
    return toks


def build(texts, out_dir):
    """Build term-major postings: term_ptr, post_docs, post_tfs, doc_len, vocab."""
    os.makedirs(out_dir, exist_ok=True)
    vocab = {}
    postings = {}
    doc_len = np.zeros(len(texts), dtype=np.int32)

    for i, text in enumerate(texts):
        counts = Counter(tokenize(text))
        doc_len[i] = sum(counts.values())
        for term, tf in counts.items():
            tid = vocab.get(term)
            if tid is None:
                tid = len(vocab)
                vocab[term] = tid
                postings[tid] = (array("i"), array("f"))
            docs, tfs = postings[tid]
            docs.append(i)
            tfs.append(tf)

    n_terms = len(vocab)
    term_ptr = np.zeros(n_terms + 1, dtype=np.int64)
    total = 0
    for tid in range(n_terms):
        term_ptr[tid] = total
        total += len(postings[tid][0])
    term_ptr[n_terms] = total

    post_docs = np.empty(total, dtype=np.int32)
    post_tfs = np.empty(total, dtype=np.float32)
    off = 0
    for tid in range(n_terms):
        docs, tfs = postings[tid]
        n = len(docs)
        post_docs[off:off + n] = np.frombuffer(docs, dtype=np.int32) if docs else []
        post_tfs[off:off + n] = np.frombuffer(tfs, dtype=np.float32) if tfs else []
        off += n

    np.save(os.path.join(out_dir, "term_ptr.npy"), term_ptr)
    np.save(os.path.join(out_dir, "post_docs.npy"), post_docs)
    np.save(os.path.join(out_dir, "post_tfs.npy"), post_tfs)
    np.save(os.path.join(out_dir, "doc_len.npy"), doc_len)
    with open(os.path.join(out_dir, "vocab.pkl"), "wb") as fh:
        pickle.dump(vocab, fh, protocol=pickle.HIGHEST_PROTOCOL)
    meta = {"n_terms": n_terms, "n_docs": len(texts), "postings": int(total),
            "avgdl": float(doc_len.mean()) if len(doc_len) else 0.0}
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return meta


class BM25:
    def __init__(self, out_dir, mmap=True):
        mode = "r" if mmap else None
        self.term_ptr = np.load(os.path.join(out_dir, "term_ptr.npy"), mmap_mode=mode)
        self.post_docs = np.load(os.path.join(out_dir, "post_docs.npy"), mmap_mode=mode)
        self.post_tfs = np.load(os.path.join(out_dir, "post_tfs.npy"), mmap_mode=mode)
        self.doc_len = np.load(os.path.join(out_dir, "doc_len.npy"), mmap_mode=mode)
        with open(os.path.join(out_dir, "vocab.pkl"), "rb") as fh:
            self.vocab = pickle.load(fh)
        with open(os.path.join(out_dir, "meta.json"), encoding="utf-8") as fh:
            self.meta = json.load(fh)
        self.n_docs = self.meta["n_docs"]
        self.avgdl = self.meta["avgdl"] or 1.0

    def scores(self, query):
        out = np.zeros(self.n_docs, dtype=np.float32)
        terms = set(tokenize(query))
        for term in terms:
            tid = self.vocab.get(term)
            if tid is None:
                continue
            a, b = int(self.term_ptr[tid]), int(self.term_ptr[tid + 1])
            if a >= b:
                continue
            docs = np.asarray(self.post_docs[a:b], dtype=np.int64)
            tfs = np.asarray(self.post_tfs[a:b], dtype=np.float32)
            df = b - a
            idf = math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))
            dl = np.asarray(self.doc_len[docs], dtype=np.float32)
            contrib = idf * (tfs * (K1 + 1)) / (tfs + K1 * (1 - B + B * dl / self.avgdl))
            out += np.bincount(docs, weights=contrib, minlength=self.n_docs).astype(np.float32)
        return out
