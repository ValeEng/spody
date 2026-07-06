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
 * SpOdy input file parser + validator.
 *
 * Implementation notes:
 *   - Backed by cktan/tomlc99 (vendored under external/tomlc99).
 *   - All TOML datums are read into the InputConfig and freed before
 *     return; tomlc99 strings need free() after use.
 *   - Relative paths are resolved against the TOML directory and
 *     stored as joined strings (not canonicalised) -- sufficient for
 *     fopen() and keeps the code dependency-free.
 *   - Errors propagate via SpodyError; nothing prints to stderr.
 */
#include "toml_input.h"

#include <ctype.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "toml.h"
#include "spody_const.h"
#include "spody_forcemodels.h"   /* spody_inertial_to_cr3bp_synodic     */
#include "spody_kepler.h"        /* keplerian -> Cartesian conversion   */
#include "central_body.h"        /* spody_central_body_get for mu lookup */

/* --------------------------------------------------------------------------
 * Path helpers
 *
 * No POSIX dirname / realpath: we want this to compile clean on MSVC too.
 * The TOML file path's parent directory is computed by scanning for the
 * last '/' or '\\'; relative paths in the TOML are joined with '/' (mixed
 * separators are fine on Windows fopen).
 * -------------------------------------------------------------------------- */

static int is_abs_path(const char *p) {
    if (!p || !*p) return 0;
    if (p[0] == '/' || p[0] == '\\') return 1;
    /* Windows drive-letter form: e.g. "C:" or "C:\foo" */
    if (((p[0] >= 'A' && p[0] <= 'Z') || (p[0] >= 'a' && p[0] <= 'z'))
        && p[1] == ':') return 1;
    return 0;
}

static void parent_dir(const char *path, char *out, size_t outsz) {
    const char *last = NULL;
    for (const char *p = path; *p; ++p) {
        if (*p == '/' || *p == '\\') last = p;
    }
    if (!last) {
        snprintf(out, outsz, ".");
        return;
    }
    size_t n = (size_t)(last - path);
    if (n >= outsz) n = outsz - 1;
    memcpy(out, path, n);
    out[n] = '\0';
}

static void resolve_path(const char *base_dir, const char *rel,
                         char *out, size_t outsz) {
    if (is_abs_path(rel)) {
        snprintf(out, outsz, "%s", rel);
    } else {
        snprintf(out, outsz, "%s/%s", base_dir, rel);
    }
}

static int file_exists(const char *path) {
    FILE *fp = fopen(path, "rb");
    if (fp) { fclose(fp); return 1; }
    return 0;
}

/* --------------------------------------------------------------------------
 * Body-name lookup
 * -------------------------------------------------------------------------- */

typedef struct {
    const char *name;
    int         naif_id;
    double      mu;
    double      radius_km;   /* mean equatorial radius from spody_const.h */
} BodyEntry;

static const BodyEntry BODY_TABLE[] = {
    { "Sun",     10,  SUN_MU,     SUN_RADIUS     },
    { "Mercury", 199, MERCURY_MU, MERCURY_RADIUS },
    { "Venus",   299, VENUS_MU,   VENUS_RADIUS   },
    { "Earth",   399, EARTH_MU,   EARTH_RADIUS   },
    { "Moon",    301, MOON_MU,    MOON_RADIUS    },
    { "Mars",    499, MARS_MU,    MARS_RADIUS    },
    { "Jupiter", 599, JUPITER_MU, JUPITER_RADIUS },
    { "Saturn",  699, SATURN_MU,  SATURN_RADIUS  },
    { "Uranus",  799, URANUS_MU,  URANUS_RADIUS  },
    { "Neptune", 899, NEPTUNE_MU, NEPTUNE_RADIUS }
};
static const int N_BODY_TABLE = (int)(sizeof BODY_TABLE / sizeof BODY_TABLE[0]);

int spody_lookup_third_body(const char *name, int *naif_id,
                            double *mu, double *radius_km) {
    if (!name) return -1;
    for (int i = 0; i < N_BODY_TABLE; ++i) {
        if (strcmp(name, BODY_TABLE[i].name) == 0) {
            if (naif_id)   *naif_id   = BODY_TABLE[i].naif_id;
            if (mu)        *mu        = BODY_TABLE[i].mu;
            if (radius_km) *radius_km = BODY_TABLE[i].radius_km;
            return 0;
        }
    }
    return -1;
}

int spody_lookup_body_by_naif(int naif_id, const char **name,
                              double *mu, double *radius_km) {
    for (int i = 0; i < N_BODY_TABLE; ++i) {
        if (BODY_TABLE[i].naif_id == naif_id) {
            if (name)      *name      = BODY_TABLE[i].name;
            if (mu)        *mu        = BODY_TABLE[i].mu;
            if (radius_km) *radius_km = BODY_TABLE[i].radius_km;
            return 0;
        }
    }
    return -1;
}

/* --------------------------------------------------------------------------
 * tomlc99 wrappers -- compact required/optional accessors that emit
 * uniformly-formatted errors.
 *
 * tomlc99 strings come malloc'd; we always free() before returning.
 * -------------------------------------------------------------------------- */

static int req_string(toml_table_t *tbl, const char *section, const char *key,
                      char *out, size_t outsz, SpodyError *err) {
    toml_datum_t d = toml_string_in(tbl, key);
    if (!d.ok) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY,
                "missing required string '%s.%s'", section, key);
        return SPODY_ERR_MISSING_KEY;
    }
    snprintf(out, outsz, "%s", d.u.s);
    free(d.u.s);
    return SPODY_OK;
}

static int req_double(toml_table_t *tbl, const char *section, const char *key,
                      double *out, SpodyError *err) {
    toml_datum_t d = toml_double_in(tbl, key);
    if (d.ok) { *out = d.u.d; return SPODY_OK; }
    /* Accept ints transparently as doubles (TOML 1.0 distinguishes them
     * but most users will write `60.0` or `60` and mean the same thing.) */
    toml_datum_t di = toml_int_in(tbl, key);
    if (di.ok) { *out = (double)di.u.i; return SPODY_OK; }
    spody_error_set(err, SPODY_ERR_MISSING_KEY,
            "missing required numeric '%s.%s'", section, key);
    return SPODY_ERR_MISSING_KEY;
}

static int req_int(toml_table_t *tbl, const char *section, const char *key,
                   int *out, SpodyError *err) {
    toml_datum_t d = toml_int_in(tbl, key);
    if (!d.ok) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY,
                "missing required integer '%s.%s'", section, key);
        return SPODY_ERR_MISSING_KEY;
    }
    if (d.u.i < (int64_t)(-2147483647) || d.u.i > (int64_t)2147483647) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "'%s.%s' = %lld is out of int32 range",
                section, key, (long long)d.u.i);
        return SPODY_ERR_BAD_VALUE;
    }
    *out = (int)d.u.i;
    return SPODY_OK;
}

static int req_bool(toml_table_t *tbl, const char *section, const char *key,
                    int *out, SpodyError *err) {
    toml_datum_t d = toml_bool_in(tbl, key);
    if (!d.ok) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY,
                "missing required boolean '%s.%s'", section, key);
        return SPODY_ERR_MISSING_KEY;
    }
    *out = d.u.b ? 1 : 0;
    return SPODY_OK;
}

static int opt_string(toml_table_t *tbl, const char *key,
                      char *out, size_t outsz, int *present) {
    *present = 0;
    if (!tbl) return SPODY_OK;
    toml_datum_t d = toml_string_in(tbl, key);
    if (!d.ok) return SPODY_OK;
    snprintf(out, outsz, "%s", d.u.s);
    free(d.u.s);
    *present = 1;
    return SPODY_OK;
}

static int req_vec3(toml_table_t *tbl, const char *section, const char *key,
                    double out[3], SpodyError *err) {
    toml_array_t *a = toml_array_in(tbl, key);
    if (!a) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY,
                "missing required array '%s.%s'", section, key);
        return SPODY_ERR_MISSING_KEY;
    }
    int n = toml_array_nelem(a);
    if (n != 3) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "'%s.%s' must have exactly 3 elements (got %d)",
                section, key, n);
        return SPODY_ERR_BAD_VALUE;
    }
    for (int i = 0; i < 3; ++i) {
        toml_datum_t d = toml_double_at(a, i);
        if (d.ok)  { out[i] = d.u.d; continue; }
        toml_datum_t di = toml_int_at(a, i);
        if (di.ok) { out[i] = (double)di.u.i; continue; }
        spody_error_set(err, SPODY_ERR_BAD_TYPE,
                "element %d of '%s.%s' is not numeric", i, section, key);
        return SPODY_ERR_BAD_TYPE;
    }
    return SPODY_OK;
}

static int req_string_array(toml_table_t *tbl,
                            const char *section, const char *key,
                            char (*out)[SPODY_MAX_BODY_NAME], int max_n,
                            int *n_out, SpodyError *err) {
    toml_array_t *a = toml_array_in(tbl, key);
    if (!a) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY,
                "missing required array '%s.%s'", section, key);
        return SPODY_ERR_MISSING_KEY;
    }
    int n = toml_array_nelem(a);
    if (n > max_n) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "'%s.%s' has %d elements, max supported is %d",
                section, key, n, max_n);
        return SPODY_ERR_BAD_VALUE;
    }
    for (int i = 0; i < n; ++i) {
        toml_datum_t d = toml_string_at(a, i);
        if (!d.ok) {
            spody_error_set(err, SPODY_ERR_BAD_TYPE,
                    "element %d of '%s.%s' is not a string",
                    i, section, key);
            return SPODY_ERR_BAD_TYPE;
        }
        snprintf(out[i], SPODY_MAX_BODY_NAME, "%s", d.u.s);
        free(d.u.s);
    }
    *n_out = n;
    return SPODY_OK;
}

/* --------------------------------------------------------------------------
 * String-to-enum mappings
 * -------------------------------------------------------------------------- */

static int parse_central_body(const char *name, SpodyCentralBody *out,
                              SpodyError *err) {
    if (spody_central_body_from_name(name, out) == 0) return SPODY_OK;
    char known[128];
    spody_central_body_known_names(known, sizeof known);
    spody_error_set(err, SPODY_ERR_BAD_VALUE,
            "force_model.central_body = '%s' is not supported "
            "(known: %s)", name, known);
    return SPODY_ERR_BAD_VALUE;
}

static int parse_frame(const char *name, SpodyFrame *out, SpodyError *err) {
    if (strcmp(name, "central_inertial") == 0) {
        *out = SPODY_FRAME_CENTRAL_INERTIAL;  return SPODY_OK;
    }
    if (strcmp(name, "synodic_rotating") == 0) {
        *out = SPODY_FRAME_SYNODIC_ROTATING;  return SPODY_OK;
    }
    if (strcmp(name, "central_body_fixed") == 0) {
        *out = SPODY_FRAME_CENTRAL_BODY_FIXED; return SPODY_OK;
    }
    spody_error_set(err, SPODY_ERR_BAD_VALUE,
            "initial_state.frame = '%s' is not supported "
            "(supported: 'central_inertial', 'synodic_rotating', "
            "'central_body_fixed')", name);
    return SPODY_ERR_BAD_VALUE;
}

/* --------------------------------------------------------------------------
 * CR3BP primary-pair lookup
 *
 * Curated table mapping a (primary_1, primary_2) pair to the canonical
 * primary-primary separation L (km). The CR3BP assumes a fixed circular
 * orbit between the two bodies; L is the radius of that circle. Adding
 * a new pair is one line. Order in the TOML is significant: primary_1
 * is the bigger body.
 * -------------------------------------------------------------------------- */
typedef struct {
    const char *primary_1;
    const char *primary_2;
    double      L_km;
} CR3BPPair;

static const CR3BPPair CR3BP_PAIRS[] = {
    { "Earth", "Moon", EARTH_MOON_DISTANCE_KM },
};
static const int N_CR3BP_PAIRS = (int)(sizeof CR3BP_PAIRS / sizeof CR3BP_PAIRS[0]);

