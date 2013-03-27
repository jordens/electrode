# -*- coding: utf8 -*-
#
#   electrode: numeric tools for Paul traps
#
#   Copyright (C) 2011-2012 Robert Jordens <jordens@phys.ethz.ch>
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.

import warnings

import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl

from traits.api import HasTraits, Array, Float, Int, Str, List

from .utils import norm, expand_tensor, area_centroid

try:
    import cvxopt, cvxopt.modeling
except ImportError:
    warnings.warn("cvxopt not found, optimizations will fail", ImportWarning)


class Electrode(HasTraits):
    name = Str()
    voltage_dc = Float(0.)
    voltage_rf = Float(0.)

    def electrical_potential(self, x):
        """return the eletrical, units are volts"""
        raise NotImplementedError

    def electrical_gradient(self, x):
        """return the eletrical potential gradient,
        units are volts/length scale"""
        raise NotImplementedError

    def electrical_curvature(self, x):
        """return the eletrical potential curvature,
        units are volts/length scale**2"""
        raise NotImplementedError

    def orientations(self):
        """return the orientation of the patches (positive orientation
        yields positive potential for positive voltage and z>0"""
        raise NotImplementedError

    def plot(self, ax, text=None, *a, **kw):
        """plot this electrode's patches in the supplied axes"""
        raise NotImplementedError


class CoverElectrode(Electrode):
    voltage_dc = 0.
    voltage_rf = 0.
    cover_height = Float(100)
    # also adjust cover_height in
    # the other electrodes to include the cover's effect on their
    # potentials

    def potential(self, x, *d):
        x = np.atleast_2d(x)
        r = []
        if 0 in d:
            r.append(x[:, 2]/self.cover_height)
        if 1 in d:
            ri = np.zeros((3, x.shape[0]))
            ri[2] = 1/self.cover_height
            r.append(ri)
        if 2 in d:
            r.append(np.zeros((3, 3, x.shape[0])))
        if 3 in d:
            r.append(np.zeros((3, 3, 3, x.shape[0])))
        if 4 in d:
            r.append(np.zeros((3, 3, 3, 3, x.shape[0])))
        if 5 in d:
            r.append(np.zeros((3, 3, 3, 3, 3, x.shape[0])))
        return r

    def electrical_potential(self, x):
        return self.voltage_dc*self.potential(x, 0)[0]

    def electrical_gradient(self, x):
        return self.voltage_dc*self.potential(x, 1)[0]

    def electrical_curvature(self, x):
        return self.voltage_dc*self.potential(x, 2)[0]

    def orientations(self):
        return np.array([1])

    def plot(self, ax, text=None, *a, **kw):
        pass


class PixelElectrode(Electrode):
    """
    Parts of the PixelElectrode code are based on:

    Roman Schmied, SurfacePattern software package
    http://atom.physik.unibas.ch/people/romanschmied/code/SurfacePattern.php

    [1] R. Schmied, "Electrostatics of gapped and finite surface
    electrodes", New J. Phys. 12:023038 (2010),
    http://dx.doi.org/10.1088/1367-2630/12/2/023038

    [2] R. Schmied, J. H. Wesenberg, and D. Leibfried, "Optimal
    Surface-Electrode Trap Lattices for Quantum Simulation with Trapped
    Ions", PRL 102:233002 (2009),
    http://dx.doi.org/10.1103/PhysRevLett.102.233002
    """
    pixel_factors = Array(dtype=np.float64, shape=(None,))
    cover_height = Float # cover plane height
    nmax = Int(0) # max components in cover plane potential expansion

    def value_no_cover(self, x, *d):
        """bare pixel potential and derivative (d) value at x.
        indices are (components if d>0, pixel, x)"""
        raise NotImplementedError

    def value(self, x, *d):
        """potential and derivative value with cover plane"""
        x = np.atleast_2d(x).astype(np.double)
        r = self.value_no_cover(x, *d)
        for n in range(-self.nmax, 0) + range(1, self.nmax+1):
            xx = x + [[0, 0, 2*n*self.cover_height]]
            for i, ri in enumerate(self.value_no_cover(xx, *d)):
                r[i] += ri
        return r

    def potential(self, x, *d):
        """return the potential/its derivatives d at x with the pixel
        voltages multiplied and the tensor expanded to full form"""
        x = np.atleast_2d(x)
        v = self.value(x, *d)
        for i, vi in enumerate(v):
            p = self.pixel_factors[:, None]*vi
            p = expand_tensor(p.sum(axis=-2))
            v[i] = p
        return v

    def electrical_potential(self, x):
        e, = self.potential(x, 0)
        return self.voltage_dc*e

    def electrical_gradient(self, x):
        e, = self.potential(x, 1)
        return self.voltage_dc*e

    def electrical_curvature(self, x):
        e, = self.potential(x, 2)
        return self.voltage_dc*e

    def electrical_thirdderiv(self, x):
        e, = self.potential(x, 3)
        return self.voltage_dc*e

    def pseudo_potential(self, x):
        e, = self.potential(x, 1)
        return self.voltage_rf**2*(e**2).sum(axis=0)

    def pseudo_gradient(self, x):
        e, g = self.potential(x, 1, 2)
        return self.voltage_rf**2*2*(e[:, None]*g).sum(axis=0)

    def pseudo_curvature(self, x):
        e, g, c = self.potential(x, 1, 2, 3)
        return self.voltage_rf**2*2*(
                g[:, :, None]*g[:, None, :]+e[:, None, None]*c
                ).sum(axis=0)

    def optimize(self, constraints, verbose=True):
        """optimize this electrode's pixel voltages with respect to
        constraints"""
        p = cvxopt.modeling.variable(len(self.pixel_factors))
        obj = []
        ctrs = []
        for ci in constraints:
            obj.extend(ci.objective(self, p))
            ctrs.extend(ci.constraints(self, p))
        B = np.matrix([i[0] for i in obj])
        b = np.matrix([i[1] for i in obj])
        # the inhomogeneous solution
        g = b*np.linalg.pinv(B).T
        # maximize this
        obj = cvxopt.matrix(g)*p
        # B*g_perp
        B1 = B - b.T*g/(g*g.T)
        #u, l, v = np.linalg.svd(B1)
        #li = np.argmin(l)
        #print li, l[li], v[li], B1*v[li].T
        #self.pixel_factors = np.array(v)[li]
        #return 0.
        #FIXME: there is one singular value, drop one constraint
        B1 = B1[:-1]
        # B*g_perp*p == 0
        ctrs.append(cvxopt.matrix(B1)*p == 0.)
        solver = cvxopt.modeling.op(-obj, ctrs)
        if not verbose:
            cvxopt.solvers.options["show_progress"] = False
        else:
            print "variables:", sum(v._size
                    for v in solver.variables())
            print "inequalities", sum(v.multiplier._size
                    for v in solver.inequalities())
            print "equalities", sum(v.multiplier._size
                    for v in solver.equalities())
        solver.solve("sparse")
        c = float(np.matrix(p.value).T*g.T/(g*g.T))
        p = np.array(p.value).ravel()
        return p, c

    def split(self, thresholds=[0]):
        if thresholds is None:
            threshold = sorted(np.unique(self.pixel_factors))
        ts = [-np.inf] + thresholds + [np.inf]
        eles = []
        for i, (ta, tb) in enumerate(zip(ts[:-1], ts[1:])):
            good = (ta <= self.pixel_factors) & (self.pixel_factors < tb)
            if not np.any(good):
                continue
            paths = [self.paths[j] for j in np.argwhere(good)]
            name = "%s_%i" % (self.name, i)
            eles.append(self.__class__(name=name, paths=paths,
                voltage_dc=self.voltage_dc, voltage_rf=self.voltage_rf))
        return eles


class PointPixelElectrode(PixelElectrode):
    points = Array(dtype=np.float64, shape=(None, 3))
    areas = Array(dtype=np.float64, shape=(None,))

    def _areas_default(self):
        return np.ones((len(self.points)))

    def _pixel_factors_default(self):
        return np.ones(self.areas.shape)

    def orientations(self):
        return np.ones(self.areas.shape)

    def plot(self, ax, text=None, alpha=1., *a, **kw):
        # color="red"?
        p = self.points
        a = (self.areas/np.pi)**.5*2
        col = mpl.collections.EllipseCollection(
                edgecolors="none", cmap=plt.cm.binary,
                norm=plt.Normalize(0, 1.),
                widths=a, heights=a, units="x", # xy in matplotlib>r8111
                angles=np.zeros(a.shape),
                offsets=p[:, (0, 1)], transOffset=ax.transData)
        col.set_array(alpha*self.pixel_factors)
        ax.add_collection(col)
        if text is None:
            text = self.name
        if text:
            ax.text(p[:,0].mean(), p[:,1].mean(), text)

    def value_no_cover(self, x, *d):
        return [v.transpose((2, 0, 1)) if v.ndim==3 else v
                for v in point_value(x, self.areas, self.points, *d)]


class PolygonPixelElectrode(PixelElectrode):
    paths = List(Array(dtype=np.float64, shape=(None, 3)))

    def _pixel_factors_default(self):
        return np.ones(len(self.paths))

    def orientations(self):
        p, = self.value_no_cover(np.array([[0, 0, 1.]]), 0)
        return np.sign(p[:, 0])

    def plot(self, ax, text=None, alpha=1., edgecolor="none", *a, **kw):
        if text is None:
            text = self.name
        for vi, p in zip(self.pixel_factors, self.paths):
            ax.fill(p[:,0], p[:,1], edgecolor=edgecolor,
                    alpha=alpha*vi, *a, **kw)
            if text:
                ax.text(p[:,0].mean(), p[:,1].mean(), text)

    def to_points(self):
        a, c = zip(*(area_centroid(p) for p in self.paths))
        return PointPixelElectrode(name=self.name,
                pixel_factors=self.pixel_factors, nmax=self.nmax,
                cover_height=self.cover_height, areas=a, points=c)

    def value_no_cover(self, x, *d):
        return [v.transpose((2, 0, 1)) if v.ndim==3 else v
                for v in polygon_value(x, list(self.paths), *d)]

try:
    # raise ImportError
    from .speedups import point_value, polygon_value
