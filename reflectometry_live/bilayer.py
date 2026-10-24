"""Cached full 2D search and independent refinement of distinct layer pairs."""

import time
import numpy as np
from scipy.ndimage import minimum_filter
from scipy.optimize import least_squares

from .optics import bilayer_reflectance, ordinary_index, silica_index


class BilayerSearch:
    def __init__(self):
        self.key = None
        self.cache_builds = 0
        self.refined_seeds = {}

    def prepare(self, wave, settings):
        key = (wave.tobytes(), settings.thickness_min_nm, settings.thickness_max_nm,
               settings.aln_min_nm, settings.aln_max_nm)
        if self.key == key:
            return
        # A rejected rebuild must not leave a usable key pointing to old models
        # while the optical coefficients have already changed wavelength grid.
        self.key = None
        # <= pi/4 round-trip phase change in either layer at every channel.
        # This resolves local basins before continuous refinement; it is not
        # the reported thickness resolution or an uncertainty estimate.
        self.oxide = silica_index(wave)
        self.aln = ordinary_index(wave, "AlN")
        sapphire = ordinary_index(wave, "sapphire")
        self.r01 = (1-self.oxide)/(1+self.oxide)
        self.r12 = (self.oxide-self.aln)/(self.oxide+self.aln)
        self.r23 = (self.aln-sapphire)/(self.aln+sapphire)
        self.q1, self.q2 = 4j*np.pi*self.oxide/wave, 4j*np.pi*self.aln/wave
        step1 = np.min(wave/(16*self.oxide))
        step2 = np.min(wave/(16*self.aln))
        count1 = int(np.ceil((settings.thickness_max_nm-settings.thickness_min_nm)/step1))+1
        count2 = int(np.ceil((settings.aln_max_nm-settings.aln_min_nm)/step2))+1
        if count1*count2 > min(24000, 16_000_000//len(wave)):
            raise ValueError("The two-layer search is too large for live fitting. Narrow the SiO₂ and AlN bounds around your rough estimates.")
        self.grid = np.linspace(settings.thickness_min_nm, settings.thickness_max_nm, count1)
        self.aln_grid = np.linspace(settings.aln_min_nm, settings.aln_max_nm, count2)
        # Build in rows, avoiding a large complex 3D temporary allocation.
        centered = np.empty((count1*count2, len(wave)))
        for i, oxide_nm in enumerate(self.grid):
            row = bilayer_reflectance(wave, oxide_nm, self.aln_grid[:, None])
            centered[i*count2:(i+1)*count2] = row-row.mean(axis=1, keepdims=True)
        self.centered = centered
        self.variance = np.einsum("ij,ij->i", centered, centered)
        self.key = key
        self.refined_seeds.clear()
        self.cache_builds += 1

    def physical(self, pair):
        p1, p2 = np.exp(self.q1*pair[0]), np.exp(self.q2*pair[1])
        bottom_den = 1+self.r12*self.r23*p2
        below = (self.r12+self.r23*p2)/bottom_den
        top_den = 1+self.r01*below*p1
        amplitude = (self.r01+below*p1)/top_den
        derivative1 = below*(1-self.r01**2)*p1*self.q1/top_den**2
        derivative2 = p1*(1-self.r01**2)/top_den**2 * (
            self.r23*(1-self.r12**2)*p2*self.q2/bottom_den**2)
        model = 100*np.abs(amplitude)**2
        jac = 200*np.real(np.conj(amplitude)[:, None]*np.column_stack((derivative1, derivative2)))
        return model, jac

    def fit(self, wave, signal, settings, started=None):
        start = started if started is not None else time.perf_counter()
        self.prepare(wave, settings)
        ym = signal.mean()
        yc = signal-ym
        covariance = self.centered @ yc
        scales = np.divide(covariance, self.variance, out=np.zeros_like(covariance), where=self.variance > 1e-12)
        loss = np.maximum(np.mean(yc**2)-scales*covariance/len(wave), 0)
        loss[(scales <= 0) | (self.variance <= 1e-12)] = np.inf
        if not np.isfinite(loss).any():
            raise ValueError("No physically positive amplitude fit was found.")
        landscape = loss.reshape(len(self.grid), len(self.aln_grid))
        local = np.flatnonzero((landscape <= minimum_filter(landscape, size=3, mode="constant", cval=np.inf)).ravel()
                               & np.isfinite(loss))
        local = local[np.argsort(loss[local])]
        selected = local[:12]
        seeds = [(int(i), np.array([self.grid[i//len(self.aln_grid)], self.aln_grid[i%len(self.aln_grid)]])) for i in selected]
        manual = (settings.oxide_estimate_nm, settings.aln_estimate_nm)
        seeds.append((("manual", *manual), np.array(manual)))
        lower = np.array([settings.thickness_min_nm, settings.aln_min_nm])
        upper = np.array([settings.thickness_max_nm, settings.aln_max_nm])
        last_pair, last_evaluation = None, None

        def evaluate(pair):
            nonlocal last_pair, last_evaluation
            pair_key = tuple(pair)
            if pair_key == last_pair:
                return last_evaluation
            model, jac = self.physical(pair)
            centered = model-model.mean()
            dc = jac-jac.mean(axis=0)
            variance = centered@centered
            a = max(float(centered@yc)/max(variance, 1e-12), 0.)
            b = ym-a*model.mean()
            residual = a*centered-yc
            da = (dc.T@yc-2*a*(centered@dc))/max(variance, 1e-12)
            projected_jac = a*dc+centered[:, None]*da
            if a == 0:
                projected_jac[:] = 0
            last_pair = pair_key
            last_evaluation = residual, projected_jac, a, b
            return last_evaluation

        candidates = []
        steps = np.array([self.grid[1]-self.grid[0], self.aln_grid[1]-self.aln_grid[0]])
        for seed_key, coarse_seed in seeds:
            seed = coarse_seed
            previous = self.refined_seeds.get(seed_key)
            if previous is not None and (isinstance(seed_key, tuple) or np.linalg.norm((previous-coarse_seed)/steps) <= 3):
                # Reuse only a basin seed, never its old error or fit. The entire
                # coarse grid and both affine parameters were recomputed above.
                # A changed spectrum with a better coarse seed starts fresh.
                if np.mean(evaluate(previous)[0]**2) <= np.mean(evaluate(coarse_seed)[0]**2)+1e-10:
                    seed = previous
            optimum = least_squares(lambda x: evaluate(x)[0], seed, jac=lambda x: evaluate(x)[1],
                                    bounds=(lower, upper), ftol=1e-8, xtol=1e-9, gtol=1e-8, max_nfev=80)
            residual, jac, a, b = evaluate(optimum.x)
            if not optimum.success or a <= 0 or not np.isfinite(residual).all():
                continue
            self.refined_seeds[seed_key] = optimum.x.copy()
            if any(np.linalg.norm(optimum.x-other[1]) < 1 for other in candidates):
                continue
            candidates.append((float(np.mean(residual**2)), optimum.x, a, b, residual, jac))
        if not candidates:
            raise ValueError("Two-layer refinement failed to find a finite positive-amplitude fit.")
        candidates.sort(key=lambda item: item[0])
        mse, pair, a, b, residual, jac = candidates[0]
        rmse = float(np.sqrt(mse))
        threshold = max(rmse*1.25, rmse+0.05)
        competing = [item for item in candidates[1:] if np.linalg.norm(item[1]-pair) >= 10 and np.sqrt(item[0]) <= threshold]
        # Unrefined competitive basins are reported as unresolved, never ignored.
        unresolved = bool(len(local) > 12 and np.sqrt(loss[local[12]]) <= threshold)
        singular = np.linalg.svd(jac, compute_uv=False)
        insensitive = singular[-1] <= max(1e-8, singular[0]*1e-3)
        status, message = "Good", "Full two-layer grid searched; distinct minima refined."
        if np.any(pair-lower < 0.01) or np.any(upper-pair < 0.01):
            status, message = "Boundary", "A layer reaches its search bound; widen that layer's interval."
        elif competing or unresolved or insensitive:
            status, message = "Ambiguous", "Similar layer pairs or weak layer sensitivity prevent a unique thickness reading."
        elif rmse > settings.max_rmse_pp:
            status, message = "Poor fit", f"RMSE exceeds {settings.max_rmse_pp:g} percentage points."
        alternatives = tuple({"sio2_nm": float(item[1][0]), "aln_nm": float(item[1][1]),
                              "rmse_pp": float(np.sqrt(item[0]))} for item in candidates[:8])
        return dict(thickness_nm=float(pair[0]), aln_thickness_nm=float(pair[1]), scale=a, offset_pp=b,
                    rmse_pp=rmse, status=status, message=message, wavelength_nm=wave, measured=signal,
                    prediction=signal+residual, residual=-residual, reflectance_full=signal,
                    valid_mask=np.ones(len(wave), bool), grid_nm=self.grid,
                    grid_rmse_pp=np.sqrt(landscape.min(axis=1)), aln_grid_nm=self.aln_grid,
                    aln_grid_rmse_pp=np.sqrt(landscape.min(axis=0)), search_rmse_pp=np.sqrt(landscape),
                    candidates=alternatives, settings=settings, fit_time_ms=(time.perf_counter()-start)*1000)
