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

int spody_io_prepare_batch_subdir(const char *output_dir,
                                  char *batch_subdir_out, size_t out_sz,
                                  SpodyError *err) {
    snprintf(batch_subdir_out, out_sz, "%s/batch", output_dir);
    if (SPODY_MKDIR(batch_subdir_out) == 0) return SPODY_OK;
    if (errno == EEXIST) return SPODY_OK;
    spody_error_set(err, SPODY_ERR_IO,
            "cannot create batch output dir '%s' (errno %d): "
            "verify that output_dir '%s' exists",
            batch_subdir_out, errno, output_dir);
    return SPODY_ERR_IO;
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
    time_t now = time(NULL);
    struct tm *tm = gmtime(&now);
    char ts[32];
    strftime(ts, sizeof ts, "%Y-%m-%dT%H%M%SZ", tm);
    snprintf(out, out_sz, "%s/%s_%s.log", batch_subdir, batch->name, ts);
}

void spody_io_case_output_paths(InputConfig *cfg, const BatchConfig *batch,
                                const char *batch_subdir, int case_idx) {
    const char *id = batch->case_ids[case_idx];
    if (cfg->csv_file[0]) {
        snprintf(cfg->csv_file, sizeof cfg->csv_file, "%s/%s_%s.csv",
                 batch_subdir, batch->name, id);
    }
    if (cfg->bin_file[0]) {
        snprintf(cfg->bin_file, sizeof cfg->bin_file, "%s/%s_%s.bin",
                 batch_subdir, batch->name, id);
    }
    if (cfg->accelerations_file[0]) {
        snprintf(cfg->accelerations_file, sizeof cfg->accelerations_file,
                 "%s/%s_%s_acc.bin", batch_subdir, batch->name, id);
    }
    if (cfg->events_log[0]) {
        snprintf(cfg->events_log, sizeof cfg->events_log,
                 "%s/%s_%s_events.bin", batch_subdir, batch->name, id);
    }
}
