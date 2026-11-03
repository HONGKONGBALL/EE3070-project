"""Prepare the selected stair context from the existing frozen MobileCLIP2 cache.

Run on the development computer, not on the Pi. Requires the existing ONNX
text encoder and tokenizers==0.22.1. This generates text prototypes, not trained
neural weights. Only the visual encoder and small prototypes go to the Pi.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"pi"))
from cane.vision.ground import session
from cane.vision.stair_context import LABELS, CROP, DECISION
from prepare_puddle_pipeline import VERIFIER_FILES, PREPROCESS_SHA


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, default=Path(__file__).resolve().parents[1]/"config/stair-context-prompts.json")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Choose a new output directory to preserve an existing model recipe")
    from tokenizers import Tokenizer
    for name in ("text.onnx", "text.onnx.data", "visual.onnx", "visual.onnx.data", "tokenizer.json"):
        if sha(args.source/name) != VERIFIER_FILES[name]:
            raise ValueError("Unexpected source encoder/tokenizer hash: "+name)
    if sha(args.source/"author_open_clip_config.json") != PREPROCESS_SHA:
        raise ValueError("Unexpected source preprocessing configuration hash")
    prompts = json.loads(args.prompts.read_text(encoding="utf-8"))
    if tuple(prompts) != LABELS:
        raise ValueError("Unexpected stair prototype classes/order")
    author = json.loads((args.source/"author_open_clip_config.json").read_text())
    if author["preprocess_cfg"] != {"mean":[0.,0.,0.],"std":[1.,1.,1.],
                                    "interpolation":"bilinear","resize_mode":"shortest"}:
        raise ValueError("Unexpected encoder preprocessing")
    if author["model_cfg"]["vision_cfg"]["image_size"] != 256:
        raise ValueError("Expected 256px MobileCLIP2-S0")
    tokenizer = Tokenizer.from_file(str(args.source/"tokenizer.json"))
    tokenizer.enable_padding(length=77, pad_id=0, pad_token="!")
    tokenizer.enable_truncation(max_length=77)
    encodings = tokenizer.encode_batch([p for phrases in prompts.values() for p in phrases])
    text = session(args.source/"text.onnx")
    vectors = text.run(["text_embeddings"], {"input_ids": np.asarray([e.ids for e in encodings], np.int64)})[0]
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    prototypes, start = [], 0
    for phrases in prompts.values():
        vector = vectors[start:start+len(phrases)].mean(axis=0)
        prototypes.append(vector/np.linalg.norm(vector))
        start += len(phrases)
    args.output.mkdir(parents=True)
    for name in ("visual.onnx", "visual.onnx.data", "author_open_clip_config.json"):
        shutil.copy2(args.source/name, args.output/name)
    np.save(args.output/"stair-context-prototypes.npy", np.stack(prototypes))
    (args.output/"stair-context-prompts.json").write_text(json.dumps(prompts, indent=2), encoding="utf-8")
    manifest = {"repository":"RuteNL/MobileCLIP2-S0-OpenCLIP-ONNX",
                "revision":"2a4361b87cd76f8493625cc31f36031ee9d52110",
                "license":"apple-amlr per source model; retain upstream terms",
                "labels":list(LABELS), "crop":list(CROP), "decision":DECISION,
                "input_size":256, "training_performed":False,
                "sha256":{p.name:sha(p) for p in args.output.iterdir() if p.is_file()},
                "text_source_sha256":{n:sha(args.source/n) for n in ("text.onnx","text.onnx.data","tokenizer.json")}}
    (args.output/"manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"runtime":str(args.output),"training_performed":False}))


if __name__ == "__main__":
    main()
