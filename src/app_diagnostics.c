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
 * Implementation of the app-level error API.
 */
#include "app_diagnostics.h"

#include <stdarg.h>
#include <stdio.h>

void spody_error_clear(SpodyError *err) {
    if (!err) return;
    err->code    = SPODY_OK;
    err->msg[0]  = '\0';
    err->file[0] = '\0';
    err->line    = -1;
}

void spody_error_set(SpodyError *err, int code, const char *fmt, ...) {
    if (!err) return;
    err->code = code;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err->msg, sizeof err->msg, fmt, ap);
    va_end(ap);
}

/* The log mirror (spody_log_open_mirror / _printf / _eprintf) lives in
 * spody-core's spody_io, so the library's own diagnoses reach the same
 * log file as the app's lines. */

void spody_error_print(const SpodyError *err) {
    if (!err) return;
    if (err->file[0] != '\0') {
        spody_log_eprintf("error: %s: %s\n", err->file, err->msg);
    } else {
        spody_log_eprintf("error: %s\n", err->msg);
    }
}
