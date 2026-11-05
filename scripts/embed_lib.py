"""Minimal BGE embedder on OpenVINO (CLS pooling + L2 normalise).

NPU needs static shapes, so the model is reshaped to [batch, seq_len] and inputs
are always padded to that length.  Pass static=None to keep dynamic shapes (CPU/GPU).
"""
import os

import numpy as np
import openvino as ov
from tokenizers import Tokenizer


class BGEEmbedder:
    def __init__(self, model_dir, tokenizer_json, device="NPU", max_len=512,
                 static_batch=None, static_len=None, pad_id=0):
        core = ov.Core()
        xml = os.path.join(model_dir, "bge.xml")
        model = core.read_model(xml)
        self.static_batch = static_batch
        self.static_len = static_len or max_len
        self.pad_id = pad_id
        if static_batch and static_len:
            model.reshape({i.get_any_name(): [static_batch, static_len] for i in model.inputs})
        self.input_names = [i.get_any_name() for i in model.inputs]
        self.compiled = core.compile_model(model, device)
        self.tok = Tokenizer.from_file(tokenizer_json)
        self.tok.enable_truncation(max_length=self.static_len)

    def _feed(self, texts):
        encs = self.tok.encode_batch(texts)
        n = len(texts)
        length = self.static_len
        ids = np.full((n, length), self.pad_id, dtype=np.int64)
        mask = np.zeros_like(ids)
        for j, e in enumerate(encs):
            k = min(len(e.ids), length)
            ids[j, :k] = e.ids[:k]
            mask[j, :k] = 1
        feed = {}
        for name in self.input_names:
            if name == "input_ids":
                feed[name] = ids
            elif name == "attention_mask":
                feed[name] = mask
            elif name == "token_type_ids":
                feed[name] = np.zeros_like(ids)
        return feed

    def embed(self, texts, batch_size=8):
        out = []
        step = self.static_batch or batch_size
        for i in range(0, len(texts), step):
            batch = list(texts[i : i + step])
            if self.static_batch and len(batch) < step:
                batch += [batch[-1]] * (step - len(batch))
            res = self.compiled(self._feed(batch))
            last = np.asarray(res[self.compiled.output(0)], dtype=np.float32)
            cls = last[:, 0, :]
            cls /= np.linalg.norm(cls, axis=1, keepdims=True) + 1e-9
            out.append(cls[: len(texts[i : i + step])])
        return np.vstack(out) if out else np.zeros((0, 512), dtype=np.float32)
