"""Exceptional values flagging along the time axis.

Inheritance diagram
-------------------

.. inheritance-diagram:: Flag
   :parts: 2

"""

import warnings
import numpy as np
from scipy.interpolate import InterpolatedUnivariateSpline
import tod_task
from raw_timestream import RawTimestream
from timestream import Timestream
from sg_filter import savitzky_golay


def flag(vis, vis_mask, li, gi, tbl, ts, **kwargs):

    time_window = kwargs.get('time_window', 15)
    sigma = kwargs.get('sigma', 5.0)

    nt = vis.shape[0]
    abs_vis = np.abs(np.ma.array(vis, mask=vis_mask))
    if np.ma.count_masked(abs_vis) > 0: # has masked value
        abs_vis_valid = abs_vis[~abs_vis.mask]
        inds_valid = np.arange(nt)[~abs_vis.mask]
        itp = InterpolatedUnivariateSpline(inds_valid, abs_vis_valid)
        abs_vis_itp = itp(np.arange(nt))
        abs_vis1 = abs_vis_itp.copy()
    else:
        abs_vis1 = abs_vis.copy()

    for cnt in xrange(10):
        if cnt != 0:
            abs_vis1[inds] = smooth[inds]
        smooth = savitzky_golay(abs_vis1, time_window, 3)

        # flage RFI
        diff = abs_vis1 - smooth
        mean = np.mean(diff)
        std = np.std(diff)
        inds = np.where(np.abs(diff - mean) > sigma*std)[0]
        if len(inds) == 0:
            break

    diff = abs_vis - smooth
    mean = np.mean(diff)
    std = np.std(diff)
    inds = np.where(np.abs(diff - mean) > sigma*std)[0] # masked inds
    # Addtional threshold
    # inds1 = np.where(np.abs(diff[inds]) > 1.0e-2*np.abs(smooth[inds]))[0]
    # inds = inds[inds1]
    vis_mask[inds] = True # set mask

    return vis, vis_mask


class Flag(tod_task.TaskTimestream):
    """Exceptional values flagging along the time axis."""

    params_init = {
                    'time_window': 15,
                    'sigma': 5.0,
                  }

    prefix = 'tf_'

    def process(self, ts):

        time_window = self.params['time_window']
        sigma = self.params['sigma']

        nt = ts.time.shape[0] # global shape

        # time_window = min(nt/2, time_window)
        # ensure window_size is an odd number
        if time_window % 2 == 0:
            time_window += 1
        if nt >= 2*time_window:

            ts.redistribute('baseline')

            if isinstance(ts, RawTimestream):
                func = ts.freq_and_bl_data_operate
            elif isinstance(ts, Timestream):
                func = ts.freq_pol_and_bl_data_operate

            func(flag, full_data=True, keep_dist_axis=False, time_window=time_window, sigma=sigma)
        else:
            warnings.warn('Not enough time points to do the smoothing')

        ts.add_history(self.history)

        # ts.info()

        return ts
