# Pattern value legend (error sequences / recorder bias)

A **pattern** is the **two shots immediately before the error** (the error shot itself is not in the pattern). Format:

**`(phase_N-2, key_N-2) -> (phase_N-1, key_N-1)`**

- **Left side** = shot N-2 (two shots before the error)
- **Right side** = shot N-1 (one shot before the error); the error is shot N (not in pattern)
- **->** = “then” (sequence in time)

---

## Phase (first token in each pair)

| Code   | Meaning                          | Shot number   |
|--------|----------------------------------|---------------|
| SERVE  | Serve                            | Shot 1        |
| RETURN | Return of serve                  | Shot 2        |
| RALLY  | Any shot after the return        | Shot 3, 4, …  |

---

## Shot key (second token) — components

### Shot kind (base)

| Code | Meaning |
|------|---------|
| GS   | Groundstroke (no volley/overhead/special) |
| SRV  | Serve (phase SERVE, shot 1) |
| VOL  | Volley (including half volley, swinging volley) |
| OH   | Overhead / smash |
| APP  | Approach shot |
| DROP | Drop shot |
| LOB  | Lob |
| SLICE| Slice or chip |

### Shot type (after _)

|------|---------|
| FH   | Forehand |
| BH   | Backhand |


### Direction (after shot type, when present)

| Code | Meaning |
|------|---------|
| CC   | Crosscourt |
| DTL  | Down the line |
| IO   | Inside-out |
| II   | Inside-in |
| DM   | Down the middle |

### Serve target (serve only, shot 1)

| Code | Meaning |
|------|---------|
| T    | T (center) |
| W    | Wide |
| B    | Body |

### Handedness (for IO/II only, when available)

| Code | Meaning |
|------|---------|
| RH   | Right-handed hitter |
| LH   | Left-handed hitter |

---

## Examples

| Pattern | Meaning |
|---------|---------|
| `(RALLY, GS_FH_CC) -> (RALLY, VOL_FH)` | Shot N-2: rally FH crosscourt. Shot N-1: rally FH volley. Error on the next shot (not in pattern). |
| `(RETURN, GS_FH_DM) -> (RALLY, VOL_FH_CC)` | Shot N-2: return FH down the middle. Shot N-1: rally FH volley crosscourt. Error on the next shot. |
| `(RALLY, GS_BH_DM) -> (RALLY, GS_FH_CC)` | Shot N-2: rally BH down the middle. Shot N-1: rally FH crosscourt. Error on the next shot. |
| `(SERVE, GS_SRV_T) -> (RETURN, GS_BH)` | Shot N-2: serve to T. Shot N-1: backhand return. Error on the next shot. |

---

## Where patterns appear

- **recorder_bias_by_pattern_surface.csv** — column `pattern`
- **flagged_points.csv** — column `pattern` (same format)

Surfaces in those files: **hard**, **clay**, **grass**, or **unknown**.
