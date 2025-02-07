"""ECLIPSR

This module contains utility functions for data processing, unit conversions
and some functions specific to TESS data.

Code written by: Luc IJspeert
"""

import os
import datetime
import warnings
import h5py
import numpy as np
import numba as nb


@nb.njit(cache=True)
def fold_time_series(times, period, zero):
    """Fold the given time series over the orbital period to get the phases

    Parameters
    ----------
    times: numpy.ndarray[float]
        Timestamps of the time series
    period: float
        Orbital period with which the time series is folded
    zero: float
        Reference zero point in time when the phase equals zero

    Returns
    -------
    phases: numpy.ndarray[float]
        Phase array for all timestamps. Phases are between -0.5 and 0.5
    """
    phases = ((times - zero) / period + 0.5) % 1 - 0.5
    return phases


@nb.njit(cache=True)
def runs_test(signal):
    """Bradley, (1968). Distribution-Free Statistical Tests, Chapter 12.
    To test a signal for its 'randomness'.

    Parameters
    ----------
    signal : numpy.ndarray[float]
        The input signal to be tested

    Returns
    -------
    z: float
        Outcome of the test:
        - 0: As many zero crossings as expected from a random signal
        - 1: More zero crossings than expected by 1 sigma (2 = 2 sigma etc.)
        - -1: Less zero crossings than expected by 1 sigma
        The absolute value represents the certainty level in sigma that
        the input signal is not random.

    Notes
    -----
    The number of zero crossings equals the number of runs minus one.
    See: https://www.itl.nist.gov/div898/handbook/eda/section3/eda35d.htm
    """
    signal_above = (signal > 0).astype(np.int_)
    n_tot = len(signal)
    n_a = max(1, np.sum(signal_above))  # number above zero (make sure it doesn't become zero)
    n_b = max(1, n_tot - n_a)  # number below zero
    r = np.sum(np.abs(signal_above[1:] - signal_above[:-1])) + 1
    # expected number of or runs in a random time series
    r_exp = 2 * n_a * n_b / n_tot + 1
    # standard deviation of the number of runs
    s_r = 2 * n_a * n_b * (2 * n_a * n_b - n_tot) / (n_tot**2 * (n_tot - 1))
    z = (r - r_exp) / s_r
    return z


@nb.njit(cache=True)
def normalise_counts(flux_counts, flux_counts_err=None, i_sectors=None):
    """Median-normalise the flux

    Parameters
    ----------
    flux_counts : numpy.ndarray[float]
        The flux to be normalised (counts or otherwise, should be positive)
    flux_counts_err : numpy.ndarray[float], optional
        The errors in the flux counts, if available
    i_sectors : numpy.ndarray[int], optional
        Indices representing sectors.
        If provided, the signal is processed per sector.

    Returns
    -------
    flux_norm : numpy.ndarray[float]
        Normalised flux counts. The result is positive and varies around one.
    flux_err_norm : numpy.ndarray[float], optional
        Normalised errors in the flux counts, if flux_counts_err is provided.
    """
    if i_sectors is None:
        median = np.median(flux_counts)
        flux_norm = flux_counts / median
        if flux_counts_err is not None:
            flux_err_norm = flux_counts_err / median
    else:
        median = np.zeros(len(i_sectors))
        flux_norm = np.zeros(len(flux_counts))
        flux_err_norm = np.zeros(len(flux_counts))
        for i, s in enumerate(i_sectors):
            median[i] = np.median(flux_counts[s[0]:s[1]])
            flux_norm[s[0]:s[1]] = flux_counts[s[0]:s[1]] / median[i]
            if flux_counts_err is not None:
                flux_err_norm[s[0]:s[1]] = flux_counts_err[s[0]:s[1]] / median[i]
    return flux_norm, flux_err_norm


@nb.njit(cache=True)
def mn_to_ppm(mn_flux):
    """Converts median normalised flux to parts per million.

    Parameters
    ----------
    mn_flux: numpy.ndarray[float]
        Median normalised flux values. Assumed to vary around one.

    Returns
    -------
    ppm_flux: numpy.ndarray[float]
        Parts per million flux values. Varies around zero.
    """
    ppm_flux = (mn_flux - 1) * 1e6
    return ppm_flux


