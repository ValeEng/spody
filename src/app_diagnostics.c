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

/* ----------------------------------------------------------------------
 * Log mirror state -- single file shared by every log helper. Process-wide
 * because the app is single-threaded today; if a future build adds OpenMP,
 * either protect this with a lock or open one mirror per thread.
 * ---------------------------------------------------------------------- */
static FILE *g_log_mirror = NULL;

int spody_log_open_mirror(const char *path) {
    if (g_log_mirror) { fclose(g_log_mirror); g_log_mirror = NULL; }
    g_log_mirror = fopen(path, "w");
    return g_log_mirror ? 0 : -1;
}

void spody_log_close_mirror(void) {
    if (!g_log_mirror) return;
    fflush(g_log_mirror);
    fclose(g_log_mirror);
    g_log_mirror = NULL;
}

/* Internal: write `fmt+args` to `term` always, and to the mirror if set.
 * Splits the va_list with va_copy so each consumer sees a fresh iterator. */
static void tee_vprintf(FILE *term, const char *fmt, va_list ap) {
    va_list ap2;
    va_copy(ap2, ap);
    vfprintf(term, fmt, ap);
    if (g_log_mirror) {
        vfprintf(g_log_mirror, fmt, ap2);
        /* No fflush per call -- spody_log_close_mirror flushes at the end. */
    }
    va_end(ap2);
}

void spody_log_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    tee_vprintf(stdout, fmt, ap);
    va_end(ap);
}

void spody_log_eprintf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    tee_vprintf(stderr, fmt, ap);
    va_end(ap);
}

void spody_error_print(const SpodyError *err) {
    if (!err) return;
    if (err->file[0] != '\0') {
        spody_log_eprintf("error: %s: %s\n", err->file, err->msg);
    } else {
        spody_log_eprintf("error: %s\n", err->msg);
    }
}
