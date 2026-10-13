# Chess Puzzles Review

Benchmark: Chess Puzzles  
Benchmark creator: Epoch AI  
Verdict: Verified  
Review date: 8th October 2026

## Summary

Chess Puzzles gives a model 100 chess positions and asks for the best next move. A second AI model reads the move out of the response, and it must match a single reference move exactly. We found that the scores measure what Epoch says: finding the chess engine's best move.

An independent engine search confirms every reference move. In 99 positions the best move is clearly better than any other. In one, two moves are exactly equal and only one is accepted. Answers are read out correctly in over 99% of cases, and the task has not changed across versions.

Three cautions apply to the leaderboard. Some runs at the highest reasoning settings ran out of output space and score far below the same models at other settings. The seven highest-scoring rows have no public log. One listed score does not match its log.

## Methodology

We used only public information and did not contact Epoch. We read the task code (Generality Labs' reconstruction of Epoch's public code), Epoch's benchmark page and its leaderboard as of 8 October 2026. We analysed all 193 public run logs: 19,500 recorded answers covering 187 of 227 leaderboard rows.

We searched every position with the Stockfish 17 chess engine, about 30 half-moves deep. An AI auditor (GPT-6 Luna) checked each of the 100 puzzles with its own engine search and also tried to trick the answer reader. We re-scored 1,500 recorded answers with GPT-5 mini as the answer reader, and all 1,500 grades matched Epoch's. We ran no new model evaluations.

## Interpretation

A score is the share of 100 positions in which the model named the engine's best move. In 99 positions every other move is worse by at least 1.5 pawns of value, as the engine measures it. So the score cleanly measures one skill: finding a single strong move in an unfamiliar position, from a text description alone.

The scale has room. A random legal move would score about 5%. A perfect player would score 99-100%. The best public score is 72%. Five strong human players scored at most 75% on a harder-than-average sample of 20 puzzles.

Each row is a single pass over 100 puzzles, so its standard error is about 4-5 percentage points. Two rows less than about 10 points apart should not be ranked against each other.

A row describes a model at one setting, not the model at its best. Eight rows are dominated by answers cut off at the output limit. Claude-opus-4-7 scores 7% at its maximum effort setting but 20% at low effort. Compare models by their best row. Treat rows with many cut-off answers as measuring the limit, not the model.

Some rows need specific care. MiniMax-M3 is listed at 14%, but 32 of its 100 answers were server errors at the provider, counted as wrong. It solved 14 of the 65 puzzles where it gave a real answer. Gemini-3.1-pro-preview is listed at 55%, but its public log shows 53 failed calls and 61.7% on the 47 puzzles that completed; we could not reproduce the listed figure. The seven highest rows, including the 72% top score, have no public log, so we could not check their settings or answers.

## Task Analysis

Epoch generated the positions from engine games. The opening moves are random, then Stockfish plays at a randomly drawn strength. Epoch kept positions where full-strength Stockfish sees a single best move that the weaker player missed. It filtered out moves that simply win an undefended or more valuable piece.

We searched all 100 positions again with Stockfish 17, about 30 half-moves deep. The reference move was the engine's first choice in every position. In 99 positions the second-best move was at least 154 centipawns (1.5 pawns) worse. Eight of these fall short of the generator's own 180-centipawn gap at this depth, but the reference still wins clearly. The per-puzzle auditors reached the same conclusion on every puzzle.

Puzzle 14 is the exception. The knight moves Nd4 and Nh4 are exactly equal, even at depth 40. Only Nd4 is accepted, and one recorded answer of Nh4 was marked wrong. No run solved puzzles 3, 21 or 93. Their reference moves are sound; models picked tempting captures or checks instead.

The prompt asks the model to end with a line such as "MOVE: b1c3". A second AI model then reads the final move from the full response. It agreed with the model's last "MOVE:" line in 99.6% of 17,045 answers that had one. Across all 19,442 scored answers, one correct answer was marked wrong and two unfinished answers were wrongly credited. Answers in standard chess notation (such as Nxd5) were handled unevenly: 9 naming the right move were marked wrong and 8 were credited.

These slips are far too few to move any row. Because the answer reader reads the whole response, text planted in a response can steer it. Our auditors wrote such text and got a wrong move credited in four puzzles. No recorded run did this.

## Elicitation and Scaffolding

Every model gets the same single prompt. It gives the position as a FEN (Forsyth-Edwards Notation) string, a standard one-line text description of a board, and asks for the best move in a fixed format. It says the model may think as long as it likes. There are no tools, no engine and no second turn. That fits the benchmark's aim, which is to test what a model can work out unaided.

Output limits appear to follow each model's own maximum or a shared setting for a batch of runs. They range from 3,000 to 384,000 tokens (word pieces), with a median of 65,536. Older models that answer briefly almost never reach their limits.

