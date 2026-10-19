# Chess Puzzles Review

Benchmark: Chess Puzzles  
Benchmark creator: Epoch AI  
Verdict: Flawed  
Review date: 8th October 2026

## Summary

Chess Puzzles asks models to choose the best move in 100 text-encoded chess positions, without tools. Credit depends on agreement with a reference move. Epoch’s current leaderboard reports a best score of 72% for GPT-6 Astra at maximum effort; the highest complete supplied run scores 54%.

The review finds the benchmark Flawed because some published settings repeatedly exhaust their output allowance before returning an answer. Sonnet 4.6 at maximum effort leaves 95/100 final answers empty and scores 3%; another supplied configuration returns every answer and scores 13%. This makes those scores poor estimates of the models’ best chess-puzzle performance. Conversely, engine checks support all 100 reference moves, and only one puzzle has a demonstrated scoring error, below the scoring-defect threshold. The findings concern particular evaluation settings, not the validity of every puzzle or every reported score.

## Methodology

We compared the public task and generator with the pinned Generality Labs reconstruction. We examined settings and outcomes across all 193 supplied logs: 19,500 attempts dated December 2025–August 2026. Every puzzle received question-label, reference-answer, specification, answer-format, environment-integrity and other-findings audits, using GPT-6 Luna with medium reasoning. Ordinary scoring probes used GPT-5 mini with low reasoning.

Stockfish 17.1 independently checked every reference move. Headers, recorded requests, identical-answer comparisons and diagnostic transcripts across all logs informed consistency, elicitation and bias. We also checked Epoch’s current leaderboard and human-baseline documentation. No adversarial scorer tests or answer-access attacks were run.

## Representative Errors

The following issues cross our threshold for a Flawed verdict.

- Output limits can prevent models from submitting any move. Across 19,500 attempts, 925 reached an output limit; 851 of those had empty final answers. These are unsuccessful attempts, but they do not show that the models selected wrong moves.
- The problem is concentrated in particular reasoning configurations. Alternative settings for the same models complete more answers and score higher. These comparisons support better achievable performance, but do not isolate the effect of token limits from changes in reasoning effort or provider behavior.

| Task | Error type | Notes | Affected logs |
|---|---|---|---|
| Sonnet 4.6: all 100 puzzles | Elicitation | Maximum effort: 95 empty answers, 3% accuracy; a reserved reasoning budget: no empty answers, 13% accuracy. | Sonnet 4.6 max and 32K (2 logs) |
| Opus 4.7: all 100 puzzles | Elicitation | Maximum effort: 88 empty answers, 7% accuracy; low effort: no empty answers, 20% accuracy. | Opus 4.7 max and low (2 logs) |
| Qwen 3.5 9B: all 100 puzzles | Elicitation | Default reasoning: 97 empty answers, 2% accuracy; reasoning disabled: no empty answers, 12% accuracy. | Qwen 3.5 9B default and none (2 logs) |

## Benchmark Review Rubric

Rubric v1. We expect future revisions to account for new types of benchmark flaws.

### 1. Reviewability

| Level | Meaning | Status | Notes |
|---|---|---|---|
| **Full** | All tasks and scoring logic inspectable, and harness/API settings used for each model (reasoning effort, token/time limits, tool access, system prompts) are fully disclosed | **[proceed to 2]** | All 100 positions, reference moves and public scoring code are inspectable. Headers and recorded requests disclose prompts, model settings, answer-reading models and tool access across all 193 supplied logs, and the runnable package reconstructs the public task. Some internal reasoning is hidden, but final answers and scores are visible. |
| Partial | A representative sample of tasks and scoring logic inspectable, with full harness/API settings disclosed | [proceed to 2] |  |
| Inadequate | Limited, biased, or no inspectable tasks or scoring logic, harness/API settings undisclosed | [stop → NEI] |  |

### 2. Scoring

This is the minimum standard required to be Verified; failing any of these items results in a Flawed verdict.

Scoring prevalence: 1 of 100 inspected tasks (1%); threshold 20%.

