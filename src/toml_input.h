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

#include "app_diagnostics.h"   /* SpodyError, SpodyErrorCode */

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

typedef enum {
    SPODY_CENTRAL_MOON = 0  /* v0 supports only Moon as central body */
} SpodyCentralBody;

typedef enum {
    SPODY_FRAME_CENTRAL_INERTIAL = 0
} SpodyFrame;

#define SPODY_MAX_THIRD_BODIES   8
#define SPODY_MAX_BODY_NAME      16
#define SPODY_MAX_SIM_NAME      128
#define SPODY_MAX_PATH         1024

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
} BatchConfig;

typedef struct {
    /* [simulation] */
    char   sim_name[SPODY_MAX_SIM_NAME];
    double et_start_s;
    double duration_s;

    /* [spacecraft] */
    double mass_kg;
    int    has_srp_block;        /* 1 if [spacecraft.srp] is present     */
    double srp_area_m2;          /* valid iff has_srp_block              */
    double srp_cr;               /* valid iff has_srp_block              */

    /* [initial_state] */
    SpodyFrame initial_frame;
    double     position_km[3];
    double     velocity_kms[3];

    /* [force_model] */
    SpodyCentralBody central_body;
    char             harmonics_file[SPODY_MAX_PATH];   /* resolved path */
    int              harmonics_degree;
    int              n_third_bodies;
    char             third_body_names[SPODY_MAX_THIRD_BODIES][SPODY_MAX_BODY_NAME];
    int              enable_srp;

    /* [ephemeris] */
    char ephemeris_file[SPODY_MAX_PATH];               /* resolved path */

    /* [integrator] */
    SpodyIntegratorType integrator_type;
    double              rel_tol;
    double              h_init_s;
    double              h_min_s;
    double              h_max_s;

    /* [output] */
    SpodyOutputMode output_mode;
    double          output_interval_s;
    char            csv_file[SPODY_MAX_PATH];   /* resolved path, "" if none */
    char            bin_file[SPODY_MAX_PATH];   /* resolved path, "" if none */
    char            log_file[SPODY_MAX_PATH];   /* resolved path, "" if none */

    /* [batch] -- NULL if absent in the TOML, heap-allocated otherwise.
     * Released by spody_free_input. */
    BatchConfig *batch;
} InputConfig;

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
 * Resolve a third-body name (e.g. "Earth", "Sun") to a NAIF id and the
 * matching gravitational parameter (km^3/s^2). Returns 0 on success or
 * -1 if the name is unknown. Used by sim_setup to populate the
 * ForceModelContext, and by the validator to catch bad names early.
 */
int spody_lookup_third_body(const char *name, int *naif_id, double *mu);

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
