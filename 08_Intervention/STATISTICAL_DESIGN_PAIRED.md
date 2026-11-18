# Statistical Design — Paired 2 × 2

**Status:** PROPOSAL. Not frozen. No formal data collected.
**Date:** 2026-11-18
**Authority:** implements the researcher's 2026-11-18 instruction (paired design).
**Related:** `EXPERIMENT_PROTOCOL.md`, `01_Research_Design/Current_Direction.md`,
`12_Research_Log/Research_Decision_Log.md`, `12_Research_Log/Pending_Decisions_For_Researcher.md` P-11.

---

## 1. Design structure

**Factors.** Model capability (M weak, S strong) × architecture (A0 baseline,
A1 reliability/efficiency). Four cells: `M-A0`, `M-A1`, `S-A0`, `S-A1`.

**Unit of the design is the task, not the trial.** The same frozen task scenarios
run in all four cells. For task `t` and repetition `r` (block `b = (t, r)`), all
four cells are executed, giving four observations per block.

```
block b = (task t, repetition r)
├── M-A0   fresh session
├── M-A1   fresh session
├── S-A0   fresh session
└── S-A1   fresh session
```

This is a **randomized complete block design** with four treatments per block.
Pairing is on the task: whatever makes a task hard (unusual item name, ambiguous
quantity, multi-turn correction) is held common to all four observations, so it
cancels out of within-task contrasts instead of inflating variance.

**Repetitions.** `R` repetitions per task is not yet fixed (P-09). If `R > 1`,
repetitions are treated as repeated measures within the task, and the task is the
resampling cluster. Repeated measures are not treated as independent trials.

## 2. Randomization

- Execution order **within each block** is randomized; the block itself is not.
- The order list is generated **before** any run: all 24 permutations of the four
  cells, shuffled once with a recorded seed, then assigned to blocks cyclically.
  With 24 or more blocks every cell occupies every position equally often, which
  balances warm-up, rate-limit cooldown, and time-of-day effects across cells.
- The seed, the schedule, and its SHA-256 are written to the run directory
  **before** the first execution and are part of `freeze.json`.
- The schedule is never regenerated mid-run. If a block is interrupted, the
  remaining cells run later in the recorded order and the interruption is logged.

**What randomization does and does not do.** It balances order effects *in
expectation*; it does not remove them. Hosted-model load and network variance
remain, and are reported as limitations.

## 3. Session isolation and initial state

Every cell execution gets:

- a **fresh session id**, fresh `OrderStore`, fresh `ToolRegistry`;
- the **same database snapshot**, loaded read-only, with its SHA-256 recorded;
- the **same pinned `business_weekday`** (never `auto`);
- **no carried dialogue history** — history starts empty;
- **no cached tool results** across blocks.

Heavy providers (hosted model, TTS) are shared to avoid reconnection cost, but no
state crosses a trial boundary. The existing `ExperimentRunner` already creates a
fresh session per trial while sharing providers; this design keeps that property.

## 4. Analysis structure

**Primary estimand (H3).** θ = p(M·A1) / p(S·A0), where p is the proportion of
tasks with a successful final state.

**Primary contrast.** `M-A1` vs `S-A0`, paired on task. One-sided
non-inferiority: H3 is supported only if the lower 95% bound of θ exceeds 0.90
(researcher decision, 2026-11-18).

**Secondary contrasts (H1, H2).**

| Contrast | Hypothesis | Estimand |
|---|---|---|
| `M-A1` vs `M-A0` | H1 accuracy gain for the weak model | risk difference, paired |
| `S-A1` vs `S-A0` | H1 for the strong model | risk difference, paired |
| `M-A1` vs `S-A0` | H3 | ratio θ, paired |
| Interaction | H4 | (p_M,A1 − p_M,A0) − (p_S,A1 − p_S,A0) |

**Why a difference for H1 and a ratio for H3.** H1 asks "does the architecture
help this model", which is naturally additive. H3 asks "how much of the strong
model's success does the weak model retain", which is naturally multiplicative.
Mixing the two is a presentational choice, not a statistical one, but it must be
declared.

