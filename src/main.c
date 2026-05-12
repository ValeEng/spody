/*
 * SpOdy - command-line driver.
 *
 * Subcommand dispatch. Each subcommand reads a TOML input file describing
 * one simulation (or batch of simulations) and writes results to a directory.
 * The Python GUI under python/ generates the input TOML and parses the
 * output files; it never calls into spody-core directly.
 *
 * Subcommands (planned):
 *   spody propagate  <input.toml> [--out <dir>]
 *   spody validate   <input.toml>
 *   spody info
 *
 * This is the initial scaffolding -- each handler is a stub.
 */
#include <stdio.h>
#include <string.h>

#include "spody_core.h"

#define SPODY_APP_VERSION "0.1.0"

static int cmd_propagate(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("[propagate] not yet implemented\n");
    return 0;
}

static int cmd_validate(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("[validate] not yet implemented\n");
    return 0;
}

static int cmd_info(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("SpOdy app  : %s\n", SPODY_APP_VERSION);
    printf("spody-core : %s  (git %s, built %s)\n",
           spody_version(), spody_git_hash(), spody_build_timestamp());
    return 0;
}

static void usage(const char *prog) {
    fprintf(stderr,
        "SpOdy %s -- Simultaneous Propagation of Orbital DYnamics\n"
        "\n"
        "usage: %s <command> [options]\n"
        "\n"
        "commands:\n"
        "  propagate  <input.toml> [--out <dir>]   run a simulation\n"
        "  validate   <input.toml>                 check input file (no run)\n"
        "  info                                    print version and capabilities\n"
        "\n",
        SPODY_APP_VERSION, prog);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        usage(argv[0]);
        return 1;
    }
    const char *cmd = argv[1];
    if      (strcmp(cmd, "propagate") == 0) return cmd_propagate(argc - 1, argv + 1);
    else if (strcmp(cmd, "validate")  == 0) return cmd_validate(argc - 1, argv + 1);
    else if (strcmp(cmd, "info")      == 0) return cmd_info(argc - 1, argv + 1);
    else if (strcmp(cmd, "-h") == 0 || strcmp(cmd, "--help") == 0) {
        usage(argv[0]);
        return 0;
    }
    fprintf(stderr, "unknown command: %s\n\n", cmd);
    usage(argv[0]);
    return 1;
}
