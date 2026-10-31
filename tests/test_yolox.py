"""Numerical YOLOX contracts; synthetic tensors do not measure model accuracy."""

import numpy as np
import pytest

pytest.importorskip('cv2')
pytest.importorskip('onnxruntime')
pytest.importorskip('ncnn')

from cane.vision.yolox import decode, preprocess, focus_pixels, YoloXPotholeDetector


def test_focus_preserves_the_original_onnx_pixel_order():
    pixels = np.arange(3 * 4 * 4,dtype=np.float32).reshape(1,3,4,4)
    focused = focus_pixels(pixels)
    assert focused.shape == (1,12,2,2) and focused.flags.c_contiguous
    for group,(y,x) in enumerate(((0,0),(1,0),(0,1),(1,1))):
        np.testing.assert_array_equal(focused[:,group*3:(group+1)*3],pixels[:,:,y::2,x::2])


def test_upstream_preprocessing_preserves_bgr_pixel_scale_and_top_left_padding():
    image = np.full((240, 640, 3), [5, 40, 210], np.uint8)
    tensor, ratio = preprocess(image)
    assert ratio == 1 and tensor.shape == (1, 3, 640, 640)
    assert tensor.dtype == np.float32 and tensor.flags.c_contiguous
    np.testing.assert_array_equal(tensor[0, :, 20, 20], [5, 40, 210])
    np.testing.assert_array_equal(tensor[0, :, 239, 639], [5, 40, 210])
    np.testing.assert_array_equal(tensor[0, :, 240, 0], [114, 114, 114])


def test_grid_stride_decode_product_score_and_nms_without_mutating_output():
    raw = np.zeros((1, 8400, 6), np.float32)
    # First stride16 cell at x=10,y=12 -> center(168,208), width64 height32.
    i = 6400 + 12 * 40 + 10
    raw[0, i] = [.5, 1, np.log(4), np.log(2), .8, .75]
    raw[0, i + 1] = [-.5, 1, np.log(4), np.log(2), .7, .75]
    # High class probability alone must not produce a candidate.
    raw[0, 1] = [.5, .5, 1, 1, .1, .99]
    original = raw.copy()
    detections = decode(raw, 2, 320, 240)
    assert len(detections) == 1
    assert detections[0]['label'] == 'pothole'
    assert detections[0]['confidence'] == pytest.approx(.6)
    assert detections[0]['bbox'] == pytest.approx([68, 96, 100, 112])
    np.testing.assert_array_equal(raw, original)


@pytest.mark.parametrize('bad', ['shape', 'nan', 'score'])
def test_rejects_unexpected_model_output_instead_of_guessing(bad):
    raw = np.zeros((1, 8400, 6), np.float32)
    if bad == 'shape':
        raw = raw.transpose(0, 2, 1)
    elif bad == 'nan':
        raw[0, 0, 0] = np.nan
    else:
        raw[0, 0, 4] = 1.1
    with pytest.raises(ValueError):
        decode(raw, 1, 640, 480)


def test_research_adapter_rejects_altered_weights_before_creating_session(tmp_path):
    import json
    (tmp_path/'manifest.json').write_text(json.dumps({
        'repository':'AMD-PAVS-AI/pothole-detection', 'sha256':'0'*64}))
    (tmp_path/'pothole_yolox_s.onnx').write_bytes(b'not the frozen weights')
    with pytest.raises(ValueError, match='checksum'):
        YoloXPotholeDetector(tmp_path)


def test_unknown_backend_fails_before_camera_or_audio_side_effects():
    from cane.vision.v2 import V2
    with pytest.raises(ValueError, match='backend'):
        V2({'pothole':{'backend':'automatic'}})
