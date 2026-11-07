"""Small, read-only exposure hints; never pose, occupancy or training-label evidence."""
import numpy as np


def image_diagnostics(rgb):
    h,w=rgb.shape[:2]
    sample=rgb[::max(1,(h+47)//48),::max(1,(w+63)//64)].astype(np.float32)
    luminance=sample[...,0]*.2126+sample[...,1]*.7152+sample[...,2]*.0722
    mean=float(luminance.mean());dark=float(np.mean(luminance<35));clipped=float(np.mean(luminance>=250))
    return dict(version='camera-image-hints-0.1',mean_luma_0_255=mean,dark_fraction=dark,
                clipped_fraction=clipped,low_light=mean<50 and dark>=.55,highlight_clipping=clipped>=.35,
                diagnostic_only=True,validation='unvalidated_visual_hints_not_exposure_measurement')
