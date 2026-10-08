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
#include <time.h>
#ifdef SPODY_HAVE_OPENMP
#include <omp.h>
#endif

#include "spody_core.h"
#include "app_diagnostics.h"
#include "app_io.h"
#include "sim_run.h"               /* spody_run_simulation */
#include "sim_setup.h"
#include "spody_time.h"            /* spody_et_to_mjd_utc */
#include "toml_input.h"

static const char *const dist_names[2]  = { "normal", "lognormal" };
static const char *const sigma_names[3] = { "sigma", "sigma_percent", "sigma_ln" };
static const char *const value_names[3] = { "", "mean", "median" };
static const char *const delta_cols[6]  = {
    "dx_km", "dy_km", "dz_km", "dvx_kms", "dvy_kms", "dvz_kms" };

/* Number of samples named in a domain refusal before "and N more". */
enum { MAX_LISTED_BAD = 10 };

/* Process-noise substreams, in the SPODY_RANDOM_DOMAIN_PROCESS_NOISE
 * domain (so they cannot meet the once-per-case draws of domain 0):
 * 0, 1, 2 reserved for the R, I, C random acceleration, 3 the
 * density. */
static const uint64_t pn_substream_density = 3;
static const uint64_t pn_substream_accel[3] = { 0, 1, 2 };
static const uint64_t pn_substream_1rev_cos[3] = { 4, 5, 6 };
static const uint64_t pn_substream_1rev_sin[3] = { 7, 8, 9 };

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
                             delta_cols[k], uq->state_field[k]->path);
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
    if (uq->pn_density) {
        spody_log_printf("  noise     : density x exp(eta), eta Gauss-Markov sigma_ln = %g, "
                         "tau = %g s, nodes every %g s, scenario value is the %s  "
                         "(process-noise substream %llu)\n",
                         uq->pn_density_sigma_ln, uq->pn_density_tau_s,
                         uq->pn_density_interval_s,
                         value_names[uq->pn_density_value_is],
                         (unsigned long long)pn_substream_density);
        if (uq->pn_density_ap_doubling > 0.0)
            spody_log_printf("  noise     : density sigma follows the activity: "
                             "sigma_ln x (1 + Ap(t) / %g), Ap 3-hourly from the "
                             "space-weather file at each node\n",
                             uq->pn_density_ap_doubling);
        if (uq->pn_density_interval_s > 0.1 * uq->pn_density_tau_s)
            spody_log_printf("  noise     : note: interval_s > tau_s / 10; between "
                             "the nodes the variance drops by up to %.0f %%\n",
                             100.0 * (1.0 - 0.5 * (1.0 + exp(-uq->pn_density_interval_s
                                                             / uq->pn_density_tau_s))));
    }
    for (int e = 0; e < 2; ++e) {
        const int on = e ? uq->pn_1rev : uq->pn_accel;
        if (!on) continue;
        const double *s  = e ? uq->pn_1rev_sigma_kms2 : uq->pn_accel_sigma_kms2;
        const double *tau = e ? uq->pn_1rev_tau_s : uq->pn_accel_tau_s;
        const double dt  = e ? uq->pn_1rev_interval_s : uq->pn_accel_interval_s;
        spody_log_printf("  noise     : RIC acceleration%s, Gauss-Markov sigma R/I/C = "
                         "%g %g %g m/s^2, tau = %g %g %g s, nodes every %g s  "
                         "(process-noise substreams %s)\n",
                         e ? " once per revolution (cos u, sin u)" : "",
                         s[0] * 1e3, s[1] * 1e3, s[2] * 1e3, tau[0], tau[1], tau[2], dt,
                         e ? "4-9" : "0-2");
        double tmin = INFINITY;
        for (int k = 0; k < 3; ++k) if (s[k] > 0.0 && tau[k] < tmin) tmin = tau[k];
        if (dt > 0.1 * tmin)
            spody_log_printf("  noise     : note: interval_s > tau_s / 10; between "
                             "the nodes the variance drops by up to %.0f %%\n",
                             100.0 * (1.0 - 0.5 * (1.0 + exp(-dt / tmin))));
    }
}

/* The states one propagation emits (the output grid, plus the trigger
 * state when an impact ends it), collected through the state sink. */
typedef struct {
    double *t;
    double *y;                 /* 6 per record */
    size_t  n, cap;
    int     oom;
} Track;

static void track_sink(double t, const double y[6], void *user)
{
    Track *tr = (Track *)user;
    if (tr->oom) return;
    if (tr->n == tr->cap) {
        size_t cap = tr->cap ? 2 * tr->cap : 4096;
        double *nt = (double *)realloc(tr->t, cap * sizeof *nt);
        if (!nt) { tr->oom = 1; return; }
        tr->t = nt;
        double *ny = (double *)realloc(tr->y, cap * 6 * sizeof *ny);
        if (!ny) { tr->oom = 1; return; }
        tr->y = ny;
        tr->cap = cap;
    }
    tr->t[tr->n] = t;
    memcpy(tr->y + 6 * tr->n, y, 6 * sizeof(double));
    ++tr->n;
}

/* The Monte Carlo keeps from the scenario only the dynamics: its output
 * files and every event but the always-on impact are dropped, so only an
 * impact can end case 0 or any other case. The log names what was
 * dropped and how to get it back. */
