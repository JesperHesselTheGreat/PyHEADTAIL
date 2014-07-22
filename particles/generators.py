'''
@file matching
@author Kevin Li, Adrian Oeftiger
@date February 2014
@brief Module for matching transverse and longitudinal distributions
@copyright CERN
'''
from __future__ import division

from abc import ABCMeta, abstractmethod

import numpy as np
from numpy.random import RandomState

from scipy.constants import c, e
from scipy.interpolate import interp2d
from scipy.integrate import quad, dblquad, cumtrapz, romb

import pylab as plt


class PhaseSpace(object):
    """Knows how to distribute particle coordinates for a beam
    according to certain distribution functions.
    """

    __metaclass__ = ABCMeta

    @abstractmethod
    def generate(self, beam):
        """Creates the beam macroparticles according to a
        distribution function (depends on the implementing class).
        """
        pass


class GaussianX(PhaseSpace):
    """Horizontal Gaussian particle phase space distribution."""

    def __init__(self, sigma_x, sigma_xp, generator_seed=None):
        """Initiates the horizontal beam coordinates
        to the given Gaussian shape.
        """
        self.sigma_x  = sigma_x
        self.sigma_xp = sigma_xp

        self.random_state = RandomState()
        self.random_state.seed(generator_seed)

    @classmethod
    def from_optics(cls, alpha_x, beta_x, epsn_x, betagamma, generator_seed=None):
        """Initialise GaussianX from the given optics functions.
        beta_x is given in meters and epsn_x in micrometers.
        """

        sigma_x  = np.sqrt(beta_x * epsn_x * 1e-6 / betagamma)
        sigma_xp = sigma_x / beta_x

        return cls(sigma_x, sigma_xp, generator_seed)

    def generate(self, beam):
        beam.x = self.sigma_x * self.random_state.randn(beam.n_macroparticles)
        beam.xp = self.sigma_xp * self.random_state.randn(beam.n_macroparticles)


class GaussianY(PhaseSpace):
    """Vertical Gaussian particle phase space distribution."""

    def __init__(self, sigma_y, sigma_yp, generator_seed=None):
        """Initiates the vertical beam coordinates
        to the given Gaussian shape.
        """
        self.sigma_y  = sigma_y
        self.sigma_yp = sigma_yp

        self.random_state = RandomState()
        self.random_state.seed(generator_seed)

    @classmethod
    def from_optics(cls, alpha_y, beta_y, epsn_y, betagamma, generator_seed=None):
        """Initialise GaussianY from the given optics functions.
        beta_y is given in meters and epsn_y in micrometers.
        """

        sigma_y  = np.sqrt(beta_y * epsn_y * 1e-6 / betagamma)
        sigma_yp = sigma_y / beta_y

        return cls(sigma_y, sigma_yp, generator_seed)

    def generate(self, beam):
        beam.y = self.sigma_y * self.random_state.randn(beam.n_macroparticles)
        beam.yp = self.sigma_yp * self.random_state.randn(beam.n_macroparticles)


class GaussianZ(PhaseSpace):
    """Longitudinal Gaussian particle phase space distribution."""

    def __init__(self, sigma_z, sigma_dp, is_accepted = None, generator_seed=None):
        """Initiates the longitudinal beam coordinates to a given
        Gaussian shape. If the argument is_accepted is set to
        the is_in_separatrix(z, dp, beam) method of a RFSystems
        object (or similar), macroparticles will be initialised
        until is_accepted returns True.
        """
        self.sigma_z = sigma_z
        self.sigma_dp = sigma_dp
        self.is_accepted = is_accepted

        self.random_state = RandomState()
        self.random_state.seed(generator_seed)

    @classmethod
    def from_optics(cls, beta_z, epsn_z, p0, is_accepted = None,
                    generator_seed=None):
        """Initialise GaussianZ from the given optics functions.
        For the argument is_accepted see __init__.
        """

        sigma_z = np.sqrt(beta_z*epsn_z/(4*np.pi) * e/p0)
        sigma_dp = sigma_z / beta_z

        return cls(sigma_z, sigma_dp, is_accepted, generator_seed)

    def generate(self, beam):
        beam.z = self.sigma_z * self.random_state.randn(beam.n_macroparticles)
        beam.dp = self.sigma_dp * self.random_state.randn(beam.n_macroparticles)
        if self.is_accepted:
            self._redistribute(beam)

    def _redistribute(self, beam):
        n = beam.n_macroparticles
        for i in xrange(n):
            while not self.is_accepted(beam.z[i], beam.dp[i], beam):
                beam.z[i]  = self.sigma_z * self.random_state.randn(n)
                beam.dp[i] = self.sigma_dp * self.random_state.randn(n)


