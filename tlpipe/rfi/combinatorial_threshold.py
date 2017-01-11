import abc
import numpy as np
from winsorized_stats import winsorized_mean_and_std, winsorized_mode


class CombinatorialThreshold(object):

    def __init__(self, time_freq_vis, time_freq_vis_mask=None, first_threshold=6.0, exp_factor=1.5, distribution='Rayleigh', max_threshold_length=1024):

        self.vis = np.abs(time_freq_vis) # fit for only the amplitude

        if time_freq_vis_mask is None:
            self.vis_mask = np.where(np.isfinite(self.vis), False, True)
        elif self.vis.shape == time_freq_vis_mask.shape:
            self.vis_mask = time_freq_vis_mask.astype(np.bool)
        else:
            raise ValueError('Invalid time_freq_vis_mask')

        max_log2_length = np.int(np.ceil(np.log2(max_threshold_length)))
        self.lengths = np.array([ 2**i for i in xrange(max_log2_length) ])

        if distribution in ('Uniform', 'Gaussian', 'Rayleigh'):
            self.distribution = distribution
        else:
            raise ValueError('Invalid noise distribution %s' % distribution)

        if first_threshold is None:
            self.init_threshold_with_flase_rate(resolution, false_alarm_rate)
        else:
            self.thresholds = first_threshold / exp_factor**(np.log2(self.lengths))
            # self.thresholds = first_threshold * exp_factor**(np.log2(self.lengths)) / self.lengths # used in aoflagger

    def init_threshold_with_flase_rate(self, resolution, false_alarm_rate):
        raise NotImplementedError('Not implemented yet')


    @abc.abstractmethod
    def execute_threshold(self, factor):
        return

    def execute(self, sensitivity=1.0):

        if self.distribution == 'Gaussian':
            mean, std = winsorized_mean_and_std(np.ma.array(self.vis, mask=self.vis_mask))
            factor = sensitivity if std == 0.0 else std * sensitivity
        elif self.distribution == 'Rayleigh':
            mode = winsorized_mode(np.ma.array(self.vis, mask=self.vis_mask))
            factor = sensitivity if mode == 0.0 else mode * sensitivity
        else:
            factor = sensitivity

        self.execute_threshold(factor)