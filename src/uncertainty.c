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
#include "uncertainty.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "spody_core.h"
#include "app_diagnostics.h"
#include "app_io.h"
#include "sim_setup.h"
#include "toml_input.h"

static const char *const dist_names[2]  = { "normal", "lognormal" };
static const char *const sigma_names[3] = { "sigma", "sigma_percent", "sigma_ln" };
static const char *const value_names[3] = { "", "mean", "median" };
static const char *const delta_cols[6]  = {
    "dx_km", "dy_km", "dz_km", "dvx_kms", "dvy_kms", "dvz_kms" };
static const char *const delta_targets[6] = {
    "initial_state.position_km[0]", "initial_state.position_km[1]",
    "initial_state.position_km[2]", "initial_state.velocity_kms[0]",
    "initial_state.velocity_kms[1]", "initial_state.velocity_kms[2]" };

/* Number of samples named in a domain refusal before "and N more". */
enum { MAX_LISTED_BAD = 10 };

/* Everything the draw of one case needs, computed once. */
typedef struct {
    const SpodyUqConfig *uq;
    int    m;              /* components of the state with variance > 0 */
    int    idx[6];         /* their indices                              */
    double L[36];          /* Cholesky factor of the m x m sub-block      */
    double R[3][3];        /* RIC -> ICRF at the nominal initial state    */
    double p0[SPODY_UQ_MAX_PARAMS];      /* scenario values               */
    double s_ln[SPODY_UQ_MAX_PARAMS];    /* lognormal sigma of ln p        */
} Sampler;

/* One case's draws: the normal deviates actually used and their effect. */
typedef struct {
    double z_state[6];                   /* 0 for zero-variance components */
    double z_par[SPODY_UQ_MAX_PARAMS];
    double delta[6];                     /* ICRF, km and km/s              */
    double value[SPODY_UQ_MAX_PARAMS];
} Draw;

static int sampler_init(Sampler *s, const SpodyUqConfig *uq,
                        const InputConfig *sc_icrf, SpodyError *err)
{
    memset(s, 0, sizeof *s);
    s->uq = uq;
    if (uq->has_initial_state) {
        double P[36], S[36];
        spody_uq_initial_covariance(uq, P);
        for (int i = 0; i < 6; ++i)
            if (P[i * 6 + i] > 0.0) s->idx[s->m++] = i;
        for (int a = 0; a < s->m; ++a)
            for (int b = 0; b < s->m; ++b)
                S[a * s->m + b] = P[s->idx[a] * 6 + s->idx[b]];
        if (spody_symmat_cholesky(s->m, S, s->L) != 0) {
            /* spody_validate_uq_input already proved it positive definite */
            spody_error_set(err, SPODY_ERR_INTERNAL,
                    "initial-state covariance: Cholesky failed after validation");
            return SPODY_ERR_INTERNAL;
        }
        if (uq->axes_ric
            && spody_getrotmatrix_ric2icrf(sc_icrf->position_km,
                                           sc_icrf->velocity_kms, s->R) != 0) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "montecarlo.initial_state.axes = \"ric\": the scenario's "
                    "initial state has r = 0 or r parallel to v (RIC undefined)");
            return SPODY_ERR_BAD_VALUE;
        }
    }
    for (int j = 0; j < uq->n_params; ++j) {
        const SpodyUqParameter *p = &uq->params[j];
        s->p0[j] = *(const double *)((const char *)sc_icrf + p->field->offset);
        if (p->distribution == SPODY_UQ_DIST_LOGNORMAL) {
            double r = p->sigma / 100.0;
            s->s_ln[j] = (p->sigma_kind == SPODY_UQ_SIGMA_LN)
                       ? p->sigma : sqrt(log(1.0 + r * r));
        }
    }
    return SPODY_OK;
}

/* Case 0 is the nominal: no draw at all. Cases >= 1 read their own
 * Philox streams: (seed, case, 0) for the state, (seed, case,
 * substream of the target) for each parameter. */
