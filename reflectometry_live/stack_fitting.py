"""One/two-film full grids and bounded multistart fitting of three/four films."""
import time
import numpy as np
from scipy.ndimage import minimum_filter
from scipy.optimize import least_squares

from .optics import StackOptics


class StackSearch:
    def __init__(self):
        self.key = None
        self.cache_builds = 0
        self.refined_seeds = {}

    def prepare(self, wave, settings):
        layers = settings.film_layers()
        key = (wave.tobytes(), tuple((l.material, l.minimum_nm, l.maximum_nm) for l in layers), settings.substrate_material(),
               tuple(l.estimate_nm for l in layers) if len(layers) > 2 else ())
        if key == self.key:
            return
        self.key = None
        self.optics = StackOptics(wave, [l.material for l in layers], settings.substrate_material())
        self.lower = np.array([l.minimum_nm for l in layers])
        self.upper = np.array([l.maximum_nm for l in layers])
        self.steps = np.array([min(2., np.min(wave/(16*np.real(n)))) if len(layers) == 1
                               else np.min(wave/(16*np.real(n))) for n in self.optics.indices])
        self.axes = tuple(np.linspace(l.minimum_nm, l.maximum_nm,
                                     int(np.ceil((l.maximum_nm-l.minimum_nm)/step))+1) for l, step in zip(layers, self.steps))
        count = int(np.prod([len(axis) for axis in self.axes]))
        if len(layers) <= 2:
            if count > min(24000, 16_000_000//len(wave)):
                raise ValueError("Search bounds are too broad for live fitting. Narrow the film bounds in Settings → Fitting.")
            self.points = np.stack(np.meshgrid(*self.axes, indexing="ij"), axis=-1).reshape(-1, len(layers))
            self.shape = tuple(len(axis) for axis in self.axes)
            self.kind = "full_grid"
        else:
            from scipy.stats import qmc
            # Higher-dimensional full grids would create gigabytes of models.
            # Sobol points span the complete bounds, with the manual estimates
            # added to the refinement seeds; this is not an exhaustive grid.
            samples = qmc.Sobol(d=len(layers), scramble=True, seed=20261008).random_base2(12)
            global_points = self.lower+(self.upper-self.lower)*samples
            # A local bank around the explicitly entered estimates supplements
            # the full-bound samples; it does not replace their search range.
            estimates = np.array([l.estimate_nm for l in layers])
            radius = np.minimum((self.upper-self.lower)/4, np.maximum(3*self.steps, .2*estimates))
            neighborhood = qmc.Sobol(d=len(layers), scramble=True, seed=2026).random_base2(9)
            local_points = np.clip(estimates+(2*neighborhood-1)*radius, self.lower, self.upper)
            self.points = np.vstack((global_points, local_points))
            self.shape = None
            self.kind = "sobol_multistart"
        self.centered = np.empty((len(self.points), len(wave)))
        for begin in range(0, len(self.points), 128):
            models = self.optics.reflectance(self.points[begin:begin+128])
            self.centered[begin:begin+128] = models-models.mean(axis=1, keepdims=True)
        self.variance = np.einsum("ij,ij->i", self.centered, self.centered)
        self.key = key
        self.refined_seeds.clear()
        self.cache_builds += 1

    def fit(self, wave, signal, settings, started=None):
        start = started if started is not None else time.perf_counter()
        self.prepare(wave, settings)
        yc = signal-signal.mean()
        def grid_loss(centered, variance):
            covariance = centered@yc
            a = np.divide(covariance, variance, out=np.zeros_like(covariance), where=variance > 1e-12)
            error = np.maximum(np.mean(yc**2)-a*covariance/len(wave), 0)
            error[(a <= 0) | (variance <= 1e-12)] = np.inf
            return error
        loss = grid_loss(self.centered, self.variance)
        if not np.isfinite(loss).any():
            raise ValueError("No positive-amplitude model fits this spectrum.")
        if self.shape:
            landscape = loss.reshape(self.shape)
            indices = np.flatnonzero((landscape <= minimum_filter(landscape, 3, mode="constant", cval=np.inf)).ravel() & np.isfinite(loss))
            ordered = indices[np.argsort(loss[indices])]
        else:
            ordered = np.argsort(loss)
        selected = []
        for index in ordered:
            if not np.isfinite(loss[index]):
                continue
            if all(np.linalg.norm((self.points[index]-self.points[other])/self.steps) >= 3 for other in selected):
                selected.append(int(index))
            if len(selected) == 12:
                break
        manual = tuple(l.estimate_nm for l in settings.film_layers())
        seeds = [(i, self.points[i]) for i in selected]+[(("manual", *manual), np.array(manual))]
        last_pair, last_evaluation = None, None
        def evaluate(pair):
            nonlocal last_pair, last_evaluation
            key = tuple(pair)
            if key == last_pair:
                return last_evaluation
            model, jac = self.optics.reflectance(pair, derivatives=True)
            centered, dc = model-model.mean(), jac-jac.mean(axis=0)
            variance = max(float(centered@centered), 1e-12)
            a = max(float(centered@yc)/variance, 0.)
            b = signal.mean()-a*model.mean()
            residual = a*centered-yc
            da = (dc.T@yc-2*a*(centered@dc))/variance
            projected = a*dc+centered[:, None]*da if a > 0 else np.zeros_like(dc)
            last_pair, last_evaluation = key, (residual, projected, a, b)
            return last_evaluation
        candidates = []
        for seed_key, coarse_seed in seeds:
            seed = coarse_seed
            previous = self.refined_seeds.get(seed_key)
            if previous is not None and (isinstance(seed_key, tuple) or np.linalg.norm((previous-coarse_seed)/self.steps) <= 3):
                if np.mean(evaluate(previous)[0]**2) <= np.mean(evaluate(coarse_seed)[0]**2)+1e-10:
                    seed = previous
            optimum = least_squares(lambda x: evaluate(x)[0], seed, jac=lambda x: evaluate(x)[1],
                                    bounds=(self.lower, self.upper), ftol=1e-8, xtol=1e-9, gtol=1e-8, max_nfev=100)
            residual, jac, a, b = evaluate(optimum.x)
            if not optimum.success or a <= 0 or not np.isfinite(residual).all():
                continue
            self.refined_seeds[seed_key] = optimum.x.copy()
            if any(np.linalg.norm(optimum.x-item[1]) < 1 for item in candidates):
                continue
            candidates.append((float(np.mean(residual**2)), optimum.x, a, b, residual, jac))
        if not candidates:
            raise ValueError("Layer refinement did not converge. Check estimates, bounds and material order.")
        candidates.sort(key=lambda item: item[0])
        mse, pair, a, b, residual, jac = candidates[0]
        rmse = float(np.sqrt(mse))
        similar = [item for item in candidates[1:] if np.linalg.norm(item[1]-pair) >= 10 and np.sqrt(item[0]) <= max(rmse*1.25, rmse+.05)]
        singular = np.linalg.svd(jac, compute_uv=False)
        insensitive = singular[-1] <= max(1e-8, singular[0]*1e-3)
        status, message = "Good", "Full thickness grid searched." if self.shape else "Bounded multistart search; not an exhaustive grid."
        if np.any(pair-self.lower < .01) or np.any(self.upper-pair < .01):
            status, message = "Boundary", "A film reaches its bound; widen that film's search interval."
        elif similar or insensitive:
            status, message = "Ambiguous", "Distinct thickness combinations or weak independent layer sensitivity prevent a unique fit."
        elif rmse > settings.max_rmse_pp:
            status, message = "Poor fit", f"RMSE exceeds {settings.max_rmse_pp:g} percentage points."
        profiles, axes = [], []
        for layer, axis in enumerate(self.axes):
            if self.shape:
                profile = landscape.min(axis=tuple(i for i in range(len(pair)) if i != layer)) if len(pair) > 1 else loss
                axes.append(axis)
            else:
                axis = np.linspace(self.lower[layer], self.upper[layer], 151)
                trials = np.tile(pair, (len(axis), 1)); trials[:, layer] = axis
                physical = self.optics.reflectance(trials)
                centered = physical-physical.mean(axis=1, keepdims=True)
                profile = grid_loss(centered, np.einsum("ij,ij->i", centered, centered))
                axes.append(axis)
            profiles.append(np.sqrt(profile))
        materials = tuple(l.material for l in settings.film_layers())
        aln = materials.index("AlN") if "AlN" in materials else None
        return dict(thickness_nm=float(pair[0]), scale=a, offset_pp=b, rmse_pp=rmse, status=status, message=message,
                    wavelength_nm=wave, measured=signal, prediction=signal+residual, residual=-residual,
                    reflectance_full=signal, valid_mask=np.ones(len(wave), bool), grid_nm=axes[0], grid_rmse_pp=profiles[0],
                    settings=settings, fit_time_ms=(time.perf_counter()-start)*1000,
                    aln_thickness_nm=float(pair[aln]) if aln is not None else None,
                    aln_grid_nm=axes[aln] if aln is not None else np.empty(0),
                    aln_grid_rmse_pp=profiles[aln] if aln is not None else np.empty(0),
                    search_rmse_pp=np.sqrt(landscape) if len(pair) == 2 else np.empty((0, 0)),
                    layer_thicknesses_nm=pair, layer_materials=materials, layer_grids_nm=tuple(axes),
                    layer_profiles_rmse_pp=tuple(profiles), search_points_nm=self.points, search_errors_pp=np.sqrt(loss),
                    search_kind=self.kind, candidates=tuple({"thicknesses_nm": item[1].tolist(), "rmse_pp": float(np.sqrt(item[0]))}
                                                           for item in candidates[:8]))
