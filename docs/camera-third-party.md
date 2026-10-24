# Camera sample: model and third-party notices

No training is performed. The sample uses the existing Ultralytics YOLO11n COCO
model, converted to NCNN and published by ncnn maintainer nihui:

- https://github.com/nihui/ncnn-assets/tree/master/models
- https://github.com/ultralytics/ultralytics/blob/main/LICENSE
- https://github.com/Tencent/ncnn/blob/master/examples/yolo11.cpp

The exact model files and SHA-256 hashes are recorded by
`tools/prepare_camera_model.py`. The application supports COCO's existing 80
classes; it does not acquire puddle, pothole or stair capabilities by renaming
classes. Model weights retain their upstream terms, including applicable
Ultralytics AGPL-3.0 requirements. The enclosing project's license does not
relicense third-party weights.

`pi/cane/vision/detector.py` follows the output layout, preprocessing and DFL
decoding of the NCNN example above; OpenCV provides NMS. This is model integration,
not a newly trained or novel perception algorithm.

## Tencent example attribution (BSD-3-Clause)

Copyright 2025 Tencent

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## English prompts

The current local demonstration WAV files were generated using the installed
Microsoft David Desktop Windows voice and the fixed English strings in
`tools/gen_camera_prompts.ps1`. They are generated evaluation assets, excluded
from Git by the existing `audio/*/` rule. No microphone recording or speech
upload was used. Before redistributing generated voice assets, check applicable
voice/provider terms or replace them with recordings you have permission to use.
