"""Phase the source-phased visibility data to the zenith, this is just the inverse operation of phs2src.py."""

import numpy as np
import ephem
import aipy as a
import tod_task
import timestream

from caput import mpiutil
from tlpipe.core import tldishes
from tlpipe.utils.date_util import get_ephdate


def phs(vis, li, gi, tbl, **kwargs):
    t = tbl[0]
    ai, aj = tbl[1]
    aa = kwargs.get('aa')
    s = kwargs.get('s')

    aa.set_jultime(t)
    s.compute(aa)
    # get fluxes vs. freq of the calibrator
    # Sc = s.get_jys()
    # get the topocentric coordinate of the calibrator at the current time
    s_top = s.get_crds('top', ncrd=3)
    # aa.sim_cache(cat.get_crds('eq', ncrd=3)) # for compute bm_response and sim
    uij = aa.gen_uvw(ai-1, aj-1, src='z').squeeze() # (rj - ri)/lambda

    return vis * np.exp(-2.0J * np.pi * np.dot(s_top, uij))[:, np.newaxis]
    # return vis * np.exp(2.0J * np.pi * np.dot(s_top, uij))[:, np.newaxis]


class Phs2zen(tod_task.SingleTimestream):
    """Phase the source-phased visibility data to the zenith, this is just the inverse operation of phs2src.py."""

    params_init = {
                    'source': 'cas', # <src_name> or <ra XX[:XX:xx]>_<dec XX[:XX:xx]> or <time y/m/d h:m:s> (array pointing of this local time)
                    'catalog': 'misc,helm,nvss',
                  }

    prefix = 'p2z_'

    def process(self, ts):

        source = self.params['source']
        catalog = self.params['catalog']

        if 'Dish' in ts.attrs['telescope']:
            ant_type = 'dish'
        elif 'Cylinder' in ts.attrs['telescope']:
            ant_type = 'cylinder'
        else:
            raise RuntimeError('Unknown antenna type %s' % ts.attrs['telescope'])

        feedno = ts['feedno'][:].tolist()
        antpointing = ts['antpointing'][-1, :, :]

        ts.redistribute(0) # make time the dist axis

        # array
        if ant_type == 'dish':
            aa = tldishes.get_aa(1.0e-3 * ts.freq[:]) # use GHz
            # make all antennas point to the pointing direction
            for ind, ai in enumerate(aa):
                if ind in feedno:
                    fi = feedno.index(ind)
                    ai.set_pointing(az=antpointing[fi, 0], alt=antpointing[fi, 1], twist=0)
        else:
            raise NotImplementedError('phs2zen for cylinder array not implemented yet')

        try:
            # convert an observing time to the ra_dec of the array pointing of that time
            src_time = get_ephdate(source, tzone=ts.attrs['timezone']) # utc time
            aa.date = str(ephem.Date(src_time)) # utc time
            # print 'date:', aa.date
            azs = antpointing[:, 0]
            alts = antpointing[:, 1]
            if np.allclose(azs, azs[0]) and np.allclose(alts, alts[0]):
                az = azs[0]
                alt = alts[0]
            else:
                raise ValueError('Antennas do not have a common pointing')
            az, alt = ephem.degrees(az), ephem.degrees(alt)
            src_ra, src_dec = aa.radec_of(az, alt)
            source = '%s_%s' % (src_ra, src_dec)
            # print 'source:', source
        except ValueError:
            pass

        # source
        srclist, cutoff, catalogs = a.scripting.parse_srcs(source, catalog)
        cat = a.src.get_catalog(srclist, cutoff, catalogs)
        assert(len(cat) == 1), 'Allow only one source'
        s = cat.values()[0]
        if mpiutil.rank0:
            print 'Undo the source-phase %s to phase to the zenith.' % source


        ts.data_operate(phs, op_axis=('time', 'baseline'), axis_vals=(ts.time, ts.bl), aa=aa, s=s)

        ts.add_history(self.history)

        return ts
