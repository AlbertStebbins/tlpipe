"""SVD RFI flagging by throwing out given number of eigenmodes. This is the first stage, which just doing the SVD decomposition."""

try:
    import cPickle as pickle
except ImportError:
    import pickle

import numpy as np
from scipy import linalg
import h5py

from tlpipe.utils import mpiutil
from tlpipe.core.base_exe import Base
from tlpipe.utils.path_util import input_path, output_path


# Define a dictionary with keys the names of parameters to be read from
# file and values the defaults.
params_init = {
               'nprocs': mpiutil.size, # number of processes to run this module
               'aprocs': range(mpiutil.size), # list of active process rank no.
               'input_file': 'data_cal.hdf5',
               'output_file': 'svd_rfi.hdf5',
               'extra_history': '',
              }
prefix = 'svr1_'



class RfiFlag(Base):
    """SVD RFI flagging by throwing out given number of eigenmodes. This is the first stage, which just doing the SVD decomposition."""

    def __init__(self, parameter_file_or_dict=None, feedback=2):

        super(RfiFlag, self).__init__(parameter_file_or_dict, params_init, prefix, feedback)

    def execute(self):

        input_file = input_path(self.params['input_file'])
        output_file = output_path(self.params['output_file'])

        # with h5py.File(input_file, 'r', driver='mpio', comm=self.comm) as f:
        with h5py.File(input_file, 'r') as f:
            dset = f['data']
            data_type = dset.dtype
            nt, nbls, npol, nfreq = dset.shape

            if mpiutil.rank0:
                with h5py.File(output_file, 'w') as fout:
                    nK = min(nt, nbls*nfreq)
                    out_dset = fout.create_dataset('U', (npol, nt, nK), dtype=data_type)
                    fout.create_dataset('s', (npol, nK,), dtype=np.float64)
                    fout.create_dataset('Vh', (npol, nK, nbls*nfreq), dtype=data_type)
                    # copy metadata from input file
                    fout.create_dataset('time', data=f['time'])
                    for attrs_name, attrs_value in dset.attrs.iteritems():
                        out_dset.attrs[attrs_name] = attrs_value
                    # update some attrs
                    out_dset.attrs['history'] = out_dset.attrs['history'] + self.history

            mpiutil.barrier(comm=self.comm)

            # parallel hdf5 can not write data > 2GB now, so...
            with h5py.File(output_file, 'r+') as fout:
                for pol_ind in mpiutil.mpirange(npol, comm=self.comm): # mpi among pols
                    data_slice = dset[:, :, pol_ind, :].reshape(nt, -1)
                    data_slice = np.where(np.isnan(data_slice), 0, data_slice)
                    U, s, Vh = linalg.svd(data_slice, full_matrices=False, overwrite_a=True)

                    fout['U'][pol_ind, :, :] = U
                    fout['s'][pol_ind, :] = s
                    fout['Vh'][pol_ind, :, :] = Vh

                mpiutil.barrier(comm=self.comm)