static void strip_scenario(InputConfig *c)
{
    char list[256] = "";
    size_t k = 0;
    const struct { const char *path, *what; } out[] = {
        { c->csv_file,           "csv_file"           },
        { c->bin_file,           "bin_file"           },
        { c->accelerations_file, "accelerations_file" },
        { c->events_log,         "events_log"         },
    };
    for (size_t i = 0; i < sizeof out / sizeof out[0]; ++i)
        if (out[i].path[0])
            k += (size_t)snprintf(list + k, sizeof list - k, "%s%s",
                                  k ? ", " : "", out[i].what);
    if (c->eclipse_event_enabled)
        k += (size_t)snprintf(list + k, sizeof list - k, "%seclipse event",
                              k ? ", " : "");
    if (c->n_altitude_crossings > 0)
        k += (size_t)snprintf(list + k, sizeof list - k, "%s%d altitude crossing%s",
                              k ? ", " : "", c->n_altitude_crossings,
                              c->n_altitude_crossings == 1 ? "" : "s");
    if (k) {
        spody_log_printf("  ignored   : %s (Monte Carlo keeps the trajectory and the\n"
                         "              impacts; run spody propagate on <ts>_input.toml "
                         "for the rest)\n", list);
    }
    c->csv_file[0] = c->bin_file[0] = '\0';
    c->accelerations_file[0] = c->events_log[0] = '\0';
    c->eclipse_event_enabled = 0;
    c->n_altitude_crossings  = 0;
}

/* Case c of the stripped scenario *base: the draws applied exactly as
 * `spody batch` applies the samples file (delta columns on the initial
 * state, override columns on the parameters), through a one-case
 * BatchConfig. Case 0 gets no draw and stays the scenario itself. */
static void case_config(const Sampler *s, const InputConfig *base, int c,
                        InputConfig *out)
{
    const SpodyUqConfig *uq = s->uq;
    Draw d;
    draw_case(s, c, &d);
    double                vals[6 + SPODY_UQ_MAX_PARAMS];
    const SpodyFieldDesc *targets[6 + SPODY_UQ_MAX_PARAMS];
    int                   is_delta[6 + SPODY_UQ_MAX_PARAMS];
    int k = 0;
    if (uq->has_initial_state) {
        for (int a = 0; a < 6; ++a, ++k) {
            vals[k] = d.delta[a]; targets[k] = uq->state_field[a]; is_delta[k] = 1;
        }
    }
    for (int j = 0; j < uq->n_params; ++j, ++k) {
        vals[k] = d.value[j]; targets[k] = uq->params[j].field; is_delta[k] = 0;
    }
    BatchConfig b;
    memset(&b, 0, sizeof b);
    b.n_cases         = 1;
    b.n_columns       = k;
    b.values          = vals;
    b.column_targets  = targets;
    b.column_is_delta = is_delta;
    spody_apply_batch_case(base, &b, 0, out);
}

/* Case c's density table under process noise: nodes every interval_s
 * from the case start, the last one at its end;
 *
 *     k_j = k_case(t_j) exp(eta_j) m,
 *
 * eta the Gauss-Markov nodes (spody_gauss_markov_nodes) of stream
 * (seed, c, pn_substream_density) in the process-noise domain, k_case
 * the case's own calibration (the shared density_scale_file, or its
 * scalar density_scale), m = 1 when the scenario value is the median
 * of k exp(eta) and exp(-sigma^2 / 2) when it is the mean (for eta
 * normal, E[exp(eta)] = exp(sigma^2 / 2), the lognormal mean: Johnson,
 * Kotz & Balakrishnan, "Continuous Univariate Distributions" vol. 1,
 * 2nd ed., 1994, ch. 14). The drag force interpolates the table
 * linearly (spody_interpolate_density_scale): exact at the nodes, a
 * continuous force between them.
 *
 * With ap_doubling > 0 the sigma of node j is sigma_ln (1 + Ap_j /
 * ap_doubling), Ap_j the 3-hourly Ap of the bin holding the node in
 * the run's space-weather file (observed, or the file's forecast past
 * its last observed day): the density error variance scaled by the
 * current geomagnetic activity (Wright, AGI, "Real-time estimation of
 * local atmospheric density"; spody_gauss_markov_nodes with a scale),
 * and m becomes the per-node exp(-sigma_j^2 / 2). On success ds->mjd
 * and ds->k are heap arrays the caller frees. */
static int noise_density_table(const SpodyUqConfig *uq, const InputConfig *ci,
                               const SimulationShared *shared, int c,
                               MappedDensityScale *ds, SpodyError *err)
{
    const double dt = uq->pn_density_interval_s, dur = ci->duration_s;
    size_t n = (size_t)floor(dur / dt) + 1;
    if (dur - (double)(n - 1) * dt > 1.0e-9 * dt) ++n;
    const int by_ap = uq->pn_density_ap_doubling > 0.0;
    double *t  = (double *)malloc(n * sizeof *t);
    double *x  = (double *)malloc(n * sizeof *x);
    double *sc = by_ap ? (double *)malloc(n * sizeof *sc) : NULL;
    ds->mjd = (double *)malloc(n * sizeof *ds->mjd);
    ds->k   = (double *)malloc(n * sizeof *ds->k);
    ds->n   = n;
    int rc = SPODY_OK;
    if (!t || !x || (by_ap && !sc) || !ds->mjd || !ds->k) {
        spody_error_set(err, SPODY_ERR_INTERNAL, "out of memory for the density noise");
        rc = SPODY_ERR_INTERNAL;
        goto out;
    }
    for (size_t j = 0; j < n; ++j) t[j] = fmin((double)j * dt, dur);
    if (by_ap) {
        MappedSpaceWeather sw;
        spody_setup_MappedSpaceWeather(&sw, &shared->sw_data);
        for (size_t j = 0; j < n; ++j) {
            double ap[7];
            if (spody_space_weather_msis_inputs(&sw, ci->et_start_s + t[j],
                                                NULL, NULL, ap) != 0) {
                spody_error_set(err, SPODY_ERR_BAD_VALUE,
                        "density noise: no 3-hourly Ap at ET %.3f in the "
                        "space-weather file (ap_doubling needs it at every node)",
                        ci->et_start_s + t[j]);
                rc = SPODY_ERR_BAD_VALUE;
                goto out;
            }
            sc[j] = 1.0 + ap[1] / uq->pn_density_ap_doubling;
        }
    }
    SpodyRandomStream st;
    spody_random_stream_init_domain(&st, uq->seed, (uint64_t)c, pn_substream_density,
                                    SPODY_RANDOM_DOMAIN_PROCESS_NOISE);
    if (spody_gauss_markov_nodes(&st, uq->pn_density_sigma_ln, sc, uq->pn_density_tau_s,
                                 t, n, x) != 0) {
        spody_error_set(err, SPODY_ERR_INTERNAL, "density noise: bad node grid");
        rc = SPODY_ERR_INTERNAL;
        goto out;
    }
    for (size_t j = 0; j < n; ++j) {
        const double s  = by_ap ? uq->pn_density_sigma_ln * sc[j] : uq->pn_density_sigma_ln;
        const double m  = (uq->pn_density_value_is == SPODY_UQ_VALUE_IS_MEAN)
                        ? exp(-0.5 * s * s) : 1.0;
        const double et = ci->et_start_s + t[j];
        const double k0 = shared->init_ds
                        ? spody_interpolate_density_scale(&shared->ds_data, et)
                        : ci->density_scale;
        ds->mjd[j] = spody_et_to_mjd_utc(et);
        ds->k[j]   = k0 * exp(x[j]) * m;
        if (j > 0 && !(ds->mjd[j] > ds->mjd[j - 1])) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "density noise: nodes %g s apart are not increasing in "
                    "UTC (a leap second?); use a larger interval_s", dt);
            rc = SPODY_ERR_BAD_VALUE;
            goto out;
        }
    }
