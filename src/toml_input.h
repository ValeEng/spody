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
 * SpOdy input file (TOML) parser + validator.
 *
 * Owns the in-memory representation of a parsed simulation input. The
 * struct mirrors the TOML schema 1:1, with paths already resolved
 * relative to the directory of the TOML file.
 *
 * Errors are returned via a stack-allocated SpodyError struct so the
 * caller decides where diagnostics go. The parser does no I/O of its
 * own to stderr.
 */
#ifndef SPODY_TOML_INPUT_H
#define SPODY_TOML_INPUT_H

#include <stddef.h>
#include <stdint.h>

#include "app_diagnostics.h"   /* SpodyError, SpodyErrorCode */
#include "spody_math.h"        /* SpodyBodyShape */
#include "central_body.h"      /* SpodyCentralBody + registry helpers */
#include "dynamics_model.h"    /* SpodyDynamicsModel + registry helpers */

#ifdef __cplusplus
extern "C" {
#endif

/* --------------------------------------------------------------------------
 * Input configuration -- a flat mirror of the TOML schema.
 * -------------------------------------------------------------------------- */
typedef enum {
    SPODY_OUT_FIXED = 0,    /* uniform-grid sampling every interval_s        */
    SPODY_OUT_STEP  = 1     /* one record per accepted integrator step       */
} SpodyOutputMode;

typedef enum {
    SPODY_INTEG_TYPE_RKDP45 = 0
} SpodyIntegratorType;

/* force_model.solid_tides: the tide system of the gravity file. */
typedef enum {
    SPODY_TIDES_OFF       = 0,   /* key absent: no solid tide            */
    SPODY_TIDES_TIDE_FREE = 1,   /* file holds no tide: add all of it    */
    SPODY_TIDES_ZERO_TIDE = 2    /* file holds the permanent deformation */
} SpodySolidTideSystem;

/* force_model.body_shape: shape of every body for shadow, IMPACT and
 * altitude. */
typedef enum {
    SPODY_BODY_SHAPE_ELLIPSOID         = 0,  /* pck00011 spheroid           */
    SPODY_BODY_SHAPE_EQUATORIAL_SPHERE = 1   /* sphere of equatorial radius */
} SpodyBodyShapeModel;

typedef enum {
    SPODY_FRAME_CENTRAL_INERTIAL  = 0,  /* HF: ICRF-aligned, central body
                                         * at origin */
    SPODY_FRAME_SYNODIC_ROTATING  = 1,  /* CR3BP: barycenter-centred,
                                         * rotating with the two
                                         * primaries; +x toward the
                                         * smaller primary */
    SPODY_FRAME_CENTRAL_BODY_FIXED = 2  /* HF: the central body's body-
                                         * fixed basis at et_start_s
                                         * (Earth ITRS, Moon PA). sim_
                                         * setup rotates the parsed
                                         * (position, velocity) into the
                                         * integrator's central_inertial
                                         * frame via the body's
                                         * bf_rotation provider; no
                                         * other downstream stage sees
                                         * the body-fixed values. The
                                         * VELOCITY is the inertial one
                                         * expressed on the body-fixed
                                         * axes: pure rotation, no
                                         * omega x r. */,
    SPODY_FRAME_CENTRAL_BODY_FIXED_ROTATING = 4, /* HF: same axes, but
                                         * the velocity is measured in
                                         * the rotating frame (ECEF-
                                         * style: SP3, receivers, other
                                         * tools). sim_setup applies the
                                         * transport theorem,
                                         * v = R v_rot + omega x r. */
    SPODY_FRAME_ORBIT_PLANE       = 3   /* HF: Ely's "orbit plane" (OP)
                                         * frame, frozen at et_start_s.
                                         * +z along the orbit normal of
                                         * a perturbing body's apparent
                                         * orbit about the central body,
                                         * +x = central pole x z. Frozen
                                         * lunar orbits (ELFO) are
                                         * defined here, not against the
                                         * lunar equator. Rotated to
                                         * central_inertial in sim_setup
                                         * exactly like the body-fixed
                                         * case above. */
} SpodyFrame;

/* How [initial_state] expresses the IC. Default is cartesian so every
 * TOML written before this slice keeps parsing unchanged. */
typedef enum {
    SPODY_INIT_CARTESIAN = 0,
    SPODY_INIT_KEPLERIAN = 1
} SpodyInitKind;

/* Reference body for keplerian elements. HF uses the central body
 * implicitly (CENTRAL); CR3BP picks one of the two primaries. */
typedef enum {
    SPODY_REF_BODY_CENTRAL   = 0,
    SPODY_REF_BODY_PRIMARY_1 = 1,
    SPODY_REF_BODY_PRIMARY_2 = 2
} SpodyRefBody;

/* Which anomaly the user typed in `anomaly_deg`. The validator wraps
 * mean -> true via spody_kepler_mean_to_true_anom before calling
 * spody_keplerian_to_cartesian. */
typedef enum {
    SPODY_ANOMALY_TRUE = 0,
    SPODY_ANOMALY_MEAN = 1
} SpodyAnomalyKind;

#define SPODY_MAX_THIRD_BODIES        8
#define SPODY_MAX_ALTITUDE_CROSSINGS 32   /* [[events.altitude_crossing]] cap */
#define SPODY_MAX_BODY_NAME          16
#define SPODY_MAX_SIM_NAME          128
#define SPODY_MAX_PATH             1024

/* One [[events.altitude_crossing]] entry. Built by parse_events;
 * fed verbatim into build_events in sim_run.c (one SpodyEvent per
 * spec). action / refined are stored as plain ints because the
 * spody_event_* enums live in spody-core's spody_events.h and this
 * header should not pull spody-core public types. The mapping is:
 *   action  : 0 = LOG, 1 = STOP, 2 = LOG_AND_STOP
 *             (spody_event_action enum in spody-core).
 *   refined : 1 = Brent + dense output (default), 0 = end-of-step
 *             (step-size precision; opt-out for catalog-style runs). */
typedef struct {
    char    body_name[SPODY_MAX_BODY_NAME];   /* central or third / cr3bp primary */
    double  altitude_km;
    int     action;
    int     refined;
} AltitudeCrossingSpec;

/* --------------------------------------------------------------------------
 * Override-target field descriptor.
 *
 * Each entry of [batch.columns] maps a CSV column name to a dotted path
 * that identifies a numeric field inside InputConfig. At parse time we
 * resolve every column to a const pointer into a small static table of
 * these descriptors so the per-case apply loop becomes a single offset +
 * type-dispatched store with no string handling.
 * -------------------------------------------------------------------------- */
typedef enum {
    SPODY_FIELD_DOUBLE   = 0,   /* a single `double` field            */
    SPODY_FIELD_INT      = 1,   /* a single `int`    field            */
    SPODY_FIELD_VEC3_AT  = 2    /* one element of a `double[3]` field */
} SpodyFieldKind;

/* Per-cell validation rule attached to each FIELD_TABLE entry. Lets the
 * batch validator check raw CSV values without ever materialising the
 * post-override InputConfig. Cross-field constraints (|r|>0, h_init in
 * [h_min, h_max], ...) are NOT covered -- the run-time catches them. */
typedef enum {
    SPODY_VAL_ANY      = 0,    /* finite, nothing else                            */
    SPODY_VAL_POSITIVE = 1,    /* > 0                                              */
    SPODY_VAL_NON_NEG  = 2,    /* >= 0                                             */
    SPODY_VAL_BOOL     = 3     /* exactly 0 or 1 (used with SPODY_FIELD_INT)       */
} SpodyValRule;

typedef struct {
    const char    *path;     /* dotted path, static string (e.g. "spacecraft.mass_kg") */
    SpodyFieldKind kind;
    size_t         offset;   /* offsetof(InputConfig, field)            */
    int            vec_idx;  /* 0..2 for SPODY_FIELD_VEC3_AT, else 0    */
    SpodyValRule   rule;     /* per-cell validation rule for batch CSV  */
} SpodyFieldDesc;

/* --------------------------------------------------------------------------
 * Batch configuration -- populated only when [batch] is present in the TOML.
 *
 * cfg->batch is NULL for a single-scenario file; otherwise it points to a
 * heap-allocated BatchConfig owning the matrix of per-case data loaded from
 * cases_file. Memory is released by spody_free_input.
 *
 * Layout of `values`: row-major, n_cases * n_columns doubles.
 *   values[i * n_columns + j] = value of column j on case i.
 *
 * `case_ids` is always populated (heap, one string per case):
 *   - if the cases_file had a column named "id", values come from there;
 *   - otherwise, ids are zero-padded 1-based row indices ("001", "002", ...).
 *
 * `column_names` lists the numeric columns in the order they appear in the
 * file, excluding the "id" column if any.
 * -------------------------------------------------------------------------- */
typedef struct {
    char   name[SPODY_MAX_SIM_NAME];
    char   output_dir[SPODY_MAX_PATH];   /* resolved path                 */
    int    thread_number;                 /* 1 = sequential; >1 needs OpenMP */
    char   cases_file[SPODY_MAX_PATH];   /* resolved path                 */

    int      n_cases;
    int      n_columns;
    char   **column_names;   /* n_columns entries, heap, strdup'd         */
    char   **case_ids;       /* n_cases entries, heap, strdup'd           */
    double  *values;         /* n_cases * n_columns doubles, row-major    */

    /* Resolved override target for each column, populated from
     * [batch.columns]. Entries point into a private static table in
     * toml_input.c -- do NOT free individual entries, only the array. */
    const SpodyFieldDesc **column_targets;   /* n_columns entries */

    /* Per-column apply mode: 0 = override (out = cell), 1 = delta
     * (out = base + cell, additive). Set from [batch.columns]: a plain
     * string is override; an inline table { target = "...", mode =
     * "delta" } selects delta. Delta cells are NOT range-checked. */
    int *column_is_delta;                    /* n_columns entries */
} BatchConfig;

typedef struct {
    /* [simulation] */
    char               sim_name[SPODY_MAX_SIM_NAME];
    SpodyDynamicsModel dynamics_model;   /* discriminator, defaults to
                                          * SPODY_DYN_HIGH_FIDELITY if the
                                          * TOML key is absent */
    double             et_start_s;
    int                has_et_start_s;   /* 1 if the TOML provided
                                          * simulation.et_start_s explicitly;
                                          * required for HF, ignored by CR3BP */
    double             duration_s;

    /* [spacecraft] OR [debris] -- exactly one is present (XOR at parse).
     * In debris mode the parser sets mass_kg=1.0 so srp_area_m2 numerically
     * equals A/m; sim_setup and spody-core stay unaware of the distinction. */
    int    debris_mode;          /* 1 if [debris] was used instead of [spacecraft] */
    double mass_kg;
    int    has_srp_block;        /* 1 if [spacecraft.srp] is present, always 1 in debris mode */
    double srp_area_m2;          /* valid iff has_srp_block; A/m numerically when debris_mode */
    double srp_cr;               /* valid iff has_srp_block              */
    /* [spacecraft.drag] (or debris.am_drag/Cd in debris mode): same
     * area-or-A/m normalisation as SRP -- drag_area_m2 numerically
     * equals A/m when debris_mode. */
    int    has_drag_block;       /* 1 if [spacecraft.drag] (or debris drag keys) present */
    double drag_area_m2;         /* valid iff has_drag_block; A/m numerically when debris_mode */
    double drag_cd;              /* valid iff has_drag_block             */

    /* [initial_state]
     *
     * Two input flavours are supported and the parser normalises both
     * into the same (frame, position_km, velocity_kms) triple:
     *
     *   kind = "cartesian" (default, back-compat): the user gives the
     *       position / velocity directly in the chosen frame.
     *   kind = "keplerian": the user gives six classical orbital
     *       elements + a reference body (central body for HF,
     *       primary_1 / primary_2 for CR3BP); spody_validate_input
     *       calls spody_keplerian_to_cartesian (and, for CR3BP,
     *       spody_inertial_to_cr3bp_synodic) AFTER all sections are
     *       parsed and populates position_km / velocity_kms so the
     *       rest of the pipeline stays unchanged.
     *
     * When init_kind == SPODY_INIT_KEPLERIAN, the kep_* fields hold
     * the as-parsed values (sma > 0 km, ecc in [0, 1), angles in
     * degrees, anomaly in degrees with anomaly_kind discriminating
     * true vs mean). When init_kind == SPODY_INIT_CARTESIAN they
     * stay zero and are unused. */
    SpodyFrame       initial_frame;
    SpodyInitKind    init_kind;
    double           position_km[3];     /* always populated post-validate */
    double           velocity_kms[3];    /* always populated post-validate */
    SpodyRefBody     kep_ref_body;       /* Keplerian only; ignored otherwise */
    double           kep_sma_km;
    double           kep_ecc;
    double           kep_inc_deg;
    double           kep_raan_deg;
    double           kep_argp_deg;
    double           kep_anomaly_deg;
    SpodyAnomalyKind kep_anomaly_kind;

    /* [force_model] */
    SpodyCentralBody central_body;
    char             harmonics_file[SPODY_MAX_PATH];   /* resolved path */
    int              harmonics_degree;
    /* Let the engine lower the harmonics degree per integrator step
     * when the satellite is far enough out that the high-degree terms
     * no longer contribute. `harmonics_degree` stays the ceiling: the
     * adaptive rule can only ask for less, never for more. Optional in
     * the schema, default 0. */
    int              harmonics_adaptive;
    int              n_third_bodies;
    char             third_body_names[SPODY_MAX_THIRD_BODIES][SPODY_MAX_BODY_NAME];
    int              enable_srp;
    /* drag: OPTIONAL key, default false, so pre-drag TOMLs parse
     * unchanged. Requires a central body with a registered atmosphere
     * model (today: Earth / NRLMSISE-00), a [spacecraft.drag] block
     * (or debris drag keys) and a space_weather_file -- all enforced
     * by spody_validate_input. */
    int              enable_drag;
    /* solid_tides: OPTIONAL key naming the tide system of the gravity
     * file ("tide_free" | "zero_tide"); absent = no solid tide. The
     * tide model itself (Love numbers, tide-raising bodies) belongs to
     * the central body's registry row. */
    int              solid_tides;          /* SpodySolidTideSystem */
    /* general_relativity: OPTIONAL bool, default false -- the
     * Schwarzschild term of the central body (IERS 2010 eq. 10.12). */
    int              enable_general_relativity;
    /* earth_radiation_pressure: OPTIONAL bool, default false -- Earth
     * albedo + infrared on the spacecraft (Knocke et al. 1988). Earth
     * central body and SRP surface data required. */
    int              enable_earth_radiation_pressure;
    /* body_shape: REQUIRED (HF), "ellipsoid" | "equatorial_sphere".
     * The shape of the central body and of every third body wherever a
     * shape matters: the shadow of the SRP force and of the eclipse
     * event, IMPACT, and the altitude of altitude crossings (geodetic
     * above the spheroid, or distance minus equatorial radius). Bodies
     * that are spheres in pck00011 are the same either way. */
    int              body_shape;           /* SpodyBodyShapeModel */
    /* Earth-only assets. Required (and validated to exist) when
     * central_body == Earth, ignored otherwise. The GUI writes these
     * fields ONLY for Earth; for Moon-or-other they stay empty strings.
     *   eop_file    : IERS finals2000A.all (daily xp, yp, dUT1, dX, dY)
     *   iau2006_dir : directory containing IAU 2006 tab5.2{a,b,d}.txt
     *                 (X, Y, s+XY/2 series)
     *   space_weather_file : CelesTrak combined space weather CSV
     *                 (SW-All.csv); required only when drag = true */
    char             eop_file[SPODY_MAX_PATH];         /* resolved path, "" if none */
    char             iau2006_dir[SPODY_MAX_PATH];      /* resolved dir,  "" if none */
    char             space_weather_file[SPODY_MAX_PATH]; /* resolved path, "" if none */
    /* Density calibration k(t) applied to the atmosphere model
     * (rho_used = k * rho_model, see MappedDensityScale in spody-core).
     * OPTIONAL, drag-only, two exclusive flavours: a constant factor
     * (`density_scale`, > 0) or a node file (`density_scale_file`,
     * `mjd,k` rows). density_scale = 1.0 with an empty file path is
     * the uncalibrated default; validation rejects setting both. */
    double           density_scale;                      /* constant k; 1.0 default   */
    int              has_density_scale;                  /* 1 if the constant key set */
    char             density_scale_file[SPODY_MAX_PATH]; /* resolved path, "" if none */

    /* [ephemeris] */
    char ephemeris_file[SPODY_MAX_PATH];               /* resolved path */

    /* [cr3bp] -- populated only when dynamics_model = "cr3bp".
     * Empty (zero-init) for high_fidelity runs.
     *   primary_1, primary_2 : verbatim strings from TOML, used in
     *                          error messages and for the (primary_1,
     *                          primary_2) -> L lookup in the curated
     *                          pair table.
     *   mu1, mu2             : resolved from the body table
     *                          (km^3/s^2); convention mu1 >= mu2.
     *   L_km                 : primary-primary separation from the
     *                          curated pair table (km). */
    char   cr3bp_primary_1[SPODY_MAX_BODY_NAME];
    char   cr3bp_primary_2[SPODY_MAX_BODY_NAME];
    double cr3bp_mu1;
    double cr3bp_mu2;
    double cr3bp_L_km;

    /* [integrator] */
    SpodyIntegratorType integrator_type;
    double              rel_tol;
    double              h_init_s;
    double              h_min_s;
    double              h_max_s;
    /* time_scale: OPTIONAL, "tdb" (default) | "tt". The time coordinate
     * the integrator advances: TDB, the time of the ephemerides, for
     * every body; or TT, the time IERS 2010 (TN36 sec. 10.3) prescribes
     * for geocentric equations of motion -- Earth central body only.
     * Files keep "ET - et0" either way. */
    int                 time_scale_tt;

    /* [output] */
    SpodyOutputMode output_mode;
    double          output_interval_s;
    char            csv_file[SPODY_MAX_PATH];           /* resolved path, "" if none */
    char            bin_file[SPODY_MAX_PATH];           /* resolved path, "" if none */
    char            log_file[SPODY_MAX_PATH];           /* resolved path, "" if none */
    char            accelerations_file[SPODY_MAX_PATH]; /* per-force acc binary; "" disables */
    char            events_log[SPODY_MAX_PATH];         /* event triggers binary; "" disables */
    /* Parent directory for the per-run timestamp subfolder. When set,
     * spody.exe creates <output_dir>/<UTC-ISO8601>/ at launch, copies
     * the source TOML inside, and rewrites every file path above so
     * the run is fully self-contained. "" disables the run-folder
     * layout: file paths above are used verbatim (legacy behaviour). */
    char            output_dir[SPODY_MAX_PATH];

    /* [events] -- IMPACT is always on (no config). Eclipse is opt-in. */
    int    eclipse_event_enabled; /* 1 if [events].eclipse_threshold was set */
    double eclipse_threshold;     /* fraction in [0,1]; crossing fires the event */

    /* [[events.altitude_crossing]] -- recurring altitude triggers.
     * Each entry adds one SpodyEvent with kind = ALT_CROSSING that
     * fires on every sign change of (|r_sat - r_body| - body_radius -
     * altitude_km), so ascending and descending crossings are both
     * logged. Fixed array; cap is generous for typical use cases. */
    AltitudeCrossingSpec altitude_crossings[SPODY_MAX_ALTITUDE_CROSSINGS];
    int                  n_altitude_crossings;

    /* [batch] -- NULL if absent in the TOML, heap-allocated otherwise.
     * Released by spody_free_input. */
    BatchConfig *batch;
} InputConfig;

/* --------------------------------------------------------------------------
 * Monte Carlo uncertainty configuration: the `<name>.uq.toml` file read by
 * `spody uncertainty montecarlo`. A separate file that points at a
 * propagate scenario; nothing of it lives in InputConfig. Fixed-size
 * arrays only (no heap), so the struct can be copied freely.
 * -------------------------------------------------------------------------- */
#define SPODY_UQ_MAX_PARAMS     16
#define SPODY_UQ_MAX_SNAPSHOTS  64
#define SPODY_UQ_MAX_TARGET     64

typedef enum {
    SPODY_UQ_DIST_NORMAL    = 0,
    SPODY_UQ_DIST_LOGNORMAL = 1
} SpodyUqDistribution;

typedef enum {
    SPODY_UQ_SIGMA_ABSOLUTE = 0,   /* `sigma`, in the parameter's unit       */
    SPODY_UQ_SIGMA_PERCENT  = 1,   /* `sigma_percent`, of the scenario value */
    SPODY_UQ_SIGMA_LN       = 2    /* `sigma_ln`, std of ln p (lognormal)    */
} SpodyUqSigmaKind;

typedef enum {
    SPODY_UQ_VALUE_IS_NONE   = 0,  /* normal: mean and median coincide */
    SPODY_UQ_VALUE_IS_MEAN   = 1,
    SPODY_UQ_VALUE_IS_MEDIAN = 2
} SpodyUqValueIs;

typedef struct {
    char                  target[SPODY_UQ_MAX_TARGET];  /* batch target path */
    const SpodyFieldDesc *field;        /* into toml_input.c's FIELD_TABLE  */
    SpodyUqDistribution   distribution;
    SpodyUqSigmaKind      sigma_kind;
    double                sigma;        /* as written (percent stays percent) */
    SpodyUqValueIs        scenario_value_is;
    uint64_t              substream;    /* spody_random_substream_id(target) */
} SpodyUqParameter;

typedef struct {
    char     path[SPODY_MAX_PATH];        /* the .uq.toml itself              */
    char     name[SPODY_MAX_SIM_NAME];
    char     scenario[SPODY_MAX_PATH];    /* resolved scenario TOML           */
    char     output_dir[SPODY_MAX_PATH];  /* resolved; "" = the scenario's    */
    int      samples;                     /* dispersed cases (nominal extra)  */
    uint64_t seed;
    int      thread_number;
    int      n_snapshots;
    double   snapshots_s[SPODY_UQ_MAX_SNAPSHOTS];
    int      case_outputs;

    /* [montecarlo.initial_state]: absent = the initial state is exact. */
    int      has_initial_state;
    int      axes_ric;                    /* 1 = RIC, 0 = ICRF                */
    int      covariance_given;            /* 1 = `covariance`, 0 = sigmas     */
    double   sigma[6];                    /* km x3, km/s x3 (sigma form)      */
    double   correlation[36];             /* row-major, identity if absent    */
    double   covariance[36];              /* row-major, `covariance` form     */

    int              n_params;
    SpodyUqParameter params[SPODY_UQ_MAX_PARAMS];
} SpodyUqConfig;

/* --------------------------------------------------------------------------
 * API
 * -------------------------------------------------------------------------- */

/*
 * Parse a TOML file into *cfg. Relative paths in the TOML are resolved
 * against the directory containing the file. Does not touch the
 * filesystem beyond reading the TOML itself.
 *
 * Returns SPODY_OK on success, or one of SpodyErrorCode on failure with
 * *err filled in.
 */
int spody_load_input(const char *toml_path,
                     InputConfig *cfg,
                     SpodyError *err);

/*
 * Release every heap resource owned by *cfg. Today this is only cfg->batch
 * (and its sub-arrays) when present. Safe to call on a zero-initialised
 * struct (no-op). Always safe to call after spody_load_input, even if the
 * call failed -- spody_load_input zeroes *cfg on entry.
 */
void spody_free_input(InputConfig *cfg);

/*
 * Materialise the per-case InputConfig for row `case_idx` of `batch` on
 * top of `base`. *out is a flat copy of *base with the per-column values
 * applied at the offsets resolved at parse time. out->batch is set to
 * NULL so the per-case config is itself a single-scenario input.
 *
 * Requires batch != NULL and 0 <= case_idx < batch->n_cases; otherwise
 * *out is just a flat copy of *base with out->batch = NULL.
 */
void spody_apply_batch_case(const InputConfig *base,
                            const BatchConfig *batch,
                            int case_idx,
                            InputConfig *out);

/*
 * Run semantic checks on an already-parsed InputConfig: file existence
 * for harmonics/ephemeris, positive tolerances, valid third-body names,
 * SRP block presence when enabled, etc.
 *
 * Returns SPODY_OK or an error code with *err filled in.
 */
int spody_validate_input(const InputConfig *cfg, SpodyError *err);

/*
 * Parse a `<name>.uq.toml` Monte Carlo file into *uq (zeroed on entry).
 * Only the [montecarlo] table and its two sub-tables are allowed, and an
 * unknown key anywhere is an error (a misspelt key must not vanish). Paths
 * resolve against the file's directory. Shape checks only; everything that
 * needs the scenario is in spody_validate_uq_input.
 */
int spody_load_uq_input(const char *path, SpodyUqConfig *uq, SpodyError *err);

/*
 * Check *uq against the scenario it names (already loaded AND validated):
 * high-fidelity, no [batch], output.mode = "fixed", snapshots on the
 * output grid, every parameter target dispersible and present in the
 * scenario, positive scenario values where the distribution needs them,
 * a valid correlation / covariance (positive definite on the components
 * with nonzero variance; the most negative eigenvalue is reported
 * otherwise), and no two quantities on the same random substream.
 */
int spody_validate_uq_input(const SpodyUqConfig *uq,
                            const InputConfig *scenario, SpodyError *err);

/*
 * The 6x6 initial-state covariance of *uq in its own axes (RIC or ICRF,
 * uq->axes_ric), km / km/s units, row-major: the `covariance` matrix as
 * given, or sigma_i sigma_j rho_ij. Zero matrix without an
 * [montecarlo.initial_state] table.
 */
void spody_uq_initial_covariance(const SpodyUqConfig *uq, double P[36]);

/*
 * Resolve a third-body name (e.g. "Earth", "Sun") to its NAIF id, the
 * matching gravitational parameter (km^3/s^2) and the equatorial body
 * radius (km). Each out-pointer may be NULL.
 * Returns 0 on success or -1 if the name is unknown. Used by sim_setup
 * to populate the ForceModelContext, by the validator to catch bad
 * names early, and by sim_run to build the always-on IMPACT event list.
 */
int spody_lookup_third_body(const char *name, int *naif_id,
                            double *mu, double *radius_km);

/*
 * Reverse lookup by NAIF id. Same semantics as spody_lookup_third_body
 * but takes the integer NAIF id directly (useful when the name has
 * already been consumed and only the id is around, e.g. when building
 * the multi-body event list from the force-model context).
 */
int spody_lookup_body_by_naif(int naif_id, const char **name,
                              double *mu, double *radius_km);

/*
 * Shape of body `naif_id` under force_model.body_shape (a
 * SpodyBodyShapeModel): the equatorial radius, plus -- for the
 * ellipsoid and a body pck00011 models as a spheroid -- the polar
 * radius and the spin axis in ICRF at `et` (IAU WGCCRE elements).
 * Otherwise r_pol = 0: a sphere of the equatorial radius. Returns 0,
 * or -1 for an unknown id.
 */
int spody_lookup_body_shape(int naif_id, int body_shape, double et,
                            SpodyBodyShape *shape);

#ifdef __cplusplus
}
#endif

