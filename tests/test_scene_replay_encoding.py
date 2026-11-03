from pathlib import Path
import hashlib
import numpy as np
import pytest

cv2=pytest.importorskip('cv2')
from build_scene_replay import build


def test_lossless_replay_preserves_pixels_and_frozen_source_hash(tmp_path):
    pixels=np.random.default_rng(4).integers(0,256,(480,640,3),dtype=np.uint8)
    source=tmp_path/'source.png'
    assert cv2.imwrite(str(source),pixels)
    checksum=hashlib.sha256(source.read_bytes()).hexdigest()
    catalog={'items':[{'file':'source.png','sha256':checksum,'ground_truth':{}}]}
    video=tmp_path/'replay.avi'
    timeline=build(catalog,tmp_path,video,seconds=1,fps=2)
    assert timeline['encoding']=={'codec':'FFV1','lossless_first_frame_verified':True}
    assert timeline['cases'][0]['source_sha256']==checksum
    capture=cv2.VideoCapture(str(video))
    try:
        for _ in range(2):
            ok,decoded=capture.read()
            assert ok and np.array_equal(decoded,pixels)
    finally:capture.release()
