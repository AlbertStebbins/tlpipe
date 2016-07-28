"""Initialize telescope array and average the timestream."""

import os
import numpy as np
from scipy.linalg import eigh
import h5py
import aipy as a
import tod_task

from caput import mpiutil
from caput import mpiarray
from caput import memh5
from tlpipe.utils.np_util import unique, average
from tlpipe.utils.path_util import output_path
from tlpipe.map.fmmode.telescope import tl_dish, tl_cylinder
from tlpipe.map.fmmode.core import beamtransfer
from tlpipe.map.fmmode.pipeline import timestream


class MapMaking(tod_task.SingleTimestream):
    """Initialize telescope array and average the timestream."""

    params_init = {
                    'beam_theta_range': [0.0, 135.0],
                    'tsys': 50.0,
                    'accuracy_boost': 1.0,
                    'l_boost': 1.0,
                    'bl_range': [0.0, 1.0e7],
                    'auto_correlations': False,
                    'pol': 'xx', # 'yy' or 'I'
                    'beam_dir': 'map/bt',
                    'gen_invbeam': True,
                    'ts_dir': 'map/ts',
                    'ts_name': 'ts',
                    'simulate': False,
                    'input_maps': [],
                  }

    prefix = 'mm_'

    def process(self, ts):

        beam_theta_range = self.params['beam_theta_range']
        tsys = self.params['tsys']
        accuracy_boost = self.params['accuracy_boost']
        l_boost = self.params['l_boost']
        bl_range = self.params['bl_range']
        auto_correlations = self.params['auto_correlations']
        pol = self.params['pol']
        beam_dir = output_path(self.params['beam_dir'])
        gen_inv = self.params['gen_invbeam']
        ts_dir = output_path(self.params['ts_dir'])
        ts_name = self.params['ts_name']
        simulate = self.params['simulate']
        input_maps = self.params['input_maps']

        ts.redistribute('frequency')

        lat = ts.attrs['sitelat']
        # lon = ts.attrs['sitelon']
        lon = 0.0
        # lon = np.degrees(ts['ra_dec'][0, 0]) # the first ra
        freqs = ts.freq.data.to_numpy_array(root=None)
        ndays = 1
        feeds = ts['feedno'][:]
        az, alt = ts['az_alt'][0]
        az = np.degrees(az)
        alt = np.degrees(alt)
        pointing = [az, alt, 0.0]
        feedpos = ts['feedpos'][:]

        if ts.is_dish:
            dish_width = ts.attrs['dishdiam']
            tel = tl_dish.TlUnpolarisedDishArray(lat, lon, freqs, beam_theta_range, tsys, ndays, accuracy_boost, l_boost, bl_range, auto_correlations, dish_width, feedpos, pointing)
        elif ts.is_cylinder:
            cyl_width = ts.attrs['cywid']
            tel = tl_cylinder.TlUnpolarisedCylinder(lat, lon, freqs, beam_theta_range, tsys, ndays, accuracy_boost, l_boost, bl_range, auto_correlations, cyl_width, feedpos)
        else:
            raise RuntimeError('Unknown array type %s' % ts.attrs['telescope'])

        # import matplotlib
        # matplotlib.use('Agg')
        # import matplotlib.pyplot as plt
        # plt.figure()
        # plt.plot(ts['ra_dec'][:])
        # # plt.plot(ts['az_alt'][:])
        # plt.savefig('ra_dec1.png')

        if not simulate:
            # mask noise on data
            on = np.where(ts['ns_on'][:])[0]
            ts['vis'].local_data[on] = complex(np.nan, np.nan)

            # average data
            nt = ts['sec1970'].shape[0]
            phi_size = tel.phi_size
            nt_m = float(nt) / phi_size

            # roll data to have phi=0 near the first
            roll_len = np.int(np.around(0.5*nt_m))
            ts['vis'].local_data[:] = np.roll(ts['vis'].local_data[:], roll_len, axis=0)
            ts['ra_dec'][:] = np.roll(ts['ra_dec'][:], roll_len, axis=0)

            # inds = np.arange(nt)
            repeat_inds = np.repeat(np.arange(nt), phi_size)
            num, start, end = mpiutil.split_m(nt*phi_size, phi_size)

            # phi = np.zeros((phi_size,), dtype=ts['ra_dec'].dtype)
            phi = np.linspace(0, 2*np.pi, phi_size, endpoint=False)
            vis = np.zeros((phi_size,)+ts['vis'].local_data.shape[1:], dtype=ts['vis'].dtype)
            # average onver time
            for idx in range(phi_size):
                inds, weight = unique(repeat_inds[start[idx]:end[idx]], return_counts=True)
                vis[idx] = average(np.ma.masked_invalid(ts['vis'].local_data[inds]), axis=0, weights=weight) # time mean
                # phi[idx] = np.average(ts['ra_dec'][:, 0][inds], axis=0, weights=weight)

            if pol == 'xx':
                vis = vis[:, :, 0, :]
            elif pol == 'yy':
                vis = vis[:, :, 1, :]
            elif pol == 'I':
                vis = 0.5 * (vis[:, :, 0, :] + vis[:, :, 1, :])
            else:
                raise ValueError('Invalid pol: %s' % pol)

            allpairs = tel.allpairs
            redundancy = tel.redundancy

            # reorder bls according to allpairs
            vis_tmp = np.zeros_like(vis)
            bls = [ tuple(bl) for bl in ts['blorder'][:] ]
            for ind, (a1, a2) in enumerate(allpairs):
                try:
                    b_ind = bls.index((feeds[a1], feeds[a2]))
                    vis_tmp[:, :, ind] = vis[:, :, b_ind]
                except ValueError:
                    b_ind = bls.index((feeds[a2], feeds[a1]))
                    vis_tmp[:, :, ind] = vis[:, :, b_ind].conj()

            # average over redundancy
            vis_stream = np.zeros(vis.shape[:-1]+(len(redundancy),), dtype=vis_tmp.dtype)
            red_bin = np.cumsum(np.insert(redundancy, 0, 0)) # redundancy bin
            # average over redundancy
            for ind in range(len(redundancy)):
                vis_stream[:, :, ind] = np.sum(vis_tmp[:, :, red_bin[ind]:red_bin[ind+1]], axis=2) / redundancy[ind]

            vis_stream = mpiarray.MPIArray.wrap(vis_stream, axis=1)
            vis_h5 = memh5.MemGroup(distributed=True)
            vis_h5.create_dataset('/timestream', data=vis_stream)
            vis_h5.create_dataset('/phi', data=phi)

            # Telescope layout data
            vis_h5.create_dataset('/feedmap', data=tel.feedmap)
            vis_h5.create_dataset('/feedconj', data=tel.feedconj)
            vis_h5.create_dataset('/feedmask', data=tel.feedmask)
            vis_h5.create_dataset('/uniquepairs', data=tel.uniquepairs)
            vis_h5.create_dataset('/baselines', data=tel.baselines)

            # Telescope frequencies
            vis_h5.create_dataset('/frequencies', data=freqs)

            # Write metadata
            # vis_h5.attrs['beamtransfer_path'] = os.path.abspath(bt.directory)
            vis_h5.attrs['ntime'] = phi_size

        # beamtransfer
        bt = beamtransfer.BeamTransfer(beam_dir, tel, gen_inv)
        bt.generate()

        if simulate:
            ndays = 733
            print ndays
            ts = timestream.simulate(bt, ts_dir, ts_name, input_maps, ndays)
        else:
            # timestream and map-making
            ts = timestream.Timestream(ts_dir, ts_name, bt)
            # Make directory if required
            try:
                os.makedirs(ts._tsdir)
            except OSError:
                 # directory exists
                 pass
            vis_h5.to_hdf5(ts._tsfile)
        # ts.generate_mmodes(vis_stream.to_numpy_array(root=None))
        ts.generate_mmodes()
        ts.mapmake_full(64, 'full')

        # ts.add_history(self.history)

        return ts
