/*
 * Copyright 2026 ValeEng
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
/*
 * Simulation setup: cfg (TOML mirror) -> spody-core handles, split into
 * a shared phase (file-mapped data, read-only after build) and a
 * per-worker phase (per-thread handles + spacecraft + integrator).
 * See sim_setup.h for the threading-model rationale.
 */
#include "sim_setup.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "spody_const.h"

/* Resolve central-body name to (mu, mean radius, NAIF). v0 supports only
 * the Moon; this is the single place to extend when adding Earth / Mars. */
static int central_body_props(SpodyCentralBody body,
                              double *mu, double *R, int *naif,
                              SpodyError *err) {
    switch (body) {
    case SPODY_CENTRAL_MOON:
        *mu = MOON_MU; *R = MOON_RADIUS; *naif = 301;
        return SPODY_OK;
    default:
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "internal: unsupported central body enum (%d)", (int)body);
        return SPODY_ERR_INTERNAL;
    }
}

/* ==================================================================
 * Shared (read-only, file-mapped) resources
 * ================================================================== */

int spody_build_shared(const InputConfig *cfg, SimulationShared *shared,
                       SpodyError *err) {
    spody_error_clear(err);
    memset(shared, 0, sizeof *shared);

    /* Ephemeris (shared, memory-mapped). */
    if (spody_setup_MappedEphemerisData(&shared->med,
                                        cfg->ephemeris_file) != 0) {
        spody_error_set(err, SPODY_ERR_IO,
                "spody_setup_MappedEphemerisData failed for '%s'",
                cfg->ephemeris_file);
        goto fail;
    }
    shared->init_med = 1;

    /* Harmonics (shared). spody-core itself rejects degree > file_N. */
    if (spody_load_HarmonicGravityData(&shared->hgd,
                                       cfg->harmonics_file,
                                       cfg->harmonics_degree) != 0) {
        spody_error_set(err, SPODY_ERR_IO,
                "spody_load_HarmonicGravityData failed for '%s' at N=%d",
                cfg->harmonics_file, cfg->harmonics_degree);
        goto fail;
    }
    shared->init_hgd = 1;

    return SPODY_OK;

fail:
    spody_free_shared(shared);
    return err ? err->code : SPODY_ERR_INTERNAL;
}

void spody_free_shared(SimulationShared *shared) {
    if (!shared) return;
    if (shared->init_hgd) { spody_free_HarmonicGravityData(&shared->hgd); shared->init_hgd = 0; }
    if (shared->init_med) { spody_free_MappedEphemerisData(&shared->med); shared->init_med = 0; }
}

/* ==================================================================
 * Per-worker state
 * ================================================================== */