static int lookup_cr3bp_pair(const char *p1, const char *p2, double *L_out) {
    for (int i = 0; i < N_CR3BP_PAIRS; ++i) {
        if (strcmp(CR3BP_PAIRS[i].primary_1, p1) == 0 &&
            strcmp(CR3BP_PAIRS[i].primary_2, p2) == 0) {
            if (L_out) *L_out = CR3BP_PAIRS[i].L_km;
            return 0;
        }
    }
    return -1;
}

static int parse_integrator_type(const char *name, SpodyIntegratorType *out,
                                 SpodyError *err) {
    if (strcmp(name, "rkdp45") == 0) {
        *out = SPODY_INTEG_TYPE_RKDP45; return SPODY_OK;
    }
    spody_error_set(err, SPODY_ERR_BAD_VALUE,
            "integrator.type = '%s' is not supported in v0 "
            "(supported: 'rkdp45')", name);
    return SPODY_ERR_BAD_VALUE;
}

static int parse_output_mode(const char *name, SpodyOutputMode *out,
                             SpodyError *err) {
    if (strcmp(name, "fixed") == 0) { *out = SPODY_OUT_FIXED; return SPODY_OK; }
    if (strcmp(name, "step")  == 0) { *out = SPODY_OUT_STEP;  return SPODY_OK; }
    spody_error_set(err, SPODY_ERR_BAD_VALUE,
            "output.mode = '%s' is invalid (expected 'fixed' or 'step')",
            name);
    return SPODY_ERR_BAD_VALUE;
}

/* --------------------------------------------------------------------------
 * Section parsers
 * -------------------------------------------------------------------------- */

static int parse_simulation(toml_table_t *root, InputConfig *cfg,
                            SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "simulation");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [simulation]");
        return SPODY_ERR_MISSING_KEY;
    }
    int rc;
    if ((rc = req_string(t, "simulation", "name",
                         cfg->sim_name, sizeof cfg->sim_name, err))) return rc;

    /* dynamics_model: optional, defaults to "high_fidelity" so every TOML
     * written before this slice parses unchanged. When present, the value
     * must match a registered model name (see dynamics_model.c). */
    cfg->dynamics_model = SPODY_DYN_HIGH_FIDELITY;
    {
        char dm_name[32] = {0};
        int  present     = 0;
        if ((rc = opt_string(t, "dynamics_model", dm_name, sizeof dm_name,
                             &present))) return rc;
        if (present) {
            if (spody_dynamics_model_from_name(dm_name,
                                               &cfg->dynamics_model) != 0) {
                char known[128];
                spody_dynamics_model_known_names(known, sizeof known);
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "simulation.dynamics_model = '%s' is not recognised "
                        "(known: %s)", dm_name, known);
                return SPODY_ERR_BAD_VALUE;
            }
        }
    }

    /* et_start_s: required for high_fidelity (drives ephemeris/EOP
     * lookups), ignorable for autonomous models like cr3bp. Read as
     * optional here, with a presence flag so the model-specific
     * validator can reject "missing for HF" without being fooled by
     * a legitimate value of 0.0 (= J2000 epoch). */
    cfg->et_start_s     = 0.0;
    cfg->has_et_start_s = 0;
    {
        toml_datum_t d  = toml_double_in(t, "et_start_s");
        toml_datum_t di = toml_int_in   (t, "et_start_s");
        if (d.ok)       { cfg->et_start_s = d.u.d;           cfg->has_et_start_s = 1; }
        else if (di.ok) { cfg->et_start_s = (double)di.u.i;  cfg->has_et_start_s = 1; }
    }

    if ((rc = req_double(t, "simulation", "duration_s",
                         &cfg->duration_s, err))) return rc;
    return SPODY_OK;
}

static int parse_spacecraft(toml_table_t *root, InputConfig *cfg,
                            SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "spacecraft");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [spacecraft]");
        return SPODY_ERR_MISSING_KEY;
    }
    int rc;
    if ((rc = req_double(t, "spacecraft", "mass_kg", &cfg->mass_kg, err))) return rc;

    toml_table_t *srp = toml_table_in(t, "srp");
    if (srp) {
        cfg->has_srp_block = 1;
        /* SRP only ever needs A/m. The user supplies exactly one of:
         *   area_m2 -> A/m derived as area / mass_kg, or
         *   am_srp  -> A/m given directly [m^2/kg].
         * am_srp is converted to its equivalent area here so the rest of
         * the pipeline (validator, sim_setup, batch) sees one area-based
         * representation. Value ranges are checked in spody_validate_input. */
        int has_area = toml_double_in(srp, "area_m2").ok ||
                       toml_int_in   (srp, "area_m2").ok;
        int has_am   = toml_double_in(srp, "am_srp").ok ||
                       toml_int_in   (srp, "am_srp").ok;
        if (has_area == has_am) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[spacecraft.srp] needs exactly one of 'area_m2' or "
                    "'am_srp' (got %s)", has_area ? "both" : "neither");
            return SPODY_ERR_BAD_VALUE;
        }
        if (has_am) {
            double am;
            if ((rc = req_double(srp, "spacecraft.srp", "am_srp", &am, err)))
                return rc;
            cfg->srp_area_m2 = am * cfg->mass_kg;
        } else {
            if ((rc = req_double(srp, "spacecraft.srp", "area_m2",
                                 &cfg->srp_area_m2, err))) return rc;
        }
        if ((rc = req_double(srp, "spacecraft.srp", "Cr",
                             &cfg->srp_cr, err))) return rc;
    } else {
        cfg->has_srp_block = 0;
        cfg->srp_area_m2   = 0.0;
        cfg->srp_cr        = 0.0;
    }

    /* [spacecraft.drag]: same area-or-A/m normalisation as SRP (the
     * drag force only ever consumes Cd * A/m). */
    toml_table_t *drag = toml_table_in(t, "drag");
    if (drag) {
        cfg->has_drag_block = 1;
        int has_area = toml_double_in(drag, "area_m2").ok ||
                       toml_int_in   (drag, "area_m2").ok;
        int has_am   = toml_double_in(drag, "am_drag").ok ||
                       toml_int_in   (drag, "am_drag").ok;
        if (has_area == has_am) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[spacecraft.drag] needs exactly one of 'area_m2' or "
                    "'am_drag' (got %s)", has_area ? "both" : "neither");
            return SPODY_ERR_BAD_VALUE;
        }
        if (has_am) {
            double am;
            if ((rc = req_double(drag, "spacecraft.drag", "am_drag", &am, err)))
                return rc;
            cfg->drag_area_m2 = am * cfg->mass_kg;
        } else {
            if ((rc = req_double(drag, "spacecraft.drag", "area_m2",
                                 &cfg->drag_area_m2, err))) return rc;
        }
        if ((rc = req_double(drag, "spacecraft.drag", "Cd",
                             &cfg->drag_cd, err))) return rc;
    } else {
        cfg->has_drag_block = 0;
        cfg->drag_area_m2   = 0.0;
        cfg->drag_cd        = 0.0;
    }
    return SPODY_OK;
}

/* [debris] -- alternative to [spacecraft] for SRP-driven workflows where
 * A/m is the natural primary parameter (debris fragments, non-cooperative
 * objects). Mass is irrelevant for SRP-only physics, so [debris] does not
 * take one; the parser sets mass_kg=1.0 internally and stores am_srp
 * directly in srp_area_m2 so srp_area_m2 numerically equals A/m and
 * spody_init_Spacecraft recomputes am_srp = area_srp / 1 = am_srp.
 * Batch targets are debris.am_srp / debris.Cr. */
static int parse_debris(toml_table_t *root, InputConfig *cfg, SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "debris");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [debris]");
        return SPODY_ERR_MISSING_KEY;
    }
    int rc;
    double am;
    if ((rc = req_double(t, "debris", "am_srp", &am,           err))) return rc;
    if ((rc = req_double(t, "debris", "Cr",     &cfg->srp_cr, err))) return rc;

    cfg->debris_mode   = 1;
    cfg->mass_kg       = 1.0;     /* fictitious; only A/m matters */
    cfg->has_srp_block = 1;       /* debris implies SRP is the point */
    cfg->srp_area_m2   = am;      /* am * mass = am * 1 = am */

    /* Optional drag pair (both-or-neither): debris.am_drag + debris.Cd.
     * Same mass=1 trick, so drag_area_m2 numerically equals A/m. */
    {
        int has_am = toml_double_in(t, "am_drag").ok ||
                     toml_int_in   (t, "am_drag").ok;
        int has_cd = toml_double_in(t, "Cd").ok ||
                     toml_int_in   (t, "Cd").ok;
        if (has_am != has_cd) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[debris] drag needs both 'am_drag' and 'Cd' "
                    "(got only %s)", has_am ? "am_drag" : "Cd");
            return SPODY_ERR_BAD_VALUE;
        }
        if (has_am) {
            double am_drag;
            if ((rc = req_double(t, "debris", "am_drag", &am_drag, err)))
                return rc;
            if ((rc = req_double(t, "debris", "Cd", &cfg->drag_cd, err)))
                return rc;
            cfg->has_drag_block = 1;
            cfg->drag_area_m2   = am_drag;
        }
    }
    return SPODY_OK;
}

static int parse_initial_state(toml_table_t *root, InputConfig *cfg,
                               SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "initial_state");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [initial_state]");
        return SPODY_ERR_MISSING_KEY;
    }
    char frame_name[32] = {0};
    int rc;
    if ((rc = req_string(t, "initial_state", "frame",
                         frame_name, sizeof frame_name, err))) return rc;
    if ((rc = parse_frame(frame_name, &cfg->initial_frame, err))) return rc;

    /* `kind` defaults to "cartesian" so every TOML written before this
     * slice keeps parsing unchanged. When present and == "keplerian"
     * we pull the six classical elements + reference body + anomaly
     * discriminator; spody_validate_input later resolves the right mu
     * and populates position_km / velocity_kms. */
    cfg->init_kind = SPODY_INIT_CARTESIAN;
    {
        char kind_name[32] = {0};
        int  present       = 0;
        if ((rc = opt_string(t, "kind", kind_name, sizeof kind_name,
                             &present))) return rc;
        if (present) {
            if (strcmp(kind_name, "cartesian") == 0) {
                cfg->init_kind = SPODY_INIT_CARTESIAN;
            } else if (strcmp(kind_name, "keplerian") == 0) {
                cfg->init_kind = SPODY_INIT_KEPLERIAN;
            } else {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "initial_state.kind = '%s' is not supported "
                        "(expected 'cartesian' or 'keplerian')", kind_name);
                return SPODY_ERR_BAD_VALUE;
            }
        }
    }

    if (cfg->init_kind == SPODY_INIT_CARTESIAN) {
        if ((rc = req_vec3(t, "initial_state", "position_km",
                           cfg->position_km, err))) return rc;
        if ((rc = req_vec3(t, "initial_state", "velocity_kms",
                           cfg->velocity_kms, err))) return rc;
        return SPODY_OK;
    }

    /* Keplerian path. The numeric ranges are checked in
     * spody_validate_input alongside the mu / synodic conversion;
     * here we only enforce that every required key is present. */
    if ((rc = req_double(t, "initial_state", "semi_major_axis_km",
                         &cfg->kep_sma_km, err))) return rc;
    if ((rc = req_double(t, "initial_state", "eccentricity",
                         &cfg->kep_ecc, err))) return rc;
    if ((rc = req_double(t, "initial_state", "inclination_deg",
                         &cfg->kep_inc_deg, err))) return rc;
    if ((rc = req_double(t, "initial_state", "raan_deg",
                         &cfg->kep_raan_deg, err))) return rc;
    if ((rc = req_double(t, "initial_state", "arg_periapsis_deg",
                         &cfg->kep_argp_deg, err))) return rc;
    if ((rc = req_double(t, "initial_state", "anomaly_deg",
                         &cfg->kep_anomaly_deg, err))) return rc;

    char anom_name[16] = {0};
    if ((rc = req_string(t, "initial_state", "anomaly_type",
                         anom_name, sizeof anom_name, err))) return rc;
    if (strcmp(anom_name, "true") == 0) {
        cfg->kep_anomaly_kind = SPODY_ANOMALY_TRUE;
    } else if (strcmp(anom_name, "mean") == 0) {
        cfg->kep_anomaly_kind = SPODY_ANOMALY_MEAN;
    } else {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "initial_state.anomaly_type = '%s' is not supported "
                "(expected 'true' or 'mean')", anom_name);
        return SPODY_ERR_BAD_VALUE;
    }

    /* reference_body is optional for HF (defaults to "central"), required
     * for CR3BP (must be primary_1 or primary_2). The cross-check against
     * dynamics_model happens in the validator -- here we only parse. */
    cfg->kep_ref_body = SPODY_REF_BODY_CENTRAL;
    {
        char rb_name[16] = {0};
        int  present     = 0;
        if ((rc = opt_string(t, "reference_body", rb_name, sizeof rb_name,
                             &present))) return rc;
        if (present) {
            if (strcmp(rb_name, "central") == 0) {
                cfg->kep_ref_body = SPODY_REF_BODY_CENTRAL;
            } else if (strcmp(rb_name, "primary_1") == 0) {
                cfg->kep_ref_body = SPODY_REF_BODY_PRIMARY_1;
            } else if (strcmp(rb_name, "primary_2") == 0) {
                cfg->kep_ref_body = SPODY_REF_BODY_PRIMARY_2;
            } else {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "initial_state.reference_body = '%s' is not supported "
                        "(expected 'central', 'primary_1', or 'primary_2')",
                        rb_name);
                return SPODY_ERR_BAD_VALUE;
            }
        }
    }

    /* position_km / velocity_kms get populated by the validator. Zero
     * them here so a downstream code path that reads them before the
     * conversion runs sees a deterministic value. */
    cfg->position_km[0]  = cfg->position_km[1]  = cfg->position_km[2]  = 0.0;
    cfg->velocity_kms[0] = cfg->velocity_kms[1] = cfg->velocity_kms[2] = 0.0;
    return SPODY_OK;
}