/* ==========================================================================
 * [batch] section -- status snapshot
 * ==========================================================================
 * Implemented:
 *   - parsing of name / output_dir / thread_number / cases_file
 *   - loading of cases_file in .csv form (header row + numeric data rows,
 *     optional `id` column for explicit case naming)
 *
 * Pending:
 *   - [batch.columns] mapping (column name -> TOML dotted path)
 *   - per-case override application (override vs delta semantics)
 *   - cases_file in .spody binary form (returns "not implemented" today)
 *   - validation that ephemeris_file / harmonics_file / harmonics_degree
 *     are NOT in [batch.columns] (they belong to the shared part)
 *   - sanitisation of `id` values when used in output filenames
 *
 * Reference shape:
 *
 *   [batch]
 *   name           = "..."
 *   output_dir     = "..."
 *   thread_number  = 1              # 1 = sequential; >1 errors until OpenMP
 *   cases_file     = "..."          # .csv (today) or .spody (future)
 *
 *   [batch.columns]                 # not yet parsed
 *   Cr             = "spacecraft.srp.Cr"
 *   A_over_m       = "spacecraft.srp.area_m2"
 *   dv_x           = "initial_state.velocity_kms[0]"
 *
 * Case naming for output files: <batch.name>_<id>.<csv|bin>
 * with <id> from the cases_file `id` column if present, otherwise a
 * zero-padded 1-based row index.
 * ========================================================================== */

#endif /* SPODY_TOML_INPUT_H */