Trouble appears at the highest reasoning settings. Across all logs, 925 of 19,442 answers (4.8%) were cut off at the limit, and only 4 of these scored. Eight logged runs lost at least half their answers this way and score 0-16%. They are gpt-oss-20b at high and medium effort, qwen3.5-35b-a3b and qwen3.5-9B at their default settings, claude-sonnet-4-6 at high and maximum effort, and claude-opus-4-7 and claude-sonnet-5 at maximum effort.

These rows measure the limit more than the model. Claude-sonnet-5 at maximum effort scores 16%, but 35% at extra-high effort. It solved 16 of the 26 puzzles it finished, though early finishes tend to be easier puzzles. Qwen3.5-9B scores 12% with reasoning turned off and 2% at its default setting.

Every affected model has a better setting on the leaderboard, so the benchmark as a whole is not held back. No developer's models get a different prompt or an unusual limit, so we found no bias.

## Limitations

While none of these limitations cross our threshold for a Flawed verdict, they do inform how a reader should characterize this benchmark.

- Eight leaderboard rows are dominated by answers cut off at the output limit and score 0-16%. They understate the models, which score higher at other listed settings.
- The seven highest-scoring rows, and 40 of 227 rows overall, have no public log we could read. We could not check their settings or answers.
- The gemini-3.1-pro-preview score of 55% does not match its public log, which shows 61.7% on the 47 of 100 puzzles that completed.
- In the MiniMax-M3 run, 32 server errors at the provider were scored as wrong answers. Another run recorded the same kind of failure as an error instead.
- Each row is one pass over 100 puzzles. Differences under about 10 percentage points are within noise.
- Text planted in a response can steer the answer reader into crediting a wrong move. No recorded run did this.
- Puzzle 14 has two equally good moves, and only one is accepted.
- All positions and reference moves are public, so future models may have seen them in training. We did not test for memorisation.
- We reviewed a reconstruction of Epoch's code, not its private scoring setup. The logs show the same prompt, reference moves and scoring behaviour.

## Recommendations

- Report the share of cut-off answers for each row. Raise the output limit, or mark rows where more than a few percent of answers are cut off.
- Publish logs for every leaderboard row, starting with the seven highest.
- Recompute the gemini-3.1-pro-preview score from a complete run, or publish the log behind the 55% figure.
- Treat server errors at the provider as errors and retry them, never as wrong answers.
- Run each model several times, ideally five, to narrow the error bars.
- Read the final "MOVE:" line directly, and use the answer reader only when that line is missing.
- Accept both equal moves in puzzle 14, or replace the puzzle.
- Keep a private set of fresh positions to check for memorisation.

## Benchmark Review Rubric

Rubric v1. We expect future revisions to account for new types of benchmark flaws.

### 1. Reviewability

| Level | Meaning | Status | Notes |
|---|---|---|---|
| Full | All tasks and scoring logic inspectable, and harness/API settings used for each model (reasoning effort, token/time limits, tool access, system prompts) are fully disclosed | [proceed to 2] |  |
| **Partial** | A representative sample of tasks and scoring logic inspectable, with full harness/API settings disclosed | [proceed to 2] | All 100 puzzles, their reference moves and the scoring code are public. 187 of 227 leaderboard rows have a readable public log that records the model's settings and every answer. The other 40 rows, including the seven highest scores, have no log we could read. One linked log (gemini-3.1-pro-preview) does not reproduce its listed score. |
| Inadequate | Limited, biased, or no inspectable tasks or scoring logic, harness/API settings undisclosed | [stop → NEI] |  |

### 2. Scoring

This is the minimum standard required to be Verified; failing any of these items results in a Flawed verdict.

Scoring prevalence: 1 of 100 inspected tasks (1%); threshold 20%.