int spody_build_worker(const InputConfig *cfg,
                       const SimulationShared *shared,
                       SimulationWorker *w, SpodyError *err) {
    spody_error_clear(err);
    memset(w, 0, sizeof *w);
    w->shared = shared;

    /* Per-thread handles bound to the shared, read-only data. */
    spody_setup_MappedEphemeris(&w->eph, &shared->med);
    w->init_eph = 1;

    spody_setup_HarmonicGravity(&w->hg, &shared->hgd);
    w->init_hg = 1;

    /* Spacecraft. Drag is disabled in v0 (placeholder in spody-core). */
    w->sat.mass      = cfg->mass_kg;
    w->sat.area_drag = 0.0;
    w->sat.Cd        = 0.0;
    if (cfg->has_srp_block) {
        w->sat.area_srp = cfg->srp_area_m2;
        w->sat.Cr       = cfg->srp_cr;
    } else {
        w->sat.area_srp = 0.0;
        w->sat.Cr       = 0.0;
    }
    spody_init_Spacecraft(&w->sat);

    /* Third bodies: cfg holds names; resolve to (NAIF, mu) pairs and
     * own the arrays here so they outlive cfg. */
    w->n_third = cfg->n_third_bodies;
    if (w->n_third > 0) {
        w->third_naif = (int    *)malloc((size_t)w->n_third * sizeof(int));
        w->third_mu   = (double *)malloc((size_t)w->n_third * sizeof(double));
        if (!w->third_naif || !w->third_mu) {
            spody_error_set(err, SPODY_ERR_INTERNAL,
                    "out of memory allocating third-body arrays (%d entries)",
                    w->n_third);
            goto fail;
        }
        for (int i = 0; i < w->n_third; ++i) {
            if (spody_lookup_third_body(cfg->third_body_names[i],
                                        &w->third_naif[i],
                                        &w->third_mu[i],
                                        NULL) != 0) {
                /* Should have been caught by spody_validate_input -- treat
                 * as an internal error if it slips through. */
                spody_error_set(err, SPODY_ERR_INTERNAL,
                        "unknown third body '%s' reached sim_setup",
                        cfg->third_body_names[i]);
                goto fail;
            }
        }
    }

    /* Force model context. */
    double mu_c = 0.0, R_c = 0.0; int naif_c = 0;
    int rc = central_body_props(cfg->central_body, &mu_c, &R_c, &naif_c, err);
    if (rc != SPODY_OK) goto fail;

    w->ctx.mu_central          = mu_c;
    w->ctx.R_central           = R_c;
    w->ctx.naif_central        = naif_c;
    w->ctx.sat                 = &w->sat;
    w->ctx.hg                  = &w->hg;
    w->ctx.eph                 = &w->eph;
    w->ctx.third_naif          = w->third_naif;
    w->ctx.third_mu            = w->third_mu;
    w->ctx.n_third             = w->n_third;
    w->ctx.enable_srp          = cfg->enable_srp;
    /* Eclipse-occulter defaults: in v0 the central body shadows the satellite.
     * For Moon, this is naif 301 and MOON_RADIUS. */
    w->ctx.srp_occulter_naif   = naif_c;
    w->ctx.srp_occulter_radius = R_c;
    w->ctx.sun_radius          = SUN_RADIUS;
    w->ctx.enable_drag         = 0;
    w->ctx.et0                 = cfg->et_start_s;

    /* Integrator. Map cfg options onto IntegratorOptions and bind the
     * default RHS + force context. */
    IntegratorOptions opt;
    spody_default_integrator_options(SPODY_INTEG_RK45, &opt);
    opt.rel_tol = cfg->rel_tol;
    opt.h_init  = cfg->h_init_s;
    opt.h_min   = cfg->h_min_s;
    opt.h_max   = cfg->h_max_s;
    /* abs_tol / safety / max_steps left at library defaults: those are
     * not exposed in the v0 TOML schema. */

    if (spody_setup_integrator(&w->integ, SPODY_INTEG_RK45, &opt,
                               6, spody_force_rhs_default,
                               &w->ctx) != SPODY_INTEG_OK) {
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "spody_setup_integrator failed");
        goto fail;
    }
    w->init_integ = 1;

    /* Initial state. Frame is 'central_inertial' in v0 -- ICRF-aligned
     * at the central body, which is exactly what the integrator expects,
     * so no rotation is needed. */
    double y0[6] = {
        cfg->position_km[0],  cfg->position_km[1],  cfg->position_km[2],
        cfg->velocity_kms[0], cfg->velocity_kms[1], cfg->velocity_kms[2]
    };
    if (spody_set_integrator_state(&w->integ, 0.0, y0) != SPODY_INTEG_OK) {
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "spody_set_integrator_state failed");
        goto fail;
    }

    return SPODY_OK;

fail:
    spody_free_worker(w);
    return err ? err->code : SPODY_ERR_INTERNAL;
}

void spody_free_worker(SimulationWorker *w) {
    if (!w) return;
    if (w->init_integ) { spody_free_integrator(&w->integ); w->init_integ = 0; }
    free(w->third_naif); w->third_naif = NULL;
    free(w->third_mu);   w->third_mu   = NULL;
    w->n_third = 0;
    if (w->init_hg)  { spody_free_HarmonicGravity(&w->hg);  w->init_hg  = 0; }
    if (w->init_eph) { spody_free_MappedEphemeris(&w->eph); w->init_eph = 0; }
    w->shared = NULL;
}