static void draw_case(const Sampler *s, int c, Draw *d)
{
    const SpodyUqConfig *uq = s->uq;
    memset(d, 0, sizeof *d);
    for (int j = 0; j < uq->n_params; ++j) d->value[j] = s->p0[j];
    if (c == 0) return;

    if (uq->has_initial_state) {
        SpodyRandomStream st;
        double z[6], da[6] = { 0 };
        spody_random_stream_init(&st, uq->seed, (uint64_t)c, 0);
        for (int k = 0; k < 6; ++k) z[k] = spody_random_next_normal(&st);
        for (int a = 0; a < s->m; ++a) {
            double acc = 0.0;
            for (int b = 0; b <= a; ++b) acc += s->L[a * s->m + b] * z[s->idx[b]];
            da[s->idx[a]] = acc;
            d->z_state[s->idx[a]] = z[s->idx[a]];
        }
        if (uq->axes_ric) {
            spody_rotate_vector(s->R, &da[0], &d->delta[0]);
            spody_rotate_vector(s->R, &da[3], &d->delta[3]);
        } else {
            memcpy(d->delta, da, sizeof da);
        }
    }
    for (int j = 0; j < uq->n_params; ++j) {
        const SpodyUqParameter *p = &uq->params[j];
        SpodyRandomStream st;
        spody_random_stream_init(&st, uq->seed, (uint64_t)c, p->substream);
        double z = spody_random_next_normal(&st);
        double v;
        if (p->distribution == SPODY_UQ_DIST_NORMAL) {
            v = (p->sigma_kind == SPODY_UQ_SIGMA_PERCENT)
              ? s->p0[j] * (1.0 + (p->sigma / 100.0) * z)
              : s->p0[j] + p->sigma * z;
        } else {
            double sl = s->s_ln[j];
            v = (p->scenario_value_is == SPODY_UQ_VALUE_IS_MEDIAN)
              ? s->p0[j] * exp(sl * z)
              : s->p0[j] * exp(sl * z - 0.5 * sl * sl);
        }
        d->z_par[j] = z;
        d->value[j] = v;
    }
}

/* Batch-style CSV column name of a target: dots and brackets become
 * underscores ("spacecraft.drag.Cd" -> "spacecraft_drag_Cd"). */
static void column_name(const char *target, const char *prefix,
                        char *out, size_t outsz)
{
    snprintf(out, outsz, "%s%s", prefix, target);
    for (char *q = out; *q; ++q)
        if (*q == '.' || *q == '[' || *q == ']') *q = '_';
}

/* Every sample within its field's domain (> 0 or >= 0), or a refusal
 * naming the cases: never skip one silently. */
static int check_domain(const Sampler *s, SpodyError *err)
{
    const SpodyUqConfig *uq = s->uq;
    for (int j = 0; j < uq->n_params; ++j) {
        const SpodyUqParameter *p = &uq->params[j];
        if (p->field->rule != SPODY_VAL_POSITIVE && p->field->rule != SPODY_VAL_NON_NEG)
            continue;
        int n_bad = 0;
        char list[256] = "";
        for (int c = 1; c <= uq->samples; ++c) {
            Draw d;
            draw_case(s, c, &d);
            double v = d.value[j];
            int bad = (p->field->rule == SPODY_VAL_POSITIVE) ? !(v > 0.0) : !(v >= 0.0);
            if (!bad) continue;
            if (n_bad < MAX_LISTED_BAD) {
                char one[48];
                snprintf(one, sizeof one, "%scase %d = %.6g", n_bad ? ", " : "", c, v);
                strncat(list, one, sizeof list - strlen(list) - 1);
            }
            ++n_bad;
        }
        if (n_bad) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "[montecarlo.parameters] '%s': %d of %d samples fall outside "
                    "the physical domain (%s): %s%s. A normal distribution "
                    "has unbounded tails; use a lognormal or a smaller sigma",
                    p->target, n_bad, uq->samples,
                    p->field->rule == SPODY_VAL_POSITIVE ? "> 0" : ">= 0",
                    list, n_bad > MAX_LISTED_BAD ? ", ..." : "");
            return SPODY_ERR_BAD_VALUE;
        }
    }
    return SPODY_OK;
}