class RFBucket(PhaseSpace):

    def __init__(self, sigma_z, rfsystem):

        self.sigma_z = sigma_z

        self.circumference = rfsystem.circumference
        self.hamiltonian = rfsystem.hamiltonian
        self.separatrix = rfsystem.separatrix
        self.z_extrema = rfsystem.z_extrema
        self.z_sep, self.p_sep = rfsystem.z_sep, rfsystem.p_sep
        self.H0 = rfsystem.H0

        self._compute_std = self._compute_std_cumtrapz

    @profile
    def _test_maximum_std(self, psi, sigma):

        # Test for maximum bunch length
        psi.H0 = self.H0(self.circumference)

        zS = self._compute_std_quad(psi.function, self.separatrix, self.z_sep[0], self.z_sep[1])
        print "\n--> Maximum rms bunch length in bucket:", zS, " m.\n"
        if sigma > zS * 0.95:
            print "\n*** WARNING! Bunch appears to be too long for bucket!\n"

        zS = self._compute_std_cumtrapz(psi.function, self.separatrix, self.z_sep[0], self.z_sep[1])
        print "\n--> Maximum rms bunch length in bucket:", zS, " m.\n"
        if sigma > zS * 0.95:
            print "\n*** WARNING! Bunch appears to be too long for bucket!\n"

        zS = self._compute_std_romberg(psi.function, self.separatrix, self.z_sep[0], self.z_sep[1])
        print "\n--> Maximum rms bunch length in bucket:", zS, " m.\n"
        if sigma > zS * 0.95:
            print "\n*** WARNING! Bunch appears to be too long for bucket!\n"

    def _set_target_std(self, psi, sigma):

        self._test_maximum_std(psi, sigma)
        psi.Hmax = np.amax(self.hamiltonian(self.z_extrema, 0))

        print 'Iterative evaluation of bunch length...'
        counter = 0
        z0 = sigma
        eps = 1

        # Iteratively obtain true H0 to make target sigma
        zH = z0
        psi.H0 = self.H0(zH)
        while abs(eps)>1e-4:
            zS = self._compute_std(psi.function, self.separatrix, self.z_sep[0], self.z_sep[1])

            eps = zS - z0
            print counter, zH, zS, eps
            zH -= 0.5 * eps
            psi.H0 = self.H0(zH)

            counter += 1
            if counter > 100:
                print "\n*** WARNING: too many interation steps! There are several possible reasons for that:"
                print "1. Is the Hamiltonian correct?"
                print "2. Is the stationary distribution function convex around zero?"
                print "3. Is the bunch too long to fit into the bucket?"
                print "4. Is this algorithm not qualified?"
                print "Aborting..."
                sys.exit(-1)

        print "*** Converged!\n"

        return psi.function

    def _compute_std_quad(self, psi, p_sep, xmin, xmax):
        '''
        Compute the variance of the distribution function psi from xmin to xmax
        along the contours p_sep using numerical integration methods.
        '''
        # plt.ion()
        # fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(14, 8))
        # ax3 = fig.add_subplot(133, projection='3d')
        # xx = np.linspace(xmin, xmax, 1000)
        # yy = np.linspace(-self.p_sep, self.p_sep, 1000)
        # XX, YY = np.meshgrid(xx, yy)
        # PP = psi(XX, YY)
        # ax1.plot(xx, p_sep(xx))
        # ax1.plot(xx, -p_sep(xx))
        # ax2.plot(xx, psi(xx, 0))
        # ax3.cla()
        # ax3.plot_surface(XX, YY, PP, cstride=100, rstride=100, cmap=plt.cm.jet)
        # # plt.draw()
        # plt.show()

        Q, error = dblquad(lambda y, x: psi(x, y), xmin, xmax,
                    lambda x: 0, lambda x: p_sep(x))
        V, error = dblquad(lambda y, x: x ** 2 * psi(x, y), xmin, xmax,
                    lambda x: 0, lambda x: p_sep(x))

        return np.sqrt(V/Q)

    def _compute_std_cumtrapz(self, psi, p_sep, xmin, xmax):
        '''
        Compute the variance of the distribution function psi from xmin to xmax
        along the contours p_sep using numerical integration methods.
        '''

        x_arr = np.linspace(xmin, xmax, 257)
        dx = x_arr[1] - x_arr[0]

        Q, V = 0, 0
        for x in x_arr:
            y = np.linspace(0, p_sep(x), 257)
            z = psi(x, y)
            Q += cumtrapz(z, y)[-1]
            z = x**2 * psi(x, y)
            V += cumtrapz(z, y)[-1]
        Q *= dx
        V *= dx

        return np.sqrt(V/Q)

    def _compute_std_romberg(self, psi, p_sep, xmin, xmax):
        '''
        Compute the variance of the distribution function psi from xmin to xmax
        along the contours p_sep using numerical integration methods.
        '''

        x_arr = np.linspace(xmin, xmax, 257)
        dx = x_arr[1] - x_arr[0]

        Q, V = 0, 0
        for x in x_arr:
            y = np.linspace(0, p_sep(x), 257)
            dy = y[1] - y[0]
            z = psi(x, y)
            Q += romb(z, dy)
            z = x**2 * psi(x, y)
            V += romb(z, dy)
        Q *= dx
        V *= dx

        return np.sqrt(V/Q)

    def generate(self, particles):
        '''
        Generate a 2d phase space of n_particles particles randomly distributed
        according to the particle distribution function psi within the region
        [xmin, xmax, ymin, ymax].
        '''
        psi = self._set_target_std(StationaryExponential(self.hamiltonian), self.sigma_z)

        x = np.zeros(particles.n_macroparticles)
        y = np.zeros(particles.n_macroparticles)

        # Bin
        i, j = 0, 0
        nx, ny = 128, 128
        xmin, xmax = self.z_sep[0], self.z_sep[1]
        ymin, ymax = -self.p_sep, self.p_sep
        lx = (xmax - xmin)
        ly = (ymax - ymin)

        xx = np.linspace(xmin, xmax, nx + 1)
        yy = np.linspace(ymin, ymax, ny + 1)
        XX, YY = np.meshgrid(xx, yy)
        HH = psi(XX, YY)
        psi_interp = interp2d(xx, yy, HH)

        while j < particles.n_macroparticles:
            u = xmin + lx * np.random.random()
            v = ymin + ly * np.random.random()

            s = np.random.random()

            i += 1
            if s < psi_interp(u, v):
                x[j] = u
                y[j] = v
                # TODO: check if this does not cause problems! Setter for item does not work - not implemented!
                # particles.dp[j] = v
                j += 1

        particles.z = x
        particles.dp = y
        particles.psi = psi
        # return x, y, j / i * dx * dy, psi


