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
#include "spody_time.h"   /* run-window vs space-weather-horizon check */

/* ==================================================================
 * Shared (read-only, file-mapped) resources
 * ================================================================== */

int spody_build_shared(const InputConfig *cfg, SimulationShared *shared,
                       SpodyError *err) {
    spody_error_clear(err);
    memset(shared, 0, sizeof *shared);

    /* Reject unimplemented dynamics models defensively. */
    {
        const SpodyDynamicsModelSpec *spec =
                spody_dynamics_model_get(cfg->dynamics_model);
        if (!spec || !spec->implemented) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "dynamics_model '%s' is not yet implemented in sim_setup",
                    spec ? spec->name : "?");
            return SPODY_ERR_BAD_VALUE;
        }
    }

    /* CR3BP has no file-mapped shared resources -- no ephemeris, no
     * harmonics, no EOP/IAU. Nothing to do; init flags stay 0 so
     * spody_free_shared is a no-op. */
    if (cfg->dynamics_model == SPODY_DYN_CR3BP) {
        return SPODY_OK;
    }

    /* From here down: high_fidelity shared build. */

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

    /* Earth-only: EOP table + IAU 2006/2000A_R06 series tables.
     * Both required by spody_bf_rotation_earth at every RHS evaluation.
     * Pre-validated by spody_validate_input to exist on disk. */
    if (cfg->central_body == SPODY_CENTRAL_EARTH) {
        if (spody_setup_MappedEOPData(&shared->eop_data,
                                      cfg->eop_file) != 0) {
            spody_error_set(err, SPODY_ERR_IO,
                    "spody_setup_MappedEOPData failed for '%s'",
                    cfg->eop_file);
            goto fail;
        }
        shared->init_eop = 1;

        if (spody_setup_MappedIAU2006Data(&shared->iau2006_data,
                                          cfg->iau2006_dir) != 0) {
            spody_error_set(err, SPODY_ERR_IO,
                    "spody_setup_MappedIAU2006Data failed for '%s' "
                    "(directory must contain tab5.2a.txt, tab5.2b.txt, "
                    "tab5.2d.txt)", cfg->iau2006_dir);
            goto fail;
        }
        shared->init_iau = 1;
    }

    /* Drag-only: space weather table + run-window horizon check. The
     * density callback fails soft (zero drag) outside the table, so
     * the hard refusal has to happen here, where we can still tell
     * the user to update the file. The NRLMSISE Ap history needs 3
     * days of records BEFORE the epoch too. Batch cases that override
     * et_start_s / duration_s beyond the base window are not
     * re-checked -- the base TOML window is the contract. */
    if (cfg->enable_drag) {
        if (spody_setup_MappedSpaceWeatherData(&shared->sw_data,
                                               cfg->space_weather_file) != 0) {
            spody_error_set(err, SPODY_ERR_IO,
                    "spody_setup_MappedSpaceWeatherData failed for '%s'",
                    cfg->space_weather_file);
            goto fail;
        }
        shared->init_sw = 1;

        {
            double mjd_start = spody_et_to_mjd_utc(cfg->et_start_s);
            double mjd_end   = spody_et_to_mjd_utc(cfg->et_start_s +
                                                   cfg->duration_s);
            double first_ok  = shared->sw_data.mjd_first + 3.0;
            double last_ok   = shared->sw_data.mjd_last_predicted + 1.0;
            if (mjd_start < first_ok || mjd_end >= last_ok) {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "run window (UTC MJD %.2f .. %.2f) is outside the "
                        "space weather table coverage (usable: %.2f .. %.2f; "
                        "observed data end at %.2f, the tail is CelesTrak "
                        "prediction). Update '%s' from "
                        "https://celestrak.org/SpaceData/SW-All.csv or "
                        "shorten the run.",
                        mjd_start, mjd_end, first_ok, last_ok,
                        shared->sw_data.mjd_last_observed,
                        cfg->space_weather_file);
                goto fail;
            }
        }
    }

    return SPODY_OK;

fail:
    spody_free_shared(shared);
    return err ? err->code : SPODY_ERR_INTERNAL;
}