except ImportError:
    def point_value(x, p, *d):
        return [v.transpose((1, 2, 0)) if v.ndim==3 else v
                for v in _point_value(x, p, *d)]

    def _point_value(x, a, p, *d):
        a = a[:, None]
        p1 = x[None, :] - p[:, None]
        r = norm(p1)
        x, y, z = p1.transpose((2, 0, 1))
        if 0 in d:
            yield a * z/(2*np.pi*r**3) #
        if 1 in d:
            yield a * np.array([
                -3*x*z,
                -3*y*z,
                r**2-3*z**2,
            ])/(2*np.pi*r**5)
        if 2 in d:
            yield a * np.array([
                -(r**2-5*x**2)*z,
                5*x*y*z,
                -x*(r**2-5*z**2),
                -(r**2-5*y**2)*z,
                -y*(r**2-5*z**2)
            ])/(2*np.pi/3*r**7)
        if 3 in d:
            yield a * np.array([
                5*(r**2-7*x**2)*y*z,
                4*x**4-y**4+3*y**2*z**2+4*z**4+3*x**2*(y**2-9*z**2),
                -x**4+4*y**4-27*y**2*z**2+4*z**4+3*x**2*(y**2+z**2),
                5*x*(r**2-7*y**2)*z,
                5*x*z*(3*(x**2+y**2)-4*z**2),
                5*y*z*(3*(x**2+y**2)-4*z**2),
                5*x*y*(r**2-7*z**2),
                ])/(2*np.pi/3*r**9)
        if 4 in d:
            yield a * np.array([
                -21*x*(r**2-3*x**2)*y*z,
                -(x*(4*x**4+x**2*(y**2-41*z**2)-3*(y**2-6*z**2)*(y**2+z**2))),
                z*(-6*x**4+51*x**2*y**2-6*y**4-5*(x**2+y**2)*z**2+z**4),
                -(z*(18*x**4-3*y**4+y**2*z**2+4*z**4+x**2*(15*y**2-41*z**2))),
                -21*x*y*(r**2-3*y**2)*z,
                3*x*((x**2+y**2)**2-12*(x**2+y**2)*z**2+8*z**4),
                -(y*(-3*x**4+4*y**4-41*y**2*z**2+18*z**4+x**2*(y**2+15*z**2))),
                3*(x**2-6*y**2)*(x**2+y**2)*z-(x**2-41*y**2)*z**3-4*z**5,
                3*y*((x**2+y**2)**2-12*(x**2+y**2)*z**2+8*z**4),
                ])/(2*np.pi/15*r**11)
        if 5 in d:
            yield a * np.array([
                7*x*z*(2*x**4+8*y**4+7*y**2*z**2-z**4+x**2*(-23*y**2+z**2)),
                7*x*y*(2*x**4-y**4+7*y**2*z**2+8*z**4+x**2*(y**2-23*z**2)),
                21*x*z*(2*x**4-y**4+y**2*z**2+2*z**4+x**2*(y**2-7*z**2)),
                7*y*z*(8*x**4-23*x**2*y**2+2*y**4+(7*x**2+y**2)*z**2-z**4),
                -2*x**6-2*y**6+15*y**4*z**2+15*y**2*z**4-2*z**6+15*x**4*(y**2+z**2)+15*x**2*(y**4-12*y**2*z**2+z**4),
                7*y*z*(8*x**4-y**4+y**2*z**2+2*z**4+x**2*(7*y**2-23*z**2)),
                -((6*x**2-y**2)*(x**2+y**2)**2)+(101*x**2-11*y**2)*(x**2+y**2)*z**2-4*(29*x**2+y**2)*z**4+8*z**6,
                -7*x*y*(x**4-2*y**4+23*y**2*z**2-8*z**4-x**2*(y**2+7*z**2)),
                -7*x*z*(x**4-8*y**4+23*y**2*z**2-2*z**4-x**2*(7*y**2+z**2)),
                21*y*z*(-x**4+2*y**4-7*y**2*z**2+2*z**4+x**2*(y**2+z**2)),
                (x**2-6*y**2)*(x**2+y**2)**2+(-11*x**4+90*x**2*y**2+101*y**4)*z**2-4*(x**2+29*y**2)*z**4+8*z**6,
                ])/(2*np.pi/45*r**13)


    def polygon_value(x, p, *d):
        v = [_polygon_value(x, pi, *d) for pi in p]
        for vi in zip(*v):
            vi = np.array(vi)
            if vi.ndim == 3:
                vi = vi.transpose((0, 2, 1))
            yield vi

    def _polygon_value(x, p, *d):
        p1 = x[None, :] - p[:, None]
        x1, y1, z = p1.transpose((2, 0, 1))
        r1 = norm(p1)
        x2 = np.roll(x1, -1, axis=0)
        y2 = np.roll(y1, -1, axis=0)
        r2 = np.roll(r1, -1, axis=0)
        l2 = (x1-x2)**2+(y1-y2)**2
        if 0 in d:
            zs = np.abs(z)
            yield np.arctan2(z*(x1*y2-y1*x2),
                    zs*(r1*r2+x1*x2+y1*y2+zs*(zs+r1+r2))).sum(axis=0)/np.pi
        if 1 in d:
            yield (np.array([
                (-y1+y2)*z, (x1-x2)*z, x2*y1-x1*y2,
                ])*(r1+r2)/(r1*r2*((r1+r2)**2-l2))).sum(axis=1)/np.pi
        if 2 in d:
            yield (np.array([
               (r2**2*(-(l2*r2)+(r1+r2)**2*(2*r1+r2))*x1+r1**2*(-(l2*r1)+(r1+r2)**2*(r1+2*r2))*x2)*(y1-y2)*z,
               (y1-y2)*(r2**2*(-(l2*r2)+(r1+r2)**2*(2*r1+r2))*y1+r1**2*(-(l2*r1)+(r1+r2)**2*(r1+2*r2))*y2)*z,
               (-r1-r2)*(y1-y2)*(r1**2*(-(l2*r2**2)+(r1+r2)**2*(x2**2+y2**2))-(r2*(r1+r2)**3-l2*(r1**2-r1*r2+r2**2))*z**2),
               (-x1+x2)*(r2**2*(-(l2*r2)+(r1+r2)**2*(2*r1+r2))*y1+r1**2*(-(l2*r1)+(r1+r2)**2*(r1+2*r2))*y2)*z,
               (r1+r2)*(x1-x2)*(r1**2*(-(l2*r2**2)+(r1+r2)**2*(x2**2+y2**2))-(r2*(r1+r2)**3-l2*(r1**2-r1*r2+r2**2))*z**2),
                ])/((r1*r2)**3*((r1+r2)**2-l2)**2)).sum(axis=1)/np.pi
        if 3 in d:
            yield (np.array([
               (-y1+y2)*(3*r2**5*(l2-r2**2)**2*x1*y1+18*r1*r2**6*(-l2+r2**2)*x1*y1+3*r1**9*x2*y2+18*r1**8*r2*x2*y2+6*r1**3*r2**6*(9*x1*y1+2*x2*y1+2*x1*y2)+r1**4*r2**5*(33*x1*y1+26*x2*y1+26*x1*y2+8*x2*y2)-2*r1**2*r2**5*(l2*(6*x1*y1+x2*y1+x1*y2)-r2**2*(22*x1*y1+x2*y1+x1*y2))+2*r1**7*(-3*l2*x2*y2+r2**2*(x2*y1+x1*y2+22*x2*y2))+r1**5*(3*l2**2*x2*y2-2*l2*r2**2*(x2*y1+x1*y2+6*x2*y2)+r2**4*(8*x1*y1+26*x2*y1+26*x1*y2+33*x2*y2))+r1**6*(-18*l2*r2*x2*y2+r2**3*(12*x2*y1+12*x1*y2+54*x2*y2)))*z,
               (y1-y2)*(2*l2*(r1+r2)**2*(-(r1**2*r2**2*(r1+r2)*(r2**2*x1+r1**2*x2))+(3*r1*r2**4*x1+3*r2**5*x1+r1**3*r2**2*(x1-2*x2)+3*r1**5*x2+3*r1**4*r2*x2+r1**2*r2**3*(-2*x1+x2))*z**2)+(r1+r2)**4*(r1**2*r2**4*(2*r1+r2)*x1+r1**4*r2**2*(r1+2*r2)*x2+(-6*r1*r2**4*x1-3*r2**5*x1-3*r1**5*x2-6*r1**4*r2*x2-2*r1**2*r2**3*(2*x1+x2)-2*r1**3*r2**2*(x1+2*x2))*z**2)+l2**2*(r1**2*r2**5*x1-3*r2**5*x1*z**2+r1**5*x2*(r2**2-3*z**2))),
               (-x1+x2)*(2*l2*(r1+r2)**2*(-(r1**2*r2**2*(r1+r2)*(r2**2*y1+r1**2*y2))+(3*r1*r2**4*y1+3*r2**5*y1+r1**3*r2**2*(y1-2*y2)+3*r1**5*y2+3*r1**4*r2*y2+r1**2*r2**3*(-2*y1+y2))*z**2)+(r1+r2)**4*(r1**2*r2**4*(2*r1+r2)*y1+r1**4*r2**2*(r1+2*r2)*y2+(-6*r1*r2**4*y1-3*r2**5*y1-3*r1**5*y2-6*r1**4*r2*y2-2*r1**2*r2**3*(2*y1+y2)-2*r1**3*r2**2*(y1+2*y2))*z**2)+l2**2*(r1**2*r2**5*y1-3*r2**5*y1*z**2+r1**5*y2*(r2**2-3*z**2))),
               (y1-y2)*(-2*l2*(r1+r2)*(r1**2*r2**2*(r1+r2)**2*(r1**2+r2**2)-3*r2**5*(2*r1+r2)*y1**2-2*r1**2*r2**2*(r1**2-r1*r2+r2**2)*y1*y2-3*r1**5*(r1+2*r2)*y2**2)+(r1+r2)**3*(r1**2*r2**2*(r1+r2)**2*(r1**2+r1*r2+r2**2)+r2**4*(-8*r1**2-9*r1*r2-3*r2**2)*y1**2-4*r1**2*r2**2*(r1**2+3*r1*r2+r2**2)*y1*y2+r1**4*(-3*r1**2-9*r1*r2-8*r2**2)*y2**2)+l2**2*(r1**2*r2**5-3*r2**5*y1**2+r1**5*(r2**2-3*y2**2)))*z,
               (y1-y2)*z*(3*l2**2*(r1**2*r2**5+r1**5*(x2**2+y2**2)-r2**5*z**2)+3*(r1+r2)**5*(r1**2*(r1*r2**3+r2**4+r1**2*(x2**2+y2**2))-r2*(r1+r2)*(r1**2+r2**2)*z**2)-2*l2*(r1+r2)**3*(3*r1**4*(x2**2+y2**2)-3*r2**4*z**2+r1**2*r2**2*(3*r2**2+z**2))),
               (-x1+x2)*z*(3*l2**2*(r1**2*r2**5+r1**5*(x2**2+y2**2)-r2**5*z**2)+3*(r1+r2)**5*(r1**2*(r1*r2**3+r2**4+r1**2*(x2**2+y2**2))-r2*(r1+r2)*(r1**2+r2**2)*z**2)-2*l2*(r1+r2)**3*(3*r1**4*(x2**2+y2**2)-3*r2**4*z**2+r1**2*r2**2*(3*r2**2+z**2))),
               (y1-y2)*(2*l2*(r1+r2)**2*(-(r1**2*r2**2*(r1+r2)*(r2**2*y1+r1**2*y2))+(3*r1*r2**4*y1+3*r2**5*y1+r1**3*r2**2*(y1-2*y2)+3*r1**5*y2+3*r1**4*r2*y2+r1**2*r2**3*(-2*y1+y2))*z**2)+(r1+r2)**4*(r1**2*r2**4*(2*r1+r2)*y1+r1**4*r2**2*(r1+2*r2)*y2+(-6*r1*r2**4*y1-3*r2**5*y1-3*r1**5*y2-6*r1**4*r2*y2-2*r1**2*r2**3*(2*y1+y2)-2*r1**3*r2**2*(y1+2*y2))*z**2)+l2**2*(r1**2*r2**5*y1-3*r2**5*y1*z**2+r1**5*y2*(r2**2-3*z**2)))
                ])/((r1*r2)**5*((r1+r2)**2-l2)**3)).sum(axis=1)/np.pi
        if 4 in d:
            yield (np.array([
              (y1-y2)*z*(l2**3*(-12*r1**2*r2**7*y1+15*r2**7*y1*(y1**2+z**2)+r1**7*(-12*r2**2*y2+15*y2*(y2**2+z**2)))+(r1+r2)**4*(-15*r2**9*y1*(y1**2+z**2)+r1**7*r2**2*(r2**2*(4*y1+46*y2)+y2*(12*x1*x2+26*x2**2-6*y1*y2-61*y2**2)+(-6*y1-61*y2)*z**2)+2*r1**5*r2**4*(2*x1**2*y2+8*x1*x2*(y1+4*y2)+r2**2*(12*y1+5*y2)-2*y1*(-4*x2**2+y2*(y1+4*y2))-2*(4*y1+y2)*z**2)+2*r1**4*r2**5*(8*x1**2*y2+8*x1*x2*(4*y1+y2)+r2**2*(23*y1+2*y2)-2*y1*(-x2**2+4*y1*y2+y2**2)-2*(y1+4*y2)*z**2)-4*r1*r2**8*y1*(-2*x1**2+13*(y1**2+z**2))+2*r1**3*r2**6*(20*r2**2*y1+24*x1*x2*y1+3*x1**2*(4*y1+y2)-3*(4*y1+3*y2)*(y1**2+z**2))+r1**2*r2**7*(12*r2**2*y1+26*x1**2*y1+12*x1*x2*y1-(61*y1+6*y2)*(y1**2+z**2))+4*r1**8*r2*y2*(10*r2**2+2*x2**2-13*(y2**2+z**2))+r1**9*(12*r2**2*y2-15*y2*(y2**2+z**2))+2*r1**6*r2**3*(24*x1*x2*y2+3*x2**2*(y1+4*y2)+r2**2*(5*y1+12*y2)-3*(3*y1+4*y2)*(y2**2+z**2)))-l2*(r1+r2)**2*(-45*r2**9*y1*(y1**2+z**2)+r1**7*r2**2*(r2**2*(8*y1+122*y2)+y2*(24*x1*x2-4*x2**2-12*y1*y2-145*y2**2)+(-12*y1-145*y2)*z**2)+2*r1**4*r2**5*(-22*x1**2*y2+8*x1*x2*(-2*y1+y2)+r2**2*(61*y1+4*y2)-y1*(x2**2+14*y1*y2+5*y2**2)+(-5*y1-14*y2)*z**2)+2*r1**5*r2**4*(-22*x2**2*y1+8*x1*x2*(y1-2*y2)-x1**2*y2-5*y1**2*y2-14*y1*y2**2+r2**2*(30*y1+11*y2)-14*y1*z**2-5*y2*z**2)-2*r1*r2**8*y1*(-4*x1**2+71*(y1**2+z**2))+2*r1**3*r2**6*(56*r2**2*y1+24*x1*x2*y1-3*x1**2*(8*y1+y2)-3*(8*y1+5*y2)*(y1**2+z**2))+r1**2*r2**7*(36*r2**2*y1-4*x1**2*y1+24*x1*x2*y1-(145*y1+12*y2)*(y1**2+z**2))+2*r1**8*r2*y2*(56*r2**2+4*x2**2-71*(y2**2+z**2))+r1**9*(36*r2**2*y2-45*y2*(y2**2+z**2))+2*r1**6*r2**3*(24*x1*x2*y2-3*x2**2*(y1+8*y2)+r2**2*(11*y1+30*y2)-3*(5*y1+8*y2)*(y2**2+z**2)))+l2**2*(104*r1**3*r2**8*y1+16*r1**6*r2**5*y2-45*r2**9*y1*(y1**2+z**2)+r1**7*r2**2*(r2**2*(4*y1+106*y2)+y2*(12*x1*x2-46*x2**2-6*y1*y2-115*y2**2)+(-6*y1-115*y2)*z**2)+8*r1**5*r2**4*(2*r2**2*y1-2*x2**2*y1-y2*(x1**2+y1**2+2*y1*y2)-(2*y1+y2)*z**2)-8*r1*r2**8*y1*(x1**2+16*(y1**2+z**2))+r1**2*r2**7*(36*r2**2*y1-46*x1**2*y1+12*x1*x2*y1-(115*y1+6*y2)*(y1**2+z**2))+24*r1**8*r2*y2*(4*r2**2-5*(y2**2+z**2))+r1**9*(36*r2**2*y2-45*y2*(y2**2+z**2))+2*r1**4*r2**5*(r2**2*(53*y1+2*y2)-4*(2*x1**2*y2+y1*(x2**2+2*y1*y2+y2**2)+(y1+2*y2)*z**2)))),
               (-y1+y2)*((r1+r2)**5*(r1**2*r2**2*(2*r1*r2*(r1*r2*(r1**4+2*r1**3*r2+2*r1*r2**3+r2**4)+r2**3*(2*r1+r2)*x1**2+2*r1*r2*(r1**2+3*r1*r2+r2**2)*x1*x2+r1**3*(r1+4*r2)*x2**2)-r2**4*(r1+r2)*(4*r1+3*r2)*y1**2+r1**5*(-3*r1-7*r2)*y2**2)+(-15*r1**8*x2**2+r1**3*r2**5*(-35*r2**2-24*x1**2-36*x1*x2)+12*r1**5*r2**3*(r2**2-3*x1*x2-2*x2**2)+15*r2**8*y1**2-3*r1**2*r2**6*(5*r2**2+6*x1**2+4*x1*x2-10*y1**2)+r1*r2**7*(-8*x1**2+37*y1**2)-2*r1**6*r2**2*(11*r2**2+6*x1*x2+9*x2**2-15*y2**2)+r1**7*r2*(-35*r2**2-8*x2**2+37*y2**2)-2*r1**4*r2**4*(13*r2**2+2*(x1**2+11*x1*x2+x2**2-y1**2-y2**2)))*z**2+r2*(37*r1**7+30*r1**6*r2+8*r1**4*r2**3+30*r1**2*r2**5+37*r1*r2**6+15*r2**7)*z**4)+l2**2*(r1+r2)*(r1**2*r2**2*(2*r1*r2*(3*r1*r2*(r1+r2)**2*(r1**2+r2**2)-r2**3*(4*r1+r2)*x1**2+2*r1*r2*(r1**2-r1*r2+r2**2)*x1*x2-r1**3*(r1+4*r2)*x2**2)-r2**4*(r1+r2)*(8*r1+9*r2)*y1**2-r1**4*(r1+r2)*(9*r1+8*r2)*y2**2)+(-45*r1**8*x2**2-12*r1**6*(r2**4+r2**2*x1*x2)-12*r1**5*(r2**5-r2**3*x1*x2)+45*r2**8*y1**2-2*r1**4*r2**4*(21*r2**2-4*x1**2+6*x1*x2-4*y1**2)+r1**3*r2**5*(-85*r2**2+8*x1**2+12*x1*x2+8*y1**2)+r1**2*r2**6*(-45*r2**2+38*x1**2-12*x1*x2+38*y1**2)+r1*r2**7*(8*x1**2+83*y1**2)+r1**7*r2*(-85*r2**2+8*x2**2+83*y2**2))*z**2+r2*(83*r1**7+8*r1**4*r2**3+8*r1**3*r2**4+38*r1**2*r2**5+83*r1*r2**6+45*r2**7)*z**4)+l2**3*(-2*r1**4*r2**7-15*r2**7*z**2*(y1**2+z**2)+3*r1**2*r2**7*(y1**2+5*z**2)+r1**7*(-2*r2**4+3*r2**2*y2**2+15*x2**2*z**2))-l2*(r1+r2)**3*(45*r2**8*z**2*(y1**2+z**2)+r1**8*(6*r2**4-9*r2**2*y2**2-45*x2**2*z**2)-3*r1**2*r2**6*(3*r2**2*y1**2+(15*r2**2-4*x1*(x1-2*x2)-20*y1**2)*z**2-20*z**4)+2*r1**4*r2**4*(r2**2*(3*r2**2-x1*(x1-4*x2)-5*y1**2)+(-31*r2**2+x1**2+20*x1*x2+5*y1**2+4*y2**2)*z**2+9*z**4)+2*r1**5*r2**3*(6*r2**4+4*r2**2*x1*x2+(-14*r2**2-12*x1*x2+21*x2**2+9*y2**2)*z**2+9*z**4)+r1**3*r2**5*(r2**2*(2*x1**2-19*y1**2)+(-95*r2**2+42*x1**2-24*x1*x2+18*y1**2)*z**2+18*z**4)+r1*r2**7*z**2*(-8*x1**2+97*(y1**2+z**2))+2*r1**6*r2**2*(5*r2**4+r2**2*(4*x1*x2-4*y2**2-31*z**2)+6*z**2*(x2*(-2*x1+x2)+5*(y2**2+z**2)))+r1**7*r2*(12*r2**4+r2**2*(2*x2**2-19*y2**2-95*z**2)+z**2*(-8*x2**2+97*(y2**2+z**2))))),
              (-y1+y2)*(-3*l2**3*(r1**2*r2**7*x1-5*r2**7*x1*y1**2+r1**7*x2*(r2**2-5*y2**2))+l2**2*(24*r1**3*r2**8*x1+r1**4*r2**7*(17*x1+2*x2)-120*r1*r2**8*x1*y1**2-45*r2**9*x1*y1**2+9*r1**9*x2*(r2**2-5*y2**2)+24*r1**8*r2*x2*(r2**2-5*y2**2)+3*r1**2*r2**7*(3*r2**2*x1+y1*(-23*x1*y1-2*x2*y1-4*x1*y2))+r1**7*r2**2*(r2**2*(2*x1+17*x2)-3*y2*(4*x2*y1+2*x1*y2+23*x2*y2)))-l2*(r1+r2)**2*(-150*r1*r2**8*x1*y1**2-45*r2**9*x1*y1**2+6*r1**3*r2**6*(5*r2**2*x1-4*x2*y1**2-8*x1*y1*y2)+r1**4*r2**5*(r2**2*(33*x1+4*x2)+16*x2*y1*(y1-y2)+8*x1*(4*y1-y2)*y2)+9*r1**9*x2*(r2**2-5*y2**2)+30*r1**8*r2*x2*(r2**2-5*y2**2)+8*r1**5*r2**4*(r2**2*(2*x1+x2)-x2*y1*(y1-4*y2)+2*x1*y2*(-y1+y2))+3*r1**2*r2**7*(3*r2**2*x1+y1*(-47*x1*y1-4*x2*y1-8*x1*y2))+8*r1**6*r2**3*(r2**2*(x1+2*x2)-3*y2*(2*x2*y1+x1*y2))+r1**7*r2**2*(r2**2*(4*x1+33*x2)-3*y2*(8*x2*y1+4*x1*y2+47*x2*y2)))+(r1+r2)**4*(-60*r1*r2**8*x1*y1**2-15*r2**9*x1*y1**2+3*r1**9*x2*(r2**2-5*y2**2)+12*r1**8*r2*x2*(r2**2-5*y2**2)+r1**4*r2**5*(r2**2*(19*x1+2*x2)-16*x2*y1*(2*y1+y2)-8*x1*y2*(8*y1+y2))+8*r1**5*r2**4*(r2**2*(2*x1+x2)-2*x1*y2*(y1+2*y2)-x2*y1*(y1+8*y2))+3*r1**2*r2**7*(r2**2*x1+y1*(-29*x1*y1-2*x2*y1-4*x1*y2))+r1**7*r2**2*(r2**2*(2*x1+19*x2)-3*y2*(4*x2*y1+2*x1*y2+29*x2*y2))+12*r1**3*r2**6*(r2**2*x1-2*y1*(x2*y1+2*x1*(y1+y2)))+8*r1**6*r2**3*(r2**2*(x1+2*x2)-3*y2*(x1*y2+2*x2*(y1+y2)))))*z,
               -3*(y1-y2)*z*(l2*(r1+r2)**4*(r1**2*r2**2*(-12*r1*r2**4*x1-9*r2**5*x1-9*r1**5*x2-12*r1**4*r2*x2-4*r1**2*r2**3*x2-4*r1**3*x1*(x2**2+y2**2))+(-4*r1**3*r2**4*x1+20*r1*r2**6*x1+15*r2**7*x1+15*r1**7*x2+20*r1**6*r2*x2-4*r1**4*r2**3*x2+4*r1**2*r2**5*x2)*z**2)+(r1+r2)**6*(r1**2*r2**4*(2*r1**3+4*r1**2*r2+6*r1*r2**2+3*r2**3)*x1+r1**4*r2**2*(3*r1**3+6*r1**2*r2+4*r1*r2**2+2*r2**3)*x2+(-10*r1*r2**6*x1-5*r2**7*x1-5*r1**7*x2-10*r1**6*r2*x2-2*r1**2*r2**5*(4*x1+x2)-2*r1**3*r2**4*(3*x1+2*x2)-2*r1**4*r2**3*(2*x1+3*x2)-2*r1**5*r2**2*(x1+4*x2))*z**2)+l2**3*(-3*r1**2*r2**7*x1+5*r2**7*x1*z**2+r1**7*x2*(-3*r2**2+5*z**2))+l2**2*(24*r1**3*r2**8*x1+r1**4*r2**7*(17*x1+2*x2)-40*r1*r2**8*x1*z**2-15*r2**9*x1*z**2+r1**7*r2**2*(r2**2*(2*x1+17*x2)+(-2*x1-27*x2)*z**2)+r1**2*r2**7*(9*r2**2*x1+(-27*x1-2*x2)*z**2)+r1**9*(9*r2**2*x2-15*x2*z**2)+r1**8*(24*r2**3*x2-40*r2*x2*z**2))),
              -3*(y1-y2)*(-(l2*(r1+r2)**2*(r1**2*r2**4*(r1+r2)**2*(4*r1**3+12*r1*r2**2+9*r2**3)*y1+r2**7*(-47*r1**2-50*r1*r2-15*r2**2)*y1**3+r1**2*r2**2*(r1**2*(r1+r2)**2*(9*r1**3+12*r1**2*r2+4*r2**3)-4*r2**2*(2*r1**3-4*r1**2*r2+6*r1*r2**2+3*r2**3)*y1**2)*y2-4*r1**4*r2**2*(3*r1**3+6*r1**2*r2-4*r1*r2**2+2*r2**3)*y1*y2**2+r1**7*(-15*r1**2-50*r1*r2-47*r2**2)*y2**3))+(r1+r2)**4*(r1**2*r2**4*(r1+r2)**2*(2*r1**3+4*r1**2*r2+6*r1*r2**2+3*r2**3)*y1+r2**6*(-16*r1**3-29*r1**2*r2-20*r1*r2**2-5*r2**3)*y1**3+r1**2*r2**2*(r1**2*(r1+r2)**2*(3*r1**3+6*r1**2*r2+4*r1*r2**2+2*r2**3)-2*r2**2*(4*r1**3+16*r1**2*r2+12*r1*r2**2+3*r2**3)*y1**2)*y2-2*r1**4*r2**2*(3*r1**3+12*r1**2*r2+16*r1*r2**2+4*r2**3)*y1*y2**2+r1**6*(-5*r1**3-20*r1**2*r2-29*r1*r2**2-16*r2**3)*y2**3)+l2**3*(-3*r1**2*r2**7*y1+5*r2**7*y1**3+r1**7*(-3*r2**2*y2+5*y2**3))+l2**2*(24*r1**3*r2**8*y1-40*r1*r2**8*y1**3-15*r2**9*y1**3+r1**4*r2**7*(17*y1+2*y2)+r1**2*r2**7*y1*(9*r2**2-23*y1**2-6*y1*y2)+r1**9*(9*r2**2*y2-15*y2**3)+r1**8*(24*r2**3*y2-40*r2*y2**3)+r1**7*r2**2*((-6*y1-23*y2)*y2**2+r2**2*(2*y1+17*y2))))*z,
              3*(y1-y2)*(l2*(r1+r2)**5*(-(r1**4*r2**4*(3*r1**2+r1*r2+3*r2**2))+2*r1**2*r2**2*(9*r1**4+3*r1**3*r2+r1**2*r2**2+3*r1*r2**3+9*r2**4)*z**2+(-15*r1**6-5*r1**5*r2+r1**4*r2**2+3*r1**3*r2**3+r1**2*r2**4-5*r1*r2**5-15*r2**6)*z**4)+(r1+r2)**7*(r1**4*r2**4*(r1**2+r1*r2+r2**2)-6*r1**2*r2**2*(r1**4+r1**3*r2+r1**2*r2**2+r1*r2**3+r2**4)*z**2+5*(r1**6+r1**5*r2+r1**4*r2**2+r1**3*r2**3+r1**2*r2**4+r1*r2**5+r2**6)*z**4)+l2**2*(r1+r2)**3*(r1**4*r2**4*(3*r1**2-r1*r2+3*r2**2)-2*r1**2*r2**2*(9*r1**4-3*r1**3*r2+r1**2*r2**2-3*r1*r2**3+9*r2**4)*z**2+(15*r1**6-5*r1**5*r2-r1**4*r2**2+3*r1**3*r2**3-r1**2*r2**4-5*r1*r2**5+15*r2**6)*z**4)-l2**3*(r1**4*r2**4*(r1**3+r2**3)-6*r1**2*r2**2*(r1**5+r2**5)*z**2+5*(r1**7+r2**7)*z**4)),
              (-x1+x2)*(l2**2*(r1+r2)*(r1**4*r2**4*(r1+r2)**2*(3*r1**2-r1*r2+3*r2**2)-3*r1**2*r2**7*(5*r1+3*r2)*y1**2-4*r1**4*r2**4*(r1**2-r1*r2+r2**2)*y1*y2-3*r1**7*r2**2*(3*r1+5*r2)*y2**2+(-(r1**2*r2**2*(r1+r2)**2*(9*r1**4-3*r1**3*r2+r1**2*r2**2-3*r1*r2**3+9*r2**4))+15*r2**7*(5*r1+3*r2)*y1**2+12*r1**2*r2**2*(r1**4-r1**3*r2+r1**2*r2**2-r1*r2**3+r2**4)*y1*y2+15*r1**7*(3*r1+5*r2)*y2**2)*z**2)+(r1+r2)**5*(r1**4*r2**4*(r1+r2)**2*(r1**2+r1*r2+r2**2)+r1**2*r2**6*(-8*r1**2-9*r1*r2-3*r2**2)*y1**2-4*r1**4*r2**4*(r1**2+3*r1*r2+r2**2)*y1*y2+r1**6*r2**2*(-3*r1**2-9*r1*r2-8*r2**2)*y2**2+(-3*r1**2*r2**2*(r1+r2)**2*(r1**4+r1**3*r2+r1**2*r2**2+r1*r2**3+r2**4)+r2**4*(8*r1**4+24*r1**3*r2+48*r1**2*r2**2+45*r1*r2**3+15*r2**4)*y1**2+4*r1**2*r2**2*(3*r1**4+9*r1**3*r2+11*r1**2*r2**2+9*r1*r2**3+3*r2**4)*y1*y2+r1**4*(15*r1**4+45*r1**3*r2+48*r1**2*r2**2+24*r1*r2**3+8*r2**4)*y2**2)*z**2)-l2*(r1+r2)**3*(r1**4*r2**4*(3*r1**4+7*r1**3*r2+7*r1*r2**3+3*r2**4+8*r1**2*x2**2)+r1**2*r2**6*(-8*r1**2-21*r1*r2-9*r2**2)*y1**2-8*r1**4*r2**4*(r1**2+r1*r2+r2**2)*y1*y2-3*r1**7*r2**2*(3*r1+7*r2)*y2**2+(105*r1*r2**7*y1**2+45*r2**8*y1**2-3*r1**3*r2**5*(7*r2**2+8*y1*(y1-y2))-9*r1**8*(r2**2-5*y2**2)+8*r1**4*r2**4*(-2*r2**2+y1**2-5*y1*y2+y2**2)-21*r1**7*(r2**3-5*r2*y2**2)-8*r1**5*r2**3*(r2**2+3*y2*(-y1+y2))-3*r1**2*r2**6*(3*r2**2-8*y1*(2*y1+y2))-8*r1**6*r2**2*(r2**2-3*y2*(y1+2*y2)))*z**2)-l2**3*(r1**4*r2**7+15*r2**7*y1**2*z**2-3*r1**2*r2**7*(y1**2+z**2)+r1**7*(r2**4+15*y2**2*z**2-3*r2**2*(y2**2+z**2)))),
              3*(x1-x2)*z*(l2*(r1+r2)**4*(r1**2*r2**2*(-12*r1*r2**4*y1-9*r2**5*y1-9*r1**5*y2-12*r1**4*r2*y2-4*r1**2*r2**3*y2-4*r1**3*y1*(x2**2+y2**2))+(-4*r1**3*r2**4*y1+20*r1*r2**6*y1+15*r2**7*y1+15*r1**7*y2+20*r1**6*r2*y2-4*r1**4*r2**3*y2+4*r1**2*r2**5*y2)*z**2)+(r1+r2)**6*(r1**2*r2**4*(2*r1**3+4*r1**2*r2+6*r1*r2**2+3*r2**3)*y1+r1**4*r2**2*(3*r1**3+6*r1**2*r2+4*r1*r2**2+2*r2**3)*y2+(-10*r1*r2**6*y1-5*r2**7*y1-5*r1**7*y2-10*r1**6*r2*y2-2*r1**2*r2**5*(4*y1+y2)-2*r1**3*r2**4*(3*y1+2*y2)-2*r1**4*r2**3*(2*y1+3*y2)-2*r1**5*r2**2*(y1+4*y2))*z**2)+l2**3*(-3*r1**2*r2**7*y1+5*r2**7*y1*z**2+r1**7*y2*(-3*r2**2+5*z**2))+l2**2*(24*r1**3*r2**8*y1+r1**4*r2**7*(17*y1+2*y2)-40*r1*r2**8*y1*z**2-15*r2**9*y1*z**2+r1**7*r2**2*(r2**2*(2*y1+17*y2)+(-2*y1-27*y2)*z**2)+r1**2*r2**7*(9*r2**2*y1+(-27*y1-2*y2)*z**2)+r1**9*(9*r2**2*y2-15*y2*z**2)+r1**8*(24*r2**3*y2-40*r2*y2*z**2))),
              -3*(x1-x2)*(l2*(r1+r2)**5*(-(r1**4*r2**4*(3*r1**2+r1*r2+3*r2**2))+2*r1**2*r2**2*(9*r1**4+3*r1**3*r2+r1**2*r2**2+3*r1*r2**3+9*r2**4)*z**2+(-15*r1**6-5*r1**5*r2+r1**4*r2**2+3*r1**3*r2**3+r1**2*r2**4-5*r1*r2**5-15*r2**6)*z**4)+(r1+r2)**7*(r1**4*r2**4*(r1**2+r1*r2+r2**2)-6*r1**2*r2**2*(r1**4+r1**3*r2+r1**2*r2**2+r1*r2**3+r2**4)*z**2+5*(r1**6+r1**5*r2+r1**4*r2**2+r1**3*r2**3+r1**2*r2**4+r1*r2**5+r2**6)*z**4)+l2**2*(r1+r2)**3*(r1**4*r2**4*(3*r1**2-r1*r2+3*r2**2)-2*r1**2*r2**2*(9*r1**4-3*r1**3*r2+r1**2*r2**2-3*r1*r2**3+9*r2**4)*z**2+(15*r1**6-5*r1**5*r2-r1**4*r2**2+3*r1**3*r2**3-r1**2*r2**4-5*r1*r2**5+15*r2**6)*z**4)-l2**3*(r1**4*r2**4*(r1**3+r2**3)-6*r1**2*r2**2*(r1**5+r2**5)*z**2+5*(r1**7+r2**7)*z**4)),
                ])/((r1*r2)**7*((r1+r2)**2-l2)**4)).sum(axis=1)/np.pi
        if 5 in d:
            yield (np.array([
              (y1-y2)*z*(l2**4*(12*r1**4*r2**9+105*r2**9*y1**2*(y1**2+z**2)-15*r1**2*r2**9*(7*y1**2+z**2)+r1**9*(12*r2**4+105*y2**2*(y2**2+z**2)-15*r2**2*(7*y2**2+z**2)))+l2**3*(-4*r1**8*r2**5*(19*r2**2-14*x2**2-29*y2**2-14*z**2)-420*r2**11*y1**2*(y1**2+z**2)+2*r1**3*r2**10*(8*x1**2+581*y1**2+83*z**2)-4*r1**5*r2**6*(34*r2**4+15*x2**2*y1**2+y2*(20*x1**2*y1+20*y1**3+9*x1**2*y2+24*y1**2*y2)+(15*y1**2+20*y1*y2+9*y2**2)*z**2+r2**2*(-14*x1**2-29*y1**2-14*z**2))-6*r1*r2**10*y1**2*(16*x1**2+191*(y1**2+z**2))+r1**11*(-48*r2**4-420*y2**2*(y2**2+z**2)+60*r2**2*(7*y2**2+z**2))-2*r1**7*r2**4*(38*r2**4+r2**2*(-5*x1**2-10*x2**2-5*y1**2-120*y1*y2-28*y2**2-15*z**2)+5*y2*(16*x2**2*y1+y2*(3*(x1**2+y1**2)+16*y1*y2)+(16*y1+3*y2)*z**2))-2*r1**4*r2**7*(24*r2**4+r2**2*(-31*x1**2+6*x1*x2-526*y1**2-24*y1*y2-76*z**2)+5*y1*(3*x2**2*y1+y2*(16*(x1**2+y1**2)+3*y1*y2)+(3*y1+16*y2)*z**2))-2*r1**6*r2**5*(70*r2**4+r2**2*(-10*x1**2-5*x2**2-28*y1**2-120*y1*y2-5*y2**2-15*z**2)+2*(x2**2*y1*(9*y1+20*y2)+y2**2*(15*x1**2+24*y1**2+20*y1*y2)+(9*y1**2+20*y1*y2+15*y2**2)*z**2))+30*r1**2*r2**9*(2*r2**2*(7*y1**2+z**2)+y1*(-13*x1**2*y1+2*x1*x2*y1-2*(16*y1+y2)*(y1**2+z**2)))-2*r1**10*r2*(68*r2**4+r2**2*(-8*x2**2-581*y2**2-83*z**2)+3*y2**2*(16*x2**2+191*(y2**2+z**2)))-2*r1**9*r2**2*(70*r2**4+r2**2*(6*x1*x2-31*x2**2-24*y1*y2-526*y2**2-76*z**2)+15*y2*(-2*x1*x2*y2+13*x2**2*y2+2*(y1+16*y2)*(y2**2+z**2))))+(r1+r2)**5*(105*r2**12*y1**2*(y1**2+z**2)+r1**4*r2**8*(12*r2**4-12*y1*(16*x1**2*(y1+y2)+8*x1*x2*(6*y1+y2)+y1*(x2**2-16*y1**2-32*y1*y2-3*y2**2))+12*(17*y1**2+32*y1*y2+2*y2**2)*z**2+r2**2*(34*x1**2+12*x1*x2-803*y1**2-48*y1*y2-119*z**2))+2*r1**8*r2**4*(34*r2**4-48*x2*(x1+2*x2)*y1*y2-6*(x1**2+48*x1*x2+16*x2**2-3*y1**2)*y2**2+192*y1*y2**3+96*y2**4+6*(2*y1**2+32*y1*y2+17*y2**2)*z**2+r2**2*(2*x1**2+64*x1*x2+23*x2**2-10*y1**2-152*y1*y2-130*y2**2-31*z**2))+2*r1**6*r2**6*(45*r2**4-3*x1**2*y2*(8*y1+17*y2)-48*x1*x2*(y1**2+9*y1*y2+y2**2)+3*y1*(-17*x2**2*y1-8*x2**2*y2+8*y1**2*y2+38*y1*y2**2+8*y2**3)+3*(19*y1**2+16*y1*y2+19*y2**2)*z**2+r2**2*(23*x1**2+64*x1*x2+2*x2**2-130*y1**2-152*y1*y2-10*y2**2-31*z**2))+3*r1*r2**11*y1**2*(-16*x1**2+159*(y1**2+z**2))+r1**12*(12*r2**4+105*y2**2*(y2**2+z**2)-15*r2**2*(7*y2**2+z**2))+2*r1**7*r2**5*(42*r2**4+r2**2*(10*(x1**2+8*x1*x2+x2**2)-35*y1**2-160*y1*y2-35*y2**2-20*z**2)+15*(-16*x1*x2*y2*(y1+y2)-x2**2*y1*(y1+8*y2)+y2**2*(-2*x1**2+5*y1**2+8*y1*y2)+(3*y1**2+8*y1*y2+2*y2**2)*z**2))+r1**5*r2**7*(52*r2**4+r2**2*(56*x1**2+60*x1*x2-649*y1**2-192*y1*y2-109*z**2)+30*(-16*x1*x2*y1*(y1+y2)-x1**2*y2*(8*y1+y2)+y1**2*(-2*x2**2+8*y1*y2+5*y2**2)+(2*y1**2+8*y1*y2+3*y2**2)*z**2))-15*r1**2*r2**10*(r2**2*(7*y1**2+z**2)+y1*(14*x1**2*y1+4*x1*x2*y1-(55*y1+4*y2)*(y1**2+z**2)))+r1**3*r2**9*(r2**2*(8*x1**2-469*y1**2-67*z**2)+3*y1*(-100*x1*x2*y1+x1**2*(-110*y1-16*y2)+(215*y1+84*y2)*(y1**2+z**2)))+r1**11*r2*(52*r2**4+r2**2*(8*x2**2-469*y2**2-67*z**2)+3*y2**2*(-16*x2**2+159*(y2**2+z**2)))+r1**10*r2**2*(90*r2**4+r2**2*(12*x1*x2+34*x2**2-48*y1*y2-803*y2**2-119*z**2)+15*y2*(-4*x1*x2*y2-14*x2**2*y2+(4*y1+55*y2)*(y2**2+z**2)))+r1**9*r2**3*(84*r2**4+r2**2*(60*x1*x2+56*x2**2-192*y1*y2-649*y2**2-109*z**2)+3*y2*(x2**2*(-16*y1-110*y2)-100*x1*x2*y2+(84*y1+215*y2)*(y2**2+z**2))))+2*l2**2*(r1+r2)*(1260*r1*r2**11*y1**2*(y1**2+z**2)+315*r2**12*y1**2*(y1**2+z**2)+r1**4*r2**8*(36*r2**4+3*y1*(-16*x1*x2*y2+x1**2*(80*y1+120*y2)+y1*(5*x2**2+80*y1**2+120*y1*y2+13*y2**2))+3*(89*y1**2+120*y1*y2+4*y2**2)*z**2+r2**2*(-25*x1**2+18*x1*x2-1882*y1**2-72*y1*y2-274*z**2))+r1**5*r2**7*(144*r2**4+105*x2**2*y1**2+48*x1*x2*y1*y2+3*y2*(20*x1**2*(4*y1+y2)+y1**2*(80*y1+47*y2))+3*(31*y1**2+80*y1*y2+16*y2**2)*z**2+r2**2*(-75*x1**2+42*x1*x2-1293*y1**2-248*y1*y2-201*z**2))+r1**8*r2**4*(144*r2**4+3*y2*(-16*x1*x2*y1+5*x1**2*y2+x2**2*(120*y1+80*y2)+y2*(13*y1**2+120*y1*y2+80*y2**2))+3*(4*y1**2+120*y1*y2+89*y2**2)*z**2+r2**2*(-5*x1**2+16*x1*x2-80*x2**2-17*y1**2-424*y1*y2-422*y2**2-93*z**2))+r1**6*r2**6*(228*r2**4-48*x1*x2*y1*y2+10*x2**2*y1*(9*y1+8*y2)+2*y2*(40*x1**2*y1+40*y1**3+45*x1**2*y2+102*y1**2*y2+40*y1*y2**2)+2*(51*y1**2+80*y1*y2+51*y2**2)*z**2+r2**2*(-80*x1**2+16*x1*x2-5*x2**2-422*y1**2-424*y1*y2-17*y2**2-93*z**2))+r1**7*r2**5*(192*r2**4+48*x1*x2*y1*y2+60*x2**2*y1*(y1+4*y2)+3*y2**2*(35*x1**2+47*y1**2+80*y1*y2)+3*(16*y1**2+80*y1*y2+31*y2**2)*z**2+r2**2*(-35*x1**2-16*x1*x2-35*x2**2-83*y1**2-496*y1*y2-83*y2**2-62*z**2))+r1**12*(36*r2**4+315*y2**2*(y2**2+z**2)-45*r2**2*(7*y2**2+z**2))+r1**11*(144*r2**5+1260*r2*y2**2*(y2**2+z**2)-180*r2**3*(7*y2**2+z**2))+15*r1**2*r2**10*(-3*r2**2*(7*y1**2+z**2)+y1*(11*x1**2*y1-6*x1*x2*y1+2*(61*y1+3*y2)*(y1**2+z**2)))+5*r1**3*r2**9*(-36*r2**2*(7*y1**2+z**2)+y1*(-42*x1*x2*y1+x1**2*(93*y1+16*y2)+(225*y1+58*y2)*(y1**2+z**2)))+r1**10*r2**2*(228*r2**4+r2**2*(18*x1*x2-25*x2**2-72*y1*y2-1882*y2**2-274*z**2)+15*y2*(-6*x1*x2*y2+11*x2**2*y2+2*(3*y1+61*y2)*(y2**2+z**2)))+r1**9*r2**3*(192*r2**4+r2**2*(42*x1*x2-75*x2**2-248*y1*y2-1293*y2**2-201*z**2)+5*y2*(-42*x1*x2*y2+x2**2*(16*y1+93*y2)+(58*y1+225*y2)*(y2**2+z**2))))-2*l2*(r1+r2)**3*(210*r2**12*y1**2*(y1**2+z**2)+r1**4*r2**8*(24*r2**4+3*y1*(-32*x1*x2*(3*y1+y2)+x1**2*(80*y1+48*y2)+y1*(-x2**2+80*y1**2+144*y1*y2+15*y2**2))+3*(87*y1**2+144*y1*y2+8*y2**2)*z**2+r2**2*(19*x1**2+18*x1*x2-1421*y1**2-72*y1*y2-209*z**2))+r1**8*r2**4*(116*r2**4-48*(2*x1-3*x2)*x2*y1*y2-3*(x1**2+96*x1*x2-80*x2**2-15*y1**2)*y2**2+432*y1*y2**3+240*y2**4+3*(8*y1**2+144*y1*y2+87*y2**2)*z**2+r2**2*(x1**2+80*x1*x2-26*x2**2-23*y1**2-424*y1*y2-386*y2**2-89*z**2))+r1**7*r2**5*(148*r2**4+48*x1*x2*y2*(-4*y1+3*y2)+6*x2**2*y1*(5*y1+68*y2)+3*y2**2*(13*x1**2+55*y1**2+88*y1*y2)+3*(26*y1**2+88*y1*y2+29*y2**2)*z**2+r2**2*(-13*x1**2+64*x1*x2-13*x2**2-91*y1**2-464*y1*y2-91*y2**2-58*z**2))+3*r1*r2**11*y1**2*(-16*x1**2+299*(y1**2+z**2))+r1**12*(24*r2**4+210*y2**2*(y2**2+z**2)-30*r2**2*(7*y2**2+z**2))+r1**5*r2**7*(100*r2**4+48*x1*x2*y1*(3*y1-4*y2)+6*x1**2*y2*(68*y1+5*y2)+3*y1**2*(13*x2**2+88*y1*y2+55*y2**2)+3*(29*y1**2+88*y1*y2+26*y2**2)*z**2-r2**2*(x1**2-66*x1*x2+1060*y1**2+264*y1*y2+172*z**2))+r1**6*r2**6*(166*r2**4+r2**2*(-26*x1**2+80*x1*x2+x2**2-386*y1**2-424*y1*y2-23*y2**2-89*z**2)+24*(x2**2*y1*(8*y1+y2)+y1*y2*(3*y1+y2)*(y1+3*y2)+x1**2*y2*(y1+8*y2)-2*x1*x2*(y1**2-6*y1*y2+y2**2)+(5*y1**2+6*y1*y2+5*y2**2)*z**2))+3*r1**2*r2**10*(-10*r2**2*(7*y1**2+z**2)+y1*(-37*x1**2*y1-30*x1*x2*y1+(473*y1+30*y2)*(y1**2+z**2)))+r1**3*r2**9*(r2**2*(8*x1**2-889*y1**2-127*z**2)+3*y1*(19*x1**2*y1-110*x1*x2*y1+2*(162*y1+55*y2)*(y1**2+z**2)))+r1**11*r2*(100*r2**4+r2**2*(8*x2**2-889*y2**2-127*z**2)+3*y2**2*(-16*x2**2+299*(y2**2+z**2)))+r1**9*r2**3*(148*r2**4+r2**2*(66*x1*x2-x2**2-264*y1*y2-1060*y2**2-172*z**2)+3*y2*(-110*x1*x2*y2+19*x2**2*y2+2*(55*y1+162*y2)*(y2**2+z**2)))+r1**10*r2**2*(166*r2**4+r2**2*(18*x1*x2+19*x2**2-72*y1*y2-1421*y2**2-209*z**2)+3*y2*(-30*x1*x2*y2-37*x2**2*y2+(30*y1+473*y2)*(y2**2+z**2))))),
               (y1-y2)*(l2**4*(12*r1**4*r2**9*y1-15*r1**2*r2**9*y1**3-105*r2**9*x1**2*y1*z**2-3*r1**9*y2*(-4*r2**4-35*z**2*(y2**2+z**2)+5*r2**2*(y2**2+7*z**2)))+l2**3*(-420*r2**11*y1*z**2*(y1**2+z**2)+2*r1**3*r2**10*y1*(8*x1**2+83*y1**2+581*z**2)-4*r1**8*r2**5*(r2**2*(4*y1+15*y2)-2*(2*y1+5*y2)*(x2**2+y2**2)+(-4*y1-25*y2)*z**2)+r1**11*(-48*r2**4*y2+60*r2**2*y2**3+420*x2**2*y2*z**2)-2*r1**10*r2*y2*(r2**2*(68*r2**2-8*x2**2-83*y2**2)+(-581*r2**2+48*x2**2+573*y2**2)*z**2+573*z**4)+4*r1**5*r2**6*(-2*r2**2*(17*r2**2*y1-(x1**2+y1**2)*(5*y1+2*y2))+(x1**2*(-10*y1-19*y2)+r2**2*(25*y1+4*y2)+y1*(-15*x2**2-10*y1**2-19*y1*y2-15*y2**2))*z**2+(-25*y1-19*y2)*z**4)+30*r1**2*r2**9*(2*r2**2*y1**3-y1*(-14*r2**2+x1*(13*x1-2*x2)+y1*(33*y1+y2))*z**2-(33*y1+y2)*z**4)+2*r1**4*r2**7*(r2**2*y1*(-24*r2**2+31*x1**2-6*x1*x2+73*y1**2+3*y1*y2)+5*(-8*x1**2*(y1+y2)+r2**2*(107*y1+3*y2)+y1*(-3*x2**2-8*y1**2-8*y1*y2-3*y2**2))*z**2-5*(11*y1+8*y2)*z**4)+2*r1**7*r2**4*(r2**2*(10*x2**2*y1+r2**2*(-30*y1-8*y2)+5*y2*(x1**2+y1**2+2*y1*y2))+(-15*(x1**2+y1**2)*y2-40*y1*y2**2-40*y2**3-40*x2**2*(y1+y2)+r2**2*(70*y1+83*y2))*z**2-5*(8*y1+11*y2)*z**4)+2*r1**6*r2**5*(r2**2*(10*x1**2*y2-2*r2**2*(34*y1+y2)+5*y1*(x2**2+2*y1*y2+y2**2))+(x2**2*(-38*y1-20*y2)-30*(x1**2+y1**2)*y2-38*y1*y2**2-20*y2**3+r2**2*(83*y1+70*y2))*z**2-2*(19*y1+25*y2)*z**4)-6*r1*r2**10*y1*z**2*(16*x1**2+191*(y1**2+z**2))+2*r1**9*r2**2*(-2*r2**4*(y1+34*y2)-15*y2*(x2*(-2*x1+13*x2)+y2*(y1+33*y2))*z**2-15*(y1+33*y2)*z**4+r2**2*(-6*x1*x2*y2+31*x2**2*y2+3*y1*y2**2+73*y2**3+15*y1*z**2+535*y2*z**2)))-2*l2*(r1+r2)**4*(210*r2**11*y1*z**2*(y1**2+z**2)+r1**11*(24*r2**4*y2-30*r2**2*y2**3-210*x2**2*y2*z**2)+r1**10*r2*y2*(r2**2*(76*r2**2+8*x2**2-97*y2**2)+(-679*r2**2-48*x2**2+687*y2**2)*z**2+687*z**4)+2*r1**5*r2**6*(2*r2**2*y1*(19*r2**2-3*x1**2+12*x1*x2-9*y1**2-6*y1*y2)-3*(x1**2*(-2*y1-27*y2)+8*x1*x2*(-3*y1+y2)+r2**2*(65*y1+20*y2)+y1*(-7*x2**2-6*y1**2-15*y1*y2-11*y2**2))*z**2+3*(17*y1+15*y2)*z**4)+3*r1**2*r2**9*(-10*r2**2*y1**3+y1*(-70*r2**2-21*x1**2-30*x1*x2+259*y1**2+15*y1*y2)*z**2+(259*y1+15*y2)*z**4)+r1**6*r2**5*(r2**2*(-14*x1**2*y2+16*x1*x2*(y1+y2)+6*r2**2*(14*y1+y2)+y1*(x2**2-22*y1*y2-7*y2**2))+(162*x2**2*y1+r2**2*(-169*y1-154*y2)-48*x1*x2*(y1-3*y2)+42*x1**2*y2+12*x2**2*y2+66*y1**2*y2+90*y1*y2**2+36*y2**3)*z**2+6*(15*y1+17*y2)*z**4)+r1**7*r2**4*(r2**2*(x1**2*y2+16*x1*x2*(y1+y2)+r2**2*(42*y1+16*y2)+y1*(-14*x2**2-7*y1*y2-22*y2**2))+(-24*(2*x1-3*x2)*x2*y1+r2**2*(-154*y1-169*y2)-3*(x1**2+32*x1*x2-64*x2**2-7*y1**2)*y2+120*y1*y2**2+96*y2**3)*z**2+3*(40*y1+39*y2)*z**4)+r1**4*r2**7*(r2**2*y1*(24*r2**2+11*x1**2+18*x1*x2-103*y1**2-9*y1*y2)+(r2**2*(-769*y1-45*y2)-48*x1*x2*(2*y1+y2)+x1**2*(192*y1+72*y2)+3*y1*(-x2**2+32*y1**2+40*y1*y2+7*y2**2))*z**2+3*(39*y1+40*y2)*z**4)+r1**3*r2**8*(120*y1*(x1*(x1-2*x2)+y1*(3*y1+y2))*z**2+120*(3*y1+y2)*z**4+r2**2*y1*(8*x1**2-97*y1**2-679*z**2))+3*r1*r2**10*y1*z**2*(-16*x1**2+229*(y1**2+z**2))+r1**9*r2**2*(6*r2**4*(y1+14*y2)+3*y2*(-30*x1*x2-21*x2**2+15*y1*y2+259*y2**2)*z**2+3*(15*y1+259*y2)*z**4+r2**2*(y2*(18*x1*x2+11*x2**2-9*y1*y2-103*y2**2)+(-45*y1-769*y2)*z**2))+2*r1**8*r2**3*(r2**4*(8*y1+21*y2)+60*y2*(-2*x1*x2+x2**2+y2*(y1+3*y2))*z**2+60*(y1+3*y2)*z**4+r2**2*(24*x1*x2*y2-6*y2*(x2**2+2*y1*y2+3*y2**2)-15*(4*y1+13*y2)*z**2)))+(r1+r2)**6*(105*r2**11*y1*z**2*(y1**2+z**2)+r1**11*(12*r2**4*y2-15*r2**2*y2**3-105*x2**2*y2*z**2)+4*r1**10*r2*y2*(r2**2*(10*r2**2+2*x2**2-13*y2**2)+(-91*r2**2-12*x2**2+93*y2**2)*z**2+93*z**4)+2*r1**6*r2**5*(r2**2*((23*r2**2+2*x2*(16*x1+x2))*y1+2*(r2**2+4*x1*(x1+x2)-4*y1**2)*y2-2*y1*y2**2)+(-3*x2*(32*x1+13*x2)*y1+r2**2*(-59*y1-56*y2)-12*(2*x1**2+14*x1*x2+x2**2-2*y1**2)*y2+45*y1*y2**2+12*y2**3)*z**2+9*(5*y1+4*y2)*z**4)+2*r1**5*r2**6*(r2**2*(20*r2**2*y1+24*x1*x2*y1+3*x1**2*(4*y1+y2)-3*y1**2*(4*y1+3*y2))-3*(-4*y1*(-11*r2**2-x1**2-14*x1*x2-2*x2**2+y1**2)+(15*r2**2+13*x1**2+32*x1*x2-15*y1**2)*y2-8*y1*y2**2)*z**2+9*(4*y1+5*y2)*z**4)+2*r1**7*r2**4*(r2**2*(8*x2*(x1+x2)*y1+2*(x1**2+16*x1*x2-y1**2)*y2-8*y1*y2**2+r2**2*(12*y1+5*y2))+(-12*x2*(2*x1+3*x2)*y1+r2**2*(-56*y1-59*y2)-6*(x1**2+32*x1*x2+8*x2**2-y1**2)*y2+60*y1*y2**2+48*y2**3)*z**2+6*(10*y1+9*y2)*z**4)+r1**4*r2**7*(r2**2*y1*(12*r2**2+26*x1**2+12*x1*x2-61*y1**2-6*y1*y2)+(x1**2*(-96*y1-72*y2)+r2**2*(-457*y1-30*y2)-48*x1*x2*(8*y1+y2)+12*y1*(-x2**2+8*y1**2+10*y1*y2+y2**2))*z**2+12*(9*y1+10*y2)*z**4)+3*r1**2*r2**9*(-5*r2**2*y1**3+y1*(-35*r2**2-54*x1**2-20*x1*x2+161*y1**2+10*y1*y2)*z**2+(161*y1+10*y2)*z**4)+12*r1*r2**10*y1*z**2*(-4*x1**2+31*(y1**2+z**2))+r1**9*r2**2*(r2**4*(4*y1+46*y2)+3*y2*(-20*x1*x2-54*x2**2+10*y1*y2+161*y2**2)*z**2+3*(10*y1+161*y2)*z**4+r2**2*(y2*(12*x1*x2+26*x2**2-6*y1*y2-61*y2**2)+(-30*y1-457*y2)*z**2))+4*r1**3*r2**8*(r2**2*y1*(2*x1**2-13*y1**2-91*z**2)+6*z**2*(-10*x1*x2*y1-x1**2*(8*y1+y2)+4*(3*y1+y2)*(y1**2+z**2)))+2*r1**8*r2**3*(r2**4*(5*y1+12*y2)+3*r2**2*(8*x1*x2*y2-3*y1*y2**2-4*y2**3+x2**2*(y1+4*y2)-15*y1*z**2-44*y2*z**2)+12*z**2*(-10*x1*x2*y2-x2**2*(y1+8*y2)+4*(y1+3*y2)*(y2**2+z**2))))+2*l2**2*(r1+r2)**2*(945*r1*r2**10*y1*z**2*(y1**2+z**2)+315*r2**11*y1*z**2*(y1**2+z**2)+r1**11*(36*r2**4*y2-45*r2**2*y2**3-315*x2**2*y2*z**2)+r1**10*(108*r2**5*y2-135*r2**3*y2**3-945*r2*x2**2*y2*z**2)+15*r1**2*r2**9*(-3*r2**2*y1**3+y1*(-21*r2**2+11*x1**2-6*x1*x2+62*y1**2+3*y1*y2)*z**2+(62*y1+3*y2)*z**4)+2*r1**5*r2**6*(r2**2*(54*r2**2*y1+12*x1*x2*y1+y1**2*(-20*y1-11*y2)-5*x1**2*(4*y1+y2))+(r2**2*(-211*y1-55*y2)+20*x1**2*(y1+y2)+x1*(-36*x2*y1+24*x2*y2)+y1*(45*x2**2+20*y1**2+38*y1*y2+33*y2**2))*z**2+(53*y1+38*y2)*z**4)+r1**6*r2**5*(r2**2*(-30*x1**2*y2+8*x1*x2*(-2*y1+y2)+6*r2**2*(19*y1+y2)+y1*(-5*x2**2-22*y1*y2-9*y2**2))+(48*x1*x2*y1+40*x2**2*y1+r2**2*(-177*y1-154*y2)+90*x1**2*y2-72*x1*x2*y2+40*x2**2*y2+66*y1**2*y2+76*y1*y2**2+40*y2**3)*z**2+2*(38*y1+53*y2)*z**4)+r1**4*r2**7*(r2**2*y1*(36*r2**2-25*x1**2+18*x1*x2-130*y1**2-9*y1*y2)+(y1*(-964*r2**2+80*x1**2+96*x1*x2+15*x2**2+80*y1**2)+(-45*r2**2+140*x1**2-24*x1*x2+92*y1**2)*y2+27*y1*y2**2)*z**2+(107*y1+92*y2)*z**4)+r1**7*r2**4*(r2**2*(8*x1*x2*(y1-2*y2)-5*x1**2*y2+18*r2**2*(3*y1+y2)+y1*(-30*x2**2-9*y1*y2-22*y2**2))+(-4*(6*x1-35*x2)*x2*y1+r2**2*(-154*y1-177*y2)+(15*x1**2+96*x1*x2+80*x2**2+27*y1**2)*y2+92*y1*y2**2+80*y2**3)*z**2+(92*y1+107*y2)*z**4)+r1**9*r2**2*(6*r2**4*(y1+19*y2)+15*y2*(-6*x1*x2+11*x2**2+3*y1*y2+62*y2**2)*z**2+15*(3*y1+62*y2)*z**4+r2**2*(y2*(18*x1*x2-25*x2**2-9*y1*y2-130*y2**2)+(-45*y1-964*y2)*z**2))+5*r1**3*r2**8*(-27*r2**2*(y1**3+7*y1*z**2)+4*z**2*(-6*x1*x2*y1+x1**2*(17*y1+2*y2)+(17*y1+5*y2)*(y1**2+z**2)))+2*r1**8*r2**3*(9*r2**4*(y1+3*y2)+r2**2*(12*x1*x2*y2-11*y1*y2**2-20*y2**3-5*x2**2*(y1+4*y2)-55*y1*z**2-211*y2*z**2)+10*z**2*(-6*x1*x2*y2+x2**2*(2*y1+17*y2)+(5*y1+17*y2)*(y2**2+z**2))))),
               (y1-y2)*z*(l2**4*(36*r1**4*r2**9+105*r2**9*z**2*(y1**2+z**2)-45*r1**2*r2**9*(y1**2+3*z**2)+r1**9*(36*r2**4+105*z**2*(y2**2+z**2)-45*r2**2*(y2**2+3*z**2)))-2*l2**3*(r1+r2)*(210*r2**10*z**2*(y1**2+z**2)+r1**8*(78*r2**6-30*r2**2*x1*x2*z**2+r2**4*(18*x1*x2-69*(x2**2+y2**2)-78*z**2))+r1**5*(132*r2**9+30*r2**5*x1*x2*z**2+r2**7*(-15*x1**2-18*x1*x2-15*y1**2-58*z**2))+r1**7*(36*r2**7+30*r2**3*x1*x2*z**2+r2**5*(-18*x1*x2-15*(x2**2+y2**2)-21*z**2))+3*r1*r2**9*z**2*(16*x1**2+121*(y1**2+z**2))+r1**10*(72*r2**4+210*z**2*(y2**2+z**2)-90*r2**2*(y2**2+3*z**2))+r1**4*r2**6*(72*r2**4+r2**2*(-69*x1**2+18*x1*x2-69*y1**2-225*z**2)+z**2*(43*x1**2-30*x1*x2+15*x2**2+43*y1**2+15*y2**2+58*z**2))+r1**6*r2**4*(78*r2**4+r2**2*(-15*x1**2+18*x1*x2-15*(x2**2+y1**2+y2**2)-110*z**2)+z**2*(15*x1**2-30*x1*x2+43*x2**2+15*y1**2+43*y2**2+58*z**2))-3*r1**2*r2**8*(30*r2**2*(y1**2+3*z**2)+z**2*(-49*x1**2+10*x1*x2-49*(y1**2+z**2)))+r1**3*r2**7*(r2**2*(-24*x1**2-159*y1**2-477*z**2)+z**2*(37*x1**2+30*x1*x2+37*(y1**2+z**2)))+3*r1**9*r2*(44*r2**4+r2**2*(-8*x2**2-53*y2**2-159*z**2)+z**2*(16*x2**2+121*(y2**2+z**2))))-6*l2*(r1+r2)**5*(70*r2**10*z**2*(y1**2+z**2)+r1**6*r2**4*(r2**2*(38*r2**2+(x1+x2)**2-7*(y1**2+y2**2))-(62*r2**2+x1**2-2*x1*x2-43*x2**2-7*y1**2-27*y2**2)*z**2+34*z**4)+r1**4*r2**6*(3*r2**2*(8*r2**2+x1**2+6*x1*x2-15*y1**2)+(-146*r2**2+43*x1**2+2*x1*x2+27*y1**2+8*y2**2)*z**2+35*z**4)+r1**5*(52*r2**9+30*r2**5*x1*x2*z**2+r2**7*(-15*(x1**2-2*x1*x2+y1**2)-50*z**2))+r1**7*(20*r2**7-50*r2**3*x1*x2*z**2+r2**5*(30*x1*x2-15*(x2**2+y2**2)-5*z**2))+r1*r2**9*z**2*(-16*x1**2+159*(y1**2+z**2))+r1**10*(24*r2**4+70*z**2*(y2**2+z**2)-30*r2**2*(y2**2+3*z**2))-5*r1**2*r2**8*(6*r2**2*(y1**2+3*z**2)+z**2*(x1**2+6*x1*x2-23*(y1**2+z**2)))+r1**3*r2**7*(r2**2*(8*x1**2-67*y1**2-201*z**2)+5*z**2*(9*x1**2-10*x1*x2+9*(y1**2+z**2)))+r1**8*r2**2*(38*r2**4+r2**2*(3*x2*(6*x1+x2)-45*y2**2-145*z**2)-5*z**2*(6*x1*x2+x2**2-23*(y2**2+z**2)))+r1**9*r2*(52*r2**4+r2**2*(8*x2**2-67*y2**2-201*z**2)+z**2*(-16*x2**2+159*(y2**2+z**2))))+2*l2**2*(r1+r2)**3*(630*r1*r2**9*z**2*(y1**2+z**2)+315*r2**10*z**2*(y1**2+z**2)+r1**10*(108*r2**4+315*z**2*(y2**2+z**2)-135*r2**2*(y2**2+3*z**2))+r1**9*(216*r2**5+630*r2*z**2*(y2**2+z**2)-270*r2**3*(y2**2+3*z**2))+r1**4*r2**6*(108*r2**4+r2**2*(-75*x1**2+54*x1*x2-147*y1**2-477*z**2)+z**2*(5*x1**2+102*x1*x2+15*x2**2+77*y1**2+27*y2**2+104*z**2))+r1**6*r2**4*(144*r2**4+r2**2*(-15*x1**2-42*x1*x2-15*x2**2-27*(y1**2+y2**2)-214*z**2)+z**2*(15*x1**2+102*x1*x2+5*x2**2+27*y1**2+77*y2**2+104*z**2))-15*r1**2*r2**8*(9*r2**2*(y1**2+3*z**2)+z**2*(-11*x1**2+6*x1*x2-23*(y1**2+z**2)))-5*r1**3*r2**7*(54*r2**2*(y1**2+3*z**2)+z**2*(-43*x1**2+6*x1*x2-19*(y1**2+z**2)))+r1**7*r2**3*(72*r2**4+r2**2*(18*x1*x2-75*x2**2-39*y2**2-179*z**2)+5*z**2*(-6*x1*x2+43*x2**2+19*(y2**2+z**2)))+3*r1**8*r2**2*(48*r2**4+r2**2*(18*x1*x2-25*x2**2-49*y2**2-159*z**2)+5*z**2*(-6*x1*x2+11*x2**2+23*(y2**2+z**2)))+r1**5*r2**5*(216*r2**4+r2**2*(-75*x1**2+18*x1*x2-39*y1**2-179*z**2)+3*z**2*(25*x1**2-42*x1*x2+25*x2**2+13*(y1**2+y2**2+2*z**2))))+3*(r1+r2)**7*(35*r2**10*z**2*(y1**2+z**2)+2*r1**7*r2**3*(6*r2**2*x2*(3*x1+2*x2)+(-19*r2**2-30*x1*x2-17*x2**2+23*y2**2)*z**2+23*z**4)+r1**2*r2**8*(-15*r2**2*y1**2+(-45*r2**2-38*x1**2-20*x1*x2+82*y1**2)*z**2+82*z**4)+r1*r2**9*z**2*(-16*x1**2+89*(y1**2+z**2))+r1**10*(12*r2**4+35*z**2*(y2**2+z**2)-15*r2**2*(y2**2+3*z**2))+2*r1**4*r2**6*(6*r2**4+r2**2*(9*x1**2+6*x1*x2-15*y1**2-48*z**2)+z**2*(-11*x1**2-42*x1*x2-2*x2**2+13*y1**2+2*y2**2+15*z**2))+2*r1**6*r2**4*(11*r2**4+2*r2**2*(x1**2+11*x1*x2+x2**2-y1**2-y2**2-10*z**2)+z**2*(-2*x1**2-42*x1*x2-11*x2**2+2*y1**2+13*y2**2+15*z**2))+r1**3*r2**7*(r2**2*(8*x1**2-37*y1**2-111*z**2)+2*z**2*(-17*x1**2-30*x1*x2+23*(y1**2+z**2)))+2*r1**8*r2**2*(11*r2**4+r2**2*(6*x1*x2+9*x2**2-15*y2**2-48*z**2)+z**2*(-10*x1*x2-19*x2**2+41*(y2**2+z**2)))+r1**9*r2*(28*r2**4+r2**2*(8*x2**2-37*y2**2-111*z**2)+z**2*(-16*x2**2+89*(y2**2+z**2)))+2*r1**5*r2**5*(14*r2**4+r2**2*(6*x1*(x1+3*x2)-6*y1**2-25*z**2)+2*z**2*(-3*x1**2-23*x1*x2+3*(-x2**2+y1**2+y2**2+2*z**2))))),
               3*(y1-y2)*(l2**4*(15*r1**2*r2**9*x1*y1-35*r2**9*x1*y1**3+r1**9*(15*r2**2*x2*y2-35*x2*y2**3))+l2**3*(-150*r1**3*r2**10*x1*y1+350*r1*r2**10*x1*y1**3+140*r2**11*x1*y1**3-6*r1**4*r2**9*(15*x1*y1+x2*y1+x1*y2)+r1**11*(-60*r2**2*x2*y2+140*x2*y2**3)+r1**10*(-150*r2**3*x2*y2+350*r2*x2*y2**3)+10*r1**2*r2**9*y1*(-6*r2**2*x1+y1*(19*x1*y1+x2*y1+3*x1*y2))-2*r1**9*r2**2*(3*r2**2*(x2*y1+x1*y2+15*x2*y2)-5*y2**2*(3*x2*y1+x1*y2+19*x2*y2)))-2*l2*(r1+r2)**3*(-315*r1*r2**11*x1*y1**3-70*r2**12*x1*y1**3+r1**12*(30*r2**2*x2*y2-70*x2*y2**3)+r1**11*(135*r2**3*x2*y2-315*r2*x2*y2**3)+5*r1**3*r2**9*y1*(27*r2**2*x1+y1*(-61*x1*y1-11*x2*y1-33*x1*y2))+15*r1**2*r2**10*y1*(2*r2**2*x1-y1*(34*x1*y1+x2*y1+3*x1*y2))+3*r1**10*r2**2*(-5*y2**2*(3*x2*y1+x1*y2+34*x2*y2)+r2**2*(3*x2*y1+3*x1*y2+76*x2*y2))+r1**9*r2**3*(-5*y2**2*(33*x2*y1+11*x1*y2+61*x2*y2)+r2**2*(33*x2*y1+33*x1*y2+171*x2*y2))+3*r1**5*r2**7*(r2**2*(57*x1*y1+11*x2*y1+11*x1*y2)+8*y1*(x2*y1*(y1-2*y2)+x1*(3*y1-2*y2)*y2))+8*r1**6*r2**6*(r2**2*(7*x1*y1+5*x2*y1+5*x1*y2+x2*y2)-x1*y2*(3*y1**2-9*y1*y2+y2**2)-x2*y1*(y1**2-9*y1*y2+3*y2**2))+3*r1**4*r2**8*(r2**2*(76*x1*y1+3*x2*y1+3*x1*y2)-8*y1*(x2*y1*(2*y1+y2)+x1*y2*(6*y1+y2)))+8*r1**7*r2**5*(2*r2**2*(x1*y1+2*x2*y1+2*x1*y2+x2*y2)+3*y2*(x1*y2*(-2*y1+y2)+x2*y1*(-2*y1+3*y2)))+8*r1**8*r2**4*(r2**2*(x1*y1+5*x2*y1+5*x1*y2+7*x2*y2)-3*y2*(x1*y2*(y1+2*y2)+x2*y1*(y1+6*y2))))+l2**2*(-1050*r1*r2**12*x1*y1**3-210*r2**13*x1*y1**3+30*r1**5*r2**10*(25*x1*y1+2*x2*y1+2*x1*y2)+2*r1**6*r2**9*(130*x1*y1+29*x2*y1+29*x1*y2+4*x2*y2)+r1**13*(90*r2**2*x2*y2-210*x2*y2**3)+r1**12*(450*r2**3*x2*y2-1050*r2*x2*y2**3)+50*r1**3*r2**10*y1*(9*r2**2*x1+y1*(-31*x1*y1-2*x2*y1-6*x1*y2))+30*r1**2*r2**11*y1*(3*r2**2*x1-y1*(65*x1*y1+x2*y1+3*x1*y2))+10*r1**10*r2**3*(-5*y2**2*(6*x2*y1+2*x1*y2+31*x2*y2)+r2**2*(6*x2*y1+6*x1*y2+75*x2*y2))+6*r1**11*r2**2*(-5*y2**2*(3*x2*y1+x1*y2+65*x2*y2)+r2**2*(3*x2*y1+3*x1*y2+143*x2*y2))+2*r1**9*r2**4*(r2**2*(4*x1*y1+29*x2*y1+29*x1*y2+130*x2*y2)+y2*(x1*(-12*y1-35*y2)*y2+x2*(-12*y1**2-105*y1*y2-220*y2**2)))+2*r1**4*r2**9*(r2**2*(429*x1*y1+9*x2*y1+9*x1*y2)+y1*(x2*y1*(-35*y1-12*y2)+x1*(-220*y1**2-105*y1*y2-12*y2**2))))+(r1+r2)**5*(-175*r1*r2**11*x1*y1**3-35*r2**12*x1*y1**3+r1**12*(15*r2**2*x2*y2-35*x2*y2**3)+r1**11*(75*r2**3*x2*y2-175*r2*x2*y2**3)+5*r1**2*r2**10*y1*(3*r2**2*x1+y1*(-69*x1*y1-2*x2*y1-6*x1*y2))+25*r1**3*r2**9*y1*(3*r2**2*x1+y1*(-13*x1*y1-2*x2*y1-6*x1*y2))+5*r1**9*r2**3*(-5*y2**2*(6*x2*y1+2*x1*y2+13*x2*y2)+r2**2*(6*x2*y1+6*x1*y2+33*x2*y2))+r1**10*r2**2*(-5*y2**2*(6*x2*y1+2*x1*y2+69*x2*y2)+r2**2*(6*x2*y1+6*x1*y2+153*x2*y2))+8*r1**6*r2**6*(r2**2*(13*x1*y1+8*x2*y1+8*x1*y2+x2*y2)+x2*y1*(-2*y1**2-27*y1*y2-6*y2**2)+x1*y2*(-6*y1**2-27*y1*y2-2*y2**2))+40*r1**7*r2**5*(r2**2*(x1*y1+2*x2*y1+2*x1*y2+x2*y2)+y2*(x1*(-3*y1-2*y2)*y2-3*x2*y1*(y1+2*y2)))+5*r1**5*r2**7*(r2**2*(33*x1*y1+6*x2*y1+6*x1*y2)-8*y1*(3*x1*y2*(2*y1+y2)+x2*y1*(2*y1+3*y2)))+8*r1**8*r2**4*(r2**2*(x1*y1+8*x2*y1+8*x1*y2+13*x2*y2)+y2*(-3*x1*y2*(y1+4*y2)+x2*(-3*y1**2-36*y1*y2-16*y2**2)))+r1**4*r2**8*(r2**2*(153*x1*y1+6*x2*y1+6*x1*y2)-8*y1*(3*x2*y1*(4*y1+y2)+x1*(16*y1**2+36*y1*y2+3*y2**2)))))*z,
               (-y1+y2)*(3*l2**4*(r1**4*r2**9*x1+35*r2**9*x1*y1**2*z**2-5*r1**2*r2**9*x1*(y1**2+z**2)+r1**9*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2)))-2*l2**3*(15*r1**5*r2**10*x1+r1**6*r2**9*(10*x1+x2)+525*r1*r2**10*x1*y1**2*z**2+210*r2**11*x1*y1**2*z**2-75*r1**3*r2**10*x1*(y1**2+z**2)+15*r1**2*r2**9*(y1*(20*x1*y1+x2*y1+2*x1*y2)*z**2-2*r2**2*x1*(y1**2+z**2))+3*r1**4*r2**9*(2*r2**2*x1-2*x1*y1*(7*y1+y2)-16*x1*z**2-x2*(y1**2+z**2))+6*r1**11*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2))+15*r1**10*r2*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2))+r1**9*r2**2*(r2**4*(x1+10*x2)+15*y2*(2*x2*y1+x1*y2+20*x2*y2)*z**2-3*r2**2*(y2*(2*x2*y1+x1*y2+14*x2*y2)+(x1+16*x2)*z**2)))-2*l2*(r1+r2)**4*(735*r1*r2**10*x1*y1**2*z**2+210*r2**11*x1*y1**2*z**2+8*r1**7*r2**4*(r2**2*(r2**2*(2*x1+x2)-x1*y2*(2*y1+y2)-x2*y1*(y1+2*y2))-3*(r2**2*(x1+x2)-2*x1*y2*(y1+y2)-x2*(y1**2+4*y1*y2-4*y2**2))*z**2)+r1**6*r2**5*(r2**2*(r2**2*(26*x1+3*x2)-8*x1*y2*(2*y1+y2)-8*x2*y1*(y1+2*y2))-24*(r2**2*(x1+x2)+x1*y2*(-2*y1+3*y2)-x2*(y1**2-6*y1*y2+y2**2))*z**2)-15*r1**2*r2**9*(y1*(-56*x1*y1-3*x2*y1-6*x1*y2)*z**2+2*r2**2*x1*(y1**2+z**2))-15*r1**3*r2**8*(-8*y1*(x2*y1+2*x1*(y1+y2))*z**2+7*r2**2*x1*(y1**2+z**2))+6*r1**11*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2))+21*r1**10*r2*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2))+3*r1**4*r2**7*(2*r2**4*x1+8*(2*x2*y1*(y1+y2)+x1*(-4*y1**2+4*y1*y2+y2**2))*z**2+r2**2*(y1*(-38*x1*y1-3*x2*y1-6*x1*y2)+(-44*x1-3*x2)*z**2))+3*r1**5*r2**6*(7*r2**4*x1+8*(x2*y1*(-3*y1+2*y2)+x1*(y1**2-6*y1*y2+y2**2))*z**2-8*r2**2*(y1*((x1+x2)*y1+2*x1*y2)+(3*x1+x2)*z**2))+8*r1**8*r2**3*(r2**4*(x1+2*x2)+15*y2*(x1*y2+2*x2*(y1+y2))*z**2-3*r2**2*(y2*(2*x2*y1+(x1+x2)*y2)+(x1+3*x2)*z**2))+r1**9*r2**2*(r2**4*(3*x1+26*x2)+15*y2*(6*x2*y1+3*x1*y2+56*x2*y2)*z**2+r2**2*(x2*(-18*y1*y2-114*y2**2-132*z**2)-9*x1*(y2**2+z**2))))+2*l2**2*(r1+r2)**2*(945*r1*r2**10*x1*y1**2*z**2+315*r2**11*x1*y1**2*z**2+15*r1**3*r2**8*(9*r2**2*x1**3+4*y1*(x2*y1+2*x1*y2)*z**2)+4*r1**7*r2**4*(r2**2*(r2**2*(2*x1+x2)-x2*y1*(y1-4*y2)+2*x1*y2*(-y1+y2))+3*(r2**2*x2+x2*y1*(y1-8*y2)+2*x1*(y1-2*y2)*y2)*z**2)+3*r1**4*r2**7*(r2**2*(3*r2**2*x1+y1*(-35*x1*y1-3*x2*y1-6*x1*y2))+(r2**2*(-41*x1-3*x2)+4*x1*y2*(-8*y1+y2)+8*x2*y1*(-2*y1+y2))*z**2)+r1**6*r2**5*(r2**2*(r2**2*(25*x1+3*x2)+8*x2*y1*(y1-y2)+4*x1*(4*y1-y2)*y2)+12*(r2**2*x1-2*x2*y1*(y1-3*y2)+x1*y2*(-4*y1+3*y2))*z**2)+12*r1**5*r2**6*(-(r2**2*(9*r2**2*x1+x2*y1**2+2*x1*y1*y2))-(r2**2*(2*x1+x2)+2*x1*y2*(-3*y1+y2)+x2*y1*(-3*y1+4*y2))*z**2)+45*r1**2*r2**9*(y1*(17*x1*y1+x2*y1+2*x1*y2)*z**2-r2**2*x1*(y1**2+z**2))+9*r1**11*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2))+27*r1**10*r2*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2))+4*r1**8*r2**3*(r2**4*(x1+2*x2)+15*y2*(2*x2*y1+x1*y2)*z**2-3*r2**2*(y2*(2*x2*y1+x1*y2)+(x1+2*x2)*z**2))+r1**9*r2**2*(r2**4*(3*x1+25*x2)+45*y2*(2*x2*y1+x1*y2+17*x2*y2)*z**2+r2**2*(x2*(-18*y1*y2-105*y2**2-123*z**2)-9*x1*(y2**2+z**2))))+(r1+r2)**6*(420*r1*r2**10*x1*y1**2*z**2+105*r2**11*x1*y1**2*z**2+8*r1**7*r2**4*(r2**2*(r2**2*(2*x1+x2)-2*x1*y2*(y1+2*y2)-x2*y1*(y1+8*y2))+3*(r2**2*(-2*x1-3*x2)+x2*y1**2+2*(x1+8*x2)*y1*y2+8*(x1+x2)*y2**2)*z**2)+r1**6*r2**5*(r2**2*(r2**2*(19*x1+2*x2)-16*x2*y1*(2*y1+y2)-8*x1*y2*(8*y1+y2))-24*(r2**2*(3*x1+2*x2)+x1*(-8*y1-7*y2)*y2-2*x2*(2*y1**2+7*y1*y2+y2**2))*z**2)-15*r1**2*r2**9*(y1*(-43*x1*y1-2*x2*y1-4*x1*y2)*z**2+r2**2*x1*(y1**2+z**2))-60*r1**3*r2**8*(-2*y1*(4*x1*y1+x2*y1+2*x1*y2)*z**2+r2**2*x1*(y1**2+z**2))+3*r1**11*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2))+12*r1**10*r2*x2*(r2**4+35*y2**2*z**2-5*r2**2*(y2**2+z**2))+3*r1**4*r2**7*(r2**4*x1+8*(8*(x1+x2)*y1**2+2*(8*x1+x2)*y1*y2+x1*y2**2)*z**2+r2**2*(y1*(-29*x1*y1-2*x2*y1-4*x1*y2)+(-33*x1-2*x2)*z**2))+12*r1**5*r2**6*(r2**4*x1+2*(x2*y1*(7*y1+8*y2)+2*x1*(y1**2+7*y1*y2+2*y2**2))*z**2-2*r2**2*(y1*(x2*y1+2*x1*(y1+y2))+(4*x1+x2)*z**2))+8*r1**8*r2**3*(r2**4*(x1+2*x2)+15*y2*(2*x2*y1+x1*y2+4*x2*y2)*z**2-3*r2**2*(y2*(x1*y2+2*x2*(y1+y2))+(x1+4*x2)*z**2))+r1**9*r2**2*(r2**4*(2*x1+19*x2)+15*y2*(4*x2*y1+2*x1*y2+43*x2*y2)*z**2+r2**2*(x2*(-12*y1*y2-87*y2**2-99*z**2)-6*x1*(y2**2+z**2))))),
               3*(y1-y2)*z*(l2**4*(15*r1**2*r2**9*x1*y1-35*r2**9*x1*y1*z**2+5*r1**9*x2*y2*(3*r2**2-7*z**2))+(r1+r2)**7*(-105*r1*r2**9*x1*y1*z**2-35*r2**10*x1*y1*z**2+5*r1**10*x2*y2*(3*r2**2-7*z**2)+15*r1**9*r2*x2*y2*(3*r2**2-7*z**2)+5*r1**2*r2**8*(3*r2**2*x1*y1-2*(12*x1*y1+x2*y1+x1*y2)*z**2)+5*r1**3*r2**7*(9*r2**2*x1*y1-2*(8*x1*y1+3*x2*y1+3*x1*y2)*z**2)+2*r1**6*r2**4*(r2**2*(4*x1*y1+11*x2*y1+11*x1*y2+4*x2*y2)+(-4*x1*y1-21*x2*y1-21*x1*y2-24*x2*y2)*z**2)+2*r1**5*r2**5*(r2**2*(12*x1*y1+9*x2*y1+9*x1*y2)+(-12*x1*y1-23*x2*y1-23*x1*y2-12*x2*y2)*z**2)+2*r1**4*r2**6*(3*r2**2*(8*x1*y1+x2*y1+x1*y2)+(-24*x1*y1-21*x2*y1-21*x1*y2-4*x2*y2)*z**2)+2*r1**7*r2**3*(r2**2*(9*x2*y1+9*x1*y2+12*x2*y2)-5*(3*x2*y1+3*x1*y2+8*x2*y2)*z**2)+2*r1**8*r2**2*(3*r2**2*(x2*y1+x1*y2+8*x2*y2)-5*(x2*y1+x1*y2+12*x2*y2)*z**2))-2*l2*(r1+r2)**5*(-175*r1*r2**9*x1*y1*z**2-70*r2**10*x1*y1*z**2+10*r1**10*x2*y2*(3*r2**2-7*z**2)+25*r1**9*r2*x2*y2*(3*r2**2-7*z**2)+5*r1**7*r2**3*(x2*y1+x1*y2)*(3*r2**2-5*z**2)+15*r1**5*r2**5*(x2*y1+x1*y2)*(r2**2+z**2)+25*r1**3*r2**7*(3*r2**2*x1*y1-(x2*y1+x1*y2)*z**2)+15*r1**2*r2**8*(2*r2**2*x1*y1-(8*x1*y1+x2*y1+x1*y2)*z**2)+r1**4*r2**6*(r2**2*(48*x1*y1+9*x2*y1+9*x1*y2)+(16*x1*y1+x2*y1+x1*y2-8*x2*y2)*z**2)+3*r1**8*r2**2*(r2**2*(3*x2*y1+3*x1*y2+16*x2*y2)-5*(x2*y1+x1*y2+8*x2*y2)*z**2)+r1**6*r2**4*(r2**2*(8*x1*y1+x2*y1+x1*y2+8*x2*y2)+(-8*x1*y1+x2*y1+x1*y2+16*x2*y2)*z**2))+l2**3*(-150*r1**3*r2**10*x1*y1-6*r1**4*r2**9*(15*x1*y1+x2*y1+x1*y2)+350*r1*r2**10*x1*y1*z**2+140*r2**11*x1*y1*z**2-20*r1**11*x2*y2*(3*r2**2-7*z**2)-50*r1**10*r2*x2*y2*(3*r2**2-7*z**2)+10*r1**2*r2**9*(-6*r2**2*x1*y1+(21*x1*y1+x2*y1+x1*y2)*z**2)-2*r1**9*r2**2*(3*r2**2*(x2*y1+x1*y2+15*x2*y2)-5*(x2*y1+x1*y2+21*x2*y2)*z**2))+2*l2**2*(15*r1**5*r2**10*(25*x1*y1+2*x2*y1+2*x1*y2)+r1**6*r2**9*(130*x1*y1+29*x2*y1+29*x1*y2+4*x2*y2)-525*r1*r2**12*x1*y1*z**2-105*r2**13*x1*y1*z**2+15*r1**13*x2*y2*(3*r2**2-7*z**2)+75*r1**12*r2*x2*y2*(3*r2**2-7*z**2)+25*r1**3*r2**10*(9*r2**2*x1*y1+(-35*x1*y1-2*x2*y1-2*x1*y2)*z**2)+15*r1**2*r2**11*(3*r2**2*x1*y1-(67*x1*y1+x2*y1+x1*y2)*z**2)+r1**9*r2**4*(r2**2*(4*x1*y1+29*x2*y1+29*x1*y2+130*x2*y2)+(-4*x1*y1-43*x2*y1-43*x1*y2-294*x2*y2)*z**2)+r1**4*r2**9*(r2**2*(429*x1*y1+9*x2*y1+9*x1*y2)+(-294*x1*y1-43*x2*y1-43*x1*y2-4*x2*y2)*z**2)+5*r1**10*r2**3*(r2**2*(6*x2*y1+6*x1*y2+75*x2*y2)-5*(2*x2*y1+2*x1*y2+35*x2*y2)*z**2)+3*r1**11*r2**2*(r2**2*(3*x2*y1+3*x1*y2+143*x2*y2)-5*(x2*y1+x1*y2+67*x2*y2)*z**2))),
               -3*(y1-y2)*(2*l2**2*(r1+r2)**4*(r1**4*r2**6*(3*r1**3-2*r1**2*r2+9*r1*r2**2+9*r2**3)*x1+r1**6*r2**4*(9*r1**3+9*r1**2*r2-2*r1*r2**2+3*r2**3)*x2-6*r1**2*r2**2*(15*r1*r2**6*x1+15*r2**7*x1+r1**5*r2**2*(3*x1-4*x2)+r1**3*r2**4*(x1-2*x2)+15*r1**7*x2+15*r1**6*r2*x2+r1**4*r2**3*(-2*x1+x2)+r1**2*r2**5*(-4*x1+3*x2))*z**2+(105*r1*r2**8*x1+105*r2**9*x1-3*r1**5*r2**4*(x1-4*x2)+15*r1**7*r2**2*(x1-2*x2)+3*r1**4*r2**5*(4*x1-x2)+105*r1**9*x2+105*r1**8*r2*x2+15*r1**2*r2**7*(-2*x1+x2)-5*r1**6*r2**3*(2*x1+x2)-5*r1**3*r2**6*(x1+2*x2))*z**4)-2*l2*(r1+r2)**6*(r1**4*r2**6*(3*r1+2*r2)*(r1**2+3*r2**2)*x1+r1**6*r2**4*(2*r1+3*r2)*(3*r1**2+r2**2)*x2-6*r1**2*r2**2*(15*r1*r2**6*x1+10*r2**7*x1+10*r1**7*x2+15*r1**6*r2*x2+r1**4*r2**3*(2*x1+x2)+r1**3*r2**4*(x1+2*x2)+r1**2*r2**5*(4*x1+3*x2)+r1**5*r2**2*(3*x1+4*x2))*z**2+(105*r1*r2**8*x1+70*r2**9*x1-5*r1**3*r2**6*(x1-2*x2)+5*r1**6*r2**3*(2*x1-x2)+70*r1**9*x2+105*r1**8*r2*x2+15*r1**2*r2**7*(2*x1+x2)-3*r1**4*r2**5*(4*x1+x2)+15*r1**7*r2**2*(x1+2*x2)-3*r1**5*r2**4*(x1+4*x2))*z**4)+(r1+r2)**8*(r1**4*r2**6*(2*r1**3+4*r1**2*r2+6*r1*r2**2+3*r2**3)*x1+r1**6*r2**4*(3*r1**3+6*r1**2*r2+4*r1*r2**2+2*r2**3)*x2-6*r1**2*r2**2*(10*r1*r2**6*x1+5*r2**7*x1+5*r1**7*x2+10*r1**6*r2*x2+2*r1**2*r2**5*(4*x1+x2)+2*r1**3*r2**4*(3*x1+2*x2)+2*r1**4*r2**3*(2*x1+3*x2)+2*r1**5*r2**2*(x1+4*x2))*z**2+5*(14*r1*r2**8*x1+7*r2**9*x1+7*r1**9*x2+14*r1**8*r2*x2+2*r1**2*r2**7*(6*x1+x2)+2*r1**3*r2**6*(5*x1+2*x2)+2*r1**4*r2**5*(4*x1+3*x2)+2*r1**5*r2**4*(3*x1+4*x2)+2*r1**6*r2**3*(2*x1+5*x2)+2*r1**7*r2**2*(x1+6*x2))*z**4)+l2**4*(3*r1**4*r2**9*x1-30*r1**2*r2**9*x1*z**2+35*r2**9*x1*z**4+r1**9*x2*(3*r2**4-30*r2**2*z**2+35*z**4))-2*l2**3*(15*r1**5*r2**10*x1+r1**6*r2**9*(10*x1+x2)-150*r1**3*r2**10*x1*z**2+175*r1*r2**10*x1*z**4+70*r2**11*x1*z**4+6*r1**4*r2**9*(r2**2*x1-(16*x1+x2)*z**2)+5*r1**2*r2**9*z**2*(-12*r2**2*x1+(22*x1+x2)*z**2)+2*r1**11*x2*(3*r2**4-30*r2**2*z**2+35*z**4)+5*r1**10*r2*x2*(3*r2**4-30*r2**2*z**2+35*z**4)+r1**9*r2**2*(r2**4*(x1+10*x2)-6*r2**2*(x1+16*x2)*z**2+5*(x1+22*x2)*z**4))),
               -3*(y1-y2)*(-2*l2**3*(r1**4*r2**6*(r1+r2)**3*(r1**2-3*r1*r2+6*r2**2)*y1+r1**2*r2**9*(-14*r1**2-25*r1*r2-10*r2**2)*y1**3+r1**4*r2**4*(r1**2*(r1+r2)**3*(6*r1**2-3*r1*r2+r2**2)-3*r2**5*y1**2)*y2-3*r1**9*r2**4*y1*y2**2+r1**9*r2**2*(-10*r1**2-25*r1*r2-14*r2**2)*y2**3+(-75*r1**3*r2**10*y1+175*r1*r2**10*y1**3+70*r2**11*y1**3-3*r1**4*r2**9*(16*y1+y2)-5*r1**2*r2**9*y1*(6*r2**2-20*y1**2-3*y1*y2)+r1**11*(-30*r2**2*y2+70*y2**3)+r1**10*(-75*r2**3*y2+175*r2*y2**3)+r1**9*r2**2*(-3*r2**2*(y1+16*y2)+5*y2**2*(3*y1+20*y2)))*z**2)+l2**4*(3*r1**4*r2**9*y1+35*r2**9*y1**3*z**2-5*r1**2*r2**9*y1*(y1**2+3*z**2)+r1**9*y2*(3*r2**4+35*y2**2*z**2-5*r2**2*(y2**2+3*z**2)))+2*l2**2*(r1+r2)**2*(315*r1*r2**10*y1**3*z**2+105*r2**11*y1**3*z**2+4*r1**7*r2**4*(r2**4*(2*y1+y2)+3*y1*(y1-4*y2)*y2*z**2-r2**2*y2*(y1**2-2*y1*y2-3*z**2))-15*r1**2*r2**9*y1*(y1*(-17*y1-3*y2)*z**2+r2**2*(y1**2+3*z**2))-15*r1**3*r2**8*y1*(-4*y1*y2*z**2+3*r2**2*(y1**2+3*z**2))+r1**6*r2**5*(r2**4*(25*y1+3*y2)-12*y1*(2*y1-3*y2)*y2*z**2+4*r2**2*y1*(2*y1*y2-y2**2+3*z**2))+r1**4*r2**7*(9*r2**4*y1+12*y1*y2*(-4*y1+y2)*z**2+r2**2*(-35*y1**3-9*y1**2*y2-123*y1*z**2-9*y2*z**2))+r1**11*(9*r2**4*y2+105*y2**3*z**2-15*r2**2*(y2**3+3*y2*z**2))+r1**10*(27*r2**5*y2+315*r2*y2**3*z**2-45*r2**3*(y2**3+3*y2*z**2))+3*r1**5*r2**6*(9*r2**4*y1+4*y1*(3*y1-2*y2)*y2*z**2-4*r2**2*(y1**2*y2+(2*y1+y2)*z**2))+4*r1**8*(r2**7*(y1+2*y2)+15*r2**3*y1*y2**2*z**2-3*r2**5*(y1*y2**2+(y1+2*y2)*z**2))+r1**9*r2**2*(r2**4*(3*y1+25*y2)+15*y2**2*(3*y1+17*y2)*z**2+r2**2*(-35*y2**3-123*y2*z**2-9*y1*(y2**2+z**2))))+(r1+r2)**6*(140*r1*r2**10*y1**3*z**2+35*r2**11*y1**3*z**2+r1**4*r2**7*(r2**2*y1*(3*r2**2-29*y1**2-6*y1*y2)+(-99*r2**2*y1+64*y1**3-6*r2**2*y2+192*y1**2*y2+24*y1*y2**2)*z**2)+4*r1**5*r2**6*(r2**2*y1*(3*r2**2-4*y1**2-6*y1*y2)-2*(3*r2**2*(4*y1+y2)+y1*(-2*y1**2-21*y1*y2-12*y2**2))*z**2)-5*r1**2*r2**9*y1*(y1*(-43*y1-6*y2)*z**2+r2**2*(y1**2+3*z**2))-20*r1**3*r2**8*y1*(-2*y1*(4*y1+3*y2)*z**2+r2**2*(y1**2+3*z**2))+r1**11*y2*(3*r2**4+35*y2**2*z**2-5*r2**2*(y2**2+3*z**2))+r1**10*(12*r2**5*y2+140*r2*y2**3*z**2-20*r2**3*(y2**3+3*y2*z**2))+r1**6*r2**5*(r2**4*(19*y1+2*y2)+8*y2*(12*y1**2+21*y1*y2+2*y2**2)*z**2-8*r2**2*(y1*y2*(4*y1+y2)+3*(3*y1+2*y2)*z**2))+8*r1**7*r2**4*(r2**4*(2*y1+y2)+y2*(3*y1**2+24*y1*y2+8*y2**2)*z**2-r2**2*(y1*y2*(y1+4*y2)+3*(2*y1+3*y2)*z**2))+8*r1**8*r2**3*(r2**4*(y1+2*y2)+5*y2**2*(3*y1+4*y2)*z**2+r2**2*((-3*y1-2*y2)*y2**2-3*(y1+4*y2)*z**2))+r1**9*r2**2*(r2**4*(2*y1+19*y2)+5*y2**2*(6*y1+43*y2)*z**2+r2**2*(-29*y2**3-99*y2*z**2-6*y1*(y2**2+z**2))))-2*l2*(r1+r2)**4*(245*r1*r2**10*y1**3*z**2+70*r2**11*y1**3*z**2+r1**4*r2**7*(r2**2*y1*(6*r2**2-38*y1**2-9*y1*y2)+(-132*r2**2*y1-32*y1**3-9*r2**2*y2+48*y1**2*y2+24*y1*y2**2)*z**2)-5*r1**2*r2**9*y1*(y1*(-56*y1-9*y2)*z**2+2*r2**2*(y1**2+3*z**2))-5*r1**3*r2**8*y1*(-8*y1*(2*y1+3*y2)*z**2+7*r2**2*(y1**2+3*z**2))+r1**6*r2**5*(r2**4*(26*y1+3*y2)+8*y2*(3*y1**2-9*y1*y2+y2**2)*z**2-8*r2**2*(y1+y2)*(y1*y2+3*z**2))+8*r1**7*r2**4*(r2**4*(2*y1+y2)+y2*(3*y1**2+6*y1*y2-4*y2**2)*z**2-r2**2*(y1+y2)*(y1*y2+3*z**2))+r1**11*(6*r2**4*y2+70*y2**3*z**2-10*r2**2*(y2**3+3*y2*z**2))+r1**10*(21*r2**5*y2+245*r2*y2**3*z**2-35*r2**3*(y2**3+3*y2*z**2))+r1**5*r2**6*(21*r2**4*y1+8*y1*(y1**2-9*y1*y2+3*y2**2)*z**2-8*r2**2*(y1**2*(y1+3*y2)+3*(3*y1+y2)*z**2))+r1**9*r2**2*(r2**4*(3*y1+26*y2)+5*y2**2*(9*y1+56*y2)*z**2+r2**2*(-38*y2**3-132*y2*z**2-9*y1*(y2**2+z**2)))+8*r1**8*r2**3*(r2**4*(y1+2*y2)+5*y2**2*(3*y1+2*y2)*z**2-r2**2*(y2**3+9*y2*z**2+3*y1*(y2**2+z**2))))),
               -3*(y1-y2)*z*(l2**4*(3*r1**4*r2**9+35*r2**9*y1**2*z**2-5*r1**2*r2**9*(3*y1**2+z**2)+r1**9*(3*r2**4+35*y2**2*z**2-5*r2**2*(3*y2**2+z**2)))+2*l2**2*(r1+r2)**3*(210*r1*r2**9*y1**2*z**2+105*r2**10*y1**2*z**2+r1**4*r2**6*(9*r2**4+2*(12*y1**2-17*y1*y2+2*y2**2)*z**2+r2**2*(-24*y1**2-18*y1*y2-14*z**2))+2*r1**8*r2**2*(5*r2**4+15*y2*(y1+2*y2)*z**2+r2**2*(-9*y1*y2-12*y2**2-7*z**2))+10*r1**3*r2**7*(y1*(-4*y1+y2)*z**2-3*r2**2*(3*y1**2+z**2))-15*r1**2*r2**8*(-2*y1*(2*y1+y2)*z**2+r2**2*(3*y1**2+z**2))+2*r1**5*r2**5*(9*r2**4-3*(2*y1**2-7*y1*y2+2*y2**2)*z**2+r2**2*(6*y1**2-3*y1*y2+z**2))+2*r1**7*r2**3*(r2**4+5*(y1-4*y2)*y2*z**2+r2**2*(-3*(y1-2*y2)*y2+z**2))+2*r1**6*r2**4*(5*r2**4+(2*y1**2-17*y1*y2+12*y2**2)*z**2+r2**2*(-2*y1**2+7*y1*y2-2*y2**2+z**2))+r1**10*(9*r2**4+105*y2**2*z**2-15*r2**2*(3*y2**2+z**2))+r1**9*(18*r2**5+210*r2*y2**2*z**2-30*r2**3*(3*y2**2+z**2)))+(r1+r2)**7*(105*r1*r2**9*y1**2*z**2+35*r2**10*y1**2*z**2+4*r1**7*r2**3*(3*r2**2*(r2**2-3*y1*y2-2*y2**2)-5*(r2**2-3*y1*y2-4*y2**2)*z**2)+r1**4*r2**6*(3*r2**2*(r2**2-4*y1*(4*y1+y2))-4*(5*r2**2-12*y1**2-21*y1*y2-2*y2**2)*z**2)+r1**5*r2**5*(9*r2**4+4*(6*y1**2+23*y1*y2+6*y2**2)*z**2+r2**2*(-24*y1**2-36*y1*y2-20*z**2))+4*r1**6*r2**4*(3*r2**4+(2*y1**2+21*y1*y2+12*y2**2)*z**2+r2**2*(-2*y1**2-11*y1*y2-2*y2**2-5*z**2))+4*r1**8*r2**2*(3*r2**4+5*y2*(y1+6*y2)*z**2+r2**2*(-3*y2*(y1+4*y2)-5*z**2))-5*r1**2*r2**8*(-4*y1*(6*y1+y2)*z**2+r2**2*(3*y1**2+z**2))-5*r1**3*r2**7*(-4*y1*(4*y1+3*y2)*z**2+3*r2**2*(3*y1**2+z**2))+r1**10*(3*r2**4+35*y2**2*z**2-5*r2**2*(3*y2**2+z**2))+r1**9*(9*r2**5+105*r2*y2**2*z**2-15*r2**3*(3*y2**2+z**2)))+l2**3*(-22*r1**6*r2**9-30*r1**5*r2**10-350*r1*r2**10*y1**2*z**2-140*r2**11*y1**2*z**2-2*r1**4*r2**9*(6*r2**2-45*y1**2-6*y1*y2-17*z**2)+50*r1**3*r2**10*(3*y1**2+z**2)-2*r1**9*r2**2*(11*r2**4+5*y2*(2*y1+21*y2)*z**2+r2**2*(-6*y1*y2-45*y2**2-17*z**2))+10*r1**2*r2**9*(y1*(-21*y1-2*y2)*z**2+2*r2**2*(3*y1**2+z**2))+r1**11*(-12*r2**4-140*y2**2*z**2+20*r2**2*(3*y2**2+z**2))+r1**10*(-30*r2**5-350*r2*y2**2*z**2+50*r2**3*(3*y2**2+z**2)))-2*l2*(r1+r2)**5*(175*r1*r2**9*y1**2*z**2+70*r2**10*y1**2*z**2+2*r1**4*r2**6*(3*r2**2*(r2**2-8*y1**2-3*y1*y2)-(11*r2**2+8*y1**2+y1*y2-4*y2**2)*z**2)+2*r1**8*r2**2*(7*r2**4+15*y2*(y1+4*y2)*z**2+r2**2*(-9*y1*y2-24*y2**2-11*z**2))-10*r1**2*r2**8*(-3*y1*(4*y1+y2)*z**2+r2**2*(3*y1**2+z**2))-25*r1**3*(-2*r2**7*y1*y2*z**2+r2**9*(3*y1**2+z**2))+10*r1**7*(r2**7+5*r2**3*y1*y2*z**2-r2**5*(3*y1*y2+z**2))+r1**5*(15*r2**9-30*r2**5*y1*y2*z**2-10*r2**7*(3*y1*y2+z**2))+r1**10*(6*r2**4+70*y2**2*z**2-10*r2**2*(3*y2**2+z**2))+r1**9*(15*r2**5+175*r2*y2**2*z**2-25*r2**3*(3*y2**2+z**2))+2*r1**6*r2**4*(7*r2**4+(4*y1**2-y1*y2-8*y2**2)*z**2-r2**2*(4*y1**2+y1*y2+4*y2**2+3*z**2)))),
               3*(x1-x2)*z*(l2**4*(3*r1**4*r2**9+35*r2**9*y1**2*z**2-5*r1**2*r2**9*(3*y1**2+z**2)+r1**9*(3*r2**4+35*y2**2*z**2-5*r2**2*(3*y2**2+z**2)))+2*l2**2*(r1+r2)**3*(210*r1*r2**9*y1**2*z**2+105*r2**10*y1**2*z**2+r1**4*r2**6*(9*r2**4+2*(12*y1**2-17*y1*y2+2*y2**2)*z**2+r2**2*(-24*y1**2-18*y1*y2-14*z**2))+2*r1**8*r2**2*(5*r2**4+15*y2*(y1+2*y2)*z**2+r2**2*(-9*y1*y2-12*y2**2-7*z**2))+10*r1**3*r2**7*(y1*(-4*y1+y2)*z**2-3*r2**2*(3*y1**2+z**2))-15*r1**2*r2**8*(-2*y1*(2*y1+y2)*z**2+r2**2*(3*y1**2+z**2))+2*r1**5*r2**5*(9*r2**4-3*(2*y1**2-7*y1*y2+2*y2**2)*z**2+r2**2*(6*y1**2-3*y1*y2+z**2))+2*r1**7*r2**3*(r2**4+5*(y1-4*y2)*y2*z**2+r2**2*(-3*(y1-2*y2)*y2+z**2))+2*r1**6*r2**4*(5*r2**4+(2*y1**2-17*y1*y2+12*y2**2)*z**2+r2**2*(-2*y1**2+7*y1*y2-2*y2**2+z**2))+r1**10*(9*r2**4+105*y2**2*z**2-15*r2**2*(3*y2**2+z**2))+r1**9*(18*r2**5+210*r2*y2**2*z**2-30*r2**3*(3*y2**2+z**2)))+(r1+r2)**7*(105*r1*r2**9*y1**2*z**2+35*r2**10*y1**2*z**2+4*r1**7*r2**3*(3*r2**2*(r2**2-3*y1*y2-2*y2**2)-5*(r2**2-3*y1*y2-4*y2**2)*z**2)+r1**4*r2**6*(3*r2**2*(r2**2-4*y1*(4*y1+y2))-4*(5*r2**2-12*y1**2-21*y1*y2-2*y2**2)*z**2)+r1**5*r2**5*(9*r2**4+4*(6*y1**2+23*y1*y2+6*y2**2)*z**2+r2**2*(-24*y1**2-36*y1*y2-20*z**2))+4*r1**6*r2**4*(3*r2**4+(2*y1**2+21*y1*y2+12*y2**2)*z**2+r2**2*(-2*y1**2-11*y1*y2-2*y2**2-5*z**2))+4*r1**8*r2**2*(3*r2**4+5*y2*(y1+6*y2)*z**2+r2**2*(-3*y2*(y1+4*y2)-5*z**2))-5*r1**2*r2**8*(-4*y1*(6*y1+y2)*z**2+r2**2*(3*y1**2+z**2))-5*r1**3*r2**7*(-4*y1*(4*y1+3*y2)*z**2+3*r2**2*(3*y1**2+z**2))+r1**10*(3*r2**4+35*y2**2*z**2-5*r2**2*(3*y2**2+z**2))+r1**9*(9*r2**5+105*r2*y2**2*z**2-15*r2**3*(3*y2**2+z**2)))+l2**3*(-22*r1**6*r2**9-30*r1**5*r2**10-350*r1*r2**10*y1**2*z**2-140*r2**11*y1**2*z**2-2*r1**4*r2**9*(6*r2**2-45*y1**2-6*y1*y2-17*z**2)+50*r1**3*r2**10*(3*y1**2+z**2)-2*r1**9*r2**2*(11*r2**4+5*y2*(2*y1+21*y2)*z**2+r2**2*(-6*y1*y2-45*y2**2-17*z**2))+10*r1**2*r2**9*(y1*(-21*y1-2*y2)*z**2+2*r2**2*(3*y1**2+z**2))+r1**11*(-12*r2**4-140*y2**2*z**2+20*r2**2*(3*y2**2+z**2))+r1**10*(-30*r2**5-350*r2*y2**2*z**2+50*r2**3*(3*y2**2+z**2)))-2*l2*(r1+r2)**5*(175*r1*r2**9*y1**2*z**2+70*r2**10*y1**2*z**2+2*r1**4*r2**6*(3*r2**2*(r2**2-8*y1**2-3*y1*y2)-(11*r2**2+8*y1**2+y1*y2-4*y2**2)*z**2)+2*r1**8*r2**2*(7*r2**4+15*y2*(y1+4*y2)*z**2+r2**2*(-9*y1*y2-24*y2**2-11*z**2))-10*r1**2*r2**8*(-3*y1*(4*y1+y2)*z**2+r2**2*(3*y1**2+z**2))-25*r1**3*(-2*r2**7*y1*y2*z**2+r2**9*(3*y1**2+z**2))+10*r1**7*(r2**7+5*r2**3*y1*y2*z**2-r2**5*(3*y1*y2+z**2))+r1**5*(15*r2**9-30*r2**5*y1*y2*z**2-10*r2**7*(3*y1*y2+z**2))+r1**10*(6*r2**4+70*y2**2*z**2-10*r2**2*(3*y2**2+z**2))+r1**9*(15*r2**5+175*r2*y2**2*z**2-25*r2**3*(3*y2**2+z**2))+2*r1**6*r2**4*(7*r2**4+(4*y1**2-y1*y2-8*y2**2)*z**2-r2**2*(4*y1**2+y1*y2+4*y2**2+3*z**2)))),
               3*(x1-x2)*(2*l2**2*(r1+r2)**4*(r1**4*r2**6*(3*r1**3-2*r1**2*r2+9*r1*r2**2+9*r2**3)*y1+r1**6*r2**4*(9*r1**3+9*r1**2*r2-2*r1*r2**2+3*r2**3)*y2-6*r1**2*r2**2*(15*r1*r2**6*y1+15*r2**7*y1+r1**5*r2**2*(3*y1-4*y2)+r1**3*r2**4*(y1-2*y2)+15*r1**7*y2+15*r1**6*r2*y2+r1**4*r2**3*(-2*y1+y2)+r1**2*r2**5*(-4*y1+3*y2))*z**2+(105*r1*r2**8*y1+105*r2**9*y1-3*r1**5*r2**4*(y1-4*y2)+15*r1**7*r2**2*(y1-2*y2)+3*r1**4*r2**5*(4*y1-y2)+105*r1**9*y2+105*r1**8*r2*y2+15*r1**2*r2**7*(-2*y1+y2)-5*r1**6*r2**3*(2*y1+y2)-5*r1**3*r2**6*(y1+2*y2))*z**4)-2*l2*(r1+r2)**6*(r1**4*r2**6*(3*r1+2*r2)*(r1**2+3*r2**2)*y1+r1**6*r2**4*(2*r1+3*r2)*(3*r1**2+r2**2)*y2-6*r1**2*r2**2*(15*r1*r2**6*y1+10*r2**7*y1+10*r1**7*y2+15*r1**6*r2*y2+r1**4*r2**3*(2*y1+y2)+r1**3*r2**4*(y1+2*y2)+r1**2*r2**5*(4*y1+3*y2)+r1**5*r2**2*(3*y1+4*y2))*z**2+(105*r1*r2**8*y1+70*r2**9*y1-5*r1**3*r2**6*(y1-2*y2)+5*r1**6*r2**3*(2*y1-y2)+70*r1**9*y2+105*r1**8*r2*y2+15*r1**2*r2**7*(2*y1+y2)-3*r1**4*r2**5*(4*y1+y2)+15*r1**7*r2**2*(y1+2*y2)-3*r1**5*r2**4*(y1+4*y2))*z**4)+(r1+r2)**8*(r1**4*r2**6*(2*r1**3+4*r1**2*r2+6*r1*r2**2+3*r2**3)*y1+r1**6*r2**4*(3*r1**3+6*r1**2*r2+4*r1*r2**2+2*r2**3)*y2-6*r1**2*r2**2*(10*r1*r2**6*y1+5*r2**7*y1+5*r1**7*y2+10*r1**6*r2*y2+2*r1**2*r2**5*(4*y1+y2)+2*r1**3*r2**4*(3*y1+2*y2)+2*r1**4*r2**3*(2*y1+3*y2)+2*r1**5*r2**2*(y1+4*y2))*z**2+5*(14*r1*r2**8*y1+7*r2**9*y1+7*r1**9*y2+14*r1**8*r2*y2+2*r1**2*r2**7*(6*y1+y2)+2*r1**3*r2**6*(5*y1+2*y2)+2*r1**4*r2**5*(4*y1+3*y2)+2*r1**5*r2**4*(3*y1+4*y2)+2*r1**6*r2**3*(2*y1+5*y2)+2*r1**7*r2**2*(y1+6*y2))*z**4)+l2**4*(3*r1**4*r2**9*y1-30*r1**2*r2**9*y1*z**2+35*r2**9*y1*z**4+r1**9*y2*(3*r2**4-30*r2**2*z**2+35*z**4))-2*l2**3*(15*r1**5*r2**10*y1+r1**6*r2**9*(10*y1+y2)-150*r1**3*r2**10*y1*z**2+175*r1*r2**10*y1*z**4+70*r2**11*y1*z**4+6*r1**4*r2**9*(r2**2*y1-(16*y1+y2)*z**2)+5*r1**2*r2**9*z**2*(-12*r2**2*y1+(22*y1+y2)*z**2)+2*r1**11*y2*(3*r2**4-30*r2**2*z**2+35*z**4)+5*r1**10*r2*y2*(3*r2**4-30*r2**2*z**2+35*z**4)+r1**9*r2**2*(r2**4*(y1+10*y2)-6*r2**2*(y1+16*y2)*z**2+5*(y1+22*y2)*z**4))),
                ])/((r1*r2)**9*((r1+r2)**2-l2)**5)).sum(axis=1)/np.pi
