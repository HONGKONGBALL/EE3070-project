"""Conservative pixel-quality limits, not a learned visibility or lens-cover detector."""

import cv2
import numpy as np


def assess_frame(image):
    height,width=image.shape[:2]
    gray=cv2.cvtColor(cv2.resize(image,(320,max(1,round(height*320/width))),
                                interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2GRAY)
    mean=float(gray.mean())
    dark=float(np.mean(gray<20))
    saturated=float(np.mean(gray>245))
    spread=float(np.percentile(gray,95)-np.percentile(gray,5))
    detail=float(cv2.Laplacian(gray,cv2.CV_32F).var())
    reasons=[]
    if mean<15 and dark>=.95:reasons.append('too_dark')
    if saturated>=.95:reasons.append('overexposed')
    if spread<4:reasons.append('insufficient_detail')
    elif spread>=25 and detail<3:reasons.append('very_low_sharpness')
    return {'state':'limited' if reasons else 'usable','usable':not reasons,'reasons':reasons,
            'metrics':{'mean_luma':round(mean,2),'dark_fraction':round(dark,4),
                       'saturated_fraction':round(saturated,4),'luma_p95_p05':round(spread,2),
                       'laplacian_variance':round(detail,2)},
            'basis':'experimental pixel thresholds; cannot prove visibility, occlusion, or path safety'}
