"""Module to gridding visibilities to uv plane."""

try:
    import cPickle as pickle
except ImportError:
    import pickle

import os
import numpy as np
import aipy as a
import h5py

from tlpipe.kiyopy import parse_ini
from tlpipe.utils import mpiutil
from tlpipe.core import tldishes
from tlpipe.utils.path_util import input_path, output_path


# Define a dictionary with keys the names of parameters to be read from
# file and values the defaults.
params_init = {
               'nprocs': mpiutil.size, # number of processes to run this module
               'aprocs': range(mpiutil.size), # list of active process rank no.
               'input_file': 'data_cal_stokes.hdf5',
               'output_file': 'uv_imag_conv.hdf5',
               'cut': [None, None],
               'pol': 'I',
               'res': 1.0, # resolution, unit: wavelength
               'max_wl': 200.0, # max wavelength
               'sigma': 0.07,
               'extra_history': '',
              }
prefix = 'gr_'


pol_dict = {'I': 0, 'Q': 1, 'U': 2, 'V': 3}

def get_uvvec(s0_top, n_top):
    """Compute unit vector in u,v direction relative to `s0_top` in topocentric coordinate.

    Parameters
    ----------
    s0_top : [3] array_like
        Unit vector of the phase center in topocentric coordinate.
    n_top : [3] array_like
        Unit vector of the north celestial pole in topocentric coordinate.

    Returns
    -------
    uvec, vvec : [3] np.ndarray
        Unit vector in u and v direction.
    """
    s0 = s0_top
    n = n_top
    s0x, s0y, s0z = s0[0], s0[1], s0[2]
    nx, ny, nz = n[0], n[1], n[2]
    # uvec is perpendicular to both s0 and n, and have ux >= 0 to point to East
    ux = 1.0 / np.sqrt(1.0 + ((nz*s0x - nx*s0z) / (ny*s0z - nz*s0y))**2 + ((ny*s0x - nx*s0y) / (nz*s0y - ny*s0z))**2)
    uy = ux * ((nz*s0x - nx*s0z) / (ny*s0z - nz*s0y))
    uz = ux * ((ny*s0x - nx*s0y) / (nz*s0y - ny*s0z))
    uvec = np.array([ux, uy, uz])
    # vvec is in the plane spanned by s0 and n, and have dot(n, vvec) > 0
    ns0 = np.dot(n, s0)
    l1 = 1.0 / np.sqrt(1.0 - ns0**2)
    l2 = - l1 * ns0
    vvec = l1*n + l2*s0

    return uvec, vvec


def conv_kernal(u, v, sigma, l0=0, m0=0):
    return np.exp(-2.0J * np.pi * (u * l0 + v * m0)) * np.exp(-0.5 * (2 * np.pi * sigma)**2 * (u**2 + v**2))

def conv_gauss(arr, c, vp, up, sigma, val=1.0, l0=0, m0=0, pix=1, npix=4):
    for ri in range(-npix, npix):
        for ci in range(-npix, npix):
            tmp = val * conv_kernal(ri*pix, ci*pix, sigma, l0, m0)
            arr[c+(vp+ri), c+(up+ci)] += tmp
            arr[c-(vp+ri), c-(up+ci)] += np.conj(tmp) # append conjugate