out:
    free(t);
    free(x);
    free(sc);
    if (rc != SPODY_OK) {
        free(ds->mjd);
        free(ds->k);
        ds->mjd = ds->k = NULL;
        ds->n = 0;
    }
    return rc;
}

/* Case c's RIC acceleration under process noise: nodes every dt =
 * the smaller interval_s of the two acceleration entries, from the
 * case start (the last one at its end); per axis k one Gauss-Markov
 * process (sigma_k, tau_k) on stream (seed, c, substream) of the
 * process-noise domain: pn_substream_accel for the constant part,
 * pn_substream_1rev_cos / _sin for the once-per-revolution
 * coefficients. An axis with sigma 0 stays exactly zero (its stream is
 * not read). The force (spody_force_empirical) interpolates the nodes
 * linearly in ET and applies them in the case's own RIC axes. On
 * success ea->et and the non-NULL columns are heap arrays the caller
 * frees. */
static int noise_accel_table(const SpodyUqConfig *uq, const InputConfig *ci, int c,
                             SpodyEmpiricalAccel *ea, SpodyError *err)
{
    double dt = INFINITY;
    if (uq->pn_accel) dt = uq->pn_accel_interval_s;
    if (uq->pn_1rev && uq->pn_1rev_interval_s < dt) dt = uq->pn_1rev_interval_s;
    const double dur = ci->duration_s;
    size_t n = (size_t)floor(dur / dt) + 1;
    if (dur - (double)(n - 1) * dt > 1.0e-9 * dt) ++n;
    double *t   = (double *)malloc(n * sizeof *t);
    double *x   = (double *)malloc(n * sizeof *x);
    double *et  = (double *)malloc(n * sizeof *et);
    double *col[3] = { NULL, NULL, NULL };            /* const, cos, sin */
    int rc = SPODY_OK;
    for (int m = 0; m < 3; ++m)
        if (m == 0 ? uq->pn_accel : uq->pn_1rev)
            col[m] = (double *)calloc(3 * n, sizeof(double));
    if (!t || !x || !et || (uq->pn_accel && !col[0])
        || (uq->pn_1rev && (!col[1] || !col[2]))) {
        spody_error_set(err, SPODY_ERR_INTERNAL, "out of memory for the acceleration noise");
        rc = SPODY_ERR_INTERNAL;
        goto out;
    }
    for (size_t j = 0; j < n; ++j) {
        t[j]  = fmin((double)j * dt, dur);
        et[j] = ci->et_start_s + t[j];
    }
    for (int m = 0; m < 3; ++m) {
        if (!col[m]) continue;
        const double *sig = m ? uq->pn_1rev_sigma_kms2 : uq->pn_accel_sigma_kms2;
        const double *tau = m ? uq->pn_1rev_tau_s : uq->pn_accel_tau_s;
        const uint64_t *sub = m == 0 ? pn_substream_accel
                            : m == 1 ? pn_substream_1rev_cos : pn_substream_1rev_sin;
        for (int k = 0; k < 3; ++k) {
            if (!(sig[k] > 0.0)) continue;
            SpodyRandomStream st;
            spody_random_stream_init_domain(&st, uq->seed, (uint64_t)c, sub[k],
                                            SPODY_RANDOM_DOMAIN_PROCESS_NOISE);
            if (spody_gauss_markov_nodes(&st, sig[k], NULL, tau[k], t, n, x) != 0) {
                spody_error_set(err, SPODY_ERR_INTERNAL, "acceleration noise: bad node grid");
                rc = SPODY_ERR_INTERNAL;
                goto out;
            }
            for (size_t j = 0; j < n; ++j) col[m][3 * j + k] = x[j];
        }
    }
out:
    free(t);
    free(x);
    if (rc != SPODY_OK) {
        free(et);
        for (int m = 0; m < 3; ++m) free(col[m]);
        et = NULL;
        col[0] = col[1] = col[2] = NULL;
        n = 0;
    }
    ea->et    = et;
    ea->a_ric = col[0];
    ea->a_cos = col[1];
    ea->a_sin = col[2];
    ea->n     = n;
    return rc;
}

/* Propagate one case configuration, writing its SPDYOUT_ trajectory to
 * bin_path when non-empty, its emitted states to *tr and its impacts
 * (plus the two life markers) to the shared events sink. A non-NULL
 * `ds` replaces the case's density calibration table, a non-NULL `ea`
 * adds an empirical RIC acceleration (process noise). */
