"""Camera perception: independent single-camera demo and legacy stereo modules.

- demo.py: Logitech + pretrained YOLO11n + live boxes + optional USB headset.

- stereo.py: depth only while the user is stationary and facing ahead
  (frames are not synchronised, so motion would corrupt the disparity).
- odometry.py: monocular visual odometry while walking, used only to tell
  whether the user has turned since aligning (heading drift), not for position.

An OAK depth camera, if borrowed, replaces StereoDepth through DepthSource.
"""