static int write_samples(const Sampler *s, const char *path, SpodyError *err)
{
    const SpodyUqConfig *uq = s->uq;
    FILE *fp = fopen(path, "w");
    if (!fp) {
        spody_error_set(err, SPODY_ERR_IO, "cannot open '%s' for write", path);
        return SPODY_ERR_IO;
    }
    char col[SPODY_UQ_MAX_TARGET + 8];
    fprintf(fp, "# spody uncertainty montecarlo samples: %s\n", uq->name);
    fprintf(fp, "# scenario %s, seed %llu, %d dispersed cases + nominal (id 0)\n",
            uq->scenario, (unsigned long long)uq->seed, uq->samples);
    fprintf(fp, "# z_*: standard normal deviates drawn; d*: initial-state "
                "offsets added to [initial_state] (ICRF, km, km/s); other "
                "columns: parameter values\n");
    fprintf(fp, "id");
    if (uq->has_initial_state)
        for (int k = 0; k < 6; ++k) fprintf(fp, ",z_state_%s", delta_cols[k]);
    for (int j = 0; j < uq->n_params; ++j) {
        column_name(uq->params[j].target, "z_", col, sizeof col);
        fprintf(fp, ",%s", col);
    }
    if (uq->has_initial_state)
        for (int k = 0; k < 6; ++k) fprintf(fp, ",%s", delta_cols[k]);
    for (int j = 0; j < uq->n_params; ++j) {
        column_name(uq->params[j].target, "", col, sizeof col);
        fprintf(fp, ",%s", col);
    }
    fprintf(fp, "\n");
    for (int c = 0; c <= uq->samples; ++c) {
        Draw d;
        draw_case(s, c, &d);
        fprintf(fp, "%d", c);
        if (uq->has_initial_state)
            for (int k = 0; k < 6; ++k) fprintf(fp, ",%.17g", d.z_state[k]);
        for (int j = 0; j < uq->n_params; ++j) fprintf(fp, ",%.17g", d.z_par[j]);
        if (uq->has_initial_state)
            for (int k = 0; k < 6; ++k) fprintf(fp, ",%.17g", d.delta[k]);
        for (int j = 0; j < uq->n_params; ++j) fprintf(fp, ",%.17g", d.value[j]);
        fprintf(fp, "\n");
    }
    int bad = ferror(fp);
    if (fclose(fp) != 0) bad = 1;
    if (bad) {
        spody_error_set(err, SPODY_ERR_IO, "write failed on '%s'", path);
        return SPODY_ERR_IO;
    }

    /* The block that reruns these exact cases with `spody batch`. */
    spody_log_printf("  samples file: %s\n", path);
    spody_log_printf("  rerun with spody batch (cases_file = this file):\n");
    spody_log_printf("    [batch.columns]\n");
    if (uq->has_initial_state)
        for (int k = 0; k < 6; ++k)
            spody_log_printf("    z_state_%s = \"\"\n", delta_cols[k]);
    for (int j = 0; j < uq->n_params; ++j) {
        column_name(uq->params[j].target, "z_", col, sizeof col);
        spody_log_printf("    %s = \"\"\n", col);
    }
    if (uq->has_initial_state)
        for (int k = 0; k < 6; ++k)
            spody_log_printf("    %s = { target = \"%s\", mode = \"delta\" }\n",
                             delta_cols[k], delta_targets[k]);
    for (int j = 0; j < uq->n_params; ++j) {
        column_name(uq->params[j].target, "", col, sizeof col);
        spody_log_printf("    %s = \"%s\"\n", col, uq->params[j].target);
    }
    return SPODY_OK;
}