static int run_case(const InputConfig *c, const SimulationShared *shared,
                    int case_idx, const char *bin_path, Track *tr,
                    FILE *events_fp, const MappedDensityScale *ds,
                    const SpodyEmpiricalAccel *ea, SpodyError *err)
{
    InputConfig ci = *c;
    snprintf(ci.bin_file, sizeof ci.bin_file, "%s", bin_path);
    SimulationWorker w;
    int rc = spody_build_worker(&ci, shared, &w, err);
    if (rc != SPODY_OK) return rc;
    if (ds) w.ctx.density_scale = ds;
    if (ea) w.ctx.empirical_accel = ea;
    w.state_sink      = track_sink;
    w.state_sink_user = tr;
    snprintf(w.log_prefix, sizeof w.log_prefix, "[case %d] ", case_idx);
    BatchEventSink sink = { events_fp, (int32_t)case_idx };
    tr->n   = 0;
    tr->oom = 0;
    rc = spody_run_simulation(&ci, &w, &sink, err);
    spody_free_worker(&w);
    if (rc == SPODY_OK && tr->oom) {
        spody_error_set(err, SPODY_ERR_INTERNAL, "out of memory storing a trajectory");
        rc = SPODY_ERR_INTERNAL;
    }
    return rc;
}

/* ---------------------------------------------------------------------
 * Statistics. Every case is folded in by Welford's update in case order
 * (1, 2, 3, ...), whatever thread propagated it: the sums run in one
 * fixed order, so the results are bit-identical for any thread count
 * and chunk size. Deviations d = x - x_n from the nominal, not absolute
 * states, so no digits are lost to 7000 km positions.
 * --------------------------------------------------------------------- */

/* Running moments at one output epoch of the nominal. */
typedef struct {
    int    n;            /* cases with a state at this epoch           */
    double mean[6];      /* bias b, ICRF                               */
    double m2[21];       /* sum of products about the mean, lower-tri  */
    double cmean[3];     /* curvilinear position bias (R, I, C)        */
    double cm2[6];
} Moments;

/* The nominal's axes at one epoch, for the curvilinear coordinates. */
typedef struct {
    int    ok;           /* 0 = RIC undefined (r = 0 or r parallel to v) */
    double R[3][3];      /* ICRF -> RIC, rows r_hat, i_hat, c_hat       */
    double rn;           /* |r_n|                                       */
} EpochFrame;

/* Welford's update of a dim-vector mean and its lower-triangular
 * product sums, n = the count including x. */
static void welford_add(int n, int dim, double *mean, double *m2, const double *x)
{
    double dl[6];
    for (int a = 0; a < dim; ++a) {
        dl[a] = x[a] - mean[a];
        mean[a] += dl[a] / n;
    }
    for (int a = 0, k = 0; a < dim; ++a)
        for (int b = 0; b <= a; ++b, ++k)
            m2[k] += dl[a] * (x[b] - mean[b]);
}

/* Curvilinear position deviation of r from the nominal (Vallado &
 * Alfano): radius difference, arc in the nominal plane, arc out of it. */
static void curvilinear(const EpochFrame *f, const double r[3], double out[3])
{
    double pr = spody_dot3(f->R[0], r);
    double pi = spody_dot3(f->R[1], r);
    double pc = spody_dot3(f->R[2], r);
    double rr = sqrt(spody_dot3(r, r));
    out[0] = rr - f->rn;
    out[1] = f->rn * atan2(pi, pr);
    out[2] = f->rn * asin(pc / rr);
}

/* What the cases are folded into. */
typedef struct {
    const Track *nominal;
    EpochFrame  *frame;          /* per epoch                               */
    Moments     *mom;            /* per epoch                               */
    int         *epoch_snap;     /* per epoch: snapshot index, or -1        */
    double      *cloud;          /* [snapshot][case slot][8]                */
    int         *cloud_n;        /* per snapshot: rows filled               */
    int          samples;
} Stats;

/* Fold case c in. Its records match the nominal epochs one by one while
 * the times are identical; the first mismatch (its impact, off the grid)
 * or the end of its records ends it: it counts at no later epoch. */
static void stats_add_case(Stats *st, int c, const Track *tr)
{
    const Track *nom = st->nominal;
    size_t j = 0;
    for (size_t e = 0; e < nom->n; ++e) {
        if (j >= tr->n || tr->t[j] != nom->t[e]) break;
        const double *x = tr->y + 6 * j, *xn = nom->y + 6 * e;
        double d[6];
        for (int k = 0; k < 6; ++k) d[k] = x[k] - xn[k];
        Moments *m = &st->mom[e];
        ++m->n;
        welford_add(m->n, 6, m->mean, m->m2, d);
        if (st->frame[e].ok) {
            double cv[3];
            curvilinear(&st->frame[e], x, cv);
            welford_add(m->n, 3, m->cmean, m->cm2, cv);
        }
        int s = st->epoch_snap[e];
        if (s >= 0) {
            double *row = st->cloud + ((size_t)s * st->samples + st->cloud_n[s]++) * 8;
            row[0] = nom->t[e];
            row[1] = (double)c;
            memcpy(row + 2, d, sizeof d);
        }
        ++j;
    }
}

/* Close a written file, the error flag included (buffered writes
 * surface only there). */
static int close_checked(FILE *fp, const char *path, SpodyError *err)
{
    int bad = ferror(fp);
    if (fclose(fp) != 0 || bad) {
        spody_error_set(err, SPODY_ERR_IO,
                "write failed on '%s': the file is incomplete", path);
        return SPODY_ERR_IO;
    }
    return SPODY_OK;
}

