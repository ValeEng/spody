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
 * SpOdy app I/O helpers -- see app_io.h for the contract.
 */
#include "app_io.h"

#include <errno.h>
#include <stdio.h>
#include <time.h>

#ifdef _WIN32
#  include <direct.h>
#  define SPODY_MKDIR(p) _mkdir(p)
#else
#  include <sys/stat.h>
#  define SPODY_MKDIR(p) mkdir((p), 0755)
#endif

const char *spody_io_basename(const char *path) {
    const char *last = path;
    for (const char *p = path; *p; ++p) {
        if (*p == '/' || *p == '\\') last = p + 1;
    }
    return last;
}

int spody_io_make_run_subdir(const char *output_dir,
                             char *run_subdir_out, size_t out_sz,
                             SpodyError *err) {
    /* Auto-create the parent output_dir (single level) so the user
     * does not have to mkdir it by hand before the first run. EEXIST
     * is the common case (subsequent runs) -- ignored. Anything else
     * we report directly because the inner mkdir would fail with a
     * less helpful message otherwise. */
    if (SPODY_MKDIR(output_dir) != 0 && errno != EEXIST) {
        spody_error_set(err, SPODY_ERR_IO,
                "cannot create output_dir '%s' (errno %d)",
                output_dir, errno);
        return SPODY_ERR_IO;
    }

    time_t now = time(NULL);
    struct tm *tm = gmtime(&now);
    char ts[32];
    strftime(ts, sizeof ts, "%Y-%m-%dT%H%M%SZ", tm);
    snprintf(run_subdir_out, out_sz, "%s/%s", output_dir, ts);

    if (SPODY_MKDIR(run_subdir_out) == 0) return SPODY_OK;
    if (errno == EEXIST) return SPODY_OK;   /* second-precision collision */
    spody_error_set(err, SPODY_ERR_IO,
            "cannot create run output dir '%s' (errno %d)",
            run_subdir_out, errno);
    return SPODY_ERR_IO;
}

void spody_io_run_subdir_filepath(const char *run_subdir,
                                  const char *basename,
                                  char *out, size_t out_sz) {
    /* `ts` is just the trailing component of run_subdir -- the
     * timestamp spody_io_make_run_subdir wrote there. */
    const char *ts = spody_io_basename(run_subdir);
    snprintf(out, out_sz, "%s/%s_%s", run_subdir, ts, basename);
}

int spody_io_copy_file(const char *src, const char *dst, SpodyError *err) {
    FILE *fin = fopen(src, "rb");
    if (!fin) {
        spody_error_set(err, SPODY_ERR_IO,
                "cannot open source for copy: '%s' (errno %d)", src, errno);
        return SPODY_ERR_IO;
    }
    FILE *fout = fopen(dst, "wb");
    if (!fout) {
        spody_error_set(err, SPODY_ERR_IO,
                "cannot open dest for copy: '%s' (errno %d)", dst, errno);
        fclose(fin);
        return SPODY_ERR_IO;
    }
    /* 64 KB chunks: small enough to live on the stack, large enough
     * that the per-iteration overhead is negligible for typical TOMLs
     * (a few KB). */
    char buf[65536];
    size_t n;
    int rc = SPODY_OK;
    while ((n = fread(buf, 1, sizeof buf, fin)) > 0) {
        if (fwrite(buf, 1, n, fout) != n) {
            spody_error_set(err, SPODY_ERR_IO,
                    "write failed during copy to '%s' (errno %d)", dst, errno);
            rc = SPODY_ERR_IO;
            break;
        }
    }
    if (rc == SPODY_OK && ferror(fin)) {
        spody_error_set(err, SPODY_ERR_IO,
                "read failed during copy of '%s' (errno %d)", src, errno);
        rc = SPODY_ERR_IO;
    }
    fclose(fout);
    fclose(fin);
    return rc;
}

/* Helper for spody_io_rewrite_outputs_to_run_subdir: replace path's
 * directory with run_subdir AND prefix its basename with the run's
 * timestamp via spody_io_run_subdir_filepath. In-place rewrite into
 * the same fixed-size buffer so callers don't reallocate. */