@nb.njit(cache=True)
def ppm_to_mn(flux_ppm):
    """Converts from parts per million to median normalised flux.

    Parameters
    ----------
    flux_ppm : numpy.ndarray[float]
        Parts per million flux values. Assumed to vary around zero.

    Returns
    -------
    mn_flux : numpy.ndarray[float]
        Median normalised flux values. Varies around one.
    """
    mn_flux = (flux_ppm / 1e6) + 1
    return mn_flux


# @nb.njit(cache=True)  (slowed down by jit)
def mn_to_mag(mn_flux):
    """Converts from median normalised flux to magnitude (relative).

    Parameters
    ----------
    mn_flux: numpy.ndarray[float]
        Median normalised flux values. Assumed to vary around one.

    Returns
    -------
    mag: numpy.ndarray[float]
        Relative magnitude values. Varies around zero.
    """
    mag = -2.5 * np.log10(mn_flux)
    return mag


@nb.njit(cache=True)  # (not sped up significantly by jit)
def mag_to_mn(mag):
    """Converts from magnitude (varying around zero) to median normalised flux.

    Parameters
    ----------
    mag: numpy.ndarray[float]
        Magnitude values. Assumed to vary around zero.

    Returns
    -------
    mn_flux: numpy.ndarray[float]
        Median normalised flux values. Varies around one.
    """
    mn_flux = ppm_to_mn(10**(-0.4 * mag))
    return mn_flux