/* The 24-byte preamble shared by every SpOdy binary. */
static int write_preamble(FILE *fp, const char magic[8], uint32_t record_bytes,
                          uint32_t reserved1, uint32_t reserved2)
{
    uint32_t hdr[4] = { 1u, record_bytes, reserved1, reserved2 };
    return (fwrite(magic, 1, 8, fp) == 8 && fwrite(hdr, sizeof hdr[0], 4, fp) == 4)
           ? 0 : -1;
}

/* SPDYUQM_ v1: one 44-double record per nominal epoch. Undefined fields
 * are NaN: everything but t, n and x_n when n = 0, the covariances when
 * n = 1, the curvilinear part where the nominal's RIC is undefined. */
enum { UQM_DOUBLES = 44, UQC_DOUBLES = 8 };

static int write_moments(const Stats *st, const char *path, SpodyError *err)
{
    FILE *fp = fopen(path, "wb");
    if (!fp) {
        spody_error_set(err, SPODY_ERR_IO, "cannot open '%s' for write", path);
        return SPODY_ERR_IO;
    }
    setvbuf(fp, NULL, _IOFBF, 1u << 20);
    write_preamble(fp, "SPDYUQM_", UQM_DOUBLES * sizeof(double),
                   (uint32_t)st->samples, 0u);
    for (size_t e = 0; e < st->nominal->n; ++e) {
        const Moments *m = &st->mom[e];
        double rec[UQM_DOUBLES];
        rec[0] = st->nominal->t[e];
        rec[1] = (double)m->n;
        memcpy(rec + 2, st->nominal->y + 6 * e, 6 * sizeof(double));
        for (int k = 8; k < UQM_DOUBLES; ++k) rec[k] = NAN;
        if (m->n >= 1) {
            memcpy(rec + 8, m->mean, 6 * sizeof(double));
            if (st->frame[e].ok) memcpy(rec + 35, m->cmean, 3 * sizeof(double));
        }
        if (m->n >= 2) {
            for (int k = 0; k < 21; ++k) rec[14 + k] = m->m2[k] / (m->n - 1);
            if (st->frame[e].ok)
                for (int k = 0; k < 6; ++k) rec[38 + k] = m->cm2[k] / (m->n - 1);
        }
        fwrite(rec, sizeof(double), UQM_DOUBLES, fp);
    }
    return close_checked(fp, path, err);
}

/* SPDYUQC_ v1: per snapshot, one record per case with a state there
 * (t, case, d[6] ICRF), snapshot-major then case order. */
static int write_clouds(const Stats *st, int n_snapshots, const char *path,
                        SpodyError *err)
{
    FILE *fp = fopen(path, "wb");
    if (!fp) {
        spody_error_set(err, SPODY_ERR_IO, "cannot open '%s' for write", path);
        return SPODY_ERR_IO;
    }
    setvbuf(fp, NULL, _IOFBF, 1u << 20);
    write_preamble(fp, "SPDYUQC_", UQC_DOUBLES * sizeof(double),
                   (uint32_t)st->samples, (uint32_t)n_snapshots);
    for (int s = 0; s < n_snapshots; ++s)
        fwrite(st->cloud + (size_t)s * st->samples * UQC_DOUBLES, sizeof(double),
               (size_t)st->cloud_n[s] * UQC_DOUBLES, fp);
    return close_checked(fp, path, err);
}

/* <name>_sigma.uq.csv: the moments in the nominal's RIC axes (rotation
 * only), read as numbers. */
static int write_sigma_csv(const Stats *st, const char *name, const char *path,
                           SpodyError *err)
{
    FILE *fp = fopen(path, "w");
    if (!fp) {
        spody_error_set(err, SPODY_ERR_IO, "cannot open '%s' for write", path);
        return SPODY_ERR_IO;
    }
    setvbuf(fp, NULL, _IOFBF, 1u << 20);
    fprintf(fp, "# spody uncertainty montecarlo: %s, %d dispersed cases\n",
            name, st->samples);
    fprintf(fp, "# RIC axes of the nominal at each epoch (rotation only). sig_*: "
                "standard deviation about the mean; bias_*: mean minus nominal; "
                "rms_*: root mean square about the nominal; curv_*: curvilinear "
                "position (radius, in-plane arc, out-of-plane arc). n = cases "
                "with a state at t. Empty = undefined (n < 2).\n");
    fprintf(fp, "t_s,n,sig_R_km,sig_I_km,sig_C_km,sig_vR_kms,sig_vI_kms,sig_vC_kms,"
                "corr_RI,corr_RC,corr_IC,bias_R_km,bias_I_km,bias_C_km,"
                "rms_R_km,rms_I_km,rms_C_km,curv_sig_R_km,curv_sig_I_km,"
                "curv_sig_C_km,curv_bias_R_km,curv_bias_I_km,curv_bias_C_km\n");
    for (size_t e = 0; e < st->nominal->n; ++e) {
        const Moments *m = &st->mom[e];
        const EpochFrame *f = &st->frame[e];
        fprintf(fp, "%.15e,%d", st->nominal->t[e], m->n);
        if (m->n < 2 || !f->ok) {
            fprintf(fp, ",,,,,,,,,,,,,,,,,,,,,\n");
            continue;
        }
        double C[6][6];
        for (int a = 0, k = 0; a < 6; ++a)
            for (int b = 0; b <= a; ++b, ++k)
                C[a][b] = C[b][a] = m->m2[k] / (m->n - 1);
        /* Position and velocity blocks rotated: S = R C R^T. */
        double S[2][3][3];
        for (int blk = 0; blk < 2; ++blk)
            for (int i = 0; i < 3; ++i)
                for (int j = 0; j < 3; ++j) {
                    double acc = 0.0;
                    for (int a = 0; a < 3; ++a)
                        for (int b = 0; b < 3; ++b)
                            acc += f->R[i][a] * C[3 * blk + a][3 * blk + b] * f->R[j][b];
                    S[blk][i][j] = acc;
                }
        double bric[3];
        spody_rotate_vector(f->R, m->mean, bric);
        double sg[3];
        for (int i = 0; i < 3; ++i) sg[i] = sqrt(S[0][i][i]);
        for (int i = 0; i < 3; ++i) fprintf(fp, ",%.15e", sg[i]);
        for (int i = 0; i < 3; ++i) fprintf(fp, ",%.15e", sqrt(S[1][i][i]));
        fprintf(fp, ",%.15e,%.15e,%.15e", S[0][1][0] / (sg[0] * sg[1]),
                S[0][2][0] / (sg[0] * sg[2]), S[0][2][1] / (sg[1] * sg[2]));
        for (int i = 0; i < 3; ++i) fprintf(fp, ",%.15e", bric[i]);
        double w = (double)(m->n - 1) / m->n;
        for (int i = 0; i < 3; ++i)
            fprintf(fp, ",%.15e", sqrt(S[0][i][i] * w + bric[i] * bric[i]));
        static const int diag3[3] = { 0, 2, 5 };
        for (int i = 0; i < 3; ++i)
            fprintf(fp, ",%.15e", sqrt(m->cm2[diag3[i]] / (m->n - 1)));
        for (int i = 0; i < 3; ++i) fprintf(fp, ",%.15e", m->cmean[i]);
        fprintf(fp, "\n");
    }
    return close_checked(fp, path, err);
}