| Defect Class | Examples | Default Threshold for Flawed | Status | Notes |
|---|---|---|---|---|
| Scoring | • Essentially impossible to correctly answer as written (e.g. underspecified task, hidden requirement, missing file/tool)<br>• False Negative (e.g. overly strict scorer, stale/incorrect ground truth, dependent on live external state that can drift, sandbox failure independent of the agent)<br>• False Positive (e.g. lax scorer, reward-hackable environment, stated skill can be bypassed via a shortcut such as exploiting an error in the scoring logic or retrieving the answer from the harness/web)<br>• Task egregiously doesn't measure the claimed capability | ≥20% of inspected sample contains errors or issue that corrupts grading at scale | **Pass**<br>Flag [stop → Flawed]<br>Not Reviewed | All 100 puzzles were assessed and 1 has a scoring defect, below the 20% threshold. Stockfish supported every reference move; one answer on puzzle 86 was rejected for keeping the MOVE prefix. |
| Benchmark consistency | • Scorer, instructions, or ground truth changed without a version bump | Leaderboard has incomparable results from different versions | **Pass**<br>Flag [stop → Flawed]<br>Not Reviewed | Prompts and references match across all 193 logs. Within version 1.1.6 the answer-reading model changed without a version bump, but 225 identical answer groups spanning both models show no correctness disagreements. Material score incomparability was not demonstrated. |
| Elicitation | • Model elicitation is extremely constraining and is not the focus of the benchmark:<br>• Under-resourced relative to task size (token/turn/time limit, sandbox resources)<br>• Poor context management<br>• Lack of agentic environment where one would be natural to provide<br>• Excessive non-voluntary termination for agentic benchmarks | Substantially reduced performance compared to reasonable alternatives for the tasks | Pass<br>**Flag [stop → Flawed]**<br>Not Reviewed | Some published settings regularly exhaust the response allowance before producing an answer, and the same models complete their answers and score higher under alternative settings. The alternatives establish substantially better achievable performance, though changes in effort and provider behaviour prevent attributing the whole difference to the token cap.[^1] |
| Bias in evaluation setup | • Uneven compute/token budgets<br>• Unfair scaffold choice (e.g. only a subset of models optimized) | Material model-specific advantage found | Pass<br>Flag [stop → Flawed]<br>**Not Reviewed** |  |

### 3. Evaluation Quality

This is the standard we would like all benchmarks to meet, but it is not necessarily disqualifying to omit or fail these items.

| Question | Status | Notes |
|---|---|---|
| Elicitation and resource adequacy: Are the resources given to models (reasoning token/turn budget, tool access, etc.) sufficient for them to perform near their ceiling? | Sufficient<br>Constraining<br>Unreasonably constraining<br>Unknown<br>**Not Reviewed** |  |
| Scaffold fairness: What scaffold does the leaderboard report? | Shared common scaffold<br>Mix of model-specific and common scaffolds<br>Model-specific scaffolds<br>**Not Reviewed** |  |
| Is there evidence/risk of contamination? | As of ___: ___% of tasks public<br>___% of solutions public<br>**Not Reviewed** |  |
| Has human completability been assessed? | All tasks<br>Representative set of tasks<br>Poor implementation (Unrepresentative set of tasks, unreasonable set of participants)<br>Not established<br>**Not reviewed** |  |
| Score range (if possible to estimate) | ___ floor<br>___ ceiling<br>**Not Reviewed** |  |
| Statistical adequacy: How many runs/model (≥ 5 recommended for error bars) | ___ runs/model<br>Unknown<br>**Not Reviewed** |  |
| Construct validity | Measures stated capabilities<br>Partially measures stated capabilities<br>Does not measure stated capabilities<br>**Not Reviewed** |  |

## Disclaimer

We undertook this audit without requesting additional information from Epoch AI to provide impartial feedback.

## Recommendations

- Reserve enough output capacity for a final answer, and test completion rates before publishing a configuration.
- Publish truncation, empty-answer and unscored-attempt counts beside accuracy, labelled by configuration.
- Use deterministic parsing for the requested move format, with a defined fallback and versioned changes to the answer reader.
- Repeat each configuration at least five times and report uncertainty from both puzzle selection and repeated runs.
- Publish the engine version and search settings behind each reference, and keep fresh unreleased puzzles for later models.

## Footnotes

[^1]: Impact: Sonnet 4.6 at maximum effort leaves 95/100 final answers empty and scores 3%; reserving 32,000 of 64,000 tokens for reasoning produces all 100 answers and scores 13%. Similar failures occur for Opus 4.7 and Qwen 3.5.