def get_tess_sectors(times, bjd_ref=2457000.0):
    """Load the times of the TESS sectors from a file and return a set of
    indices indicating the separate sectors in the time series.

    Parameters
    ----------
    times: numpy.ndarray[float]
        Timestamps of the time series
    bjd_ref: float, optional
        Barycentric Julian Date (BJD) reference date for the data.
        Default is 2457000.0.

    Returns
    -------
    i_sectors: numpy.ndarray[int]
        Set of indices indicating the separate sectors in the time series.

    Notes
    -----
    Make sure to use the appropriate BJD reference date for your data.
    Handy link: https://archive.stsci.edu/tess/tess_drn.html
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))  # absolute dir the script is in
    data_dir = os.path.join(script_dir, 'data')
    jd_sectors = np.loadtxt(os.path.join(data_dir, 'tess_sectors.dat'), usecols=(2, 3)) - bjd_ref
    # use a quick searchsorted to get the positions of the sector transitions
    i_start = np.searchsorted(times, jd_sectors[:, 0])
    i_end = np.searchsorted(times, jd_sectors[:, 1])
    sectors_included = (i_start != i_end)  # this tells which sectors it received data for
    i_sectors = np.column_stack([i_start[sectors_included], i_end[sectors_included]])
    return i_sectors


@nb.njit(cache=True)
def remove_outliers(signal):
    """Removes outliers in the signal

    Parameters
    ----------
    signal: numpy.ndarray[float]
        Median-normalized signal.

    Returns
    -------
    numpy.ndarray[bool]
        Boolean mask indicating outliers (as False)

    Notes
    -----
    Removes outliers in the signal that are more than 4 standard deviations
    higher or lower than the median (=1, signal needs to be median normalised!),
    but only if both points adjacent to the anomaly are themselves not anomalous.
    """
    thr_mask = np.ones(len(signal), dtype=np.bool_)
    indices = np.arange(len(signal))
    m_s_std = np.std(signal)
    # check for anomalously high points
    high_bool = (signal > 1 + 4 * m_s_std)
    not_high_left = np.invert(np.append(high_bool[1:], [False]))
    not_high_right = np.invert(np.append([False], high_bool[:-1]))
    high_p = indices[high_bool & not_high_left & not_high_right]
    if (len(high_p) > 0):
        thr_mask[high_p] = False
    # check for anomalously low points
    low_bool = (signal < 1 - 4 * m_s_std)
    not_low_left = np.invert(np.append(low_bool[1:], [False]))
    not_low_right = np.invert(np.append([False], low_bool[:-1]))
    low_p = indices[low_bool & not_low_left & not_low_right]
    if (len(low_p) > 0):
        thr_mask[low_p] = False
    return thr_mask


@nb.njit(cache=True)
def rescale_tess(times, signal, i_sectors):
    """Scales different TESS sectors by a constant to make them match in amplitude.
    times are in TESS bjd by default, but a different bjd_ref can be given to use
    a different time reference point.
    This rescaling will make sure the rest of eclipse finding goes as intended.

    Parameters
    ----------
    times: numpy.ndarray[float]
        Timestamps of the time series
    signal: numpy.ndarray[float]
        Measurement values of the time series
    i_sectors: numpy.ndarray[int]
        Indices indicating the separate sectors in the time series

    Returns
    -------
    numpy.ndarray[float]
        Rescaled signal
    numpy.ndarray[bool]
        Boolean mask indicating outliers
    """
    signal_copy = np.copy(signal)
    thr_mask = np.ones(len(times), dtype=np.bool_)
    # determine the range of the signal
    low = np.zeros(len(i_sectors))
    high = np.zeros(len(i_sectors))
    averages = np.zeros(len(i_sectors))
    threshold = np.zeros(len(i_sectors))
    for i, s in enumerate(i_sectors):
        masked_s = signal[s[0]:s[1]]
        if (len(masked_s) < 4):
            # not enough data
            threshold[i] = 0.9 * np.min(masked_s)
            continue
        # find the upper and lower representative levels
        threshold[i] = np.max(masked_s) + 1  # make sure the loop is entered at least once
        while (not np.any(masked_s > threshold[i])) & (len(masked_s) > 4):
            # we might have an outlier, so redo all if condition not met
            masked_s = np.delete(masked_s, np.argmax(masked_s))
            averages[i] = np.mean(masked_s)
            low[i] = np.mean(masked_s[masked_s < averages[i]])
            high[i] = np.mean(masked_s[masked_s > averages[i]])
            while (not np.any(masked_s > high[i])) & (len(masked_s) > 4):
                # same goes here, might have an outlier
                masked_s = np.delete(masked_s, np.argmax(masked_s))
                averages[i] = np.mean(masked_s)
                low[i] = np.mean(masked_s[masked_s < averages[i]])
                high[i] = np.mean(masked_s[masked_s > averages[i]])
            if np.any(masked_s > high[i]):
                threshold[i] = np.mean(masked_s[masked_s > high[i]])
            else:
                break
        if (len(masked_s) < 4):
            threshold[i] = 0.9 * np.min(masked_s)  # not enough data left
        elif not np.any(masked_s > threshold[i]):
            continue
        else:
            threshold[i] = np.mean(masked_s[masked_s > threshold[i]])
    
    difference = high - low
    if np.any(difference != 0):
        min_diff = np.min(difference[difference != 0])
    else:
        min_diff = 0
    threshold = 3 * threshold - averages  # to remove spikes (from e.g. momentum dumps)
    # adjust the signal so that it has a more uniform range (and reject (mask) upward outliers)
    for i, s in enumerate(i_sectors):
        signal_copy[s[0]:s[1]] = (signal[s[0]:s[1]] - averages[i]) / difference[i] * min_diff + averages[i]
        thr_mask[s[0]:s[1]] &= (signal[s[0]:s[1]] < threshold[i])
    return signal_copy, thr_mask


def check_constant(signal):
    """Does a simple check to see if the signal is worth while processing further.

    Parameters
    ----------
    signal: numpy.ndarray[float]
        Measurement values of the time series, must be median normalised

    Returns
    -------
    not_constant: bool
        True if the signal is deemed not constant

    Notes
    -----
    The 10th percentile of the signal centered around zero is compared to the
    10th percentile of the point-to-point differences.
    """
    low = 1 - np.percentile(signal, 10)
    low_diff = abs(np.percentile(np.diff(signal), 10))
    not_constant = (low < low_diff)
    return not_constant


def ingest_signal(times, signal, signal_err=None, tess_sectors=True, quality=None):
    """Take a signal and process it for ingest into the algorithm.

    Parameters
    ----------
    times: numpy.ndarray[float]
        Timestamps of the signal
    signal: numpy.ndarray[float]
        Measurement values of the signal
    signal_err: numpy.ndarray[float], optional
        Error values associated with the signal measurements
    tess_sectors: bool, optional
        Whether to handle each TESS sector separately
    quality: numpy.ndarray[bool], optional
        Boolean mask indicating the quality of data points

    Returns
    -------
    times: numpy.ndarray[float]
        Processed timestamps
    signal: numpy.ndarray[float]
        Processed signal measurements
    signal_err: numpy.ndarray[float]
        Processed error values associated with the signal measurements

    Notes
    -----
    The signal (raw counts or ppm) will be median normalised
    after the removal of non-finite values.
    [Note] signal must not be mean subtracted (or otherwise negative)!
    If your signal is already 'clean' and normalised to vary around 1,
    skip this function.

    If tess_sectors is True, each sector is handled separately and
    the signal will be rescaled for more consistent eclipse depths across sectors.
    The separate sectors are also rescaled to better match in amplitude.

    Boolean quality flags can be provided as a mask for the light curve
    (Data points with True are kept).
    The light curve is also sorted before further processing.
    """
    if signal_err is None:
        signal_err = np.ones(len(times))
    if quality is not None:
        times = times[quality]
        signal = signal[quality]
        signal_err = signal_err[quality]
    sorter = np.argsort(times)
    times = times[sorter]
    signal = signal[sorter]
    signal_err = signal_err[sorter]
    finites = np.isfinite(signal) & np.isfinite(signal_err)
    times = times[finites].astype(np.float_)
    signal = signal[finites].astype(np.float_)
    signal_err = signal_err[finites].astype(np.float_)
    
    if (len(times) < 10):
        warnings.warn('given signal does not contain enough finite values.')
        return np.zeros(0), np.zeros(0), np.zeros(0)
    if tess_sectors:
        i_sectors = get_tess_sectors(times)
        if (len(i_sectors) == 0):
            warnings.warn('given times do not fall into any TESS sectors. '
                          'Set tess_sectors=False or change the reference BJD.')
            signal, signal_err = normalise_counts(signal, flux_counts_err=signal_err)
            outlier_mask = remove_outliers(signal)
            times = times[outlier_mask]
            signal = signal[outlier_mask]
            signal_err = signal_err[outlier_mask]
        else:
            # rescale the different TESS sectors for more consistent amplitude and better operation
            signal, thr_mask = rescale_tess(times, signal, i_sectors)
            # remove any other upward outliers
            times = times[thr_mask]
            signal = signal[thr_mask]
            signal_err = signal_err[thr_mask]
            # normalise
            signal, signal_err = normalise_counts(signal, flux_counts_err=signal_err, i_sectors=i_sectors)
            outlier_mask = remove_outliers(signal)
            times = times[outlier_mask]
            signal = signal[outlier_mask]
            signal_err = signal_err[outlier_mask]

    else:
        signal, signal_err = normalise_counts(signal, flux_counts_err=signal_err)
    return times, signal, signal_err


def save_results(results, file_name, identifier='none', overwrite=False):
    """Save the full output of the find_eclipses function to an hdf5 file.

    Parameters
    ----------
    results: tuple
        A tuple containing the results from the `find_eclipses` function.
        The tuple should contain:
            - t_0: float
            - period: float
            - score: float
            - features: numpy.ndarray[float]
            - sine_like: bool
            - wide: bool
            - n_kernel: int
            - width_stats: numpy.ndarray[float]
            - depth_stats: numpy.ndarray[float]
            - ecl_mid: numpy.ndarray[float]
            - widths: numpy.ndarray[float]
            - depths: numpy.ndarray[float]
            - ratios: numpy.ndarray[float]
            - added_snr: numpy.ndarray[float]
            - ecl_indices: numpy.ndarray[int]
            - flags_lrf: numpy.ndarray[int]
            - flags_pst: numpy.ndarray[int]
    file_name: str
        The name of the HDF5 file to save the results to
    identifier: str, optional
        An identifier to be inserted into the hdf5 file
    overwrite: bool
        Whether to respect an existing file or overwrite it

    Returns
    -------
    None
    """
    # unpack all the variables
    t_0, period, score, features, sine_like, wide, n_kernel, width_stats, depth_stats, \
        ecl_mid, widths, depths, ratios, added_snr, ecl_indices, flags_lrf, flags_pst = results
    # check some input
    if not file_name.endswith('.hdf5'):
        file_name += '.hdf5'
    # create the file
    mode = 'w-'
    if overwrite:
        mode = 'w'
    with h5py.File(file_name, mode) as file:
        file.attrs['identifier'] = identifier
        file.attrs['date_time'] = str(datetime.datetime.now())
        file.attrs['t_0'] = t_0
        file.attrs['period'] = period
        file.attrs['score'] = score
        file.attrs['features'] = features
        file.attrs['sine_like'] = sine_like
        file.attrs['wide'] = wide
        file.attrs['n_kernel'] = n_kernel
        file.attrs['width_stats'] = width_stats
        file.attrs['depth_stats'] = depth_stats
        file.create_dataset('ecl_mid', data=ecl_mid)
        file.create_dataset('widths', data=widths)
        file.create_dataset('depths', data=depths)
        file.create_dataset('ratios', data=ratios)
        file.create_dataset('added_snr', data=added_snr)
        file.create_dataset('ecl_indices', data=ecl_indices)
        file.create_dataset('flags_lrf', data=flags_lrf)
        file.create_dataset('flags_pst', data=flags_pst)
    return None


def load_results(file_name):
    """Load the full output of the find_eclipses function from the hdf5 file.

    Parameters
    ----------
    file_name: str
        The name of the HDF5 file containing the results

    Returns
    -------
    file: h5py.File
        An HDF5 file object containing the results.
        Has to be closed by the user (file.close())
    """
    file = h5py.File(file_name, 'r')
    return file
    

def read_results(file_name, verbose=False):
    """Read the full output of the find_eclipses function from the hdf5 file.

    Parameters
    ----------
    file_name: str
        The name of the HDF5 file to read the results from
    verbose: bool, optional
        If True, prints information about the opened file

    Returns
    -------
    tuple
        A tuple containing the variables as they appear in eclipsr:
            - t_0: float
            - period: float
            - score: float
            - features: numpy.ndarray[float]
            - sine_like: bool
            - wide: bool
            - n_kernel: int
            - width_stats: numpy.ndarray[float]
            - depth_stats: numpy.ndarray[float]
            - ecl_mid: numpy.ndarray[float]
            - widths: numpy.ndarray[float]
            - depths: numpy.ndarray[float]
            - ratios: numpy.ndarray[float]
            - added_snr: numpy.ndarray[float]
            - ecl_indices: numpy.ndarray[int]
            - flags_lrf: numpy.ndarray[int]
            - flags_pst: numpy.ndarray[int]

    Notes
    -----
    Returns the set of variables as they appear in eclipsr and closes the file.
    This function closes the HDF5 file after reading.
    """
    # check some input
    if not file_name.endswith('.hdf5'):
        file_name += '.hdf5'
    # open the file
    with h5py.File(file_name, 'r') as file:
        identifier = file.attrs['identifier']
        date_time = file.attrs['date_time']
        t_0 = file.attrs['t_0']
        period = file.attrs['period']
        try:
            score = file.attrs['score']
        except KeyError:
            score = file.attrs['confidence']  # for backward compatibility
        try:
            features = file.attrs['features']
        except KeyError:
            features = np.zeros(6)  # for backward compatibility
        sine_like = file.attrs['sine_like']
        wide = file.attrs['wide']
        n_kernel = file.attrs['n_kernel']
        width_stats = file.attrs['width_stats']
        depth_stats = file.attrs['depth_stats']
        ecl_mid = np.copy(file['ecl_mid'])
        widths = np.copy(file['widths'])
        depths = np.copy(file['depths'])
        ratios = np.copy(file['ratios'])
        added_snr = np.copy(file['added_snr'])
        ecl_indices = np.copy(file['ecl_indices'])
        flags_lrf = np.copy(file['flags_lrf'])
        flags_pst = np.copy(file['flags_pst'])
    
    if verbose:
        print(f'Opened eclipsr file with identifier: {identifier}, created on {date_time}')
    return t_0, period, score, features, sine_like, wide, n_kernel, width_stats, depth_stats, \
        ecl_mid, widths, depths, ratios, added_snr, ecl_indices, flags_lrf, flags_pst