**Multiplicity.** One primary contrast (H3), three secondary. Secondary contrasts
are Holm-corrected at α = 0.05. H4 is pre-specified as **exploratory** unless the
researcher promotes it, because an interaction needs roughly four times the
sample of a main effect to detect.

**Clustering.** All variance estimates resample the **task**, not the trial. Two
repetitions of the same task are not independent draws of task difficulty, and
treating them as such would understate the interval.

**Latency (H2).** Paired per-task difference in first-audio latency and in
turn-completion latency, reported **separately**, each as median + bootstrap CI
over tasks. H2 is falsified if the CI for first-audio includes 0 or is positive.
No composite accuracy–latency score exists.

**Cost.** Per cell, total variable cost / successes, with failures in the
numerator. Same boundary caveat as the accuracy ratio (see §5).

**Failure handling.** Intention-to-treat. A trial that ran counts, failed or not.
Technical exclusions are only those fixed **before** collection and verifiable
without seeing model output (e.g. a recorded provider outage with HTTP status).
An excluded trial is reported, never silently dropped.

## 5. P-11 correction — the actual boundary problem

**Correction first.** The earlier note said that a strong-baseline success
proportion of 1.0 makes the ratio undefined. **That was wrong.** A denominator of
exactly 1.0 is a perfectly well-defined number: θ̂ = p̂(M·A1) / 1 = p̂(M·A1). No
division by zero occurs. The record has been corrected, and the real problem is
different and more serious.

### 5.1 The real problem: the denominator's uncertainty is estimated as zero

With `n` trials and every one successful, `p̂_S = 1` and the plug-in variance
`p̂_S(1 − p̂_S)/n = 0`. Every delta-method interval therefore treats the
denominator as **known exactly**, when it is not:

- 30/30 successes gives a 95% Clopper–Pearson interval for `p_S` of roughly
  **[0.885, 1.000]** — the true value could plausibly be 0.89.
- If `p_S` were 0.89 rather than 1.0, the same numerator that scores θ̂ = 0.95
  would score 0.85, which **fails** the 0.90 criterion.

So the failure mode is **anti-conservative coverage**: the interval is too narrow
exactly when the data look most perfect, and H3 is declared supported more often
than the nominal 95% allows. This is a boundary-inference problem, not an
undefinedness problem.

### 5.2 The bootstrap does not rescue it

Percentile or BCa bootstrap resamples the observed trials. If all 30 strong-arm
trials succeeded, **every resample** yields `p̂_S = 1`, so the bootstrap
distribution of θ̂ is just the distribution of p̂(M·A1). The bootstrap inherits
the boundary degeneracy; it reproduces the same anti-conservative interval. An
earlier suggestion that bootstrap solves this was wrong and is retracted here.

### 5.3 The estimand is also lenient at the ceiling

The 0.90 ratio permits different absolute losses depending on where accuracy sits:

| p(S·A0) | Maximum allowed p(M·A1) | Absolute loss permitted |
|---|---|---|
| 0.70 | 0.63 | 7 pp |
| 0.80 | 0.72 | 8 pp |
| 0.90 | 0.81 | 9 pp |
| 1.00 | 0.90 | 10 pp |

A near-perfect reference therefore buys the loosest absolute tolerance. That is a
property of the estimand the researcher chose, not a defect to fix silently — but
the **absolute difference must always be reported alongside the ratio**, so a
reader can see the magnitude the ratio is hiding.

### 5.4 Candidate methods for paired binary data

Every method below estimates the same θ. They differ in how they handle the
boundary and in what they assume. **None is selected.**