void spody_free_shared(SimulationShared *shared) {
    if (!shared) return;
    if (shared->init_sw)  { spody_free_MappedSpaceWeatherData(&shared->sw_data); shared->init_sw = 0; }
    if (shared->init_iau) { spody_free_MappedIAU2006Data(&shared->iau2006_data); shared->init_iau = 0; }
    if (shared->init_eop) { spody_free_MappedEOPData     (&shared->eop_data);    shared->init_eop = 0; }
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

    /* Reject unimplemented dynamics models defensively. */
    {
        const SpodyDynamicsModelSpec *spec =
                spody_dynamics_model_get(cfg->dynamics_model);
        if (!spec || !spec->implemented) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "dynamics_model '%s' is not yet implemented in sim_setup",
                    spec ? spec->name : "?");
            return SPODY_ERR_BAD_VALUE;
        }
    }

    /* CR3BP branch: no eph/hg/eop/iau handles, no Spacecraft, no
     * third-body arrays. Populate ctx with cr3bp_* fields, cache the
     * derived quantities via spody_init_CR3BPContext, bind the
     * integrator to spody_force_rhs_cr3bp. State y is (r, v) in km,
     * km/s in the synodic rotating frame. */
    if (cfg->dynamics_model == SPODY_DYN_CR3BP) {
        w->ctx.cr3bp_mu1 = cfg->cr3bp_mu1;
        w->ctx.cr3bp_mu2 = cfg->cr3bp_mu2;
        w->ctx.cr3bp_L   = cfg->cr3bp_L_km;
        spody_init_CR3BPContext(&w->ctx);

        IntegratorOptions opt;
        spody_default_integrator_options(SPODY_INTEG_RK45, &opt);
        opt.rel_tol = cfg->rel_tol;
        opt.h_init  = cfg->h_init_s;
        opt.h_min   = cfg->h_min_s;
        opt.h_max   = cfg->h_max_s;

        if (spody_setup_integrator(&w->integ, SPODY_INTEG_RK45, &opt,
                                   6, spody_force_rhs_cr3bp,
                                   &w->ctx) != SPODY_INTEG_OK) {
            spody_error_set(err, SPODY_ERR_INTERNAL,
                    "spody_setup_integrator failed (cr3bp)");
            goto fail;
        }
        w->init_integ = 1;

        double y0[6] = {
            cfg->position_km[0],  cfg->position_km[1],  cfg->position_km[2],
            cfg->velocity_kms[0], cfg->velocity_kms[1], cfg->velocity_kms[2]
        };
        if (spody_set_integrator_state(&w->integ, 0.0, y0) != SPODY_INTEG_OK) {
            spody_error_set(err, SPODY_ERR_INTERNAL,
                    "spody_set_integrator_state failed (cr3bp)");
            goto fail;
        }
        return SPODY_OK;
    }

    /* From here down: high_fidelity worker build. */

    /* Per-thread handles bound to the shared, read-only data. */
    spody_setup_MappedEphemeris(&w->eph, &shared->med);
    w->init_eph = 1;

    spody_setup_HarmonicGravity(&w->hg, &shared->hgd);
    w->init_hg = 1;

    /* Earth-only: per-thread EOP / IAU 2006 handles bound to the
     * shared, read-only data. We bind them whenever the Shared has
     * them initialised (which Shared does iff central_body == Earth);
     * the ctx pointers below decide whether the engine actually uses
     * them. Keeping the bind unconditional simplifies cleanup. */
    if (shared->init_eop) {
        spody_setup_MappedEOP(&w->eop, &shared->eop_data);
        w->init_eop_w = 1;
    }
    if (shared->init_iau) {
        spody_setup_MappedIAU2006(&w->iau2006, &shared->iau2006_data);
        w->init_iau_w = 1;
    }
    if (shared->init_sw) {
        spody_setup_MappedSpaceWeather(&w->sw, &shared->sw_data);
        w->init_sw_w = 1;
    }

    /* Spacecraft. */
    w->sat.mass      = cfg->mass_kg;
    if (cfg->has_drag_block) {
        w->sat.area_drag = cfg->drag_area_m2;
        w->sat.Cd        = cfg->drag_cd;
    } else {
        w->sat.area_drag = 0.0;
        w->sat.Cd        = 0.0;
    }
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

    /* Force model context. Central-body properties (mu, radius, NAIF,
     * body-fixed rotation provider) come from the central_body.{h,c}
     * registry -- this TU stays body-agnostic. */
    const SpodyCentralBodySpec *body = spody_central_body_get(cfg->central_body);
    if (!body) {
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "internal: unsupported central body enum (%d)",
                (int)cfg->central_body);
        goto fail;
    }

    w->ctx.mu_central          = body->mu;
    w->ctx.R_central           = body->radius_km;
    w->ctx.naif_central        = body->naif;
    w->ctx.get_bf_rotation     = body->bf_rotation;
    w->ctx.sat                 = &w->sat;
    w->ctx.hg                  = &w->hg;
    w->ctx.eph                 = &w->eph;
    /* EOP / IAU 2006 slots are body-specific. spody_bf_rotation_earth
     * reads them on every step; spody_bf_rotation_moon (and any other
     * non-Earth provider) ignores them, so passing NULL there is the
     * documented contract. */
    w->ctx.eop                 = w->init_eop_w ? &w->eop     : NULL;
    w->ctx.iau2006             = w->init_iau_w ? &w->iau2006 : NULL;
    w->ctx.third_naif          = w->third_naif;
    w->ctx.third_mu            = w->third_mu;
    w->ctx.n_third             = w->n_third;
    w->ctx.enable_srp          = cfg->enable_srp;
    /* Eclipse-occulter defaults: the central body shadows the satellite. */
    w->ctx.srp_occulter_naif   = body->naif;
    w->ctx.srp_occulter_radius = body->radius_km;
    w->ctx.sun_radius          = SUN_RADIUS;
    /* Drag plumbing comes from the central-body registry: density
     * model + spin rate are body properties, the space weather handle
     * is per-worker. All three stay inert (NULL / 0) when drag is off
     * or the body has no atmosphere -- spody_force_drag checks. */
    w->ctx.enable_drag         = cfg->enable_drag;
    w->ctx.atmosphere          = body->atmosphere;
    w->ctx.space_weather       = w->init_sw_w ? &w->sw : NULL;
    w->ctx.body_spin_rad_s     = body->spin_rad_s;
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

    /* Initial state. The integrator runs in central_inertial; when
     * `frame = "central_body_fixed"` was selected the cfg's
     * position_km / velocity_kms are in the central body's body-
     * fixed basis at et_start_s, so we rotate them up here via the
     * same `get_bf_rotation` callback the force-model uses on every
     * integrator step (Earth ITRS via IAU 2006 + EOP, Moon PA via
     * the DE-series libration angles). Pure rotation only -- no
     * omega x r correction; matches the GUI / spopy plotting
     * convention so values round-trip through both. */
    double y0[6] = {
        cfg->position_km[0],  cfg->position_km[1],  cfg->position_km[2],
        cfg->velocity_kms[0], cfg->velocity_kms[1], cfg->velocity_kms[2]
    };
    if (cfg->initial_frame == SPODY_FRAME_CENTRAL_BODY_FIXED) {
        if (!w->ctx.get_bf_rotation) {
            spody_error_set(err, SPODY_ERR_INTERNAL,
                    "central body '%s' has no body-fixed rotation "
                    "provider; initial_state.frame = 'central_body_fixed' "
                    "is not supported for this body",
                    body->name);
            goto fail;
        }
        double R_icrf_to_bf[3][3], R_bf_to_icrf[3][3];
        w->ctx.get_bf_rotation(&w->ctx, cfg->et_start_s,
                                R_icrf_to_bf, R_bf_to_icrf);
        double r_bf[3] = { y0[0], y0[1], y0[2] };
        double v_bf[3] = { y0[3], y0[4], y0[5] };
        for (int i = 0; i < 3; ++i) {
            y0[i]     = R_bf_to_icrf[i][0] * r_bf[0]
                      + R_bf_to_icrf[i][1] * r_bf[1]
                      + R_bf_to_icrf[i][2] * r_bf[2];
            y0[i + 3] = R_bf_to_icrf[i][0] * v_bf[0]
                      + R_bf_to_icrf[i][1] * v_bf[1]
                      + R_bf_to_icrf[i][2] * v_bf[2];
        }
    }
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
    if (w->init_iau_w) { spody_free_MappedIAU2006(&w->iau2006); w->init_iau_w = 0; }
    if (w->init_sw_w)  { spody_free_MappedSpaceWeather(&w->sw); w->init_sw_w  = 0; }
    if (w->init_eop_w) { spody_free_MappedEOP(&w->eop);         w->init_eop_w = 0; }
    if (w->init_hg)    { spody_free_HarmonicGravity(&w->hg);    w->init_hg    = 0; }
    if (w->init_eph)   { spody_free_MappedEphemeris(&w->eph);   w->init_eph   = 0; }
    w->shared = NULL;
}
