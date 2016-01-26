"""Module to gridding visibilities to uv plane."""

try:
    import cPickle as pickle
except ImportError:
    import pickle

import os
import numpy as np
import ephem
import aipy as a
import h5py

from tlpipe.utils import mpiutil
from tlpipe.core.base_exe import Base
from tlpipe.core import tldishes
from tlpipe.utils.path_util import input_path, output_path

from conv_ker import conv_kernal


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
               'conv_pixel': 10,
               'phase_center': 'cas',
               'catalog': 'misc,helm,nvss',
               'extra_history': '',
              }
prefix = 'gr_'


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


def conv_factor(u, v, ui, vi, sigma, l0=0, m0=0):
    # return np.exp(2 * (l0**2 + m0**2) / sigma**2) * np.exp(2.0J * np.pi * ((u - ui) * l0 + (v - vi) * m0)) * np.exp(-(2 * np.pi * sigma)**2 * ((u - ui)**2 + (v - vi)**2))
    return np.exp(-( (2 * np.pi * sigma * (u - ui) - 0.5J * l0 / sigma)**2 + (2 * np.pi * sigma * (v - vi) - 0.5J * m0 / sigma)**2 ))


# def conv_kernal(u, v, sigma, l0=0, m0=0):
#     # return np.exp(-2.0J * np.pi * (u * l0 + v * m0)) * np.exp(-0.5 * (2 * np.pi * sigma)**2 * (u**2 + v**2))
#     return np.exp(2 * (l0**2 + m0**2) / sigma**2) * np.exp(2.0J * np.pi * (u * l0 + v * m0)) * np.exp(-(2 * np.pi * sigma)**2 * (u**2 + v**2))

# def conv_gauss(arr, c, vp, up, sigma, val=1.0, l0=0, m0=0, pix=1, npix=4):
#     for ri in range(-npix, npix+1): # plus 1 make it symmetric
#         for ci in range(-npix, npix+1):
#             tmp = val * conv_kernal(ri*pix, ci*pix, sigma, l0, m0)
#             # tmp = val * conv_kernal((vp+ri)*pix, (up+ci)*pix, sigma, l0, m0)
#             arr[c+(vp+ri), c+(up+ci)] += tmp
#             arr[c-(vp+ri), c-(up+ci)] += np.conj(tmp) # append conjugate



