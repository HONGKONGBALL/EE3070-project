"""Pinned YOLOX ONNX -> NCNN conversion, without training or changed weights.

PNNX does not support this graph's step-2 Slice. Move only the exact Focus
pixel rearrangement into preprocessing, verify it against the source graph,
and convert the unchanged convolutional suffix. Run on the export PC only.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

REPOSITORY = 'AMD-PAVS-AI/pothole-detection'
REVISION = 'cd5dd7a24988ac3cb8e58825a9ad477323787ed9'
CHECKSUM = '9d55b8cc134f034b1885bd97aded14dcb17d887b8d5b7fbb3940e820d24c7660'


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--pnnx-binary', type=Path, required=True)
    args = parser.parse_args()
    import numpy as np
    import onnx
    import onnxruntime as ort
    from cane.vision.yolox import focus_pixels

    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source = directory/'pothole_yolox_s.onnx'
    url = f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{source.name}'
    if not source.exists():
        temporary = source.with_suffix('.download')
        with urllib.request.urlopen(url, timeout=30) as response, temporary.open('wb') as output:
            while block := response.read(1048576):
                output.write(block)
        if digest(temporary) != CHECKSUM:
            raise ValueError('Downloaded YOLOX checksum differs')
        temporary.replace(source)
    if digest(source) != CHECKSUM:
        raise ValueError('Frozen YOLOX source checksum differs')
    model = onnx.load(source)
    prefix = list(model.graph.node[:31])
    boundary = '/backbone/backbone/stem/Concat_output_0'
    if (prefix[-1].op_type != 'Concat' or list(prefix[-1].output) != [boundary] or
            any(node.op_type not in {'Constant', 'Slice', 'Concat'} for node in prefix) or
            model.graph.node[31].op_type != 'Conv' or model.graph.node[31].input[0] != boundary):
        raise ValueError('Source Focus boundary differs from the reviewed graph')
    focus_graph = onnx.helper.make_graph(prefix, 'original_focus', list(model.graph.input),
        [onnx.helper.make_tensor_value_info(boundary, onnx.TensorProto.FLOAT, [1,12,320,320])])
    focus_model = onnx.helper.make_model(focus_graph, opset_imports=list(model.opset_import))
    focus_model.ir_version = model.ir_version
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    focus_session = ort.InferenceSession(focus_model.SerializeToString(), options,
                                        providers=['CPUExecutionProvider'])
    pixels = np.random.default_rng(3070).integers(0, 256, (1,3,640,640)).astype(np.float32)
    expected = focus_session.run(None, {model.graph.input[0].name:pixels})[0]
    np.testing.assert_array_equal(focus_pixels(pixels), expected)
    del model.graph.node[:31]
    model.graph.input[0].CopyFrom(onnx.helper.make_tensor_value_info(
        boundary, onnx.TensorProto.FLOAT, [1,12,320,320]))
    onnx.checker.check_model(model)
    rewritten = directory/'pothole_yolox_focused.onnx'
    onnx.save(model, rewritten)
    command = [str(args.pnnx_binary.resolve()), rewritten.name,
               'inputshape=[1,12,320,320]', 'fp16=0']
    converted = subprocess.run(command, cwd=directory, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=120)
    (directory/'conversion.log').write_text(converted.stdout+converted.stderr, encoding='utf-8')
    converted.check_returncode()
    if 'not supported' in converted.stderr.lower():
        raise RuntimeError('Unsupported NCNN conversion; inspect conversion.log')
    files = [directory/'pothole_yolox_focused.ncnn.param', directory/'pothole_yolox_focused.ncnn.bin']
    manifest = {'repository':REPOSITORY, 'revision':REVISION, 'filename':source.name,
        'sha256':CHECKSUM, 'bytes':source.stat().st_size, 'source_url':url,
        'training_performed':False, 'license':'apache-2.0 (publisher model card)',
        'fork_specific_preprocessing_verified':False,
        'preprocessing':'Upstream YOLOX: BGR float0..255, top-left114, then source-equivalent Focus',
        'export_ncnn':{'input_shape':[1,12,320,320], 'logical_image_size':640,
            'focus_matches_original_onnx_exactly':True, 'weights_changed':False,
            'focused_onnx_sha256':digest(rewritten),
            'pnnx_binary_sha256':digest(args.pnnx_binary),
            'files':[{'filename':p.name, 'bytes':p.stat().st_size, 'sha256':digest(p)} for p in files]}}
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest['export_ncnn'],indent=2))


if __name__ == '__main__':
    main()