/* Copy the .uq.toml into the run folder with its `scenario` line
 * rewritten to the scenario snapshot next to it, so the folder is
 * self-contained: re-running the copy uses the copied scenario, never
 * the original (which may have changed since). The original value
 * stays as a comment. Exactly one `scenario = ...` line must exist in
 * [montecarlo]; anything else is refused rather than guessed. */
static int copy_uq_pointing_at(const char *src, const char *dst,
                               const char *scenario_basename, SpodyError *err)
{
    FILE *in = fopen(src, "r");
    if (!in) {
        spody_error_set(err, SPODY_ERR_IO, "cannot reopen '%s'", src);
        return SPODY_ERR_IO;
    }
    FILE *out = fopen(dst, "w");
    if (!out) {
        fclose(in);
        spody_error_set(err, SPODY_ERR_IO, "cannot open '%s' for write", dst);
        return SPODY_ERR_IO;
    }
    char line[4096];
    int in_mc = 0, n_found = 0;
    while (fgets(line, sizeof line, in)) {
        const char *p = line;
        while (*p == ' ' || *p == '\t') ++p;
        if (*p == '[') in_mc = strncmp(p, "[montecarlo]", 12) == 0;
        const char *q = p + 8;
        while (*q == ' ' || *q == '\t') ++q;
        if (in_mc && strncmp(p, "scenario", 8) == 0 && *q == '=') {
            char was[4096];
            snprintf(was, sizeof was, "%s", q + 1);
            size_t n = strlen(was);
            while (n && (was[n - 1] == '\n' || was[n - 1] == '\r')) was[--n] = '\0';
            fprintf(out, "scenario = \"%s\"   # snapshot of:%s\n",
                    scenario_basename, was);
            ++n_found;
        } else {
            fputs(line, out);
        }
    }
    int bad = ferror(in) || ferror(out);
    fclose(in);
    if (fclose(out) != 0) bad = 1;
    if (bad) {
        spody_error_set(err, SPODY_ERR_IO, "write failed on '%s'", dst);
        return SPODY_ERR_IO;
    }
    if (n_found != 1) {
        spody_error_set(err, SPODY_ERR_BAD_VALUE,
                "cannot point the snapshot at the copied scenario: expected "
                "one 'scenario = ...' line in [montecarlo], found %d", n_found);
        return SPODY_ERR_BAD_VALUE;
    }
    return SPODY_OK;
}

static void print_summary(const SpodyUqConfig *uq, const InputConfig *sc,
                          const char *out_dir)
{
    spody_log_printf("spody uncertainty montecarlo: %s\n", uq->path);
    spody_log_printf("  name      : %s\n", uq->name);
    spody_log_printf("  scenario  : %s (%s)\n", uq->scenario, sc->sim_name);
    spody_log_printf("  samples   : %d dispersed + nominal case 0\n", uq->samples);
    spody_log_printf("  seed      : %llu\n", (unsigned long long)uq->seed);
    spody_log_printf("  threads   : %d\n", uq->thread_number);
    spody_log_printf("  output_dir: %s\n", out_dir);
    spody_log_printf("  snapshots : %d\n", uq->n_snapshots);
    if (uq->has_initial_state) {
        if (uq->covariance_given) {
            spody_log_printf("  state     : full covariance, %s axes\n",
                             uq->axes_ric ? "RIC" : "ICRF");
        } else {
            spody_log_printf("  state     : %s sigma pos [%g %g %g] km, "
                             "vel [%g %g %g] km/s\n",
                             uq->axes_ric ? "RIC" : "ICRF",
                             uq->sigma[0], uq->sigma[1], uq->sigma[2],
                             uq->sigma[3], uq->sigma[4], uq->sigma[5]);
        }
    } else {
        spody_log_printf("  state     : exact (no [montecarlo.initial_state])\n");
    }
    for (int i = 0; i < uq->n_params; ++i) {
        const SpodyUqParameter *p = &uq->params[i];
        spody_log_printf("  parameter : %-28s %-9s %s = %g%s%s  (substream %016llx)\n",
                         p->target, dist_names[p->distribution],
                         sigma_names[p->sigma_kind], p->sigma,
                         p->scenario_value_is ? ", scenario value is the " : "",
                         value_names[p->scenario_value_is],
                         (unsigned long long)p->substream);
    }
}