/* Convert the parsed Keplerian elements into a Cartesian state in the
 * configured `initial_frame`, writing position_km / velocity_kms in
 * place. Called from spody_parse_toml after every section is parsed so
 * the mu of the reference body is resolvable.
 *
 *   HF + reference_body = "central" (default)
 *       -> mu = central body's GM; elements live in central_inertial;
 *          straight kepler -> Cartesian.
 *   CR3BP + reference_body = "primary_1" | "primary_2"
 *       -> mu = cr3bp_mu1 | cr3bp_mu2; elements live in the primary's
 *          local inertial frame; chain through spody_inertial_to_cr3bp_
 *          synodic to land in the synodic frame the integrator expects. */
static int finalize_keplerian_initial_state(InputConfig *cfg, SpodyError *err) {
    if (cfg->kep_sma_km <= 0.0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "initial_state.semi_major_axis_km must be positive (got %.6g)",
                cfg->kep_sma_km);
        return SPODY_ERR_BAD_VALUE;
    }
    if (cfg->kep_ecc < 0.0 || cfg->kep_ecc >= 1.0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "initial_state.eccentricity must be in [0, 1) (got %.6g); "
                "hyperbolic and parabolic orbits are not supported via "
                "Keplerian input", cfg->kep_ecc);
        return SPODY_ERR_BAD_VALUE;
    }
    if (cfg->kep_inc_deg < 0.0 || cfg->kep_inc_deg > 180.0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "initial_state.inclination_deg must be in [0, 180] (got %.6g)",
                cfg->kep_inc_deg);
        return SPODY_ERR_BAD_VALUE;
    }

    /* Resolve mu and validate (reference_body, dynamics_model, frame)
     * are consistent. */
    double mu_ref = 0.0;
    if (cfg->dynamics_model == SPODY_DYN_CR3BP) {
        if (cfg->initial_frame != SPODY_FRAME_SYNODIC_ROTATING) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "initial_state.frame must be 'synodic_rotating' for "
                    "Keplerian input under dynamics_model = 'cr3bp'");
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->kep_ref_body == SPODY_REF_BODY_CENTRAL) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "initial_state.reference_body is required for Keplerian "
                    "input under cr3bp (expected 'primary_1' or 'primary_2')");
            return SPODY_ERR_BAD_VALUE;
        }
        mu_ref = (cfg->kep_ref_body == SPODY_REF_BODY_PRIMARY_2)
                 ? cfg->cr3bp_mu2 : cfg->cr3bp_mu1;
    } else {  /* high_fidelity */
        if (cfg->initial_frame != SPODY_FRAME_CENTRAL_INERTIAL
                && cfg->initial_frame != SPODY_FRAME_CENTRAL_BODY_FIXED) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "initial_state.frame must be 'central_inertial' or "
                    "'central_body_fixed' for Keplerian input under "
                    "dynamics_model = 'high_fidelity'");
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->kep_ref_body != SPODY_REF_BODY_CENTRAL) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "initial_state.reference_body must be 'central' (or "
                    "omitted) for Keplerian input under high_fidelity; the "
                    "central body is implicit");
            return SPODY_ERR_BAD_VALUE;
        }
        const SpodyCentralBodySpec *cb = spody_central_body_get(cfg->central_body);
        if (!cb || cb->mu <= 0.0) {
            spody_error_set(err, SPODY_ERR_INTERNAL,
                    "could not resolve mu for central body (enum=%d)",
                    (int)cfg->central_body);
            return SPODY_ERR_INTERNAL;
        }
        mu_ref = cb->mu;
    }

    /* True anomaly (radians) -- handles either anomaly_type by routing
     * mean through Kepler's equation first. */
    double nu_rad = cfg->kep_anomaly_deg * DEG2RAD;
    if (cfg->kep_anomaly_kind == SPODY_ANOMALY_MEAN) {
        nu_rad = spody_kepler_mean_to_true_anom(nu_rad, cfg->kep_ecc);
    }

    double r_ref[3], v_ref[3];
    spody_keplerian_to_cartesian(cfg->kep_sma_km, cfg->kep_ecc,
                                 cfg->kep_inc_deg * DEG2RAD,
                                 cfg->kep_raan_deg * DEG2RAD,
                                 cfg->kep_argp_deg * DEG2RAD,
                                 nu_rad, mu_ref, r_ref, v_ref);

    if (cfg->dynamics_model == SPODY_DYN_CR3BP) {
        int primary_idx = (cfg->kep_ref_body == SPODY_REF_BODY_PRIMARY_2) ? 2 : 1;
        spody_inertial_to_cr3bp_synodic(r_ref, v_ref,
                                        cfg->cr3bp_mu1, cfg->cr3bp_mu2,
                                        cfg->cr3bp_L_km, primary_idx,
                                        cfg->position_km, cfg->velocity_kms);
    } else {
        cfg->position_km[0]  = r_ref[0]; cfg->position_km[1]  = r_ref[1]; cfg->position_km[2]  = r_ref[2];
        cfg->velocity_kms[0] = v_ref[0]; cfg->velocity_kms[1] = v_ref[1]; cfg->velocity_kms[2] = v_ref[2];
    }
    return SPODY_OK;
}

static int parse_force_model(toml_table_t *root, const char *toml_dir,
                             InputConfig *cfg, SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "force_model");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [force_model]");
        return SPODY_ERR_MISSING_KEY;
    }
    char cb_name[32] = {0};
    int rc;
    if ((rc = req_string(t, "force_model", "central_body",
                         cb_name, sizeof cb_name, err))) return rc;
    if ((rc = parse_central_body(cb_name, &cfg->central_body, err))) return rc;

    char rel_harmonics[SPODY_MAX_PATH] = {0};
    if ((rc = req_string(t, "force_model", "harmonics_file",
                         rel_harmonics, sizeof rel_harmonics, err))) return rc;
    resolve_path(toml_dir, rel_harmonics,
                 cfg->harmonics_file, sizeof cfg->harmonics_file);

    if ((rc = req_int(t, "force_model", "harmonics_degree",
                      &cfg->harmonics_degree, err))) return rc;
    if ((rc = req_string_array(t, "force_model", "third_bodies",
                               cfg->third_body_names,
                               SPODY_MAX_THIRD_BODIES,
                               &cfg->n_third_bodies, err))) return rc;
    if ((rc = req_bool(t, "force_model", "srp",
                       &cfg->enable_srp, err))) return rc;

    /* drag: optional, default false -- pre-drag TOMLs parse unchanged. */
    cfg->enable_drag = 0;
    {
        toml_datum_t d = toml_bool_in(t, "drag");
        if (d.ok) cfg->enable_drag = d.u.b ? 1 : 0;
    }

    /* Earth-only assets. Both are OPTIONAL at the schema level so
     * Moon-centred TOMLs (the majority today) parse unchanged; the
     * required-when-Earth check lives in spody_validate_input. The
     * relative -> absolute path resolution is done here so the
     * resolved strings are available to validation and to sim_setup
     * regardless of whether the body actually uses them. */
    {
        char rel[SPODY_MAX_PATH] = {0};
        int  present = 0;
        if ((rc = opt_string(t, "eop_file", rel, sizeof rel, &present))) return rc;
        if (present) {
            resolve_path(toml_dir, rel,
                         cfg->eop_file, sizeof cfg->eop_file);
        }
    }
    {
        char rel[SPODY_MAX_PATH] = {0};
        int  present = 0;
        if ((rc = opt_string(t, "iau2006_dir", rel, sizeof rel, &present))) return rc;
        if (present) {
            resolve_path(toml_dir, rel,
                         cfg->iau2006_dir, sizeof cfg->iau2006_dir);
        }
    }
    {
        char rel[SPODY_MAX_PATH] = {0};
        int  present = 0;
        if ((rc = opt_string(t, "space_weather_file", rel, sizeof rel,
                             &present))) return rc;
        if (present) {
            resolve_path(toml_dir, rel,
                         cfg->space_weather_file,
                         sizeof cfg->space_weather_file);
        }
    }

    /* density calibration: optional, drag-only, constant XOR file
     * (the XOR and the >0 range live in spody_validate_input). */
    cfg->density_scale = 1.0;
    cfg->has_density_scale = 0;
    {
        toml_datum_t d = toml_double_in(t, "density_scale");
        if (d.ok) {
            cfg->density_scale = d.u.d;
            cfg->has_density_scale = 1;
        }
    }
    {
        char rel[SPODY_MAX_PATH] = {0};
        int  present = 0;
        if ((rc = opt_string(t, "density_scale_file", rel, sizeof rel,
                             &present))) return rc;
        if (present) {
            resolve_path(toml_dir, rel,
                         cfg->density_scale_file,
                         sizeof cfg->density_scale_file);
        }
    }
    return SPODY_OK;
}

/* [cr3bp] -- two primaries by name. mu1/mu2 are resolved from the
 * shared body table (BODY_TABLE), L is looked up in the curated
 * CR3BP_PAIRS table. The two primaries are required and must be
 * distinct; only registered pairs (today: Earth-Moon) are accepted. */