class Gridding(object):
    """Gridding."""

    def __init__(self, parameter_file_or_dict=None, feedback=2):

        # Read in the parameters.
        self.params = parse_ini.parse(parameter_file_or_dict, params_init,
                                 prefix=prefix, feedback=feedback)
        self.feedback = feedback
        nprocs = min(self.params['nprocs'], mpiutil.size)
        procs = set(range(mpiutil.size))
        aprocs = set(self.params['aprocs']) & procs
        self.aprocs = (list(aprocs) + list(set(range(nprocs)) - aprocs))[:nprocs]
        assert 0 in self.aprocs, 'Process 0 must be active'
        self.comm = mpiutil.active_comm(self.aprocs) # communicator consists of active processes

    @property
    def history(self):
        """History that will be added to the output file."""

        hist = 'Execute %s.%s with %s.\n' % (__name__, self.__class__.__name__, self.params)
        if self.params['extra_history'] != '':
            hist = self.params['extra_history'] + ' ' + hist

        return hist

    def execute(self):

        input_file = input_path(self.params['input_file'])
        output_file = output_path(self.params['output_file'])
        cut = self.params['cut']

        with h5py.File(input_file, 'r') as f:
            dset = f['data']
            # data_cal_stokes = dset[...]
            ants = dset.attrs['ants']
            ts = f['time'][...]
            freq = dset.attrs['freq']
            az = np.radians(dset.attrs['az_alt'][0][0])
            alt = np.radians(dset.attrs['az_alt'][0][1])

            # cut head and tail
            nt_origin = len(ts)
            if cut[0] is not None and cut[1] is not None:
                ts = np.concatenate((ts[:int(cut[0] * nt_origin)], ts[-int(cut[1] * nt_origin):]))
                dset = np.concatenate((dset[:int(cut[0] * nt_origin)], dset[-int(cut[1] * nt_origin):]))
            elif cut[0] is not None:
                ts = ts[:int(cut[0] * nt_origin)]
                dset = dset[:int(cut[0] * nt_origin)]
            elif cut[1] is not None:
                ts = ts[-int(cut[1] * nt_origin):]
                dset = dset[-int(cut[1] * nt_origin):]

            npol = dset.shape[2]
            nt = len(ts)
            nfreq = len(freq)
            nants = len(ants)
            bls = [(ants[i], ants[j]) for i in range(nants) for j in range(i, nants)] # start from 1
            nbls = len(bls)

            lt, st, et = mpiutil.split_local(nt)
            local_data = dset[st:et] # data section used only in this process


        res = self.params['res']
        max_wl = self.params['max_wl']
        max_lm = 0.5 * 1.0 / res
        size = np.int(2 * max_wl / res) + 1
        center = np.int(max_wl / res) # the central pixel
        sigma = self.params['sigma']

        uv = np.zeros((size, size), dtype=np.complex128)
        uv_cov = np.zeros((size, size), dtype=np.complex128)

        src = 'cas'
        cat = 'misc'
        # calibrator
        srclist, cutoff, catalogs = a.scripting.parse_srcs(src, cat)
        cat = a.src.get_catalog(srclist, cutoff, catalogs)
        assert(len(cat) == 1), 'Allow only one calibrator'
        s = cat.values()[0]
        if mpiutil.rank0:
            print 'Calibrating for source with',
            print 'strength', s._jys,
            print 'measured at', s.mfreq, 'GHz',
            print 'with index', s.index


        # pointting vector in topocentric coord
        pt_top = a.coord.azalt2top((az, alt))

        # array
        aa = tldishes.get_aa(1.0e-3 * freq) # use GHz
        for ti, t_ind in enumerate(range(st, et)): # mpi among time
            t = ts[t_ind]
            aa.set_jultime(t)
            s.compute(aa)
            # get the topocentric coordinate of the calibrator at the current time
            s_top = s.get_crds('top', ncrd=3)
            # the north celestial pole
            NP = a.phs.RadioFixedBody(0.0, np.pi/2.0, name='north pole', epoch=str(aa.epoch))

            # get the topocentric coordinate of the north celestial pole at the current time
            NP.compute(aa)
            n_top = NP.get_crds('top', ncrd=3)

            # unit vector in u,v direction in topocentric coordinate at current time relative to the calibrator
            uvec, vvec = get_uvvec(s_top, n_top)

            # l,m of the pointing relative to phase center (the calibrator)
            l0 = np.dot(pt_top, uvec)
            m0 = np.dot(pt_top, vvec)

            for bl_ind in range(nbls):
                i, j = bls[bl_ind]
                if i == j:
                    continue
                us, vs, ws = aa.gen_uvw(i-1, j-1, src=s) # NOTE start from 0
                for fi, (u, v) in enumerate(zip(us.flat, vs.flat)):
                    val = local_data[ti, bl_ind, 0, fi] # only I here
                    if np.isfinite(val):
                        up = np.int(u / res)
                        vp = np.int(v / res)
                        # uv_cov[center+vp, center+up] += 1.0
                        # uv_cov[center-vp, center-up] += 1.0 # append conjugate
                        # uv[center+vp, center+up] += val
                        # uv[center-vp, center-up] += np.conj(val)# append conjugate
                        conv_gauss(uv_cov, center, vp, up, sigma, 1.0, l0, m0, res)
                        conv_gauss(uv, center, vp, up, sigma, val, l0, m0, res)


        # Reduce data in separate processes
        if self.comm is not None and self.comm.size > 1: # Reduce only when there are multiple processes
            if mpiutil.rank0:
                self.comm.Reduce(mpiutil.IN_PLACE, uv_cov, op=mpiutil.SUM, root=0)
            else:
                self.comm.Reduce(uv_cov, uv_cov, op=mpiutil.SUM, root=0)
            if mpiutil.rank0:
                self.comm.Reduce(mpiutil.IN_PLACE, uv, op=mpiutil.SUM, root=0)
            else:
                self.comm.Reduce(uv, uv, op=mpiutil.SUM, root=0)


        if mpiutil.rank0:
            uv_cov_fft = np.fft.ifft2(np.fft.ifftshift(uv_cov))
            uv_cov_fft = np.fft.ifftshift(uv_cov_fft)
            uv_fft = np.fft.ifft2(np.fft.ifftshift(uv))
            uv_fft = np.fft.ifftshift(uv_fft)
            uv_imag_fft = np.fft.ifft2(np.fft.ifftshift(1.0J * uv.imag))
            uv_imag_fft = np.fft.ifftshift(uv_imag_fft)

            # save data
            with h5py.File(output_file, 'w') as f:
                f.create_dataset('uv_cov', data=uv_cov)
                f.create_dataset('uv', data=uv)
                f.create_dataset('uv_cov_fft', data=uv_cov_fft)
                f.create_dataset('uv_fft', data=uv_fft)
                f.create_dataset('uv_imag_fft', data=uv_imag_fft)
                f.attrs['max_wl'] = max_wl
                f.attrs['max_lm'] = max_lm
