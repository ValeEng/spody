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
#ifndef APP_SHA256_H
#define APP_SHA256_H

/* SHA-256 (FIPS 180-4) of a whole file, streamed in blocks, no
 * dependency. Identifies the exact bytes a conversion read and wrote,
 * so a log line can be matched to a file on disk later.
 *
 * hex_out receives 64 lowercase hex digits + NUL; *size_out (may be
 * NULL) the file size in bytes. Returns 0 on success, -1 when the file
 * cannot be opened or read to the end. */
int spody_sha256_file(const char *path, char hex_out[65],
                      long long *size_out);

#endif /* APP_SHA256_H */