static int parse_cr3bp(toml_table_t *root, InputConfig *cfg, SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "cr3bp");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [cr3bp]");
        return SPODY_ERR_MISSING_KEY;
    }
    int rc;
    if ((rc = req_string(t, "cr3bp", "primary_1",
                         cfg->cr3bp_primary_1, sizeof cfg->cr3bp_primary_1, err))) return rc;
    if ((rc = req_string(t, "cr3bp", "primary_2",
                         cfg->cr3bp_primary_2, sizeof cfg->cr3bp_primary_2, err))) return rc;

    if (strcmp(cfg->cr3bp_primary_1, cfg->cr3bp_primary_2) == 0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cr3bp.primary_1 and primary_2 must be different bodies (both = '%s')",
                cfg->cr3bp_primary_1);
        return SPODY_ERR_BAD_VALUE;
    }

    /* Resolve mu from the shared body table. */
    if (spody_lookup_third_body(cfg->cr3bp_primary_1, NULL,
                                &cfg->cr3bp_mu1, NULL) != 0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cr3bp.primary_1 = '%s' is not a known body",
                cfg->cr3bp_primary_1);
        return SPODY_ERR_BAD_VALUE;
    }
    if (spody_lookup_third_body(cfg->cr3bp_primary_2, NULL,
                                &cfg->cr3bp_mu2, NULL) != 0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cr3bp.primary_2 = '%s' is not a known body",
                cfg->cr3bp_primary_2);
        return SPODY_ERR_BAD_VALUE;
    }

    /* Look up L in the curated pair table. */
    if (lookup_cr3bp_pair(cfg->cr3bp_primary_1, cfg->cr3bp_primary_2,
                          &cfg->cr3bp_L_km) != 0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cr3bp primary pair ('%s', '%s') is not in the curated table "
                "(known today: 'Earth' + 'Moon'). Add a row to CR3BP_PAIRS "
                "in src/toml_input.c to register a new pair.",
                cfg->cr3bp_primary_1, cfg->cr3bp_primary_2);
        return SPODY_ERR_BAD_VALUE;
    }
    return SPODY_OK;
}

static int parse_ephemeris(toml_table_t *root, const char *toml_dir,
                           InputConfig *cfg, SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "ephemeris");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [ephemeris]");
        return SPODY_ERR_MISSING_KEY;
    }
    char rel_eph[SPODY_MAX_PATH] = {0};
    int rc = req_string(t, "ephemeris", "file",
                        rel_eph, sizeof rel_eph, err);
    if (rc) return rc;
    resolve_path(toml_dir, rel_eph,
                 cfg->ephemeris_file, sizeof cfg->ephemeris_file);
    return SPODY_OK;
}

static int parse_integrator(toml_table_t *root, InputConfig *cfg,
                            SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "integrator");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [integrator]");
        return SPODY_ERR_MISSING_KEY;
    }
    char type_name[32] = {0};
    int rc;
    if ((rc = req_string(t, "integrator", "type",
                         type_name, sizeof type_name, err))) return rc;
    if ((rc = parse_integrator_type(type_name, &cfg->integrator_type, err))) return rc;
    if ((rc = req_double(t, "integrator", "rel_tol",  &cfg->rel_tol,  err))) return rc;
    if ((rc = req_double(t, "integrator", "h_init_s", &cfg->h_init_s, err))) return rc;
    if ((rc = req_double(t, "integrator", "h_min_s",  &cfg->h_min_s,  err))) return rc;
    if ((rc = req_double(t, "integrator", "h_max_s",  &cfg->h_max_s,  err))) return rc;
    return SPODY_OK;
}

static int parse_output(toml_table_t *root, const char *toml_dir,
                        InputConfig *cfg, SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "output");
    if (!t) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY, "missing section [output]");
        return SPODY_ERR_MISSING_KEY;
    }
    char mode_name[16] = {0};
    int rc;
    if ((rc = req_string(t, "output", "mode",
                         mode_name, sizeof mode_name, err))) return rc;
    if ((rc = parse_output_mode(mode_name, &cfg->output_mode, err))) return rc;

    if (cfg->output_mode == SPODY_OUT_FIXED) {
        if ((rc = req_double(t, "output", "interval_s",
                             &cfg->output_interval_s, err))) return rc;
    } else {
        cfg->output_interval_s = 0.0;
    }

    char rel_csv[SPODY_MAX_PATH] = {0};
    char rel_bin[SPODY_MAX_PATH] = {0};
    char rel_log[SPODY_MAX_PATH] = {0};
    char rel_acc[SPODY_MAX_PATH] = {0};
    char rel_evt[SPODY_MAX_PATH] = {0};
    char rel_dir[SPODY_MAX_PATH] = {0};
    int has_csv = 0, has_bin = 0, has_log = 0, has_acc = 0, has_evt = 0, has_dir = 0;
    if ((rc = opt_string(t, "csv_file",           rel_csv, sizeof rel_csv, &has_csv))) return rc;
    if ((rc = opt_string(t, "bin_file",           rel_bin, sizeof rel_bin, &has_bin))) return rc;
    if ((rc = opt_string(t, "log_file",           rel_log, sizeof rel_log, &has_log))) return rc;
    if ((rc = opt_string(t, "accelerations_file", rel_acc, sizeof rel_acc, &has_acc))) return rc;
    if ((rc = opt_string(t, "events_log",         rel_evt, sizeof rel_evt, &has_evt))) return rc;
    /* output_dir: where the per-run timestamp folder is created. Was a
     * GUI-only memo before this slice; now it's the parent the C side
     * uses to compose `<output_dir>/<timestamp>/<file>` per run. */
    if ((rc = opt_string(t, "output_dir",         rel_dir, sizeof rel_dir, &has_dir))) return rc;

    cfg->csv_file[0]           = '\0';
    cfg->bin_file[0]           = '\0';
    cfg->log_file[0]           = '\0';
    cfg->accelerations_file[0] = '\0';
    cfg->events_log[0]         = '\0';
    cfg->output_dir[0]         = '\0';
    if (has_csv) resolve_path(toml_dir, rel_csv,
                              cfg->csv_file, sizeof cfg->csv_file);
    if (has_bin) resolve_path(toml_dir, rel_bin,
                              cfg->bin_file, sizeof cfg->bin_file);
    if (has_log) resolve_path(toml_dir, rel_log,
                              cfg->log_file, sizeof cfg->log_file);
    if (has_acc) resolve_path(toml_dir, rel_acc,
                              cfg->accelerations_file, sizeof cfg->accelerations_file);
    if (has_evt) resolve_path(toml_dir, rel_evt,
                              cfg->events_log, sizeof cfg->events_log);
    if (has_dir) resolve_path(toml_dir, rel_dir,
                              cfg->output_dir, sizeof cfg->output_dir);
    return SPODY_OK;
}

/* Map "log" / "stop" / "log_and_stop" to the spody_event_action enum
 * value (0/1/2). Anything else -> -1 + error. The string-to-int hop
 * keeps spody_events.h out of toml_input.h. */
static int parse_event_action(const char *s, int *out) {
    if (strcmp(s, "log") == 0)          { *out = 0; return 0; }
    if (strcmp(s, "stop") == 0)         { *out = 1; return 0; }
    if (strcmp(s, "log_and_stop") == 0) { *out = 2; return 0; }
    *out = -1;
    return -1;
}

/* Parse the [[events.altitude_crossing]] array-of-tables. Each entry
 * adds one AltitudeCrossingSpec to cfg->altitude_crossings. Body
 * name resolution + dynamics-model compatibility check happen later
 * in spody_validate_input (where the central body / third body
 * list / CR3BP primaries are all known). */
static int parse_altitude_crossings(toml_table_t *events_tab,
                                     InputConfig *cfg, SpodyError *err) {
    cfg->n_altitude_crossings = 0;
    toml_array_t *arr = toml_array_in(events_tab, "altitude_crossing");
    if (!arr) return SPODY_OK;

    int n = toml_array_nelem(arr);
    if (n == 0) return SPODY_OK;
    if (n > SPODY_MAX_ALTITUDE_CROSSINGS) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "[[events.altitude_crossing]] has %d entries, cap is %d",
                n, SPODY_MAX_ALTITUDE_CROSSINGS);
        return SPODY_ERR_BAD_VALUE;
    }

    for (int i = 0; i < n; ++i) {
        toml_table_t *t = toml_table_at(arr, i);
        if (!t) {
            spody_error_set(err, SPODY_ERR_BAD_TYPE,
                    "[[events.altitude_crossing]] entry %d is not a table", i);
            return SPODY_ERR_BAD_TYPE;
        }
        AltitudeCrossingSpec *ac = &cfg->altitude_crossings[i];

        int rc;
        if ((rc = req_string(t, "events.altitude_crossing", "body",
                              ac->body_name, sizeof ac->body_name, err))) return rc;
        if ((rc = req_double(t, "events.altitude_crossing", "altitude_km",
                              &ac->altitude_km, err))) return rc;
        if (!(ac->altitude_km > 0.0)) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "events.altitude_crossing[%d].altitude_km must be > 0 (got %g);"
                    " use SPODY_EVENT_KIND_IMPACT for surface impacts",
                    i, ac->altitude_km);
            return SPODY_ERR_BAD_VALUE;
        }

        /* action: optional, default "log" (the natural choice for
         * monitoring a set of altitude bands -- propagation keeps
         * going so all bands have a chance to fire). */
        char action_name[16];
        int has_action = 0;
        if ((rc = opt_string(t, "action", action_name, sizeof action_name,
                              &has_action))) return rc;
        ac->action = 0;  /* LOG */
        if (has_action) {
            if (parse_event_action(action_name, &ac->action) != 0) {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "events.altitude_crossing[%d].action = '%s' is not"
                        " supported (expected 'log', 'stop', or 'log_and_stop')",
                        i, action_name);
                return SPODY_ERR_BAD_VALUE;
            }
        }

        /* refined: optional bool, default true. The toggle is
         * essentially free in steady state -- Brent runs only at the
         * actual crossing step -- but it's exposed for users who
         * profile and want step-size-precision triggers instead. */
        ac->refined = 1;
        toml_datum_t d = toml_bool_in(t, "refined");
        if (d.ok) ac->refined = d.u.b ? 1 : 0;
    }

    cfg->n_altitude_crossings = n;
    return SPODY_OK;
}

/* Optional [events] section. IMPACT is always on and needs no config;
 * eclipse is opt-in (`events.eclipse_threshold` fraction in [0,1]);
 * altitude crossings are opt-in array-of-tables. Absent section ->
 * only the always-on IMPACT runs. */
static int parse_events(toml_table_t *root, InputConfig *cfg, SpodyError *err) {
    cfg->eclipse_event_enabled = 0;
    cfg->eclipse_threshold     = 0.0;
    cfg->n_altitude_crossings  = 0;

    toml_table_t *t = toml_table_in(root, "events");
    if (!t) return SPODY_OK;   /* no [events] -> only the always-on IMPACT */

    int rc;

    /* eclipse_threshold is optional now that altitude crossings share
     * the section: if it's absent we just leave eclipse disabled. */
    toml_datum_t et = toml_double_in(t, "eclipse_threshold");
    toml_datum_t eti = toml_int_in(t, "eclipse_threshold");
    if (et.ok || eti.ok) {
        cfg->eclipse_threshold = et.ok ? et.u.d : (double)eti.u.i;
        if (cfg->eclipse_threshold < 0.0 || cfg->eclipse_threshold > 1.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "events.eclipse_threshold must be in [0, 1] (got %g)",
                    cfg->eclipse_threshold);
            return SPODY_ERR_BAD_VALUE;
        }
        cfg->eclipse_event_enabled = 1;
    }

    if ((rc = parse_altitude_crossings(t, cfg, err))) return rc;
    return SPODY_OK;
}

/* --------------------------------------------------------------------------
 * [batch] section -- CSV cases-file loader and TOML parser
 *
 * Self-contained CSV reader because tomlc99 only gives us TOML; we don't
 * want to pull in another dependency for a header + numeric matrix file.
 * Format: optional comment lines starting with '#', a header row of
 * comma-separated names, then numeric data rows. An optional column named
 * "id" carries explicit per-case identifiers (otherwise the loader fills
 * case_ids with zero-padded 1-based row indices).
 * -------------------------------------------------------------------------- */

#define BATCH_MAX_LINE     4096
#define BATCH_MAX_COLUMNS  64

/* Portable strdup: POSIX `strdup` is not in ISO C, so re-implement it
 * with malloc + memcpy. Returns NULL on allocation failure. */