/* Impacts among the dispersed cases, read back from the events file,
 * and the line that says how far the count can be trusted: Wilson's 95 %
 * interval for 0 < k < N, the exact one-sided bound 1 - 0.05^(1/N) for
 * k = 0 (about 3/N) and its mirror for k = N. */
static int report_impacts(const char *events_path, int samples, SpodyError *err)
{
    FILE *fp = fopen(events_path, "rb");
    if (!fp || fseek(fp, 24, SEEK_SET) != 0) {
        if (fp) fclose(fp);
        spody_error_set(err, SPODY_ERR_IO, "cannot read back '%s'", events_path);
        return SPODY_ERR_IO;
    }
    double *t_imp = (double *)malloc(((size_t)samples + 1) * sizeof *t_imp);
    if (!t_imp) {
        fclose(fp);
        spody_error_set(err, SPODY_ERR_INTERNAL, "out of memory reading impacts");
        return SPODY_ERR_INTERNAL;
    }
    for (int c = 0; c <= samples; ++c) t_imp[c] = NAN;
    BatchEventRecord r;
    while (fread(&r, sizeof r, 1, fp) == 1)
        if (r.ev.kind == SPODY_EVENT_KIND_IMPACT && r.case_idx >= 0
            && r.case_idx <= samples)
            t_imp[r.case_idx] = r.ev.t;
    fclose(fp);

    if (!isnan(t_imp[0]))
        spody_log_printf("  nominal   : impact at t = %.6f s; the statistics stop there\n",
                         t_imp[0]);
    int k = 0;
    for (int c = 1; c <= samples; ++c) k += !isnan(t_imp[c]);
    const double alpha = 0.05;                       /* 95 % confidence */
    const double z     = -spody_normal_quantile(alpha / 2.0);
    const double N     = (double)samples;
    if (k == 0) {
        spody_log_printf("  impacts   : 0 of %d cases, probability < %.2f %% at 95 %%\n",
                         samples, 100.0 * (1.0 - pow(alpha, 1.0 / N)));
    } else if (k == samples) {
        spody_log_printf("  impacts   : %d of %d cases, probability > %.2f %% at 95 %%\n",
                         k, samples, 100.0 * pow(alpha, 1.0 / N));
    } else {
        double p = k / N, d = 1.0 + z * z / N;
        double centre = (p + z * z / (2.0 * N)) / d;
        double half   = z * sqrt(p * (1.0 - p) / N + z * z / (4.0 * N * N)) / d;
        spody_log_printf("  impacts   : %d of %d cases (%.2f %%), 95 %% interval "
                         "%.2f %% .. %.2f %% (Wilson)\n", k, samples, 100.0 * p,
                         100.0 * (centre - half), 100.0 * (centre + half));
        int listed = 0;
        spody_log_printf("  impacted  :");
        for (int c = 1; c <= samples && listed < MAX_LISTED_BAD; ++c)
            if (!isnan(t_imp[c])) {
                spody_log_printf("%s case %d at t = %.3f s", listed ? "," : "",
                                 c, t_imp[c]);
                ++listed;
            }
        if (k > listed) spody_log_printf(" and %d more", k - listed);
        spody_log_printf("\n");
    }
    free(t_imp);
    return SPODY_OK;
}

/* A snapshot time matches a nominal epoch within this relative
 * tolerance, the one the validator used to accept it on the grid. */
static const double snap_rel_tol = 1.0e-9;

/* Memory the trajectories of one chunk of cases may hold at once. */
static const size_t chunk_budget_bytes = (size_t)512 << 20;

/* Propagate the nominal and the dispersed cases of the stripped scenario
 * *sc, fold them into the statistics in case order and write the
 * outputs into run_dir. */