| # | Method | How it handles `p_S = 1` | Assumptions | Limitations |
|---|---|---|---|---|
| A | **Paired cluster bootstrap** over tasks (BCa) | Does **not** — degenerates | Tasks exchangeable and independent; resampling captures the pairing covariance | Inherits the 30/30 degeneracy (§5.2); needs fixed resample count and seed; coverage at n≈30 unverified |
| B | **Delta method on log θ** with paired covariance | Does **not** — zero variance term | Asymptotic normality; no cell at 0 or n | Anti-conservative at the boundary; needs a zero-cell correction (Agresti–Caffo / add-½) which itself biases toward the interior |
| C | **MOVER-R / Newcombe hybrid score** ratio, extended with the paired covariance | Partly — score intervals stay finite at the boundary | Asymptotic; score-based variance recovery | Paired extension is much less studied than the unpaired case; more moving parts to justify in a school-level paper |
| D | **Bayesian Beta–Binomial** (Jeffreys prior) per arm, posterior of θ, 95% credible interval | **Yes** — 30/30 gives Beta(30.5, 0.5), which keeps real mass below 1 | Prior choice; a copula or shared random effect to encode pairing; exchangeability | Credible interval is not a confidence interval and must be labelled as such; prior sensitivity must be reported; framework change for the whole paper |
| E | **Exact conditional test on discordant pairs** (McNemar-type) | **Yes**, for the *difference* | Paired binary data; exact combinatorics | Tests the difference, not the ratio — usable only as a pre-declared fallback |

**Why C and D are the serious candidates.** They are the two that produce a
finite, non-degenerate interval when the reference arm is perfect, which is the
likely outcome with a strong model on 30 tasks. B is the most familiar and the
easiest to defend in writing, but it is precisely the method that fails in the
case most likely to occur.

### 5.5 The rule that cannot be chosen after the data

Whichever method is chosen, the **boundary rule must be fixed in advance**:
"if the S·A0 arm has zero failures, the reported interval is computed by method
X". Deciding after seeing 30/30 whether to switch methods is selection bias, and
no amount of post-hoc justification repairs it.

Two defensible pre-specifications:

1. **Single method throughout** — pick C or D and use it whether or not the
   boundary is hit. Cleanest, but commits the whole analysis to a less familiar
   estimator.
2. **Pre-declared switch** — B as primary, with a written rule that a perfect
   reference arm triggers D for that contrast, reported alongside B. Must state
   which one decides H3.

**Recommendation (not a decision):** option 2 with **D deciding** when the
boundary is hit, because the case is likely, not hypothetical.

## 6. Sample size

Not computed here. For paired binary data the variance of `log θ̂` is

```
Var(log θ̂) ≈ (1/n) · [ (1−p_M)/p_M  −  2·(p11 − p_M·p_S)/(p_M·p_S)  +  (1−p_S)/p_S ]
```

where `p11` is the proportion of tasks succeeding in **both** compared cells. The
middle term is negative when tasks correlate across cells, so pairing reduces the
required n in proportion to how much task difficulty is shared. `n` can only be
computed after the pilot supplies `p_M`, `p_S`, and `p11`.

**Consequence for the 360-trial figure.** 30 tasks × 4 cells × 3 repetitions = 360
blocks-worth of trials, but the effective sample size for a task-level contrast is
the **number of tasks (30)**, not 120. Thirty clusters is a small sample for any
ratio estimator; the pilot must show whether the interval is narrow enough to
decide anything at the chosen margin.

## 7. What this changes in the existing instrument

| Area | Change needed | Risk |
|---|---|---|
| Trial scheduling | block/order schedule + seed + hash in the run dir | new files only |
| `freeze.json` | record the schedule hash and the seed | additive field |
| Runner | iterate blocks rather than a flat trial list | affects run layout, not past runs |
| Analysis | new module; nothing existing is rewritten | new code |
| Existing data | none touched; `data/pilot/` is legacy and synthetic | — |

## 8. Open decisions

1. **Method** for the ratio CI (A–E) and the boundary rule (§5.5). Blocks analysis
   and sample size, not implementation.
2. **R** (repetitions per task) and whether the analysis unit is the task or the
   (task, repetition) pair with task clustering.
3. Whether **H4** is confirmatory or exploratory.
4. Whether the design is **fully paired** (all four cells on every task) or
   partially paired to save budget.
5. **P-10** latency-failure thresholds.