| Defect Class | Examples | Default Threshold for Flawed | Status | Notes |
|---|---|---|---|---|
| Scoring | • Essentially impossible to correctly answer as written (e.g. underspecified task, hidden requirement, missing file/tool)<br>• False Negative (e.g. overly strict scorer, stale/incorrect ground truth, dependent on live external state that can drift, sandbox failure independent of the agent)<br>• False Positive (e.g. lax scorer, reward-hackable environment, stated skill can be bypassed via a shortcut such as exploiting an error in the scoring logic or retrieving the answer from the harness/web)<br>• Task egregiously doesn't measure the claimed capability | ≥20% of inspected sample contains errors or issue that corrupts grading at scale | **Pass**<br>Flag [stop → Flawed]<br>Not Reviewed | An engine search and per-puzzle audits of all 100 puzzles found one defect (1%): in puzzle 14 two knight moves are exactly equal and only one is accepted. The model that reads the answer out of each response matched the model's own stated move in 99.6% of 17,045 cases. Text planted in a response can trick it into crediting a wrong move, but no recorded run did so. One run scored 32 provider failures as wrong answers. |
| Benchmark consistency | • Scorer, instructions, or ground truth changed without a version bump | Leaderboard has incomparable results from different versions | **Pass**<br>Flag [stop → Flawed]<br>Not Reviewed | All 193 logs, versions 1.0.0 to 1.1.6, use the same prompt, the same 100 reference moves and the same scoring code. Only the model that reads out answers changed: three were used, and one switch came without a version bump. All three agree with the stated move in 98.7-99.9% of cases, so results stay comparable. One listed score (gemini-3.1-pro-preview, 0.55) does not match its log (0.617 on 47 puzzles). |
| Elicitation | • Model elicitation is extremely constraining and is not the focus of the benchmark:<br>• Under-resourced relative to task size (token/turn/time limit, sandbox resources)<br>• Poor context management<br>• Lack of agentic environment where one would be natural to provide<br>• Excessive non-voluntary termination for agentic benchmarks | Substantially reduced performance compared to reasonable alternatives for the tasks | **Pass**<br>Flag [stop → Flawed]<br>Not Reviewed | Each model gets one response, is told it may think as long as it likes, and has an output cap with a median of 65,536 tokens. Most runs finish their answers. Eight of 193 logged runs lost at least half their answers to the cap and scored 0-16%. Each of those models scores higher at another listed setting, so the cap misleads single rows but does not depress the benchmark as a whole. |
| Bias in evaluation setup | • Uneven compute/token budgets<br>• Unfair scaffold choice (e.g. only a subset of models optimized) | Material model-specific advantage found | **Pass**<br>Flag [stop → Flawed]<br>Not Reviewed | All models get the same prompt, one response and no tools. Output caps appear to follow each model's own limit or a shared batch setting. Cut-off answers hit models from Alibaba, Anthropic and OpenAI alike, and each affected model has a better setting listed. We found no setting that gives one model or developer a material advantage. |

### 3. Evaluation Quality

This is the standard we would like all benchmarks to meet, but it is not necessarily disqualifying to omit or fail these items.

| Question | Status | Notes |
|---|---|---|
| Elicitation and resource adequacy: Are the resources given to models (reasoning token/turn budget, tool access, etc.) sufficient for them to perform near their ceiling? | Sufficient<br>**Constraining**<br>Unreasonably constraining<br>Unknown<br>Not Reviewed | Most runs have room to finish: the median output cap is 65,536 tokens. At the highest reasoning settings several models ran out of room before naming a move. 925 of 19,442 answers (4.8%) were cut off, and eight rows lost at least half their answers. The same models score higher at lower settings that are also listed. |
| Scaffold fairness: What scaffold does the leaderboard report? | **Shared common scaffold**<br>Mix of model-specific and common scaffolds<br>Model-specific scaffolds<br>Not Reviewed | Every model gets the same single prompt and gives one response, with no tools. Only each model's own settings vary, such as reasoning effort and output cap. |
| Is there evidence/risk of contamination? | **As of 2026-10-08: 100% of tasks public<br>100% of solutions public**<br>Not Reviewed | The positions were generated from engine games for this benchmark, not taken from puzzle collections. But all 100 positions and their reference moves are public in the task code and in public logs. Models trained after release could have seen them. We did not test for memorisation. |
| Has human completability been assessed? | All tasks<br>**Representative set of tasks**<br>Poor implementation (Unrepresentative set of tasks, unreasonable set of participants)<br>Not established<br>Not reviewed | Epoch asked five players rated 1700-2400 to each solve 20 randomly chosen puzzles without an engine. The best two scored 75%. Epoch notes the sample was harder than average. Our engine search confirms every reference move, so every puzzle is solvable. |
| Score range (if possible to estimate) | **about 5% (random legal move) floor<br>99-100% (puzzle 14 has two equally good moves) ceiling**<br>Not Reviewed | Positions have about 34 legal moves on average, so a random legal move scores about 5%. A perfect player scores 99-100%, because only one of the two equal moves in puzzle 14 is accepted. The best public score is 72%. |
| Statistical adequacy: How many runs/model (≥ 5 recommended for error bars) | **1 runs/model**<br>Unknown<br>Not Reviewed | Each leaderboard row is one pass over the 100 puzzles; two runs used two passes. Standard errors are about 4-5 percentage points, so rows less than about 10 points apart cannot be ranked reliably. At least five runs are recommended. |
| Construct validity | **Measures stated capabilities**<br>Partially measures stated capabilities<br>Does not measure stated capabilities<br>Not Reviewed | Epoch says the benchmark asks for the single best move as judged by Stockfish. In 99 of 100 puzzles the reference move beats every alternative by at least 1.5 pawns, so the score tracks that skill. Epoch's wider suggestion that it plausibly reflects spatial reasoning and planning is hedged, and we did not test it. |

## Disclaimer

We undertook this audit without requesting additional information from Epoch AI to provide impartial feedback.
