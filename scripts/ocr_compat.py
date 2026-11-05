"""Compatibility shim for rapidocr-openvino on OpenVINO 2025+/2026.

The package still imports `openvino.runtime`, a namespace removed in recent
OpenVINO releases.  We register a lightweight stand-in module instead of
touching site-packages, and expose the thread-count knob the package lacks.
"""
import os
import sys
import types

import openvino as _ov

if "openvino.runtime" not in sys.modules:
    _shim = types.ModuleType("openvino.runtime")
    _shim.Core = _ov.Core
    _shim.__doc__ = "compat shim provided by local-semantic-search"
    sys.modules["openvino.runtime"] = _shim


def patch_threads():
    """Honour RAPIDOCR_THREADS so parallel workers do not oversubscribe the CPU."""
    try:
        import rapidocr_openvino.utils.infer_engine as ie

        if getattr(ie.OpenVINOInferSession.__init__, "_patched", False):
            return
        original = ie.OpenVINOInferSession.__init__

        def init(self, config):
            cfg = dict(config)
            threads = int(os.environ.get("RAPIDOCR_THREADS", cfg.get("inference_num_threads", -1)))
            cfg["inference_num_threads"] = threads
            original(self, cfg)

        init._patched = True
        ie.OpenVINOInferSession.__init__ = init
    except Exception:
        pass


def prepare():
    patch_threads()


if __name__ == "__main__":
    import rapidocr_openvino  # noqa: F401
    print("rapidocr-openvino import OK")