static char *xstrdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *r = (char *)malloc(n);
    if (r) memcpy(r, s, n);
    return r;
}

static int read_csv_line(FILE *fp, char *buf, size_t bufsz, int *line_no) {
    if (!fgets(buf, (int)bufsz, fp)) return 0;
    (*line_no)++;
    size_t n = strlen(buf);
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) buf[--n] = '\0';
    return 1;
}

static int csv_is_blank_or_comment(const char *s) {
    while (*s == ' ' || *s == '\t') s++;
    return (*s == '\0' || *s == '#');
}

static char *csv_trim_inplace(char *s) {
    while (*s == ' ' || *s == '\t') s++;
    char *end = s + strlen(s);
    while (end > s && (end[-1] == ' ' || end[-1] == '\t')) end--;
    *end = '\0';
    return s;
}

/* Split a comma-separated line in place. Returns number of tokens written
 * into tokens[]; each token points into the (now NUL-terminated) buffer. */
static int csv_split_line(char *line, char **tokens, int max_tokens) {
    int n = 0;
    char *p = line;
    while (n < max_tokens) {
        char *comma = strchr(p, ',');
        if (comma) *comma = '\0';
        tokens[n++] = csv_trim_inplace(p);
        if (!comma) break;
        p = comma + 1;
    }
    return n;
}

/* Load a CSV cases file. On success populates batch->{n_cases, n_columns,
 * column_names, case_ids, values}. On failure returns an error code with
 * *err filled in; the four pointer fields are left unmodified (NULL). */
static int load_cases_csv(const char *path, BatchConfig *batch,
                          SpodyError *err) {
    FILE   *fp           = NULL;
    char  **column_names = NULL;
    char  **case_ids     = NULL;
    double *values       = NULL;
    int     n_columns    = 0;
    int     n_cases      = 0;
    int     rc           = SPODY_OK;

    fp = fopen(path, "r");
    if (!fp) {
        spody_error_set(err, SPODY_ERR_IO,
                "cannot open cases_file '%s'", path);
        rc = SPODY_ERR_IO; goto cleanup;
    }

    char line[BATCH_MAX_LINE];
    int  line_no = 0;

    /* Header = first non-blank, non-comment line. */
    int got_header = 0;
    while (read_csv_line(fp, line, sizeof line, &line_no)) {
        if (!csv_is_blank_or_comment(line)) { got_header = 1; break; }
    }
    if (!got_header) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cases_file '%s': empty or only comments", path);
        rc = SPODY_ERR_BAD_VALUE; goto cleanup;
    }

    char *htokens[BATCH_MAX_COLUMNS];
    int n_total = csv_split_line(line, htokens, BATCH_MAX_COLUMNS);
    if (n_total < 1) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cases_file '%s': header has no columns", path);
        rc = SPODY_ERR_BAD_VALUE; goto cleanup;
    }

    int id_idx = -1;
    for (int j = 0; j < n_total; ++j) {
        if (strcmp(htokens[j], "id") == 0) { id_idx = j; break; }
    }
    n_columns = n_total - (id_idx >= 0 ? 1 : 0);
    if (n_columns < 1) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cases_file '%s': must have at least one numeric column "
                "besides 'id'", path);
        rc = SPODY_ERR_BAD_VALUE; goto cleanup;
    }

    column_names = (char **)calloc((size_t)n_columns, sizeof(char *));
    if (!column_names) goto oom;
    {
        int out = 0;
        for (int j = 0; j < n_total; ++j) {
            if (j == id_idx) continue;
            column_names[out] = xstrdup(htokens[j]);
            if (!column_names[out]) goto oom;
            out++;
        }
    }

    int cap_cases = 16; //starting capacity, grows as needed
    case_ids = (char **)calloc((size_t)cap_cases, sizeof(char *));
    values   = (double *)malloc((size_t)cap_cases * (size_t)n_columns * sizeof(double));
    if (!case_ids || !values) goto oom;

    while (read_csv_line(fp, line, sizeof line, &line_no)) {
        if (csv_is_blank_or_comment(line)) continue;

        char *rtokens[BATCH_MAX_COLUMNS];
        int n_row = csv_split_line(line, rtokens, BATCH_MAX_COLUMNS);
        if (n_row != n_total) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "cases_file '%s' line %d: expected %d columns, got %d",
                    path, line_no, n_total, n_row);
            rc = SPODY_ERR_BAD_VALUE; goto cleanup;
        }

        if (n_cases == cap_cases) {
            int new_cap = cap_cases * 2;
            char **new_ids = (char **)realloc(case_ids,
                    (size_t)new_cap * sizeof(char *));
            if (!new_ids) goto oom;
            case_ids = new_ids;
            double *new_vals = (double *)realloc(values,
                    (size_t)new_cap * (size_t)n_columns * sizeof(double));
            if (!new_vals) goto oom;
            values = new_vals;
            for (int i = cap_cases; i < new_cap; ++i) case_ids[i] = NULL;
            cap_cases = new_cap;
        }

        /* Parse numeric columns first; this can fail without leaking
         * a partially-allocated id string for the current row. */
        int out = 0;
        for (int j = 0; j < n_total; ++j) {
            if (j == id_idx) continue;
            char *end = NULL;
            double v = strtod(rtokens[j], &end);
            if (end == rtokens[j] || *end != '\0') {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "cases_file '%s' line %d: column '%s' value '%s' "
                        "is not numeric",
                        path, line_no, column_names[out], rtokens[j]);
                rc = SPODY_ERR_BAD_VALUE; goto cleanup;
            }
            values[n_cases * n_columns + out] = v;
            out++;
        }

        if (id_idx >= 0) {
            case_ids[n_cases] = xstrdup(rtokens[id_idx]);
            if (!case_ids[n_cases]) goto oom;
        }
        n_cases++;
    }

    if (n_cases == 0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cases_file '%s': no data rows after header", path);
        rc = SPODY_ERR_BAD_VALUE; goto cleanup;
    }

    /* If the file had no 'id' column, fabricate zero-padded row indices. */
    if (id_idx < 0) {
        int width = 1;
        for (int n = n_cases; n >= 10; n /= 10) width++;
        char fmt[16];
        snprintf(fmt, sizeof fmt, "%%0%dd", width);
        for (int i = 0; i < n_cases; ++i) {
            char buf[32];
            snprintf(buf, sizeof buf, fmt, i + 1);
            case_ids[i] = xstrdup(buf);
            if (!case_ids[i]) goto oom;
        }
    }

    /* Transfer ownership; cleanup below sees NULLs and does nothing. */
    batch->n_cases      = n_cases;
    batch->n_columns    = n_columns;
    batch->column_names = column_names;
    batch->case_ids     = case_ids;
    batch->values       = values;
    column_names = NULL;
    case_ids     = NULL;
    values       = NULL;
    rc = SPODY_OK;
    goto cleanup;

oom:
    spody_error_set(err, SPODY_ERR_INTERNAL,
            "out of memory while loading cases_file '%s'", path);
    rc = SPODY_ERR_INTERNAL;
    /* fall through */

cleanup:
    if (fp) fclose(fp);
    if (column_names) {
        for (int j = 0; j < n_columns; ++j) free(column_names[j]);
        free(column_names);
    }
    if (case_ids) {
        for (int i = 0; i < n_cases; ++i) free(case_ids[i]);
        free(case_ids);
    }
    free(values);
    return rc;
}

/* --------------------------------------------------------------------------
 * [batch.columns] override-target table
 *
 * Static catalogue of InputConfig fields that may be overridden per case.
 * Anything not in this table is rejected at parse time (most string fields
 * fall outside it; shared resources like harmonics_degree and the central
 * body are deliberately excluded because changing them per case would
 * invalidate the SimulationShared).
 * -------------------------------------------------------------------------- */
static const SpodyFieldDesc FIELD_TABLE[] = {
    /* simulation */
    { "simulation.et_start_s",           SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, et_start_s),         0, SPODY_VAL_ANY      },
    { "simulation.duration_s",           SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, duration_s),         0, SPODY_VAL_POSITIVE },

    /* spacecraft */
    { "spacecraft.mass_kg",              SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, mass_kg),            0, SPODY_VAL_POSITIVE },
    { "spacecraft.srp.area_m2",          SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, srp_area_m2),        0, SPODY_VAL_POSITIVE },
    { "spacecraft.srp.Cr",               SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, srp_cr),             0, SPODY_VAL_NON_NEG  },
    { "spacecraft.drag.area_m2",         SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, drag_area_m2),       0, SPODY_VAL_POSITIVE },
    { "spacecraft.drag.Cd",              SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, drag_cd),            0, SPODY_VAL_POSITIVE },

    /* debris -- am_srp/Cr alias srp_area_m2/srp_cr (debris mode forces
     * mass=1 so srp_area_m2 numerically equals A/m). Mutually exclusive
     * with the spacecraft.* paths; cross-validated against debris_mode. */
    { "debris.am_srp",                   SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, srp_area_m2),        0, SPODY_VAL_POSITIVE },
    { "debris.Cr",                       SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, srp_cr),             0, SPODY_VAL_NON_NEG  },
    { "debris.am_drag",                  SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, drag_area_m2),       0, SPODY_VAL_POSITIVE },
    { "debris.Cd",                       SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, drag_cd),            0, SPODY_VAL_POSITIVE },

    /* initial_state (vec3 elements) -- |r|, |v| cross-field, not per-cell */
    { "initial_state.position_km[0]",    SPODY_FIELD_VEC3_AT,
      offsetof(InputConfig, position_km),        0, SPODY_VAL_ANY      },
    { "initial_state.position_km[1]",    SPODY_FIELD_VEC3_AT,
      offsetof(InputConfig, position_km),        1, SPODY_VAL_ANY      },
    { "initial_state.position_km[2]",    SPODY_FIELD_VEC3_AT,
      offsetof(InputConfig, position_km),        2, SPODY_VAL_ANY      },
    { "initial_state.velocity_kms[0]",   SPODY_FIELD_VEC3_AT,
      offsetof(InputConfig, velocity_kms),       0, SPODY_VAL_ANY      },
    { "initial_state.velocity_kms[1]",   SPODY_FIELD_VEC3_AT,
      offsetof(InputConfig, velocity_kms),       1, SPODY_VAL_ANY      },
    { "initial_state.velocity_kms[2]",   SPODY_FIELD_VEC3_AT,
      offsetof(InputConfig, velocity_kms),       2, SPODY_VAL_ANY      },

    /* force_model -- toggles only; harmonics_* and central_body are shared. */
    { "force_model.srp",                 SPODY_FIELD_INT,
      offsetof(InputConfig, enable_srp),         0, SPODY_VAL_BOOL     },
    { "force_model.drag",                SPODY_FIELD_INT,
      offsetof(InputConfig, enable_drag),        0, SPODY_VAL_BOOL     },
    { "force_model.density_scale",       SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, density_scale),      0, SPODY_VAL_POSITIVE },

    /* integrator (tolerances and step bounds may vary per case) */
    { "integrator.rel_tol",              SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, rel_tol),            0, SPODY_VAL_POSITIVE },
    { "integrator.h_init_s",             SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, h_init_s),           0, SPODY_VAL_POSITIVE },
    { "integrator.h_min_s",              SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, h_min_s),            0, SPODY_VAL_POSITIVE },
    { "integrator.h_max_s",              SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, h_max_s),            0, SPODY_VAL_POSITIVE },

    /* output (sampling cadence may vary per case; paths are fixed by batch) */
    { "output.interval_s",               SPODY_FIELD_DOUBLE,
      offsetof(InputConfig, output_interval_s),  0, SPODY_VAL_POSITIVE }
};
static const int N_FIELD_TABLE =
    (int)(sizeof FIELD_TABLE / sizeof FIELD_TABLE[0]);

static const SpodyFieldDesc *resolve_field(const char *path) {
    if (!path) return NULL;
    for (int i = 0; i < N_FIELD_TABLE; ++i) {
        if (strcmp(FIELD_TABLE[i].path, path) == 0) return &FIELD_TABLE[i];
    }
    return NULL;
}

