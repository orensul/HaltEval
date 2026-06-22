# Non-Termination Discovery — Claude Code (Opus 4.7)

Claude Code (Opus 4.7) was run as an agent over 13 real-world C/C++ projects to
**discover** non-terminating functions in the wild. Every function it flagged as
non-terminating (`outcome = NT`) was then reviewed by a human expert (Julien),
who assigned a ground-truth classification and recorded whether the finding was
already present in our HaltEval benchmark.

Source: [`claude_code_opus_4.7_alerts.csv`](non_termination_discovery/claude_code_opus_4.7_alerts.csv) — 153 alerts.

## Key statistics

| Metric | Value |
| --- | --- |
| Total NT alerts raised by Claude Code | **153** |
| **False-positive rate** (predicted NT, actually terminates) | **5 / 153 = 3.3%** |
| Precision (genuinely non-terminating: NT + UNT + test code) | **146 / 153 = 95.4%** |
| **New non-terminations not already in HaltEval** | **133 / 151 = 88.1%** |
| Unintended NT — candidate real bugs (UNT) | **26** |

### False positives

Of 153 alerts, only **5 (3.3%)** were false positives — cases where Claude Code
predicted non-termination but the function actually terminates. Four of these
share the same root cause: Claude misreads `goto retry` logic in proftpd's TLS
code (`tls_accept`, `tls_connect`, `tls_read`), not recognizing that the retry
only fires when more data is available. (Excluding the 2 alerts still under
review, the FP rate is 5 / 151 = 3.3%.)

### New discoveries vs. existing benchmark

**88.1%** of Claude Code's non-termination alerts (133 of the 151 classified
alerts) are **new** — they do *not* already exist in our HaltEval dataset. Only
18 were already covered. This shows the agentic discovery loop surfaces a large
amount of non-termination beyond our current benchmark, and motivates extending
HaltEval with these findings (127 alerts are marked `to_extend`).

Of the new findings, **26 are "unintended" non-terminations (UNT)** — plausible
real defects rather than intentional infinite loops (e.g. server/worker threads).
Highlights flagged by the reviewer as genuine new bugs include FreeImage's IFF/ILBM
PackBits decoders, which loop forever on a malformed file that keeps emitting
`0x80` bytes.

## Expert classification breakdown

| Julien's classification | Count | Meaning |
| --- | --- | --- |
| NT | 114 | Intended non-termination (legit, e.g. concurrency / top-level loops) |
| UNT | 26 | Unintended non-termination — possible real bug |
| TEST | 6 | Intended NT inside test/fuzzing code (excluded) |
| FP | 5 | False positive — function actually terminates |
| TBD | 2 | Long function, still under review |

## Per-project alert counts

| Project | Alerts |
| --- | --- |
| bde | 43 |
| openssl | 16 |
| exim | 13 |
| comdb2 | 12 |
| bind9 | 11 |
| proftpd | 11 |
| gpac | 11 |
| sqlite | 11 |
| wireshark | 9 |
| libgit2 | 6 |
| libxml2 | 5 |
| freeimage | 3 |
| cryptopp | 2 |
