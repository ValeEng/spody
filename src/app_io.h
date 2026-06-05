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
 * SpOdy app I/O helpers: filesystem and output-path plumbing shared by
 * the CLI handlers.
 *
 * Everything here is about WHERE output goes and HOW its name is built:
 *   - basename extraction (separator-agnostic),
 *   - creating the per-batch output subdirectory,
 *   - injecting a UTC timestamp into a log filename,
 *   - composing the per-case / batch-log output paths.
 *
 * No simulation logic lives here -- just path strings and directories.
 * Errors are reported through the shared SpodyError type.
 */
#ifndef SPODY_APP_IO_H
#define SPODY_APP_IO_H

#include <stddef.h>

#include "app_diagnostics.h"   /* SpodyError */
#include "toml_input.h"        /* InputConfig, BatchConfig */

#ifdef __cplusplus
extern "C" {
#endif

/* Strip the directory portion of `path`, keep only the trailing filename
 * component. Handles both '/' and '\\' so it works for paths produced on
 * either platform. Returns a pointer into `path` (no allocation). */
const char *spody_io_basename(const char *path);

/* Create <output_dir>/<UTC-ISO8601>/ for a fresh run and return the
 * full path. The timestamp is captured once on entry (so every file
 * within the run shares the same folder name) and uses the same
 * compact "%Y-%m-%dT%H%M%SZ" format as spody_io_timestamp_filename.
 *
 * Returns SPODY_OK on success, SPODY_ERR_IO if the parent dir doesn't
 * exist or the mkdir failed for any other reason (already-exists is
 * very unlikely with the timestamp resolution but treated as OK). */
int spody_io_make_run_subdir(const char *output_dir,
                             char *run_subdir_out, size_t out_sz,
                             SpodyError *err);

/* Byte-for-byte copy of src -> dst. Used to snapshot the source TOML
 * into a fresh run subdir so each run is self-contained. dst is opened
 * with "wb" -- if it exists it's overwritten without warning. */
int spody_io_copy_file(const char *src, const char *dst, SpodyError *err);

/* Rewrite every non-empty output path in cfg so its basename is
 * preserved but its directory is replaced by `run_subdir`. Used right
 * after spody_io_make_run_subdir so the simulator writes files inside
 * the freshly-created per-run folder instead of cfg's original paths. */
void spody_io_rewrite_outputs_to_run_subdir(InputConfig *cfg,
                                            const char *run_subdir);

/* Inject a UTC timestamp before the extension of `base`. If `base` has
 * no extension, append the timestamp. Format: ISO 8601 compact UTC,
 * e.g. "run.log" -> "run_2026-05-19T143022Z.log". The "last dot" search
 * is scoped to the basename so paths like "/a.b/log" keep the timestamp
 * appended (no fake extension). */
void spody_io_timestamp_filename(const char *base, char *out, size_t out_sz);

/* Compose the batch-level log path:
 *   <batch_subdir>/<batch.name>_<ts>.log
 * The path is derived from the batch name + a UTC timestamp inside
 * output_dir/batch/, independent of cfg.log_file's original value. */
void spody_io_batch_log_path(const BatchConfig *batch,
                             const char *batch_subdir,
                             char *out, size_t out_sz);

/* Rewrite cfg's output paths to per-case names inside batch_subdir,
 * using <batch.name>_<id>.<ext>. Each output toggle is presence-driven:
 * only paths that were set in the base TOML are rewritten -- an empty
 * path stays empty. */
void spody_io_case_output_paths(InputConfig *cfg, const BatchConfig *batch,
                                const char *batch_subdir, int case_idx);

#ifdef __cplusplus
}
#endif

#endif /* SPODY_APP_IO_H */