/* Release every heap-owned resource inside a BatchConfig (column_names,
 * case_ids, values, column_targets). Leaves the struct in a state that
 * is safe to free or reuse. Used by both the parse_batch error path and
 * spody_free_input. */
static void free_batch_contents(BatchConfig *b) {
    if (!b) return;
    if (b->column_names) {
        for (int j = 0; j < b->n_columns; ++j) free(b->column_names[j]);
        free(b->column_names);
        b->column_names = NULL;
    }
    if (b->case_ids) {
        for (int i = 0; i < b->n_cases; ++i) free(b->case_ids[i]);
        free(b->case_ids);
        b->case_ids = NULL;
    }
    free(b->values);             b->values          = NULL;
    free(b->column_targets);     b->column_targets  = NULL;
    free(b->column_is_delta);    b->column_is_delta = NULL;
    b->n_cases   = 0;
    b->n_columns = 0;
}

/* Parse the optional [batch] section. If absent, cfg->batch stays NULL
 * (already zeroed by spody_load_input). If present, allocates a
 * BatchConfig, fills metadata, and loads the cases_file. */
static int parse_batch(toml_table_t *root, const char *toml_dir,
                       InputConfig *cfg, SpodyError *err) {
    toml_table_t *t = toml_table_in(root, "batch");
    if (!t) return SPODY_OK;   /* not a batch input, leave cfg->batch NULL */

    BatchConfig *batch = (BatchConfig *)calloc(1, sizeof *batch);
    if (!batch) {
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "out of memory allocating BatchConfig");
        return SPODY_ERR_INTERNAL;
    }

    int rc;
    if ((rc = req_string(t, "batch", "name",
                         batch->name, sizeof batch->name, err))) goto fail;

    char rel_outdir[SPODY_MAX_PATH] = {0};
    if ((rc = req_string(t, "batch", "output_dir",
                         rel_outdir, sizeof rel_outdir, err))) goto fail;
    resolve_path(toml_dir, rel_outdir,
                 batch->output_dir, sizeof batch->output_dir);

    if ((rc = req_int(t, "batch", "thread_number",
                      &batch->thread_number, err))) goto fail;

    char rel_cases[SPODY_MAX_PATH] = {0};
    if ((rc = req_string(t, "batch", "cases_file",
                         rel_cases, sizeof rel_cases, err))) goto fail;
    resolve_path(toml_dir, rel_cases,
                 batch->cases_file, sizeof batch->cases_file);

    /* Auto-detect file format from extension. */
    const char *dot = strrchr(batch->cases_file, '.');
    if (dot && strcmp(dot, ".csv") == 0) {
        rc = load_cases_csv(batch->cases_file, batch, err);
        if (rc != SPODY_OK) goto fail;
    } else if (dot && strcmp(dot, ".spody") == 0) {
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "cases_file '%s': .spody binary format not yet implemented "
                "(use .csv for now)", batch->cases_file);
        rc = SPODY_ERR_INTERNAL; goto fail;
    } else {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cases_file '%s': unrecognised extension "
                "(expected .csv or .spody)", batch->cases_file);
        rc = SPODY_ERR_BAD_VALUE; goto fail;
    }

    /* Resolve [batch.columns]: every CSV column (besides 'id') must have
     * a string entry mapping it to a dotted path of an overridable field. */
    toml_table_t *cols = toml_table_in(t, "columns");
    if (!cols) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY,
                "[batch] is missing the required [batch.columns] sub-table");
        rc = SPODY_ERR_MISSING_KEY; goto fail;
    }

    batch->column_targets = (const SpodyFieldDesc **)calloc(
            (size_t)batch->n_columns, sizeof(SpodyFieldDesc *));
    batch->column_is_delta = (int *)calloc(
            (size_t)batch->n_columns, sizeof(int));
    if (!batch->column_targets || !batch->column_is_delta) {
        spody_error_set(err, SPODY_ERR_INTERNAL,
                "out of memory allocating batch column metadata");
        rc = SPODY_ERR_INTERNAL; goto fail;
    }

    for (int j = 0; j < batch->n_columns; ++j) {
        const char *col = batch->column_names[j];

        /* A column entry is either a plain string (override target) or
         * an inline table { target = "...", mode = "override"|"delta" }.
         * Empty-string target ("" or table with target="") marks the
         * column as metadata: the value gets parsed (for type-checking)
         * but never applied to any field. Use this for CSV columns the
         * user wants to keep in the cases file (e.g. fragment L_char,
         * batch ids) without spody knowing how to interpret them.
         * Missing entry remains an error -- that is the typo guard. */
        char target_path[SPODY_MAX_PATH] = {0};
        int  is_delta = 0;

        toml_datum_t d = toml_string_in(cols, col);
        if (d.ok) {
            snprintf(target_path, sizeof target_path, "%s", d.u.s);
            free(d.u.s);
        } else {
            toml_table_t *ct = toml_table_in(cols, col);
            if (!ct) {
                spody_error_set(err, SPODY_ERR_MISSING_KEY,
                        "cases_file column '%s' has no entry in [batch.columns]",
                        col);
                rc = SPODY_ERR_MISSING_KEY; goto fail;
            }
            toml_datum_t td = toml_string_in(ct, "target");
            if (!td.ok) {
                spody_error_set(err, SPODY_ERR_MISSING_KEY,
                        "[batch.columns].%s is a table but has no 'target' key",
                        col);
                rc = SPODY_ERR_MISSING_KEY; goto fail;
            }
            snprintf(target_path, sizeof target_path, "%s", td.u.s);
            free(td.u.s);

            toml_datum_t md = toml_string_in(ct, "mode");
            if (md.ok) {
                if      (strcmp(md.u.s, "delta")    == 0) is_delta = 1;
                else if (strcmp(md.u.s, "override") == 0) is_delta = 0;
                else {
                    spody_error_set(err, SPODY_ERR_BAD_VALUE,
                            "[batch.columns].%s.mode = '%s' is invalid "
                            "(expected 'override' or 'delta')", col, md.u.s);
                    free(md.u.s);
                    rc = SPODY_ERR_BAD_VALUE; goto fail;
                }
                free(md.u.s);
            }
            /* mode absent -> override (default) */
        }

        /* Empty target == metadata column. Mark the slot NULL so the
         * override-apply / validation loops skip it (they already check
         * for `!fd`). The values column is still read out of the CSV
         * row-by-row so the data is preserved on disk; spody just does
         * not apply it anywhere. */
        if (target_path[0] == '\0') {
            batch->column_targets[j]  = NULL;
            batch->column_is_delta[j] = 0;
            continue;
        }

        const SpodyFieldDesc *fd = resolve_field(target_path);
        if (!fd) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[batch.columns].%s target '%s' is not a recognised "
                    "per-case override target", col, target_path);
            rc = SPODY_ERR_BAD_VALUE; goto fail;
        }
        /* Cross-validate target against the object mode: spacecraft.* paths
         * make no sense in debris mode (massa irrelevant; would corrupt A/m
         * via spody_init_Spacecraft), and debris.* paths likewise belong
         * only to debris mode. Catches typos and copy-pasted batch tables. */
        int target_is_debris = strncmp(fd->path, "debris.", 7) == 0;
        int target_is_spc    = strncmp(fd->path, "spacecraft.", 11) == 0;
        if (cfg->debris_mode && target_is_spc) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[batch.columns].%s targets '%s' but the TOML uses [debris] "
                    "(use debris.am_srp / debris.Cr instead)", col, fd->path);
            rc = SPODY_ERR_BAD_VALUE; goto fail;
        }
        if (!cfg->debris_mode && target_is_debris) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[batch.columns].%s targets '%s' but the TOML uses "
                    "[spacecraft] (use spacecraft.srp.area_m2 / spacecraft.srp.Cr "
                    "instead)", col, fd->path);
            rc = SPODY_ERR_BAD_VALUE; goto fail;
        }
        batch->column_targets[j]  = fd;
        batch->column_is_delta[j] = is_delta;
    }

    /* Reject mappings in [batch.columns] that have no corresponding CSV
     * column -- almost always a typo the user wants to know about. */
    for (int k = 0; ; ++k) {
        const char *key = toml_key_in(cols, k);
        if (!key) break;
        int found = 0;
        for (int j = 0; j < batch->n_columns; ++j) {
            if (strcmp(key, batch->column_names[j]) == 0) { found = 1; break; }
        }
        if (!found) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[batch.columns].%s has no corresponding column "
                    "in cases_file '%s'", key, batch->cases_file);
            rc = SPODY_ERR_BAD_VALUE; goto fail;
        }
    }

    cfg->batch = batch;
    return SPODY_OK;

fail:
    free_batch_contents(batch);
    free(batch);
    return rc;
}

/* --------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------- */

int spody_load_input(const char *toml_path, InputConfig *cfg, SpodyError *err) {
    spody_error_clear(err);
    memset(cfg, 0, sizeof *cfg);
    if (err) snprintf(err->file, sizeof err->file, "%s", toml_path);

    FILE *fp = fopen(toml_path, "r");
    if (!fp) {
        spody_error_set(err, SPODY_ERR_IO,
                "cannot open TOML file '%s'", toml_path);
        return SPODY_ERR_IO;
    }
    char tomlerr[256] = {0};
    toml_table_t *root = toml_parse_file(fp, tomlerr, sizeof tomlerr);
    fclose(fp);
    if (!root) {
        spody_error_set(err, SPODY_ERR_TOML_PARSE,
                "TOML parse error: %s", tomlerr);
        return SPODY_ERR_TOML_PARSE;
    }

    char toml_dir[SPODY_MAX_PATH];
    parent_dir(toml_path, toml_dir, sizeof toml_dir);

    int rc;
    if ((rc = parse_simulation   (root,            cfg, err))) goto out;

    /* Reject any registered-but-not-implemented dynamics model BEFORE we
     * touch any model-specific section. New models register here once
     * their parse path is wired below. */
    {
        const SpodyDynamicsModelSpec *spec =
                spody_dynamics_model_get(cfg->dynamics_model);
        if (!spec || !spec->implemented) {
            char known[128];
            spody_dynamics_model_known_names(known, sizeof known);
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "simulation.dynamics_model = '%s' is registered but not "
                    "yet implemented in this release (known: %s)",
                    spec ? spec->name : "?", known);
            rc = SPODY_ERR_BAD_VALUE; goto out;
        }
    }

    /* [initial_state] is shared across all models; the frame string is
     * parsed here but its compatibility with the model is enforced by
     * spody_validate_input. */
    if ((rc = parse_initial_state(root,            cfg, err))) goto out;

    /* Model-specific sections. */
    if (cfg->dynamics_model == SPODY_DYN_HIGH_FIDELITY) {
        /* [spacecraft] XOR [debris]: exactly one selects the object
         * parameterisation. Spacecraft = named vehicle (mass + area),
         * debris = A/m-driven fragment (mass irrelevant). */
        toml_table_t *sc_t = toml_table_in(root, "spacecraft");
        toml_table_t *db_t = toml_table_in(root, "debris");
        if (!sc_t == !db_t) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "TOML must contain exactly one of [spacecraft] or [debris] "
                    "(got %s)", sc_t ? "both" : "neither");
            rc = SPODY_ERR_BAD_VALUE; goto out;
        }
        if (db_t) {
            if ((rc = parse_debris       (root,            cfg, err))) goto out;
        } else {
            if ((rc = parse_spacecraft   (root,            cfg, err))) goto out;
        }
        if ((rc = parse_force_model  (root, toml_dir,  cfg, err))) goto out;
        if ((rc = parse_ephemeris    (root, toml_dir,  cfg, err))) goto out;
    } else if (cfg->dynamics_model == SPODY_DYN_CR3BP) {
        if ((rc = parse_cr3bp        (root,            cfg, err))) goto out;
    }

    /* Shared trailing sections. */
    if ((rc = parse_integrator   (root,            cfg, err))) goto out;
    if ((rc = parse_output       (root, toml_dir,  cfg, err))) goto out;
    if ((rc = parse_events       (root,            cfg, err))) goto out;
    if ((rc = parse_batch        (root, toml_dir,  cfg, err))) goto out;

    /* All sections are now parsed; if the user picked Keplerian IC,
     * resolve the reference mu (HF central body / CR3BP primary 1|2)
     * and overwrite position_km / velocity_kms with the Cartesian
     * equivalent. Done HERE rather than in spody_validate_input so the
     * validator stays read-only and downstream consumers always see a
     * Cartesian state. */
    if (cfg->init_kind == SPODY_INIT_KEPLERIAN) {
        if ((rc = finalize_keplerian_initial_state(cfg, err))) goto out;
    }

