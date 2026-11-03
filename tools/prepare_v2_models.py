"""Download pinned V2 weights; --export converts YOLO on the development PC, never trains."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request

MODELS = {
    "road-distress": {
        "repository":"vinothvikas1987/pothole-detection-yolov8",
        "revision":"b001687443175e43442f63bef4691c3546629def", "size":416,
        "files":{"best.pt":"8b8a587c021bd1a497912d59e5b379f4876655c7296399ccd3ebee608b3694f1"}},
    "pothole": {
        "repository":"keremberke/yolov8n-pothole-segmentation",
        "revision":"132feb1b360d9b3b5b3eed9b96add0ad42d2a026", "size":640,
        "files":{"best.pt":"375c8a7c2603a3fffe22c546d24aeab9ae9ffeb71550363354fc414473b16eba"}},
    "segformer": {
        "repository":"Xenova/segformer-b0-finetuned-ade-512-512",
        "revision":"d3e5499fa8701ff0453ca940a8dfeae39b2f1504",
        "files":{
            "onnx/model.onnx":"3e5c18a4be395f16646438d54c42377ddc202edfa33d5eced0c9506de75c44c2",
            "config.json":"435799652b2b64c3e422dea20fed4c59651dae9f0e291fd885e9e067fee0ce2a",
            "preprocessor_config.json":"dbabd93c735c8a5c39ef207c6c4459bf2d261a5dcc55e1ba1c1b982e5947f518"}},
}


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    default=Path('D:/SmartCaneRuntime/models/v2') if os.name=='nt' else Path('models/v2')
    parser.add_argument('--output',type=Path,default=default)
    parser.add_argument('--export',action='store_true',help='Requires the separate CPU export environment')
    args=parser.parse_args()
    for name,spec in MODELS.items():
        directory=args.output/name;directory.mkdir(parents=True,exist_ok=True)
        old=directory/'manifest.json'
        manifest=json.loads(old.read_text()) if old.exists() else {}
        manifest.update(repository=spec['repository'],revision=spec['revision'],files=[])
        for filename,checksum in spec['files'].items():
            path=directory/Path(filename).name
            url=f"https://huggingface.co/{spec['repository']}/resolve/{spec['revision']}/{filename}"
            if not path.exists():
                temporary=path.with_suffix('.download')
                with urllib.request.urlopen(url,timeout=60) as response,temporary.open('wb') as output:
                    while block:=response.read(1048576): output.write(block)
                if sha(temporary)!=checksum: raise RuntimeError('Checksum mismatch: '+filename)
                temporary.replace(path)
            if sha(path)!=checksum: raise RuntimeError('Checksum mismatch: '+filename)
            manifest['files'].append({'filename':path.name,'url':url,'bytes':path.stat().st_size,'sha256':checksum})
        if args.export and name!='segformer':
            import torch,ultralytics
            model=ultralytics.YOLO(directory/'best.pt')
            output=Path(model.export(format='ncnn',imgsz=spec['size'],device='cpu'))
            files=[{'filename':p.name,'bytes':p.stat().st_size,'sha256':sha(p)}
                   for p in (output/'model.ncnn.param',output/'model.ncnn.bin')]
            manifest['export_ncnn']={'input_size':spec['size'],'classes':model.names,
                                     'task':model.task,'files':files,'ultralytics':ultralytics.__version__,
                                     'torch':torch.__version__}
        old.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(name,'verified',flush=True)
    if not args.export:
        print('YOLO NCNN files must be exported once on the development PC using --export.')


if __name__=='__main__': main()