class Gridding(Base):
    """Gridding."""

    def __init__(self, parameter_file_or_dict=None, feedback=2):

        super(Gridding, self).__init__(parameter_file_or_dict, params_init, prefix, feedback)

    def execute(self):

        input_file = input_path(self.params['input_file'])
        output_file = output_path(self.params['output_file'])
        cut = self.params['cut']
        pol = self.params['pol']
        phase_center = self.params['phase_center']
        catalog = self.params['catalog']

        with h5py.File(input_file, 'r') as f:
            dset = f['data']
            # data_cal_stokes = dset[...]
            ants = dset.attrs['ants']
            ts = f['time'][...]
            freq = dset.attrs['freq']
            pols = dset.attrs['pol'].tolist()
            assert pol in pols, 'Required pol %s is not in this data set with pols %s' % (pol, pols)
            az = np.radians(dset.attrs['az_alt'][0][0])
            alt = np.radians(dset.attrs['az_alt'][0][1])
            start_time = dset.attrs['start_time']
            history = dset.attrs['history']

            nt = len(ts)
            t_inds = range(nt)
            if cut[0] is not None and cut[1] is not None:
                t_inds = t_inds[:int(cut[0] * nt)] + t_inds[-int(cut[1] * nt):]
            elif cut[0] is not None:
                t_inds = t_inds[:int(cut[0] * nt)]
            elif cut[1] is not None:
                t_inds = t_inds[-int(cut[1] * nt):]

            npol = dset.shape[2]
            nt = len(ts)
            nfreq = len(freq)
            nants = len(ants)
            bls = [(ants[i], ants[j]) for i in range(nants) for j in range(i, nants)] # start from 1
            nbls = len(bls)

            lt_inds = mpiutil.mpilist(t_inds)
            local_data = dset[lt_inds, :, :, :] # data section used only in this process

        res = self.params['res']
        max_wl = self.params['max_wl']
        max_lm = 0.5 * 1.0 / res
        size = np.int(2 * max_wl / res) + 1
        center = np.int(max_wl / res) # the central pixel
        sigma = self.params['sigma']
        conv_pixel = self.params['conv_pixel']

        u_axis = np.linspace(-max_wl, max_wl, size)
        v_axis = np.linspace(-max_wl, max_wl, size)
        # u_axis, v_axis = np.meshgrid(u_axis, v_axis, sparse=True)

        uv = np.zeros((size, size), dtype=np.complex128)
        uv_cov = np.zeros((size, size), dtype=np.complex128)

        # phase center
        srclist, cutoff, catalogs = a.scripting.parse_srcs(phase_center, catalog)
        cat = a.src.get_catalog(srclist, cutoff, catalogs)
        assert(len(cat) == 1), 'Allow only one phase center'
        s = cat.values()[0]
        if mpiutil.rank0:
            print 'Imaging relative to phase center %s.' % phase_center

        # pointting vector in topocentric coord
        pt_top = a.coord.azalt2top((az, alt))

        # array
        aa = tldishes.get_aa(1.0e-3 * freq) # use GHz
        nlt = len(lt_inds)
        for ti, t_ind in enumerate(lt_inds): # mpi among time
            if mpiutil.rank0:
                print '%d of %d...' % (ti, nlt)
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

            efactor = np.exp(7 * (l0**2 + m0**2) / (4 * sigma**2))

            for bl_ind in range(nbls):
                i, j = bls[bl_ind]
                if i == j:
                    continue
                us, vs, ws = aa.gen_uvw(i-1, j-1, src=s) # NOTE start from 0
                for fi, (u, v) in enumerate(zip(us.flat, vs.flat)):
                    val = local_data[ti, bl_ind, pols.index(pol), fi]
                    if np.isfinite(val):
                        # up = np.int(u / res)
                        # vp = np.int(v / res)
                        # uc, vc = center + up, center + vp
                        # ulb, uub = uc - conv_pixel, uc + conv_pixel + 1
                        # vlb, vub = vc - conv_pixel, vc + conv_pixel + 1
                        # uc1, vc1 = center - up, center - vp
                        # ulb1, uub1 = uc1 - conv_pixel, uc1 + conv_pixel + 1
                        # vlb1, vub1 = vc1 - conv_pixel, vc1 + conv_pixel + 1

                        # # uv_cov[center+vp, center+up] += 1.0
                        # # uv_cov[center-vp, center-up] += 1.0 # append conjugate
                        # # uv[center+vp, center+up] += val
                        # # uv[center-vp, center-up] += np.conj(val)# append conjugate
                        # conv_gauss(uv_cov, center, vp, up, sigma, 1.0, l0, m0, res)
                        # conv_gauss(uv, center, vp, up, sigma, val, l0, m0, res)

                        # tmp1 = efactor * conv_factor(u_axis, v_axis, up, vp, sigma, l0, m0)
                        # tmp2 = efactor * conv_factor(u_axis, v_axis, -up, -vp, sigma, l0, m0)
                        tmp1 = efactor * conv_kernal(u_axis, v_axis, u, v, sigma, l0, m0)
                        tmp2 = efactor * conv_kernal(u_axis, v_axis, -u, -v, sigma, l0, m0)
                        uv_cov += 1.0 * tmp1
                        uv_cov += 1.0 * tmp2
                        uv += val * tmp1
                        uv += np.conj(val) * tmp2

                        # tmp1 = efactor * conv_kernal(u_axis[ulb:uub], v_axis[vlb:vub], u, v, sigma, l0, m0)
                        # tmp2 = efactor * conv_kernal(u_axis[ulb1:uub1], v_axis[vlb1:vub1], -u, -v, sigma, l0, m0)
                        # uv_cov[vlb:vub, ulb:uub] += 1.0 * tmp1
                        # uv_cov[vlb1:vub1, ulb1:uub1] += 1.0 * tmp2
                        # uv[vlb:vub, ulb:uub] += val * tmp1
                        # uv[vlb1:vub1, ulb1:uub1] += np.conj(val) * tmp2


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

            cen = ephem.Equatorial(s.ra, s.dec, epoch=aa.epoch)
            # We precess the coordinates of the center of the image here to
            # J2000, just to have a well-defined epoch for them.  For image coords to
            # be accurately reconstructed, precession needs to be applied per pixel
            # and not just per phase-center because ra/dec axes aren't necessarily
            # aligned between epochs.  When reading these images, to be 100% accurate,
            # one should precess the ra/dec coordinates back to the date of the
            # observation, infer the coordinates of all the pixels, and then
            # precess the coordinates for each pixel independently.
            cen = ephem.Equatorial(cen, epoch=ephem.J2000)

            # save data
            with h5py.File(output_file, 'w') as f:
                f.create_dataset('uv_cov', data=uv_cov)
                f.create_dataset('uv', data=uv)
                f.create_dataset('uv_cov_fft', data=uv_cov_fft)
                f.create_dataset('uv_fft', data=uv_fft)
                f.create_dataset('uv_imag_fft', data=uv_imag_fft)
                f.attrs['pol'] = pol
                f.attrs['max_wl'] = max_wl
                f.attrs['max_lm'] = max_lm
                f.attrs['src_name'] = s.src_name
                f.attrs['obs_date'] = start_time
                f.attrs['ra'] = np.degrees(cen.ra)
                f.attrs['dec'] = np.degrees(cen.dec)
                f.attrs['epoch'] = 'J2000'
                f.attrs['d_ra'] = np.degrees(2.0 * max_lm / size)
                f.attrs['d_dec'] = np.degrees(2.0 * max_lm / size)
                f.attrs['freq'] = freq[nfreq/2]
                f.attrs['history'] = history + self.history
