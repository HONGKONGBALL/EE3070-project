"""Two unsynchronised USB cameras.

- stereo.py: depth only while the user is stationary and facing ahead
  (frames are not synchronised, so motion would corrupt the disparity).
- odometry.py: monocular visual odometry while walking, used only to tell
  whether the user has turned since aligning (heading drift), not for position.

An OAK depth camera, if borrowed, replaces StereoDepth through DepthSource.
"""
