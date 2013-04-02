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
    if False: # test slow python only expressions
        raise ImportError
    from .cexpressions import (point_potential, polygon_potential,
            mesh_potential)
except ImportError:
    from .expressions import (point_potential, polygon_potential,
            mesh_potential)


class Electrode(HasTraits):
    name = Str()
    dc = Float(0.)
    rf = Float(0.)

    def potential(self, x, derivative):
        """return the specified derivative of the eletrical potential,
        units are volts"""
        raise NotImplementedError

    def orientations(self):
        """return the orientation of pathes (positive orientation
        yields positive potential for positive voltage and z>0"""
        return np.array([])

    def plot(self, ax, label=None, color=None, **kw):
        """plot this electrode's in the supplied axes"""
        pass


class CoverElectrode(Electrode):
    height = Float(50)
    # also adjust cover_height in
    # the other electrodes to include the cover's effect on their
    # potentials

    def potential(self, x, derivative=0):
        x = np.atleast_2d(x)
        if derivative == 0:
            return x[:, 2]/self.height
        elif derivative == 1:
            ri = np.zeros((x.shape[0], 3))
            ri[:, 2] = 1/self.height
            return ri
        else:
            return np.zeros((x.shape[0], 2*derivative+1))


class SurfaceElectrode(Electrode):
    """
    Gapless surface electrode patch set.

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
    cover_height = Float(50) # cover plane height
    cover_nmax = Int(0) # max components in cover plane potential expansion

    def bare_potential(self, x, derivative, potential, out):
        """bare pixel potential and derivative (d) value at x.
        indices are (x, components 2*d+1)"""
        raise NotImplementedError

    def potential(self, x, derivative=0, potential=1., out=None):
        """potential and derivative value with cover plane"""
        x = np.atleast_2d(x).astype(np.double)
        out = self.bare_potential(x, derivative, potential, out)
        for n in range(-self.cover_nmax, 0) + range(1, self.cover_nmax+1):
            xx = x + [[0, 0, 2*n*self.cover_height]]
            self.bare_potential(xx, derivative, potential, out)
        return out


class PointPixelElectrode(SurfaceElectrode):
    points = Array(dtype=np.double, shape=(None, 2))
    areas = Array(dtype=np.double, shape=(None,))

    def _areas_default(self):
        return np.ones((len(self.points)))

    def orientations(self):
        return np.ones_like(self.areas)

    def plot(self, ax, label=None, color=None, **kw):
        # color="red"?
        p = self.points
        a = (self.areas/np.pi)**.5*2
        col = mpl.collections.EllipseCollection(
                edgecolors="none",
                #cmap=plt.cm.binary, norm=plt.Normalize(0, 1.),
                facecolor=color,
                widths=a, heights=a, units="x", # FIXME xy in matplotlib>r8111
                angles=np.zeros(a.shape),
                offsets=p[:, (0, 1)], transOffset=ax.transData)
        ax.add_collection(col)
        if label is None:
            label = self.name
        if label:
            ax.text(p[:,0].mean(), p[:,1].mean(), label)

    def bare_potential(self, x, derivative, potential, out):
        return point_potential(x, self.points, self.areas, potential,
                derivative, out)


class PolygonPixelElectrode(SurfaceElectrode):
    paths = List(Array(dtype=np.double, shape=(None, 2)))

    def orientations(self):
        return np.sign([area_centroid(pi)[0] for pi in self.paths])

    def plot(self, ax, label=None, color=None, **kw):
        if label is None:
            label = self.name
        for p in self.paths:
            ax.fill(p[:,0], p[:,1],
                    edgecolor=kw.get("edgecolor", "none"),
                    color=color, **kw)
            if label:
                ax.text(p[:,0].mean(), p[:,1].mean(), label)

    def to_points(self):
        a, c = [], []
        for p in self.paths:
            ai, ci = area_centroid(p)
            a.append(ai)
            c.append(ci)
        e = PointPixelElectrode(name=self.name, dc=self.dc, rf=self.rf,
                cover_nmax=self.cover_nmax, cover_height=self.cover_height,
                areas=a, points=c)
        return e

    def bare_potential(self, x, derivative, potential, out):
        return polygon_potential(x, self.paths, potential, derivative, out)


class MeshPixelElectrode(SurfaceElectrode):
    points = Array(dtype=np.double, shape=(None, 2))
    edges = Array(dtype=np.intc, shape=(None, 2))
    polygons = Array(dtype=np.intc, shape=(None,))
    potentials = Array(dtype=np.double, shape=(None,))

    def bare_potential(self, x, derivative, potential, out):
        return mesh_potential(x, self.points, self.edges, self.polygons,
                self.potentials*potential, derivative, out)