class UniformX(PhaseSpace):
    """
    Horizontal uniform particle phase space distribution.
    """

    def __init__(self, x_min, x_max):

        self.x_min, self.x_max = x_min, x_max

    def generate(self, particles):
        dx = self.x_max - self.x_min
        particles.x = self.x_min + np.random.rand(particles.n_macroparticles) * dx
        particles.xp = 0. * particles.x


class UniformY(PhaseSpace):
    """
    Vertical uniform particle phase space distribution.
    """

    def __init__(self, y_min, y_max):

        self.y_min, self.y_max = y_min, y_max

    def generate(self, particles):
        dy = self.y_max - self.y_min
        particles.y = self.y_min + np.random.rand(particles.n_macroparticles) * dy
        particles.yp = 0. * particles.y


class UniformZ(PhaseSpace):
    """
    Longitudinal uniform particle phase space distribution.
    """

    def __init__(self, z_min, z_max):

        self.z_min, self.z_max = z_min, z_max

    def generate(self, particles):
        dz = self.z_max - self.z_min
        particles.z = self.z_min + np.random.rand(particles.n_macroparticles) * dz
        particles.dp = 0. * particles.z


class ImportX(PhaseSpace):

    def __init__(x, xp):

        self.x = x
        self.xp = xp

    def generate(self, particles):

        assert(particles.n_particles == len(self.x) == len(self.xp))
        particles.x = self.x.copy()
        particles.xp = self.xp.copy()


class ImportY(PhaseSpace):

    def __init__(y, yp):

        self.y = y
        self.yp = yp

    def generate(self, particles):

        assert(particles.n_particles == len(self.y) == len(self.yp))
        particles.y = self.y.copy()
        particles.yp = self.yp.copy()


class ImportZ(PhaseSpace):

    def __init__(z, dp):

        self.z = z
        self.dp = dp

    def generate(self, particles):

        assert(particles.n_particles == len(self.z) == len(self.dp))
        particles.z = self.z.copy()
        particles.dp = self.dp.copy()


class StationaryExponential(object):

    def __init__(self, H):
        self.H = H
        self.H0 = 1
        self.Hmax = H(0, 0)

    def function(self, z, dp):
        return (np.exp(self.H(z, dp)/self.H0) - 1) / (np.exp(self.Hmax/self.H0) - 1)
