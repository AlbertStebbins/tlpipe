"""Rebin the frequency channels."""

import warnings
import numpy as np
import tod_task

from caput import mpiutil
from caput import mpiarray
from tlpipe.utils.np_util import unique, average


class Rebin(tod_task.IterTimestream):
    """Rebin the frequency channels."""

    params_init = {
                    'bin_number': 16,
                  }

    prefix = 'rb_'

    def process(self, ts):

        bin_number = self.params['bin_number']

        ts.redistribute('baseline')

        nt = len(ts.time)
        nfreq = len(ts.freq)
        if bin_number >= nfreq:
            warnings.warn('The number of bins can not exceed the number of frequencies, do nothing')
        else:
            repeat_inds = np.repeat(np.arange(nfreq), bin_number)
            num, start, end = mpiutil.split_m(nfreq*bin_number, bin_number)
            freq = np.zeros(bin_number, dtype=ts.freq.dtype)
            vis = np.zeros((nt, bin_number)+ts.local_vis.shape[2:], dtype=ts.vis.dtype)
            vis_mask= np.zeros((nt, bin_number)+ts.local_vis.shape[2:], dtype=ts.vis_mask.dtype) # all False

            # average over frequency
            for idx in range(bin_number):
                inds, weight = unique(repeat_inds[start[idx]:end[idx]], return_counts=True)
                freq[idx] = average(ts.freq[inds], axis=0, weights=weight)
                masked_vis = np.ma.array(ts.local_vis[:, inds], mask=ts.local_vis_mask[:, inds])
                vis[:, idx] = average(masked_vis, axis=1, weights=weight) # freq mean
                if masked_vis.count == 0:
                    vis_mask[:, idx] = True

            # create rebinned datasets
            vis = mpiarray.MPIArray.wrap(vis, axis=3)
            vis_mask= mpiarray.MPIArray.wrap(vis_mask, axis=3)
            ts.create_main_data(vis, recreate=True, copy_attrs=True)
            axis_order = ts.main_axes_ordered_datasets['vis']
            ts.create_main_axis_ordered_dataset(axis_order, 'vis_mask', vis_mask, axis_order, recreate=True, copy_attrs=True)
            ts.create_freq_ordered_dataset('freq', freq, recreate=True, copy_attrs=True, check_align=True)

            # for other freq_axis datasets
            for name in ts.freq_ordered_datasets.keys():
                if not name in ('freq', 'vis', 'vis_mask'): # exclude already rebinned datasets
                    raise RuntimeError('Should not have other freq_ordered_datasets')

        ts.add_history(self.history)

        return ts