out:
    toml_free(root);
    return rc;
}

void spody_free_input(InputConfig *cfg) {
    if (!cfg || !cfg->batch) return;
    free_batch_contents(cfg->batch);
    free(cfg->batch);
    cfg->batch = NULL;
}

void spody_apply_batch_case(const InputConfig *base, const BatchConfig *batch,
                            int case_idx, InputConfig *out) {
    /* InputConfig has no heap-owned fields besides `batch`, so a flat copy
     * is sufficient. We deliberately drop the batch pointer in `out` so
     * the per-case config looks like a plain single-scenario input. */
    *out = *base;
    out->batch = NULL;

    if (!batch || case_idx < 0 || case_idx >= batch->n_cases) return;

    for (int j = 0; j < batch->n_columns; ++j) {
        const SpodyFieldDesc *fd = batch->column_targets[j];
        if (!fd) continue;
        double v = batch->values[case_idx * batch->n_columns + j];
        int is_delta = batch->column_is_delta ? batch->column_is_delta[j] : 0;
        char *base_ptr = (char *)out + fd->offset;
        switch (fd->kind) {
            case SPODY_FIELD_DOUBLE:
                *(double *)base_ptr = is_delta ? *(double *)base_ptr + v : v;
                break;
            case SPODY_FIELD_INT:
                *(int *)base_ptr = is_delta ? *(int *)base_ptr + (int)v : (int)v;
                break;
            case SPODY_FIELD_VEC3_AT: {
                double *slot = &((double *)base_ptr)[fd->vec_idx];
                *slot = is_delta ? *slot + v : v;
                break;
            }
        }
    }
}

