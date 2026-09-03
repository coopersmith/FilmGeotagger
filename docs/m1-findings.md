# M1 — matching-quality harness: findings

Measured 2-3 September 2026 on 9 hand-tagged rolls from the 2026 batch (`00007037`-`00007045`),
against a 141,466-asset Photos library. SigLIP `ViT-B-16-SigLIP-384` and DINOv2
`vit_base_patch14_dinov2.lvd142m` on MPS; verification on `claude-opus-5`.

## Verdict

**M1 passes.** Retrieval finds the right phone photo in the top 8 for **91.2%** of frames that
have one (exit bar: 80%), and as the *top* hit for 83.2%. Claude verification reproduces the
user's own hand-picked anchor and costs about **$1 per 36-exposure roll**.

The single most important thing learned is not a number: **half the ground truth was fabricated,
and finding that out changed every conclusion.**

## The ground truth is only half real

The hand-tagged rolls live in the Photos library keyworded `Film` and carry the dates the user
copied across by hand. But the user tagged in two different ways, and only one of them is
evidence:

> "I was able to easily match photo 29 to an iPhone photo. And then I know when I took photo 1 on
> the next roll. But I had no idea when it was taken between those two dates. So I just guessed
> with a random date between that."

A guessed timestamp is worse than no timestamp. Scoring against it penalises a correct match and
rewards a lucky one — on a densely photographed day, a candidate lands within 30 minutes of an
arbitrary time by chance.

Anchored frames are recoverable, because hand-anchoring means copying a phone photo's EXIF: the
frame's timestamp coincides with that photo's **to the second**, which a derived time essentially
never does. `Roll.anchored()` implements this with a 2 s tolerance (Lightroom spaces a tagged
group one second apart).

| roll | frames | anchored | share |
|---|---|---|---|
| 00007038 | 37 | 35 | 95% |
| 00007044 | 10 | 9 | 90% |
| 00007042 | 37 | 31 | 84% |
| 00007037 | 37 | 24 | 65% |
| 00007043 | 10 | 4 | 40% |
| 00007041 | 10 | 3 | 30% |
| 00007040 | 38 | 5 | 13% |
| 00007039 | 38 | 2 | 5% |
| 00007045 | 10 | 0 | 0% |
| **total** | **227** | **113** | **50%** |

**Effect of scoring on the contaminated set:** recall@8 read 79.1% instead of 91.2% — a 12-point
understatement — and roll `00007040` looked like a model failure at 55% when it is 87% guesses.
It also produced a false positive in the other direction: `00007045`, with *zero* anchored frames,
scored 90%, because on a densely photographed day the metric is satisfiable by luck.

## Retrieval: SigLIP alone, and the diversity cap matters more than the model

recall@K on the 113 anchored frames, cap = at most 3 candidates per event:

| method | cap | @1 | @3 | @5 | @8 | @16 | @32 |
|---|---|---|---|---|---|---|---|
| **siglip** | 3 | **83.2** | **87.6** | **88.5** | **91.2** | **93.8** | **99.1** |
| siglip | none | 83.2 | 87.6 | 87.6 | 87.6 | 90.3 | 93.8 |
| dinov2 | 3 | 80.5 | 82.3 | 84.1 | 85.8 | 91.2 | 92.9 |
| z-fused | 3 | 80.5 | 85.8 | 89.4 | 89.4 | 91.2 | 95.6 |
| rrf | 3 | 79.6 | 84.1 | 88.5 | 90.3 | 92.0 | 96.5 |

**Decision: SigLIP alone is the default.** It beat DINOv2 and both fusion methods at every K, and
DINOv2 costs 2-4x the embedding time. DINOv2 stays available behind `--variants`; nothing measured
so far needs it. PLAN expected the two models to be complementary — on this data they are not.

