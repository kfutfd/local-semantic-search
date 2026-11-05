"""Convert the BGE ONNX export to OpenVINO IR (no torch required)."""
import os

import openvino as ov

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "models", "bge-small-zh-src")

JOBS = [
    (os.path.join(SRC, "onnx", "model.onnx"), "bge-small-zh-fp16", True),
    (os.path.join(SRC, "onnx", "model_int8.onnx"), "bge-small-zh-int8", False),
]

for src, out, fp16 in JOBS:
    dst_dir = os.path.join(ROOT, "models", out)
    os.makedirs(dst_dir, exist_ok=True)
    xml = os.path.join(dst_dir, "bge.xml")
    model = ov.convert_model(src)
    ov.save_model(model, xml, compress_to_fp16=fp16)
    size = os.path.getsize(xml) + os.path.getsize(xml.replace(".xml", ".bin"))
    print(f"[ok] {out}: {size / (1 << 20):.1f} MB  inputs={[i.get_any_name() for i in model.inputs]}")