int spody_validate_input(const InputConfig *cfg, SpodyError *err) {
    spody_error_clear(err);

    /* Dispatch on the dynamics model. */
    {
        const SpodyDynamicsModelSpec *spec =
                spody_dynamics_model_get(cfg->dynamics_model);
        if (!spec || !spec->implemented) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "simulation.dynamics_model = '%s' is not yet implemented",
                    spec ? spec->name : "?");
            return SPODY_ERR_BAD_VALUE;
        }
    }

    /* CR3BP branch: minimal validation. The CR3BP system has no
     * spacecraft (no mass / SRP / drag), no third bodies, no
     * harmonics, no ephemeris -- the entire HF validator below is
     * skipped. Per-cell batch validation re-uses FIELD_TABLE, which
     * only contains shared knobs (integrator, output, IC) plus HF
     * fields that are unreachable here. */
    if (cfg->dynamics_model == SPODY_DYN_CR3BP) {
        if (cfg->duration_s <= 0.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "simulation.duration_s must be positive (got %.6g)",
                    cfg->duration_s);
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->initial_frame != SPODY_FRAME_SYNODIC_ROTATING) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "initial_state.frame must be 'synodic_rotating' "
                    "when dynamics_model = 'cr3bp'");
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->cr3bp_mu1 <= 0.0 || cfg->cr3bp_mu2 <= 0.0 ||
            cfg->cr3bp_L_km <= 0.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "cr3bp primaries unresolved (mu1=%.6g, mu2=%.6g, L=%.6g)",
                    cfg->cr3bp_mu1, cfg->cr3bp_mu2, cfg->cr3bp_L_km);
            return SPODY_ERR_BAD_VALUE;
        }
        double rmag = sqrt(cfg->position_km[0]*cfg->position_km[0] +
                           cfg->position_km[1]*cfg->position_km[1] +
                           cfg->position_km[2]*cfg->position_km[2]);
        if (rmag < 1.0e-3) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "initial_state.position_km is essentially at the "
                    "barycenter (|r| = %.3e km)", rmag);
            return SPODY_ERR_BAD_VALUE;
        }
        /* Integrator + output sanity (identical rules HF-side). */
        if (cfg->rel_tol <= 0.0 || cfg->h_min_s <= 0.0 ||
            cfg->h_max_s <= cfg->h_min_s ||
            cfg->h_init_s < cfg->h_min_s || cfg->h_init_s > cfg->h_max_s) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "integrator settings out of bounds "
                    "(rel_tol=%.3g, h_min=%.3g, h_init=%.3g, h_max=%.3g)",
                    cfg->rel_tol, cfg->h_min_s, cfg->h_init_s, cfg->h_max_s);
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->output_mode == SPODY_OUT_FIXED &&
            cfg->output_interval_s <= 0.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "output.interval_s must be positive when mode = 'fixed' "
                    "(got %.6g)", cfg->output_interval_s);
            return SPODY_ERR_BAD_VALUE;
        }
        /* Per-force breakdown is HF-only: it reads ctx->hg / ctx->eph
         * which CR3BP never populates. Impacts against the primaries
         * use the explicit-ref-point path and are wired by build_events
         * in sim_run; eclipse needs a Sun position and is not modelled. */
        if (cfg->accelerations_file[0] != '\0') {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "output.accelerations_file is not supported when "
                    "dynamics_model = 'cr3bp' (no per-force breakdown applies)");
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->eclipse_event_enabled) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[events].eclipse_threshold is not supported when "
                    "dynamics_model = 'cr3bp' (no Sun in this model)");
            return SPODY_ERR_BAD_VALUE;
        }
        /* CR3BP altitude crossings: body must be one of the two
         * primaries (the only bodies whose synodic position is
         * known to the engine via the fixed cr3bp_x1 / cr3bp_x2
         * caches). Anything else has no resolvable position. */
        for (int i = 0; i < cfg->n_altitude_crossings; ++i) {
            const AltitudeCrossingSpec *ac = &cfg->altitude_crossings[i];
            int matches_p1 = (strcmp(ac->body_name, cfg->cr3bp_primary_1) == 0);
            int matches_p2 = (strcmp(ac->body_name, cfg->cr3bp_primary_2) == 0);
            if (!matches_p1 && !matches_p2) {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "events.altitude_crossing[%d].body = '%s' must be"
                        " one of the CR3BP primaries ('%s' or '%s')",
                        i, ac->body_name,
                        cfg->cr3bp_primary_1, cfg->cr3bp_primary_2);
                return SPODY_ERR_BAD_VALUE;
            }
        }
        return SPODY_OK;
    }

    /* From here down: high_fidelity validator. */

    /* Initial state frame: HF accepts central_inertial (no
     * transformation) OR central_body_fixed (sim_setup rotates the
     * parsed values via the central body's bf_rotation provider at
     * et_start_s, so the downstream integrator still sees a plain
     * central_inertial state). */
    if (cfg->initial_frame != SPODY_FRAME_CENTRAL_INERTIAL
            && cfg->initial_frame != SPODY_FRAME_CENTRAL_BODY_FIXED) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "initial_state.frame must be 'central_inertial' or "
                "'central_body_fixed' when dynamics_model = "
                "'high_fidelity'");
        return SPODY_ERR_BAD_VALUE;
    }

    /* et_start_s anchors every time-dependent ephemeris / EOP / IAU
     * query: a HF run without it would silently propagate against
     * the J2000 epoch, almost never what the user meant. */
    if (!cfg->has_et_start_s) {
        spody_error_set(err, SPODY_ERR_MISSING_KEY,
                "simulation.et_start_s is required when dynamics_model = "
                "'high_fidelity' (anchors ephemeris / EOP lookups)");
        return SPODY_ERR_MISSING_KEY;
    }

    /* Time / duration */
    if (cfg->duration_s <= 0.0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "simulation.duration_s must be positive (got %.6g)",
                cfg->duration_s);
        return SPODY_ERR_BAD_VALUE;
    }

    /* Object parameterisation. In debris mode mass is forced to 1.0 by the
     * parser so the mass check is skipped; the spacecraft.srp-missing check
     * only fires for spacecraft mode (debris always has has_srp_block=1).
     * Value ranges apply to both modes (srp_area_m2 == am_srp numerically
     * in debris mode); messages are mode-aware. */
    if (!cfg->debris_mode) {
        if (cfg->mass_kg <= 0.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "spacecraft.mass_kg must be positive (got %.6g)",
                    cfg->mass_kg);
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->enable_srp && !cfg->has_srp_block) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "force_model.srp = true but [spacecraft.srp] is missing");
            return SPODY_ERR_BAD_VALUE;
        }
    }
    if (cfg->has_srp_block) {
        if (cfg->srp_area_m2 <= 0.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "%s must be positive (got %.6g)",
                    cfg->debris_mode ? "debris.am_srp"
                                     : "spacecraft.srp.area_m2",
                    cfg->srp_area_m2);
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->srp_cr < 0.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "%s must be non-negative (got %.6g)",
                    cfg->debris_mode ? "debris.Cr" : "spacecraft.srp.Cr",
                    cfg->srp_cr);
            return SPODY_ERR_BAD_VALUE;
        }
    }
    /* Drag parameterisation: needed in both object modes when the
     * force is on (debris drag keys are optional, so the check cannot
     * be folded into the spacecraft-only block above). */
    if (cfg->enable_drag && !cfg->has_drag_block) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                cfg->debris_mode
                    ? "force_model.drag = true but [debris] lacks the "
                      "'am_drag' / 'Cd' pair"
                    : "force_model.drag = true but [spacecraft.drag] "
                      "is missing");
        return SPODY_ERR_BAD_VALUE;
    }
    if (cfg->has_drag_block) {
        if (cfg->drag_area_m2 <= 0.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "%s must be positive (got %.6g)",
                    cfg->debris_mode ? "debris.am_drag"
                                     : "spacecraft.drag.area_m2",
                    cfg->drag_area_m2);
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->drag_cd <= 0.0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "%s must be positive (got %.6g)",
                    cfg->debris_mode ? "debris.Cd" : "spacecraft.drag.Cd",
                    cfg->drag_cd);
            return SPODY_ERR_BAD_VALUE;
        }
    }

    /* Initial state -- a degenerate IC defeats validation entirely. */
    double rmag = sqrt(cfg->position_km[0]*cfg->position_km[0] +
                       cfg->position_km[1]*cfg->position_km[1] +
                       cfg->position_km[2]*cfg->position_km[2]);
    double vmag = sqrt(cfg->velocity_kms[0]*cfg->velocity_kms[0] +
                       cfg->velocity_kms[1]*cfg->velocity_kms[1] +
                       cfg->velocity_kms[2]*cfg->velocity_kms[2]);
    if (rmag < 1.0e-3) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "initial_state.position_km is essentially at the origin "
                "(|r| = %.3e km)", rmag);
        return SPODY_ERR_BAD_VALUE;
    }
    if (vmag < 1.0e-12) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "initial_state.velocity_kms is zero -- need a real orbit");
        return SPODY_ERR_BAD_VALUE;
    }

    /* Harmonics degree -- 1200 is the upper bound of GRGM1200B coefficients. */
    /* The schema-level cap (2200) covers every model in current use
     * (GRGM1200B N=1200, EIGEN-6C4 N=2190, EGM2008 N=2190) with a
     * little headroom for future ones. The ACTUAL usable maximum for
     * a given run is the N declared in the harmonics_file's header --
     * spody_load_HarmonicGravityData rejects degree > file_N at load
     * time with a separate error. The lower bound is 2 since
     * degree 0 (point mass) and degree 1 (CoM origin) are absorbed
     * in the central-body convention. */
    if (cfg->harmonics_degree < 2 || cfg->harmonics_degree > 2200) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "force_model.harmonics_degree = %d is outside the schema "
                "range [2, 2200]. Note: the effective upper bound is the "
                "N declared in the chosen harmonics_file (e.g. 1200 for "
                "GRGM1200B, 2190 for EIGEN-6C4 / EGM2008); this 2200 cap "
                "is only the absolute schema ceiling.",
                cfg->harmonics_degree);
        return SPODY_ERR_BAD_VALUE;
    }

    /* Third bodies: known names, no duplicate with central body. */
    for (int i = 0; i < cfg->n_third_bodies; ++i) {
        int naif = 0; double mu = 0.0;
        if (spody_lookup_third_body(cfg->third_body_names[i], &naif, &mu, NULL) != 0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "force_model.third_bodies[%d] = '%s' is not a known body",
                    i, cfg->third_body_names[i]);
            return SPODY_ERR_BAD_VALUE;
        }
        const SpodyCentralBodySpec *cb =
                spody_central_body_get(cfg->central_body);
        if (cb && naif == cb->naif) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "force_model.third_bodies[%d] = '%s' cannot coexist "
                    "with central_body = '%s'", i,
                    cfg->third_body_names[i], cb->name);
            return SPODY_ERR_BAD_VALUE;
        }
        for (int j = 0; j < i; ++j) {
            if (strcmp(cfg->third_body_names[i], cfg->third_body_names[j]) == 0) {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "force_model.third_bodies has duplicate '%s'",
                        cfg->third_body_names[i]);
                return SPODY_ERR_BAD_VALUE;
            }
        }
    }

    /* Altitude crossings (HF): each body must be the central body OR
     * one of the configured third bodies. Anything else has no
     * position resolvable by the runtime. The body's radius is
     * pulled from the body table at build_events time; we require it
     * to be > 0 here so a known-but-radiusless body (debris, etc.)
     * is rejected up front rather than silently dropped. */
    {
        const SpodyCentralBodySpec *cb = spody_central_body_get(cfg->central_body);
        for (int i = 0; i < cfg->n_altitude_crossings; ++i) {
            const AltitudeCrossingSpec *ac = &cfg->altitude_crossings[i];
            int is_central = (cb && strcmp(ac->body_name, cb->name) == 0);
            int is_third = 0;
            for (int j = 0; j < cfg->n_third_bodies; ++j) {
                if (strcmp(ac->body_name, cfg->third_body_names[j]) == 0) {
                    is_third = 1;
                    break;
                }
            }
            if (!is_central && !is_third) {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "events.altitude_crossing[%d].body = '%s' must be the"
                        " central body ('%s') or one of force_model.third_bodies",
                        i, ac->body_name, cb ? cb->name : "?");
                return SPODY_ERR_BAD_VALUE;
            }
            if (!is_central) {
                double r_km = 0.0;
                if (spody_lookup_third_body(ac->body_name, NULL, NULL, &r_km) != 0
                        || r_km <= 0.0) {
                    spody_error_set(err, SPODY_ERR_BAD_VALUE,
                            "events.altitude_crossing[%d].body = '%s' has no"
                            " known physical radius -- altitude is undefined",
                            i, ac->body_name);
                    return SPODY_ERR_BAD_VALUE;
                }
            }
        }
    }

    /* Data files must exist. */
    if (!file_exists(cfg->harmonics_file)) {
        spody_error_set(err, SPODY_ERR_FILE_NOT_FOUND,
                "harmonics_file not found: %s", cfg->harmonics_file);
        return SPODY_ERR_FILE_NOT_FOUND;
    }
    if (!file_exists(cfg->ephemeris_file)) {
        spody_error_set(err, SPODY_ERR_FILE_NOT_FOUND,
                "ephemeris_file not found: %s", cfg->ephemeris_file);
        return SPODY_ERR_FILE_NOT_FOUND;
    }

    /* Earth-only: both EOP and IAU 2006 paths required, and the canonical
     * file inside each must exist. We probe iau2006_dir/tab5.2a.txt
     * (the X series, biggest of the three -- if that's present the dir
     * is almost certainly the right one) rather than stat'ing the
     * directory itself, so the check is portable without a dir-stat
     * helper. spody_setup_MappedIAU2006Data fails loudly later if the
     * other two tables are missing. */
    if (cfg->central_body == SPODY_CENTRAL_EARTH) {
        if (cfg->eop_file[0] == '\0') {
            spody_error_set(err, SPODY_ERR_MISSING_KEY,
                    "force_model.eop_file is required when central_body = 'Earth'");
            return SPODY_ERR_MISSING_KEY;
        }
        if (cfg->iau2006_dir[0] == '\0') {
            spody_error_set(err, SPODY_ERR_MISSING_KEY,
                    "force_model.iau2006_dir is required when central_body = 'Earth'");
            return SPODY_ERR_MISSING_KEY;
        }
        if (!file_exists(cfg->eop_file)) {
            spody_error_set(err, SPODY_ERR_FILE_NOT_FOUND,
                    "eop_file not found: %s", cfg->eop_file);
            return SPODY_ERR_FILE_NOT_FOUND;
        }
        char probe[SPODY_MAX_PATH];
        snprintf(probe, sizeof probe, "%s/tab5.2a.txt", cfg->iau2006_dir);
        if (!file_exists(probe)) {
            spody_error_set(err, SPODY_ERR_FILE_NOT_FOUND,
                    "iau2006_dir does not contain tab5.2a.txt: %s",
                    cfg->iau2006_dir);
            return SPODY_ERR_FILE_NOT_FOUND;
        }
    }

    /* Drag needs an atmosphere model registered on the central body
     * (registry-driven: adding Mars+MCD later makes this check pass
     * without touching it) plus the space weather table. */
    if (cfg->enable_drag) {
        const SpodyCentralBodySpec *cb =
                spody_central_body_get(cfg->central_body);
        if (!cb || !cb->atmosphere) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "force_model.drag = true but central body '%s' has no "
                    "atmosphere model registered (today: 'Earth' / "
                    "NRLMSISE-00)", cb ? cb->name : "?");
            return SPODY_ERR_BAD_VALUE;
        }
        if (cfg->space_weather_file[0] == '\0') {
            spody_error_set(err, SPODY_ERR_MISSING_KEY,
                    "force_model.space_weather_file is required when "
                    "drag = true (CelesTrak SW-All.csv)");
            return SPODY_ERR_MISSING_KEY;
        }
        if (!file_exists(cfg->space_weather_file)) {
            spody_error_set(err, SPODY_ERR_FILE_NOT_FOUND,
                    "space_weather_file not found: %s",
                    cfg->space_weather_file);
            return SPODY_ERR_FILE_NOT_FOUND;
        }
    }

    /* Density calibration: constant XOR file, positive constant,
     * meaningful only under the drag force. */
    if (cfg->has_density_scale && cfg->density_scale_file[0] != '\0') {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "force_model.density_scale and density_scale_file are "
                "mutually exclusive (constant factor vs k(t) node "
                "table)");
        return SPODY_ERR_BAD_VALUE;
    }
    if ((cfg->has_density_scale || cfg->density_scale_file[0] != '\0')
            && !cfg->enable_drag) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "force_model.density_scale%s requires drag = true (it "
                "calibrates the atmosphere density)",
                cfg->density_scale_file[0] != '\0' ? "_file" : "");
        return SPODY_ERR_BAD_VALUE;
    }
    if (cfg->has_density_scale
            && !(cfg->density_scale > 0.0 && isfinite(cfg->density_scale))) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "force_model.density_scale must be positive and finite "
                "(got %.6g)", cfg->density_scale);
        return SPODY_ERR_BAD_VALUE;
    }
    if (cfg->density_scale_file[0] != '\0'
            && !file_exists(cfg->density_scale_file)) {
        spody_error_set(err, SPODY_ERR_FILE_NOT_FOUND,
                "density_scale_file not found: %s",
                cfg->density_scale_file);
        return SPODY_ERR_FILE_NOT_FOUND;
    }

    /* Integrator */
    if (cfg->rel_tol <= 0.0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "integrator.rel_tol must be positive (got %.6g)",
                cfg->rel_tol);
        return SPODY_ERR_BAD_VALUE;
    }
    if (cfg->h_min_s <= 0.0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "integrator.h_min_s must be positive (got %.6g)",
                cfg->h_min_s);
        return SPODY_ERR_BAD_VALUE;
    }
    if (cfg->h_max_s <= cfg->h_min_s) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "integrator.h_max_s (%.6g) must exceed h_min_s (%.6g)",
                cfg->h_max_s, cfg->h_min_s);
        return SPODY_ERR_BAD_VALUE;
    }
    if (cfg->h_init_s < cfg->h_min_s || cfg->h_init_s > cfg->h_max_s) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "integrator.h_init_s = %.6g must lie within [h_min_s=%.6g, h_max_s=%.6g]",
                cfg->h_init_s, cfg->h_min_s, cfg->h_max_s);
        return SPODY_ERR_BAD_VALUE;
    }

    /* Output */
    if (cfg->output_mode == SPODY_OUT_FIXED && cfg->output_interval_s <= 0.0) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "output.interval_s must be positive when mode = 'fixed' "
                "(got %.6g)", cfg->output_interval_s);
        return SPODY_ERR_BAD_VALUE;
    }
    /* Note: omitting both output.csv_file AND output.bin_file is allowed.
     * In that mode the propagation runs but writes no trajectory file --
     * useful for benchmarking, sanity checks, and (future) event-only
     * runs where the trajectory itself is not needed. */

    /* Batch: validate each cell of the cases matrix against the per-cell
     * rule attached to its target field descriptor. No InputConfig copy,
     * no override application -- just raw value checks. Cross-field rules
     * (|r|, |v|, h_init within bounds) are intentionally not re-evaluated;
     * run-time setup catches the rare misconfigurations. */
    if (cfg->batch) {
        const BatchConfig *b = cfg->batch;
        for (int i = 0; i < b->n_cases; ++i) {
            for (int j = 0; j < b->n_columns; ++j) {
                const SpodyFieldDesc *fd = b->column_targets[j];
                if (!fd) continue;
                double v = b->values[i * b->n_columns + j];
                const char *id = b->case_ids[i];
                if (!isfinite(v)) {
                    spody_error_set(err, SPODY_ERR_BAD_VALUE,
                            "batch case '%s': %s = %g is not finite",
                            id, fd->path, v);
                    return SPODY_ERR_BAD_VALUE;
                }
                /* Delta columns are additive offsets, not absolute values:
                 * the per-field rule (POSITIVE, NON_NEG, ...) applies to
                 * base + delta, not to the raw cell, so a negative delta
                 * is legitimate. They are left unchecked here (only the
                 * finiteness guard above applies). */
                if (b->column_is_delta && b->column_is_delta[j]) continue;
                switch (fd->rule) {
                    case SPODY_VAL_ANY:
                        break;
                    case SPODY_VAL_POSITIVE:
                        if (v <= 0.0) {
                            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                                    "batch case '%s': %s must be > 0 (got %g)",
                                    id, fd->path, v);
                            return SPODY_ERR_BAD_VALUE;
                        }
                        break;
                    case SPODY_VAL_NON_NEG:
                        if (v < 0.0) {
                            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                                    "batch case '%s': %s must be >= 0 (got %g)",
                                    id, fd->path, v);
                            return SPODY_ERR_BAD_VALUE;
                        }
                        break;
                    case SPODY_VAL_BOOL:
                        if (v != 0.0 && v != 1.0) {
                            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                                    "batch case '%s': %s must be 0 or 1 (got %g)",
                                    id, fd->path, v);
                            return SPODY_ERR_BAD_VALUE;
                        }
                        break;
                }
            }
        }
    }

    return SPODY_OK;
}