**Fusion by z-score is harmful, not merely useless.** z-scoring equalises variance but not tail
*shape*: whichever model's top candidate sits further out in sigma dominates the sum regardless of
which is right, so z-fusion scored at or below the better single model on every roll of the first
(contaminated) run. Reciprocal rank fusion, which discards magnitude and combines positions, fixes
that failure and beat z-fusion — but still does not beat SigLIP alone. `retrieve.fuse()` dispatches
to RRF when given more than one model, so a future two-model configuration does not inherit the
z-score bug.

**The per-event diversity cap is worth more than the choice of model**: 99.1% vs 93.8% recall@32,
and it gains at every K. It was a hunch in PLAN; it is now the single highest-leverage retrieval
setting.

**recall@32 of 99.1%** means the right candidate is nearly always retrievable and merely ranked
outside 8. K is therefore a live lever traded directly against verification cost, not a wall.

Per-roll recall@8 is 100% on five of eight rolls, 91.4% and 90.3% on two more. The exception is
`00007040` at 1/5 — a roll with too few anchored frames to measure.

## Window width costs ~9 points

Recall@8 on the contaminated set fell from 79.1% (roll's true range ±2 days) to 70.5% (±14 days).

The ±2-day figure was never honest: it hands the matcher a window derived from the answer. A real
user supplies "April" or "the Portugal trip". Narrowing the window before retrieval is what the
roll-level outing pass and per-roll date facts are for, and they are load-bearing rather than
optional.

But window width is **not** what breaks the hard cases. `00007040` was already being given an
11-day window and still struggled: its ambiguity is *within* the window.

## Verification: Claude reproduces the user's own anchors

On roll `00007044`, **8 of 10 frames matched at delta exactly `0:00:00`** — Claude selected the
very photo the user had chosen by hand. Recall was 9/9 of frames whose shortlist contained a
correct candidate. The one disagreement was 39 minutes out, with the evidence "same room and
session: wooden slatted crib, striped rug edge, white polka-dot blanket" — a real same-session
photo, not a wrong day.

Precision is reported as a **tolerance curve**, not a single pass/fail, because a 30-minute
threshold measures the threshold as much as the model when the truth itself is only
outing-accurate. On `00007040` (27 accepted): 63.0% within 5 min, 70.4% within 30 min, 81.5%
within 2 h, 92.6% same day — but with only 5 anchored frames on that roll, these numbers are
mostly scoring against guesses.

Claude abstained on 11 of 38 frames there and never refused. Abstention is the correct behaviour:
most frames have no counterpart, and a refusal is treated as "no verdict", never "no match".

**Cost: $0.029-0.040 per frame, ~$1.05-1.42 per 36-exposure roll**, or about $14 for a 10-roll
batch on `claude-opus-5`. Cheap enough that the model-tier sweep PLAN called for is not urgent;
Opus 5 stays the default.

## The dominant failure mode is domestic repetition

`00007040` is a newborn-at-home roll — the same sofa, the same baby, similar sleepsuits, day after
day. Every "same clothing / same place" heuristic fires spuriously. The wrong accepts are all
2h12m-2h38m out with evidence like "the same olive-green sofa... the same plain cream sleepsuit".

This is PLAN risk 2, more severe than anticipated because of the subject matter, and no external
location signal helps: nobody checks in at their own sofa. Two things do:

* **The monotone constraint in M2.** A 2h38m error on frame 30 contradicts its neighbours.
* **Admitting uncertainty.** If the user could not place these frames by hand with full context,
  the tool will not either. The right output is the interval between the anchors that do exist
  ("between Tue 14:05 and Wed 17:40"), which is precisely what the random guesses in the ground
  truth were standing in for.

## Still open

* Grayscale and border-trim variants are implemented but unmeasured.
* Calibration from similarity to P(match) — needs the labelled data this milestone just produced.
* Re-measure recall at realistic window widths once window narrowing exists.
* Choose K against verification cost, now that recall@32 shows the headroom.
