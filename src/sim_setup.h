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
 * Simulation setup -- maps a parsed InputConfig onto the spody-core
 * structs that drive a propagation.
 *
 * Threading model
 *   The setup is split in two phases that mirror spody-core's
 *   shared/per-thread contract:
 *
 *     SimulationShared   owns the read-only resources that may be
 *                        safely shared across worker threads:
 *                          - MappedEphemerisData (DE440)
 *                          - HarmonicGravityData (e.g. GRGM1200)
 *                        Built once on the main thread.
 *
 *     SimulationWorker   owns the per-thread mutable handles, the
 *                        force-model context, the integrator workspace,
 *                        the spacecraft and the third-body arrays.
 *                        One per propagation. References the
 *                        SimulationShared by const pointer (no ownership).
 *
 *
 * Lifecycle:
 *
 *     SimulationShared shared;
 *     spody_build_shared(&cfg, &shared, &err);
 *
 *     SimulationWorker w;
 *     spody_build_worker(&cfg, &shared, &w, &err);
 *
 *     ... spody_run_simulation(&cfg, &w, &err) ...
 *
 *     spody_free_worker(&w);
 *     spody_free_shared(&shared);
 *
 * Partial-failure cleanup is automatic in both builders: only the
 * resources successfully initialised so far are released.
 */
#ifndef SPODY_SIM_SETUP_H
#define SPODY_SIM_SETUP_H

#include "app_diagnostics.h"
#include "toml_input.h"
#include "spody_core.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------
 * Shared, read-only resources (one per process).
 *
 * Threading: every field is read-only after spody_build_shared returns,
 * so a single instance may be safely shared by reference across worker
 * threads. The spody-core *Data structs themselves are documented as
 * thread-safe to share.
 * ------------------------------------------------------------------ */
typedef struct {
    MappedEphemerisData med;
    HarmonicGravityData hgd;
    /* Earth-only inputs (populated iff cfg->central_body == Earth).
     * MappedEOPData parses finals2000A.all once at process start;
     * MappedIAU2006Data parses the three IERS series tables (X, Y,
     * s+XY/2) at the same time. Both are read-only post-build and can
     * be safely shared across worker threads. */
    MappedEOPData       eop_data;
    MappedIAU2006Data   iau2006_data;

    unsigned init_med : 1;
    unsigned init_hgd : 1;
    unsigned init_eop : 1;
    unsigned init_iau : 1;
} SimulationShared;

/* ------------------------------------------------------------------
 * Per-worker state (one per thread / per propagation).
 *
 * Holds the spody-core per-thread handles bound to the shared data,
 * the spacecraft (per-worker so future constellations can have one
 * spacecraft per worker), the force-model context, the third-body
 * arrays, and the integrator workspace.
 *
 * `shared` is a non-owning pointer: the SimulationShared instance must
 * outlive every worker that references it.
 * ------------------------------------------------------------------ */
typedef struct {
    const SimulationShared *shared;   /* not owned; must outlive *this */

    MappedEphemeris    eph;
    HarmonicGravity    hg;
    /* Earth-only per-thread handles. MappedEOP carries a per-thread
     * lookup cache (cached_idx + cached_valid) so we cannot share one
     * across worker threads; MappedIAU2006 is currently stateless but
     * kept symmetric for the same reason should it grow per-thread
     * state later. Both reference the read-only Shared data above. */
    MappedEOP          eop;
    MappedIAU2006      iau2006;
    Spacecraft         sat;
    ForceModelContext  ctx;
    IntegratorAllData  integ;

    /* Heap-owned, parallel: third_naif[i] <-> third_mu[i]. */
    int    *third_naif;
    double *third_mu;
    int     n_third;

    /* Init flags drive cleanup -- each handle's free() is only called
     * when the corresponding setup actually succeeded. */
    unsigned init_eph     : 1;
    unsigned init_hg      : 1;
    unsigned init_eop_w   : 1;
    unsigned init_iau_w   : 1;
    unsigned init_integ   : 1;
} SimulationWorker;

/* ------------------------------------------------------------------
 * Builders / destructors
 * ------------------------------------------------------------------ */

/* Open the shared data files (ephemeris, gravity coefficients) named
 * by cfg. Returns SPODY_OK or an error code with *err filled in. On
 * failure any partially-initialised resource is released. */
int  spody_build_shared(const InputConfig *cfg,
                        SimulationShared *shared, SpodyError *err);

/* Release everything owned by shared. Safe on a zero-initialised
 * struct. */
void spody_free_shared (SimulationShared *shared);

/* Build a worker against an already-built shared. Sets up the
 * per-thread eph/hg handles, spacecraft, force-model context,
 * third-body arrays, integrator workspace and initial state.
 *
 * On entry the integrator is left at t = 0 with y = [position_km,
 * velocity_kms] from the TOML; ctx.et0 is set to cfg->et_start_s, so
 * the ephemeris is queried at the correct absolute epoch from step 1. */
int  spody_build_worker(const InputConfig *cfg,
                        const SimulationShared *shared,
                        SimulationWorker *w, SpodyError *err);

/* Release everything owned by w (handles, third-body arrays,
 * integrator workspace). Does NOT touch the SimulationShared the
 * worker references. Safe on a zero-initialised struct. */
void spody_free_worker (SimulationWorker *w);

#ifdef __cplusplus
}
#endif

#endif /* SPODY_SIM_SETUP_H */
