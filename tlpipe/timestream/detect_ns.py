"""Detect noise source signal.

Inheritance diagram
-------------------

.. inheritance-diagram:: Detect
   :parts: 2

"""

import warnings
from collections import Counter
import numpy as np
import tod_task

from raw_timestream import RawTimestream
from caput import mpiutil
from caput import mpiarray


class Detect(tod_task.TaskTimestream):
    """Detect noise source signal.

    This task automatically finds out the time points that the noise source
    is **on**, and creates a new bool dataset "ns_on" with elements *True*
    corresponding to time points when the noise source is **on**.

    """

    params_init = {
                    'feed': None, # use this feed
                    'sigma': 3.0,
                    'mask_near': 1, # how many extra near ns_on int_time to be masked
                  }

    prefix = 'dt_'

    def process(self, rt):

        assert isinstance(rt, RawTimestream), '%s only works for RawTimestream object currently' % self.__class__.__name__

        feed = self.params['feed']
        sigma = self.params['sigma']
        mask_near = max(0, int(self.params['mask_near']))

        rt.redistribute(0) # make time the dist axis

        auto_inds = np.where(rt.bl[:, 0]==rt.bl[:, 1])[0].tolist() # inds for auto-correlations
        feeds = [ rt.bl[ai, 0] for ai in auto_inds ] # all chosen feeds
        if feed is not None:
            if feed in feeds:
                bl_ind = auto_inds[feeds.index(feed)]
            else:
                bl_ind = auto_inds[0]
                if mpiutil.rank0:
                    print 'Warning: Required feed %d doen not in the data, use feed %d instead' % (feed, rt.bl[bl_ind, 0])
        else:
            bl_ind = auto_inds[0]
        # move the chosen feed to the first
        auto_inds.remove(bl_ind)
        auto_inds = [bl_ind] + auto_inds

        for bl_ind in auto_inds:
            vis = np.ma.array(rt.local_vis[:, :, bl_ind].real, mask=rt.local_vis_mask[:, :, bl_ind])
            cnt = vis.count() # number of not masked vals
            total_cnt = mpiutil.allreduce(cnt)
            vis_shp = rt.vis.shape
            ratio = float(total_cnt) / np.prod((vis_shp[0], vis_shp[1])) # ratio of un-maksed vals
            if ratio < 0.5: # too many masked vals
                continue

            tt_mean = mpiutil.gather_array(np.ma.mean(vis, axis=-1).filled(0), root=None)
            df =  np.diff(tt_mean, axis=-1)
            pdf = np.where(df>0, df, 0)
            pinds = np.where(pdf>pdf.mean() + sigma*pdf.std())[0]
            pinds = pinds + 1
            pT = Counter(np.diff(pinds)).most_common(1)[0][0] # period of pinds
            ndf = np.where(df<0, df, 0)
            ninds = np.where(ndf<ndf.mean() - sigma*ndf.std())[0]
            ninds = ninds + 1
            nT = Counter(np.diff(ninds)).most_common(1)[0][0] # period of ninds
            if pT != nT: # failed to detect correct period
                continue
            else:
                period = pT

            ninds = ninds.reshape(-1, 1)
            dinds = (ninds - pinds).flatten()
            on_time = Counter(dinds[dinds>0] % period).most_common(1)[0][0]
            off_time = Counter(-dinds[dinds<0] % period).most_common(1)[0][0]

            if period != on_time + off_time: # incorrect detect
                continue
            else:
                if 'noisesource' in rt.iterkeys():
                    if rt['noisesource'].shape[0] == 1: # only 1 noise source
                        start, stop, cycle = rt['noisesource'][0, :]
                        int_time = rt.attrs['inttime']
                        true_on_time = np.round((stop - start)/int_time)
                        true_period = np.round(cycle / int_time)
                        if on_time != true_on_time and period != true_period: # inconsistant with the record in the data
                            continue
                    elif rt['noisesource'].shape[0] >= 2: # more than 1 noise source
                        warnings.warn('More than 1 noise source, do not know how to deal with this currently')

                # break if succeed
                break

        else:
            raise RuntimeError('Failed to detect noise source signal')

        if mpiutil.rank0:
            print 'Detected noise source: period = %d, on_time = %d, off_time = %d' % (period, on_time, off_time)
        num_period = np.int(np.ceil(len(tt_mean) / np.float(period)))
        tmp_ns_on = np.array(([True] * on_time + [False] * off_time) * num_period)[:len(tt_mean)]
        on_start = Counter(pinds % period).most_common(1)[0][0]
        ns_on = np.roll(tmp_ns_on, on_start)

        # import matplotlib
        # matplotlib.use('Agg')
        # import matplotlib.pyplot as plt
        # plt.figure()
        # plt.plot(np.where(ns_on, np.nan, tt_mean))
        # # plt.plot(pinds, tt_mean[pinds], 'RI')
        # # plt.plot(ninds, tt_mean[ninds], 'go')
        # plt.savefig('df.png')
        # err

        ns_on1 = mpiarray.MPIArray.from_numpy_array(ns_on)

        rt.create_main_time_ordered_dataset('ns_on', ns_on1)
        rt['ns_on'].attrs['period'] = period
        rt['ns_on'].attrs['on_time'] = on_time
        rt['ns_on'].attrs['off_time'] = off_time

        # set vis_mask corresponding to ns_on
        on_inds = np.where(rt['ns_on'].local_data[:])[0]
        rt.local_vis_mask[on_inds] = True

        if mask_near > 0:
            on_inds = np.where(ns_on)[0]
            new_on_inds = on_inds.tolist()
            for i in xrange(1, mask_near+1):
                new_on_inds = new_on_inds + (on_inds-i).tolist() + (on_inds+i).tolist()
            new_on_inds = np.unique(new_on_inds)

            start = rt.vis_mask.local_offset[0]
            end = start + rt.vis_mask.local_shape[0]
            global_inds = np.arange(start, end).tolist()
            new_on_inds = np.intersect1d(new_on_inds, global_inds)
            local_on_inds = [ global_inds.index(i) for i in new_on_inds ]
            rt.local_vis_mask[local_on_inds] = True # set mask using global slicing

        return super(Detect, self).process(rt)