int spody_uncertainty_montecarlo_run(const char *uq_path, int samples_only)
{
    SpodyUqConfig    uq;
    InputConfig      sc;
    SimulationShared shared;
    SpodyError       err;
    int rc = 1;

    memset(&sc, 0, sizeof sc);
    memset(&shared, 0, sizeof shared);
    if (spody_load_uq_input(uq_path, &uq, &err) != SPODY_OK) {
        spody_error_print(&err);
        return 1;
    }
    if (!samples_only) {
        fprintf(stderr, "uncertainty montecarlo: the propagation of the cases "
                        "is not in this build yet; run with --samples-only\n");
        return 1;
    }
    if (spody_load_input(uq.scenario, &sc, &err) != SPODY_OK
        || spody_validate_input(&sc, &err) != SPODY_OK
        || spody_validate_uq_input(&uq, &sc, &err) != SPODY_OK) {
        if (err.file[0] == '\0') snprintf(err.file, sizeof err.file, "%s", uq.scenario);
        spody_error_print(&err);
        goto done;
    }
    const char *out_dir = uq.output_dir[0] ? uq.output_dir : sc.output_dir;
    if (!out_dir[0]) {
        spody_error_set(&err, SPODY_ERR_MISSING_KEY,
                "no output folder: set montecarlo.output_dir or the "
                "scenario's output.output_dir");
        snprintf(err.file, sizeof err.file, "%s", uq.path);
        spody_error_print(&err);
        goto done;
    }
    print_summary(&uq, &sc, out_dir);

    /* The nominal initial state in ICRF: the RIC axes and every offset
     * refer to it, exactly as spody batch adds its delta columns. */
    if (spody_build_shared(&sc, &shared, &err) != SPODY_OK
        || spody_resolve_initial_state_icrf(&sc, &shared, &err) != SPODY_OK) {
        spody_error_print(&err);
        goto done;
    }

    Sampler s;
    if (sampler_init(&s, &uq, &sc, &err) != SPODY_OK
        || check_domain(&s, &err) != SPODY_OK) {
        if (err.file[0] == '\0') snprintf(err.file, sizeof err.file, "%s", uq.path);
        spody_error_print(&err);
        goto done;
    }

    char run_dir[SPODY_MAX_PATH], path[SPODY_MAX_PATH], base[SPODY_MAX_SIM_NAME + 32];
    if (spody_io_make_run_subdir(out_dir, run_dir, sizeof run_dir, &err) != SPODY_OK) {
        spody_error_print(&err);
        goto done;
    }
    spody_log_printf("  run folder: %s\n", run_dir);
    spody_io_run_subdir_filepath(run_dir, "input.toml", path, sizeof path);
    if (spody_io_copy_file(uq.scenario, path, &err) != SPODY_OK) {
        spody_error_print(&err);
        goto done;
    }
    char scenario_copy[SPODY_MAX_PATH];
    snprintf(scenario_copy, sizeof scenario_copy, "%s", spody_io_basename(path));
    snprintf(base, sizeof base, "%s.uq.toml", uq.name);
    spody_io_run_subdir_filepath(run_dir, base, path, sizeof path);
    if (copy_uq_pointing_at(uq.path, path, scenario_copy, &err) != SPODY_OK) {
        if (err.file[0] == '\0') snprintf(err.file, sizeof err.file, "%s", uq.path);
        spody_error_print(&err);
        goto done;
    }
    snprintf(base, sizeof base, "%s_samples.uq.csv", uq.name);
    spody_io_run_subdir_filepath(run_dir, base, path, sizeof path);
    if (write_samples(&s, path, &err) != SPODY_OK) {
        spody_error_print(&err);
        goto done;
    }
    rc = 0;
done:
    spody_free_shared(&shared);
    spody_free_input(&sc);
    return rc;
}
