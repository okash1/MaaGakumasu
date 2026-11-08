"""reco_test - run a pipeline node's recognition (or an ad-hoc TemplateMatch/OCR) against a saved screenshot.

Useful to check why a node did or did not match, using the exact MaaFramework build of the runtime.
Needs a connected ADB device (the controller is only used to satisfy Tasker binding; nothing is clicked).

    <runtime>\\python\\python.exe tools\\reco_test.py --runtime <runtime> --image shot.png --node ProduceRecognitionWorkOptions
    <runtime>\\python\\python.exe tools\\reco_test.py --runtime <runtime> --image shot.png --template produce/choose_Vo.png,produce/choose_Da.png --roi 0,640,720,320 --green-mask
    <runtime>\\python\\python.exe tools\\reco_test.py --runtime <runtime> --image shot.png --ocr --roi 0,600,720,400

Prints every candidate box with its score (TemplateMatch) or text (OCR) and whether the node would hit.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import zlib
from pathlib import Path

import numpy as np


# --------------------------------------------------------------------------- minimal PNG reader (no PIL/cv2 in the runtime)
def read_png_bgr(path: str) -> np.ndarray:
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, w = 8, [], None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and interlace == 0, f"unsupported PNG (depth={depth}, interlace={interlace})"
            channels = {2: 3, 6: 4, 0: 1, 4: 2}[color]
        elif ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat))
    stride = w * channels
    out = np.zeros((h, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.int32)
    off = 0
    for y in range(h):
        f = raw[off]
        line = np.frombuffer(raw[off + 1 : off + 1 + stride], dtype=np.uint8).astype(np.int32)
        off += 1 + stride
        if f == 0:
            cur = line
        elif f == 2:
            cur = (line + prev) & 0xFF
        else:
            cur = line.copy()
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                if f == 1:
                    pred = a
                elif f == 3:
                    pred = (a + b) >> 1
                else:  # 4 paeth
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[i] = (cur[i] + pred) & 0xFF
        out[y] = cur.astype(np.uint8)
        prev = cur
    img = out.reshape(h, w, channels)
    if channels == 1:
        img = np.repeat(img, 3, axis=2)
    elif channels == 2:
        img = np.repeat(img[:, :, :1], 3, axis=2)
    elif channels == 4:
        img = img[:, :, :3]
    return np.ascontiguousarray(img[:, :, ::-1])  # RGB -> BGR (OpenCV order used by MaaFramework)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runtime", default=os.environ.get("MAA_RUNTIME", r"Z:\gakumasu-bot\MaaGakumasu"))
    ap.add_argument("--image", required=True, help="screenshot png (720x1280 for this project)")
    ap.add_argument("--resource", default="官服")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--node", help="pipeline node name whose recognition to run")
    g.add_argument("--template", help="comma separated template paths relative to resource image dir")
    g.add_argument("--ocr", action="store_true", help="ad-hoc OCR")
    ap.add_argument("--roi", help="x,y,w,h")
    ap.add_argument("--threshold", type=float, help="override threshold")
    ap.add_argument("--green-mask", action="store_true")
    ap.add_argument("--expected", help="OCR expected (regex), comma separated")
    args = ap.parse_args()

    runtime = Path(args.runtime).resolve()
    os.chdir(runtime)
    import maa.pipeline as mp
    from maa.controller import AdbController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit

    Toolkit.init_option(str(runtime))
    iface = json.load(open(runtime / "interface.json", encoding="utf-8"))
    res_def = next(r for r in iface["resource"] if r["name"] == args.resource)

    devs = Toolkit.find_adb_devices()
    if not devs:
        print("no ADB device (needed only to bind the tasker)")
        return 2
    d = devs[0]
    ctrl = AdbController(d.adb_path, d.address, d.screencap_methods, d.input_methods, d.config)
    if not ctrl.post_connection().wait().succeeded:
        print("controller connection failed")
        return 2
    res = Resource()
    for rel in res_def["path"]:
        if not res.post_bundle((runtime / rel).resolve()).wait().succeeded:
            print("resource load failed", rel)
            return 2
    tasker = Tasker()
    if not tasker.bind(res, ctrl) or not tasker.inited:
        print("tasker init failed")
        return 2

    if args.node:
        node = res.get_node_data(args.node)
        if not node:
            print(f"node {args.node!r} not found")
            return 2
        reco = node.get("recognition", {})
        if isinstance(reco, str):
            reco = {"type": reco, "param": {}}
        param = dict(reco.get("param", {}))
        rtype = reco.get("type", "DirectHit")
    elif args.template:
        rtype = "TemplateMatch"
        param = {"template": args.template.split(","), "green_mask": args.green_mask}
    else:
        rtype = "OCR"
        param = {}
        if args.expected:
            param["expected"] = args.expected.split(",")
    if args.roi:
        param["roi"] = [int(v) for v in args.roi.split(",")]
    if args.threshold is not None:
        param["threshold"] = args.threshold
    print(f"recognition: {rtype} {json.dumps(param, ensure_ascii=False)}")

    jcls = {"TemplateMatch": mp.JTemplateMatch, "OCR": mp.JOCR, "ColorMatch": mp.JColorMatch, "FeatureMatch": mp.JFeatureMatch, "DirectHit": mp.JDirectHit}.get(rtype)
    if jcls is None:
        print(f"recognition type {rtype} not supported by this tool")
        return 2
    fields = set(getattr(jcls, "__dataclass_fields__", {}).keys())
    jparam = jcls(**{k: v for k, v in param.items() if k in fields})
    dropped = [k for k in param if k not in fields]
    if dropped:
        print("note: ignored params", dropped)

    image = read_png_bgr(args.image)
    print(f"image: {image.shape[1]}x{image.shape[0]}")
    job = tasker.post_recognition(getattr(mp.JRecognitionType, rtype), jparam, image).wait()
    detail = tasker.get_task_detail(job.job_id)
    rd = None
    if detail and detail.nodes:
        rd = detail.nodes[0].recognition
    if rd is None:
        print("no recognition detail")
        return 1
    print(f"hit={rd.hit} best={_fmt(rd.best_result)}")
    print("candidates:")
    for r in sorted(rd.all_results, key=lambda r: -float(getattr(r, "score", 0) or 0)):
        print("  ", _fmt(r))
    return 0 if rd.hit else 1


def _fmt(r) -> str:
    if r is None:
        return "None"
    parts = [f"box={list(r.box) if getattr(r, 'box', None) is not None else None}"]
    for k in ("score", "text", "label", "count"):
        v = getattr(r, k, None)
        if v is not None:
            parts.append(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}")
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
