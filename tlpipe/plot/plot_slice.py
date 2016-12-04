"""Plot time or frequency slices.

Inheritance diagram
-------------------

.. inheritance-diagram:: Plot
   :parts: 2

"""

import numpy as np
from tlpipe.timestream import tod_task
from tlpipe.timestream.raw_timestream import RawTimestream
from tlpipe.timestream.timestream import Timestream
from tlpipe.utils.path_util import output_path
import matplotlib.pyplot as plt


def plot(vis, vis_mask, li, gi, bl, ts, **kwargs):

    plot_type = kwargs.get('plot_type', 'time')
    bl_incl = kwargs.get('bl_incl', 'all')
    bl_excl = kwargs.get('bl_excl', [])
    flag_mask = kwargs.get('flag_mask', False)
    flag_ns = kwargs.get('flag_ns', True)
    slices = kwargs.get('slices', 10)
    fig_prefix = kwargs.get('fig_name', 'slice')
    tag_output_iter= kwargs.get('tag_output_iter', True)
    iteration= kwargs.get('iteration', None)

    if isinstance(ts, Timestream): # for Timestream
        pol = bl[0]
        bl = tuple(bl[1])
    elif isinstance(ts, RawTimestream): # for RawTimestream
        pol = None
        bl = tuple(bl)
    else:
        raise ValueError('Need either a RawTimestream or Timestream')

    if bl_incl != 'all':
        bl1 = set(bl)
        bl_incl = [ {f1, f2} for (f1, f2) in bl_incl ]
        bl_excl = [ {f1, f2} for (f1, f2) in bl_excl ]
        if (not bl1 in bl_incl) or (bl1 in bl_excl):
            return vis, vis_mask

    if plot_type == 'time':
        nt = vis.shape[0]
        c = nt/2
        s = max(0, c-slices/2)
        e = min(nt, c+slices/2)
        if flag_mask:
            vis1 = np.ma.array(vis[s:e], mask=vis_mask[s:e])
        elif flag_ns:
            vis1 = vis[s:e].copy()
            ns_on = ts['ns_on'][s:e]
            on = np.where(ns_on)[0]
            vis1[on] = complex(np.nan, np.nan)
        else:
            vis1 = vis[s:e]

        o = c - s
        shift = 0.1 * np.ma.max(np.abs(vis1[o]))

        ax_val = ts.freq[:]
        xlabel = r'$\nu$ / MHz'
    elif plot_type == 'freq':
        nfreq = vis.shape[1]
        c = nfreq/2
        s = max(0, c-slices/2)
        e = min(nfreq, c+slices/2)
        if flag_mask:
            vis1 = np.ma.array(vis[:, s:e], mask=vis_mask[:, s:e])
        elif flag_ns:
            vis1 = vis[:, s:e].copy()
            ns_on = ts['ns_on'][:]
            on = np.where(ns_on)[0]
            vis1[on] = complex(np.nan, np.nan)
        else:
            vis1 = vis[:, s:e]

        o = c - s
        shift = 0.1 * np.ma.max(np.abs(vis1[:, o]))

        ax_val = ts.time[:]
        xlabel = r'$t$ / Julian Date'
    else:
        raise ValueError('Unknown plot_type %s, must be either time or freq' % plot_type)

    plt.figure()
    f, axarr = plt.subplots(3, sharex=True)
    for i in range(e - s):
        if plot_type == 'time':
            axarr[0].plot(ax_val, vis1[i].real + (i - o)*shift, label='real')
        elif plot_type == 'freq':
            axarr[0].plot(ax_val, vis1[:, i].real + (i - o)*shift, label='real')
        if i == 0:
            axarr[0].legend()

        if plot_type == 'time':
            axarr[1].plot(ax_val, vis1[i].imag + (i - o)*shift, label='imag')
        elif plot_type == 'freq':
            axarr[1].plot(ax_val, vis1[:, i].imag + (i - o)*shift, label='imag')
        if i == 0:
            axarr[1].legend()

        if plot_type == 'time':
            axarr[2].plot(ax_val, np.abs(vis1[i]) + (i - o)*shift, label='abs')
        elif plot_type == 'freq':
            axarr[2].plot(ax_val, np.abs(vis1[:, i]) + (i - o)*shift, label='abs')
        if i == 0:
            axarr[2].legend()

    axarr[2].set_xlabel(xlabel)

    if pol is None:
        fig_name = '%s_%s_%d_%d.png' % (fig_prefix, plot_type, bl[0], bl[1])
    else:
        fig_name = '%s_%s_%d_%d_%s.png' % (fig_prefix, plot_type, bl[0], bl[1], pol)
    if tag_output_iter:
        fig_name = output_path(fig_name, iteration=iteration)
    else:
        fig_name = output_path(fig_name)
    plt.savefig(fig_name)
    plt.close()

    return vis, vis_mask


class Plot(tod_task.TaskTimestream):
    """Plot time or frequency slices."""

    params_init = {
                    'plot_type': 'time', # or 'freq'
                    'bl_incl': 'all', # or a list of include (bl1, bl2)
                    'bl_excl': [],
                    'flag_mask': True,
                    'flag_ns': True,
                    'slices': 10, # number of slices to plot
                    'fig_name': 'slice',
                  }

    prefix = 'psl_'

    def process(self, ts):

        plot_type = self.params['plot_type']
        bl_incl = self.params['bl_incl']
        bl_excl = self.params['bl_excl']
        flag_mask = self.params['flag_mask']
        flag_ns = self.params['flag_ns']
        slices = self.params['slices']
        fig_name = self.params['fig_name']
        tag_output_iter = self.params['tag_output_iter']

        ts.redistribute('baseline')

        if isinstance(ts, RawTimestream):
            func = ts.bl_data_operate
        elif isinstance(ts, Timestream):
            func = ts.pol_and_bl_data_operate

        func(plot, full_data=True, keep_dist_axis=False, plot_type=plot_type, bl_incl=bl_incl, bl_excl=bl_excl, fig_name=fig_name, iteration=self.iteration, tag_output_iter=tag_output_iter, flag_mask=flag_mask, flag_ns=flag_ns, slices=slices)

        ts.add_history(self.history)

        return ts
