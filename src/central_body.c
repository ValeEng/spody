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
#include "central_body.h"

#include <stdio.h>
#include <string.h>

#include "spody_const.h"             /* MOON_MU, MOON_RADIUS, EARTH_MU, EARTH_RADIUS */
#include "spody_earth_orientation.h" /* spody_bf_rotation_earth */

/* Static registry of supported central bodies. Order is irrelevant --
 * lookups are linear over a single-digit list.
 *
 * Earth's bf_rotation provider needs MappedEOP + MappedIAU2006 data
 * via ForceModelContext (see spody_bf_rotation_earth in spody-core);
 * sim_setup attaches those to ctx.eop / ctx.iau2006 when
 * central_body == "Earth". The registry row itself stays the same
 * shape as Moon -- the per-body data plumbing is owned by sim_setup. */
static const SpodyCentralBodySpec _registry[] = {
    {
        .body        = SPODY_CENTRAL_MOON,
        .name        = "Moon",
        .naif        = 301,
        .mu          = MOON_MU,
        .radius_km   = MOON_RADIUS,
        .bf_rotation = spody_bf_rotation_moon,
    },
    {
        .body        = SPODY_CENTRAL_EARTH,
        .name        = "Earth",
        .naif        = 399,
        .mu          = EARTH_MU,
        .radius_km   = EARTH_RADIUS,
        .bf_rotation = spody_bf_rotation_earth,
    },
};
static const size_t _registry_n = sizeof _registry / sizeof _registry[0];

const SpodyCentralBodySpec *spody_central_body_get(SpodyCentralBody body) {
    for (size_t i = 0; i < _registry_n; ++i) {
        if (_registry[i].body == body) return &_registry[i];
    }
    return NULL;
}

int spody_central_body_from_name(const char *name, SpodyCentralBody *out) {
    if (!name || !out) return -1;
    for (size_t i = 0; i < _registry_n; ++i) {
        if (strcmp(_registry[i].name, name) == 0) {
            *out = _registry[i].body;
            return 0;
        }
    }
    return -1;
}

size_t spody_central_body_known_names(char *buf, size_t bufsz) {
    if (!buf || bufsz == 0) return 0;
    buf[0] = '\0';
    size_t pos = 0;
    for (size_t i = 0; i < _registry_n && pos < bufsz; ++i) {
        int n = snprintf(buf + pos, bufsz - pos,
                         "%s'%s'", i ? ", " : "", _registry[i].name);
        if (n <= 0) break;
        pos += (size_t)n;
    }
    return pos;
}

const char *spody_central_body_name(SpodyCentralBody body) {
    const SpodyCentralBodySpec *s = spody_central_body_get(body);
    return s ? s->name : "?";
}

int spody_central_body_naif(SpodyCentralBody body) {
    const SpodyCentralBodySpec *s = spody_central_body_get(body);
    return s ? s->naif : -1;
}
