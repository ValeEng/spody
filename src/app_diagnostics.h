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
 * SpOdy app diagnostics: error reporting types and helpers shared by
 * every CLI module.
 *
 * Errors are returned via a caller-supplied stack-allocated SpodyError
 * struct, populated by spody_error_set() with printf-style formatting.
 * The CLI's top-level handlers use spody_error_print() to emit a
 * uniformly-formatted line on stderr; no other module touches stderr.
 */
#ifndef SPODY_APP_DIAGNOSTICS_H
#define SPODY_APP_DIAGNOSTICS_H

#include "spody_io.h"   /* spody_log_* text mirror (spody-core) */

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    SPODY_OK                  = 0,
    SPODY_ERR_IO              = 1,   /* fopen/fread failed */
    SPODY_ERR_TOML_PARSE      = 2,   /* tomlc99 reported a syntax error */
    SPODY_ERR_MISSING_KEY     = 3,   /* required key absent */
    SPODY_ERR_BAD_TYPE        = 4,   /* key present but wrong TOML type */
    SPODY_ERR_BAD_VALUE       = 5,   /* value out of range or invalid */
    SPODY_ERR_FILE_NOT_FOUND  = 6,   /* a referenced data file is missing */
    SPODY_ERR_INTERNAL        = 99
} SpodyErrorCode;

typedef struct {
    int  code;          /* one of SpodyErrorCode */
    char msg[512];      /* human-readable message */
    char file[260];     /* file the error is attached to (e.g. input TOML) */
    int  line;          /* line in that file, or -1 if unknown */
} SpodyError;

/* Reset *err to the "no error" state (SPODY_OK, empty fields, line = -1).
 * Safe to call on NULL. */
void spody_error_clear(SpodyError *err);

/* Populate *err with `code` and a printf-formatted message. The msg buffer
 * is fixed-size; the formatted text is truncated (always NUL-terminated)
 * if it would overflow. err->file and err->line are left untouched, so
 * callers that already stamped a file can call this without losing it.
 * Safe to call on NULL (no-op). */
void spody_error_set(SpodyError *err, int code, const char *fmt, ...);

/* Pretty-print *err to stderr in a uniform format. If err->file is set,
 * the message is anchored on it ("error: <file>: <msg>"); otherwise just
 * "error: <msg>". Safe to call on NULL (no-op). When a log mirror is
 * open (see spody_log_open_mirror), the same line is also written to it. */
void spody_error_print(const SpodyError *err);

/* The log mirror (spody_log_open_mirror, spody_log_close_mirror,
 * spody_log_printf, spody_log_eprintf) is part of spody-core: see
 * spody_io.h. Included here so every app module that reports through
 * this header keeps reaching it. */

#ifdef __cplusplus
}
#endif

#endif /* SPODY_APP_DIAGNOSTICS_H */