static int run_montecarlo(const Sampler *s, const InputConfig *sc,
                          const SimulationShared *shared, const char *run_dir,
                          int n_threads, SpodyError *err)
{
    const SpodyUqConfig *uq = s->uq;
    const int N = uq->samples;
    char base[SPODY_MAX_SIM_NAME + 32], path[SPODY_MAX_PATH];
    char ev_path[SPODY_MAX_PATH];
    int  rc = SPODY_OK;
    FILE *ev_fp = NULL;
    Track nominal = { 0 };
    Track *tr = NULL;
    int   *case_rc = NULL;
    int    chunk = 0;
    Stats st;
    memset(&st, 0, sizeof st);

    /* Every case's final values and run window, before any case runs: a
     * case that cannot run would bias the statistics, so the run stops
     * here naming them rather than skipping them. */
    {
        int n_bad = 0;
        char list[512] = "";
        size_t k = 0;
        for (int c = 1; c <= N; ++c) {
            InputConfig ci;
            SpodyError  e;
            case_config(s, sc, c, &ci);
            if (spody_check_case(&ci, shared, &e) == SPODY_OK) continue;
            if (n_bad++ < MAX_LISTED_BAD && k < sizeof list)
                k += (size_t)snprintf(list + k, sizeof list - k,
                                      "%scase %d: %s", n_bad > 1 ? "; " : "", c, e.msg);
        }
        if (n_bad) {
            spody_error_set(err, SPODY_ERR_BAD_VALUE,
                    "%d of %d cases cannot run (%s%s); a skipped case would bias "
                    "the statistics", n_bad, N, list,
                    n_bad > MAX_LISTED_BAD ? "; ..." : "");
            return SPODY_ERR_BAD_VALUE;
        }
    }

    /* Impacts of every case, with case 0 the nominal, in one SPDYEVTB. */
    snprintf(base, sizeof base, "%s_events.uq.bin", uq->name);
    spody_io_run_subdir_filepath(run_dir, base, ev_path, sizeof ev_path);
    if (spody_open_batch_events(ev_path, &ev_fp) != 0) {
        spody_error_set(err, SPODY_ERR_IO, "cannot open '%s'", ev_path);
        return SPODY_ERR_IO;
    }

    /* Case 0, the nominal: the scenario as it is, kept in memory (every
     * deviation refers to it) and written as SPDYOUT_. */
    snprintf(base, sizeof base, "%s_nominal.uq.bin", uq->name);
    spody_io_run_subdir_filepath(run_dir, base, path, sizeof path);
    if ((rc = run_case(sc, shared, 0, path, &nominal, ev_fp, NULL, NULL, err)) != SPODY_OK)
        goto out;
    spody_log_printf("  nominal   : %s (%zu records, t = %.6g .. %.6g s)\n",
                     path, nominal.n, nominal.t[0], nominal.t[nominal.n - 1]);

    /* Statistics: one Moments and one RIC frame per nominal epoch, the
     * snapshot epochs, the cloud rows. */
    const size_t K = nominal.n;
    st.nominal    = &nominal;
    st.samples    = N;
    st.mom        = (Moments *)calloc(K, sizeof *st.mom);
    st.frame      = (EpochFrame *)calloc(K, sizeof *st.frame);
    st.epoch_snap = (int *)malloc(K * sizeof *st.epoch_snap);
    st.cloud_n    = (int *)calloc((size_t)uq->n_snapshots + 1, sizeof *st.cloud_n);
    st.cloud      = (double *)malloc(((size_t)uq->n_snapshots * N + 1)
                                     * UQC_DOUBLES * sizeof *st.cloud);
    if (!st.mom || !st.frame || !st.epoch_snap || !st.cloud_n || !st.cloud) {
        spody_error_set(err, SPODY_ERR_INTERNAL, "out of memory for the statistics");
        rc = SPODY_ERR_INTERNAL;
        goto out;
    }
    for (size_t e = 0; e < K; ++e) {
        const double *xn = nominal.y + 6 * e;
        EpochFrame *f = &st.frame[e];
        f->ok = spody_getrotmatrix_icrf2ric(xn, xn + 3, f->R) == 0;
        f->rn = sqrt(spody_dot3(xn, xn));
        st.epoch_snap[e] = -1;
    }
    for (int i = 0; i < uq->n_snapshots; ++i) {
        double sn = uq->snapshots_s[i], tol = snap_rel_tol * fmax(1.0, fabs(sn));
        for (size_t e = 0; e < K; ++e)
            if (fabs(nominal.t[e] - sn) <= tol) { st.epoch_snap[e] = i; break; }
    }

    /* Chunks of cases: propagated in parallel, folded in case order. */
    size_t per_case = (K + 2) * 7 * sizeof(double);
    chunk = 8 * n_threads;
    if ((size_t)chunk * per_case > chunk_budget_bytes)
        chunk = (int)(chunk_budget_bytes / per_case);
    if (chunk < n_threads) chunk = n_threads;
    if (chunk > N) chunk = N;
    tr      = (Track *)calloc((size_t)chunk, sizeof *tr);
    case_rc = (int *)calloc((size_t)chunk, sizeof *case_rc);
    if (!tr || !case_rc) {
        spody_error_set(err, SPODY_ERR_INTERNAL, "out of memory for the cases");
        rc = SPODY_ERR_INTERNAL;
        goto out;
    }
    int width = snprintf(NULL, 0, "%d", N);
    spody_log_printf("  cases     : %d in chunks of %d on %d thread%s\n", N, chunk,
                     n_threads, n_threads == 1 ? "" : "s");
#ifdef SPODY_HAVE_OPENMP
    double t_start = omp_get_wtime();
#else
    double t_start = (double)clock() / CLOCKS_PER_SEC;
#endif
    for (int c0 = 1; c0 <= N; c0 += chunk) {
        int m = (N - c0 + 1 < chunk) ? N - c0 + 1 : chunk;
        int i;
#ifdef SPODY_HAVE_OPENMP
        #pragma omp parallel for schedule(dynamic, 1) num_threads(n_threads)
#endif
        for (i = 0; i < m; ++i) {
            int c = c0 + i;
            InputConfig ci;
            SpodyError  e;
            char cpath[SPODY_MAX_PATH] = "", cname[SPODY_MAX_SIM_NAME + 32];
            case_config(s, sc, c, &ci);
            if (uq->case_outputs) {
                snprintf(cname, sizeof cname, "%s_case%0*d.uq.bin", uq->name, width, c);
                spody_io_run_subdir_filepath(run_dir, cname, cpath, sizeof cpath);
            }
            MappedDensityScale  nds = { NULL, NULL, 0 };
            SpodyEmpiricalAccel ea  = { NULL, NULL, 0, NULL, NULL };
            const int with_accel = uq->pn_accel || uq->pn_1rev;
            case_rc[i] = uq->pn_density
                       ? noise_density_table(uq, &ci, shared, c, &nds, &e) : SPODY_OK;
            if (case_rc[i] == SPODY_OK && with_accel)
                case_rc[i] = noise_accel_table(uq, &ci, c, &ea, &e);
            if (case_rc[i] == SPODY_OK)
                case_rc[i] = run_case(&ci, shared, c, cpath, &tr[i], ev_fp,
                                      uq->pn_density ? &nds : NULL,
                                      with_accel ? &ea : NULL, &e);
            free(nds.mjd);
            free(nds.k);
            free((void *)ea.et);
            free((void *)ea.a_ric);
            free((void *)ea.a_cos);
            free((void *)ea.a_sin);
            if (case_rc[i] != SPODY_OK) {
#ifdef SPODY_HAVE_OPENMP
                #pragma omp critical(log)
#endif
                spody_log_printf("  [case %d] FAILED: %s\n", c, e.msg);
            }
        }
        for (i = 0; i < m; ++i) {
            if (case_rc[i] != SPODY_OK) {
                spody_error_set(err, SPODY_ERR_INTERNAL,
                        "case %d failed to propagate (message above); the "
                        "statistics would be incomplete, so the run stops", c0 + i);
                rc = SPODY_ERR_INTERNAL;
                goto out;
            }
        }
        for (i = 0; i < m; ++i) stats_add_case(&st, c0 + i, &tr[i]);
#ifdef SPODY_HAVE_OPENMP
        double t_now = omp_get_wtime();
#else
        double t_now = (double)clock() / CLOCKS_PER_SEC;
#endif
        spody_log_printf("  cases     : %d / %d done (%.1f s)\n", c0 + m - 1, N,
                         t_now - t_start);
    }

    rc = close_checked(ev_fp, ev_path, err);
    ev_fp = NULL;
    if (rc != SPODY_OK) goto out;

    snprintf(base, sizeof base, "%s_moments.uq.bin", uq->name);
    spody_io_run_subdir_filepath(run_dir, base, path, sizeof path);
    if ((rc = write_moments(&st, path, err)) != SPODY_OK) goto out;
    spody_log_printf("  moments   : %s\n", path);
    if (uq->n_snapshots > 0) {
        snprintf(base, sizeof base, "%s_clouds.uq.bin", uq->name);
        spody_io_run_subdir_filepath(run_dir, base, path, sizeof path);
        if ((rc = write_clouds(&st, uq->n_snapshots, path, err)) != SPODY_OK) goto out;
        spody_log_printf("  clouds    : %s\n", path);
    }
    snprintf(base, sizeof base, "%s_sigma.uq.csv", uq->name);
    spody_io_run_subdir_filepath(run_dir, base, path, sizeof path);
    if ((rc = write_sigma_csv(&st, uq->name, path, err)) != SPODY_OK) goto out;
    spody_log_printf("  sigma     : %s\n", path);
    spody_log_printf("  events    : %s\n", ev_path);
    rc = report_impacts(ev_path, N, err);

out:
    if (ev_fp) fclose(ev_fp);
    if (tr) for (int i = 0; i < chunk; ++i) { free(tr[i].t); free(tr[i].y); }
    free(tr);
    free(case_rc);
    free(st.mom);
    free(st.frame);
    free(st.epoch_snap);
    free(st.cloud_n);
    free(st.cloud);
    free(nominal.t);
    free(nominal.y);
    return rc;
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

    /* Run folder and log first, so the log holds the whole run, a
     * refusal below included. */
    char run_dir[SPODY_MAX_PATH], path[SPODY_MAX_PATH], base[SPODY_MAX_SIM_NAME + 32];
    if (spody_io_make_run_subdir(out_dir, run_dir, sizeof run_dir, &err) != SPODY_OK) {
        spody_error_print(&err);
        goto done;
    }
    snprintf(base, sizeof base, "%s.uq.log", uq.name);
    spody_io_run_subdir_filepath(run_dir, base, path, sizeof path);
    if (spody_log_open_mirror(path) != 0) {
        spody_error_set(&err, SPODY_ERR_IO, "cannot open the run log '%s'", path);
        spody_error_print(&err);
        goto done;
    }
    print_summary(&uq, &sc, out_dir);
    spody_log_printf("  run folder: %s\n", run_dir);
    spody_input_warn_deprecated(&sc);
    if (!samples_only) strip_scenario(&sc);

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
    if (samples_only) { rc = 0; goto done; }

    /* Thread count: capped at omp_get_max_threads, refused above 1 on a
     * build without OpenMP (same rule as spody batch). */
    int n_threads = uq.thread_number;
#ifdef SPODY_HAVE_OPENMP
    if (n_threads > omp_get_max_threads()) {
        spody_log_printf("  threads   : %d requested, capped at %d "
                         "(omp_get_max_threads)\n", n_threads, omp_get_max_threads());
        n_threads = omp_get_max_threads();
    }
#else
    if (n_threads != 1) {
        spody_error_set(&err, SPODY_ERR_BAD_VALUE,
                "montecarlo.thread_number = %d: this binary was built without "
                "OpenMP; use thread_number = 1", n_threads);
        snprintf(err.file, sizeof err.file, "%s", uq.path);
        spody_error_print(&err);
        goto done;
    }
#endif
    if (run_montecarlo(&s, &sc, &shared, run_dir, n_threads, &err) != SPODY_OK) {
        spody_error_print(&err);
        goto done;
    }
    rc = 0;
done:
    spody_log_close_mirror();
    spody_free_shared(&shared);
    spody_free_input(&sc);
    return rc;
}
