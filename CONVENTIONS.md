# How TEM-view maps a microscope onto abTEM

This is the page to review. TEM-view builds a `temview-abtem/1` request (JSON) that is already in abTEM's
conventions; the bridge (`temview_abtem/simulate.py`) only passes the values to abTEM. So there are two places a
convention can go wrong: the client-side mapping (excerpt below) and how `simulate.py` hands the values to abTEM.

## The mapping

| TEM-view quantity | Request field → abTEM | Notes |
| --- | --- | --- |
| defocus `df` (nm, negative = underfocus) | `optics.C10` = 10·df Å → `aberration_coefficients` | abTEM's `defocus` = −C10 |
| Cs (mm uncorrected, µm corrected) | `C30` (Å) | |
| A1, angle (nm, °) | `C12`, `phi12` (Å, rad) | |
| B2 axial coma, angle (Krivanek notation) | `C21` = 3·B2, `phi21` | **please check** the factor 3 and the angle |
| A2 (three-fold), A3 (four-fold) | `C23`/`phi23`, `C34`/`phi34` | |
| focal spread: TEM-view computes a standard deviation Δ from Cc, ΔE, HT and lens stability | `focal_spread_A` = √2·Δ → `CTF(focal_spread=…)` | **please check**: we assumed abTEM's focal spread is a 1/e width (Kirkland) |
| TEM illumination semi-angle | `angular_spread_mrad` → `CTF(angular_spread=…)` | **please check**: treated as equivalent; is that abTEM's meaning? |
| objective aperture 40 / 20 / 10 µm | `objective_aperture_mrad` 12 / 6 / 3 → `CTF(semiangle_cutoff=…)` | fixed angles, independent of kV |
| probe convergence semi-angle | `semiangle_mrad` → `Probe(semiangle_cutoff=…)` / `SMatrix` | |
| small specimen tilt (°) | `specimen.tilt_mrad` → `PlaneWave(tilt=…)` / `Probe(tilt=…)` | beam tilt as an approximation to specimen tilt |
| HAADF/ADF inner, outer (mrad) | `detector.inner_mrad`, `outer_mrad` → `AnnularDetector` | |
| specimen temperature | `specimen.u2_ratio` = u²(T)/u²(293 K) → `FrozenPhonons(sigmas=σ₂₉₃·√ratio)` | σ₂₉₃ per element in `simulate.py` (`U_RMS_293`) |
| potential | `Potential(projection="infinite", parametrization="lobato", slice_thickness=2 Å)` | sampling from θ_max = (2/3)·λ/(2·dx) |

## Client side (TEM-view, for review only; TEM-view code is not part of this GPL package)

```js
export function abtemRequest(S, sim, { preset = 'quick', algorithm = 'multislice', seed = 0 } = {}) {
  const why = abtemUnsupported(S);
  if (why) throw new Error(why);
  const sp = sim.spec, ab = sim.abA(), m = S.mode, probe = !['tem', 'diff'].includes(m);
  const optics = {
    // TEM-view's defocus enters χ exactly as abTEM's C10 (negative = underfocus); B2 is C21/3 (Krivanek)
    C10: sim.dfA(), C30: sim.CsA(),
    C12: ab.A1, phi12: ab.A1a * DEG, C21: 3 * ab.B2, phi21: ab.B2a * DEG, C23: ab.A2, phi23: ab.A2a * DEG, C34: ab.A3, phi34: ab.A3a * DEG,
    focal_spread_A: Math.SQRT2 * sim.focalSpread(), // TEM-view: standard deviation; abTEM: 1/e width
  };
  if (m === 'tem') {
    optics.angular_spread_mrad = S.profile.convTEM;
    const ap = AP_MRAD[S.objAp];
    if (Number.isFinite(ap) && S.objAp !== 'df') optics.objective_aperture_mrad = ap;
  }
  if (probe) optics.semiangle_mrad = sim.alpha() * 1000;
  const fovA = m === 'diff' ? S.sa * 10 : m === 'cbed' ? 30 : sim.fovA();
  const recipe = {};
  if (S.spec === 'au') recipe.particles = sp.parts.map((p) => ({ x: p.x, y: p.y, r: p.r, rot: p.rot }));
  if (S.spec === 'si') {
    const ys = []; for (let y = -300; y <= 300; y += 0.5) ys.push(y);
    Object.assign(recipe, { boundary_y_A: ys, boundary_x_A: ys.map((y) => +sp.bnd(y).toFixed(3)), origin_A: [0.3, 0.2] });
  // … specimen recipe and detector, see the example request below
```

## Check we did

Graphene at 80 kV, TEM: same field of view as TEM-view, zero offset between the two images, correlation 0.94.

## Questions for the abTEM authors

1. Is `C21 = 3·B2` (and `phi21` unchanged) the right conversion from Krivanek's B2?
2. Is `CTF(focal_spread=…)` a 1/e width, so √2× a standard deviation?
3. Is `angular_spread` the illumination semi-angle as we use it, or a Gaussian width?
4. Is beam tilt an acceptable stand-in for small specimen tilts (≲ 1°), or should we rotate the atoms?
5. Anything in the potential, sampling or frozen-phonon settings you would choose differently?