static void rewrite_one_path(char *path, size_t path_sz,
                             const char *run_subdir) {
    if (!path[0]) return;
    const char *bn = spody_io_basename(path);
    char tmp[SPODY_MAX_PATH];
    spody_io_run_subdir_filepath(run_subdir, bn, tmp, sizeof tmp);
    snprintf(path, path_sz, "%s", tmp);
}

void spody_io_rewrite_outputs_to_run_subdir(InputConfig *cfg,
                                            const char *run_subdir) {
    rewrite_one_path(cfg->csv_file,           sizeof cfg->csv_file,           run_subdir);
    rewrite_one_path(cfg->bin_file,           sizeof cfg->bin_file,           run_subdir);
    rewrite_one_path(cfg->log_file,           sizeof cfg->log_file,           run_subdir);
    rewrite_one_path(cfg->accelerations_file, sizeof cfg->accelerations_file, run_subdir);
    rewrite_one_path(cfg->events_log,         sizeof cfg->events_log,         run_subdir);
}

void spody_io_timestamp_filename(const char *base, char *out, size_t out_sz) {
    time_t now = time(NULL);
    struct tm *tm = gmtime(&now);
    char ts[32];
    strftime(ts, sizeof ts, "%Y-%m-%dT%H%M%SZ", tm);

    const char *dot = NULL;
    for (const char *p = base; *p; ++p) {
        if (*p == '/' || *p == '\\') dot = NULL;
        else if (*p == '.')          dot = p;
    }
    if (!dot || dot == base) {
        snprintf(out, out_sz, "%s_%s", base, ts);
    } else {
        size_t len = (size_t)(dot - base);
        snprintf(out, out_sz, "%.*s_%s%s", (int)len, base, ts, dot);
    }
}

void spody_io_batch_log_path(const BatchConfig *batch,
                             const char *batch_subdir,
                             char *out, size_t out_sz) {
    /* Reuse the run-folder's own timestamp (its trailing path
     * component) instead of re-sampling time(NULL), so the log
     * filename matches exactly the run dir it lives in. */
    const char *ts = spody_io_basename(batch_subdir);
    snprintf(out, out_sz, "%s/%s_%s.log", batch_subdir, ts, batch->name);
}

void spody_io_case_output_paths(InputConfig *cfg, const BatchConfig *batch,
                                const char *batch_subdir, int case_idx) {
    /* Per-case file names follow the same `<subject>_<frame>` pattern as
     * the GUI's auto-naming for single-propagate runs, with the case id
     * inserted between batch name and subject so the directory listing
     * groups by case visually. Every name is prefixed with the run
     * folder's timestamp -- consistent with the single-propagate
     * rewrite (see `rewrite_one_path`) so editor-side tools cannot
     * accidentally treat snapshots as sources. Events are aggregated
     * batch-wide (see cmd_batch) and so don't appear here. */
    const char *id = batch->case_ids[case_idx];
    const char *ts = spody_io_basename(batch_subdir);
    if (cfg->csv_file[0]) {
        snprintf(cfg->csv_file, sizeof cfg->csv_file,
                 "%s/%s_%s_%s_state_icrf.csv", batch_subdir, ts, batch->name, id);
    }
    if (cfg->bin_file[0]) {
        snprintf(cfg->bin_file, sizeof cfg->bin_file,
                 "%s/%s_%s_%s_state_icrf.bin", batch_subdir, ts, batch->name, id);
    }
    if (cfg->accelerations_file[0]) {
        snprintf(cfg->accelerations_file, sizeof cfg->accelerations_file,
                 "%s/%s_%s_%s_acc_icrf.bin", batch_subdir, ts, batch->name, id);
    }
    if (cfg->events_log[0]) {
        snprintf(cfg->events_log, sizeof cfg->events_log,
                 "%s/%s_%s_%s_events.bin", batch_subdir, ts, batch->name, id);
    }
}
