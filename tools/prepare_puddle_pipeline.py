"""Prepare fixed research weights on Windows; no training. Only runtime artifacts go to Pi."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import urllib.request

DETECTOR_REPO='Ayush0000002/Puddle-detection-model'
DETECTOR_REV='7a8ef53d4b0fee120c513ddb9889ce8cb2a999aa'
DETECTOR_SHA='e4f0843a4d6f81d16639c402380e91f24eb3e350182f13fed5f6b56a4223a526'
VERIFIER_REPO='RuteNL/MobileCLIP2-S0-OpenCLIP-ONNX'
VERIFIER_REV='2a4361b87cd76f8493625cc31f36031ee9d52110'
PREPROCESS_REPO='timm/MobileCLIP2-S0-OpenCLIP'
PREPROCESS_REV='095906d28bf54d7584dc411e8ffe448f34289e05'
PREPROCESS_SHA='43e7162c531e845155dbf47e8fef8fe5718b0eab3d6beefb8d615a2c55b4c1e5'
VERIFIER_FILES={
    'text.onnx':'f073494cc3c1c383f5f8396572f97e7b73b5d6116657ebb71aba80b7fea0b171',
    'text.onnx.data':'52f21c52cf421b2af19eea9f942ea8fac606c7d5e937929342c1acf3f40f3da8',
    'visual.onnx':'7f8232fa8fd99cbf6ff4c9da11a24c39c3ec4fe0af2ef9db2856f84c7335876e',
    'visual.onnx.data':'0090fa77264217b1993ae9de50068f151f7efea2fc19412441605b22e12c6342',
    'tokenizer.json':'43d4281659ce30b6aca2c7485944b3d73925bb7f87c7234daf3b6b094a6d618c',
    'README.md':'c538418a9ae7adabeb168445ebdc463f97c74394aff6732a77b7299943af77eb'}
NCNN_FILES={'model.ncnn.param':'6f85d49b5af2e70c149df4d8cf9b3ede667a95b3e546c2bb66f70ed81d505c0d',
            'model.ncnn.bin':'628e17aa55ae5da3a1f96d3285b14ad3cb6b4471dbb87702d1709054f3e44aee'}


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as file:
        while block:=file.read(1048576):digest.update(block)
    return digest.hexdigest()


def download(repo,revision,name,target,expected):
    if not target.exists():
        partial=target.with_suffix(target.suffix+'.download')
        with urllib.request.urlopen(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',timeout=60) as response,partial.open('wb') as output:
            shutil.copyfileobj(response,output)
        if sha(partial)!=expected:raise RuntimeError('Download checksum mismatch: '+name)
        partial.replace(target)
    if sha(target)!=expected:raise RuntimeError('Cached checksum mismatch: '+name)


def export_detector(folder):
    import torch
    from ultralytics import YOLO
    from ultralytics.nn.tasks import DetectionModel
    torch.set_num_threads(1)
    checkpoint=folder/'best.pt'
    globals=[]
    for name in torch.serialization.get_unsafe_globals_in_checkpoint(checkpoint):
        if not name.startswith(('torch.nn.','ultralytics.nn.')):raise ValueError('Unexpected checkpoint global: '+name)
        module,attribute=name.rsplit('.',1);globals.append(getattr(importlib.import_module(module),attribute))
    with torch.serialization.safe_globals(globals):data=torch.load(checkpoint,map_location='cpu',weights_only=True)
    trained=data.get('ema') or data['model']
    if not isinstance(trained,DetectionModel) or list(trained.names.values())!=['puddle']:
        raise ValueError('Expected the actual single-class puddle checkpoint')
    model=YOLO('yolov8n.yaml');model.model=trained.float().eval();model.task='detect'
    model.overrides={'task':'detect','mode':'predict','model':str(checkpoint)}
    model.model.pt_path=str(checkpoint);model.model.task='detect'
    model.export(format='ncnn',imgsz=640,batch=1,device='cpu',half=False,verbose=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('D:/SmartCaneRuntime'))
    parser.add_argument('--output',type=Path,help='Prepared runtime directory; default models/v2/puddle-calibrated')
    parser.add_argument('--prompts',type=Path,default=Path(__file__).resolve().parents[1]/'config/puddle-ground-prompts.json')
    parser.add_argument('--export',action='store_true',help='Convert the checkpoint with the separate CPU export environment')
    args=parser.parse_args()
    if os.name!='nt':parser.error('Prepare on the Windows development PC; copy only runtime files to Pi')
    root=args.root.resolve();folder=args.output.resolve() if args.output else root/'models/v2/puddle-calibrated'
    cache=root/'models/v2/mobileclip2-research'
    folder.mkdir(parents=True,exist_ok=True);cache.mkdir(parents=True,exist_ok=True)
    os.environ['YOLO_CONFIG_DIR']=str(root/'ultralytics-settings');os.environ['PIP_CACHE_DIR']=str(root/'pip-cache')
    previous=root/'models/v2/puddle-research'
    if not (folder/'best.pt').exists() and (previous/'best.pt').is_file() and sha(previous/'best.pt')==DETECTOR_SHA:
        shutil.copy2(previous/'best.pt',folder/'best.pt')
    download(DETECTOR_REPO,DETECTOR_REV,'best.pt',folder/'best.pt',DETECTOR_SHA)
    for name,checksum in VERIFIER_FILES.items():download(VERIFIER_REPO,VERIFIER_REV,name,cache/name,checksum)
    download(PREPROCESS_REPO,PREPROCESS_REV,'open_clip_config.json',cache/'author_open_clip_config.json',PREPROCESS_SHA)
    if args.export:export_detector(folder)
    for name,checksum in NCNN_FILES.items():
        path=folder/'best_ncnn_model'/name
        cached=previous/'best_ncnn_model'/name
        if not path.exists() and cached.is_file() and sha(cached)==checksum:
            path.parent.mkdir(exist_ok=True);shutil.copy2(cached,path)
        if not path.exists():raise FileNotFoundError('Export NCNN once using --export in the CPU export environment')
        if sha(path)!=checksum:raise RuntimeError('NCNN artifact differs from the verified export: '+name)
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer
    prompts=json.loads(args.prompts.read_text(encoding='utf-8'))
    if 'indoor_floor' not in prompts:raise ValueError('Ground recipe requires indoor floor negatives')
    options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
    text=ort.InferenceSession(str(cache/'text.onnx'),options,providers=['CPUExecutionProvider'])
    tokenizer=Tokenizer.from_file(str(cache/'tokenizer.json'))
    tokenizer.enable_padding(length=77,pad_id=0,pad_token='!');tokenizer.enable_truncation(max_length=77)
    encoded=tokenizer.encode_batch([phrase for phrases in prompts.values() for phrase in phrases])
    vectors=text.run(['text_embeddings'],{'input_ids':np.asarray([e.ids for e in encoded],np.int64)})[0]
    vectors/=np.linalg.norm(vectors,axis=1,keepdims=True)
    prototypes=vectors.reshape(len(prompts),3,-1).mean(axis=1)
    prototypes/=np.linalg.norm(prototypes,axis=1,keepdims=True)
    verifier=folder/'verifier';verifier.mkdir(exist_ok=True)
    for name in ('visual.onnx','visual.onnx.data'):shutil.copy2(cache/name,verifier/name)
    shutil.copy2(cache/'README.md',verifier/'MODEL_CARD.md')
    shutil.copy2(cache/'author_open_clip_config.json',verifier/'author_open_clip_config.json')
    config=json.loads((cache/'author_open_clip_config.json').read_text())
    from cane.vision.puddle import PREPROCESS, DECISION
    expected={key:PREPROCESS[key] for key in ('mean','std','interpolation','resize_mode')}
    if config['preprocess_cfg']!=expected or config['model_cfg']['vision_cfg']['image_size']!=256:
        raise ValueError('Unexpected source preprocessing configuration')
    np.save(verifier/'puddle-text-prototypes.npy',prototypes)
    (verifier/'puddle-prompts.json').write_text(json.dumps(prompts,indent=2))
    runtime_names=('visual.onnx','visual.onnx.data','puddle-text-prototypes.npy','puddle-prompts.json','author_open_clip_config.json')
    metadata=lambda path:{'bytes':path.stat().st_size,'sha256':sha(path)}
    manifest={'repository':DETECTOR_REPO,'revision':DETECTOR_REV,'checkpoint_sha256':DETECTOR_SHA,
        'license':'Detector source provides no explicit license; research prototype, not cleared for redistribution',
        'export_ncnn':{'input_size':640,'task':'detect','classes':{'0':'puddle'},
                      'files':{name:metadata(folder/'best_ncnn_model'/name) for name in NCNN_FILES}},
        'verifier':{'repository':VERIFIER_REPO,'revision':VERIFIER_REV,'license':'apple-amlr per source model card',
                    'labels':list(prompts),'input_size':256,'preprocess':PREPROCESS,'decision':DECISION,
                    'preprocess_source':{'repository':PREPROCESS_REPO,'revision':PREPROCESS_REV,'sha256':PREPROCESS_SHA},
                    'files':{name:metadata(verifier/name) for name in runtime_names}},
        'recipe':'Ground ROI detector; one full-frame shortest-edge 256 bilinear/center crop RGB /255; puddle top1 or detector >=0.7 with wet_pavement top1; indoor-floor negatives; no training'}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'runtime':str(folder),'training_performed':False,'detector_license':'not specified'}))


if __name__=='__main__':main()
