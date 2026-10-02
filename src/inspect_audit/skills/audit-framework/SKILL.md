---
name: audit-framework
description: Generality Labs assessment definitions and rubric. Read before assigning framework judgments; distinguish item findings from benchmark-wide evidence.
---

The pinned GL framework defines the questions below. Audit items and per-question defect
labels provide evidence for these questions; they are not substitutes for them. Apply only
checks within operator scope. Do not attempt to establish population representativeness or
cross-model effects from one sample. Report useful observations even when they do not settle
a framework check, with that limitation explicit.

Minor issues add undesired noise without changing headline conclusions. Major issues distort
results or their interpretation. Critical issues severely affect the dimension. No concern,
Not assessed and Not applicable have different meanings. Rate consequence using evidence,
not a fixed numerical defect threshold.

Source: Generality-Labs/audit_reports at ad7de89658b6794112c2f65847991c7ed41d9e30,
auditframework.sty and ScoringCriteria.tex. Definitions below are extracted from that registry.

- **A: Construct validity** — Does performance on the eval validly measure the capability or behaviour the eval aims to assess?
- **A.1: measurement distortion** — flaws in implementation validity dimensions distort measurement of the target construct
- **A.2: contamination** — benchmark items or their solutions were present in training data or otherwise accessible during training
- **A.3: memorisation** — performance depends on recalling a memorised solution rather than exercising the intended capability
- **A.4: eval awareness** — models recognise the evaluation context and alter their behaviour as a result
- **A.5: sandbagging** — models deliberately or strategically underperform relative to their capability
- **A.6: confounds** — scores are systematically affected by factors unrelated to the target construct
- **A.7: proxy measurement** — the measured outcome is only an imperfect proxy for the target capability or behaviour
- **A.8: capability vs propensity** — the evaluation conflates what a model can do with what it tends to do
- **B: Content validity** — Do the tasks adequately represent the target domain and situations the evaluation aims to cover?
- **B.1: coverage gaps** — important parts of the target domain or capability are absent or underrepresented
- **B.2: unrepresentative task distribution** — the distribution of tasks differs materially from the target domain
- **C: Task Specification** — Are individual tasks correctly specified, solvable, and free from defects that affect performance?
- **C.1: incorrect reference answer** — the reference answer is incorrect or inconsistent with the task
- **C.2: impossible tasks** — no correct answer is attainable as posed
- **C.3: missing information** — required parameters or context are not supplied
- **C.4: guessability** — tasks can be solved using superficial cues rather than the intended competence
- **C.5: prompt defects** — the prompts are ambiguous, incomplete, misleading, or otherwise fail to specify correctly what the model should do
- **C.6: hidden requirements** — success depends on unstated conventions or assumptions
- **C.7: unintended shortcuts** — answers are reachable without performing the intended work
- **D: Scaffolding** — Does the scaffolding provide the model with an appropriate setup for performing the task?
- **D.1: system prompt issues** — system-level instructions are ambiguous, misleading, or otherwise distort performance
- **D.2: planning or agent loop** — the agent structure constrains or alters performance
- **D.3: memory & context strategy** — the strategy for retaining and presenting context loses or obscures information needed to solve the task
- **D.4: retry/error feedback** — failures are surfaced poorly or recovery opportunities are handled inconsistently
- **D.5: coaching** — the scaffolding provides hints or information that artificially improves performance
- **E: Harness** — Does the evaluation infrastructure correctly execute the specified task and scaffolding?
- **E.1: tool-call handling** — tool calls are parsed, routed, or executed incorrectly
- **E.2: submission/termination logic** — episodes terminate earlier or later than intended
- **E.3: context delivery** — task materials or interaction history reach the model incompletely
- **E.4: message handling** — messages are truncated, reordered, duplicated, or otherwise mishandled
- **E.5: state management** — evaluation state is lost, corrupted, or inconsistently maintained
- **E.6: harness bugs** — other defects in the evaluation infrastructure affect performance
- **E.7: harness sensitivity** — results change materially across equivalent harnesses
- **F: Environment** — Does the task environment and its resources function as specified?
- **F.1: tool failures** — tools error, malfunction, or hang during episodes
- **F.2: missing resources** — files, packages, or services assumed by the task are absent
- **F.3: broken files & dependencies** — inputs or dependencies are corrupt, incompatible, or unreadable
- **F.4: non-determinism** — the environment varies between otherwise equivalent runs
- **F.5: information leakage** — the environment exposes answers or unintended hints
- **F.6: implementation differs from specification** — the environment behaves differently from its documented design
- **G: Grading** — Does the grading procedure correctly determine whether task performance satisfies the evaluation criteria?
- **G.1: false positives and false negatives** — the grading procedure incorrectly classifies responses as satisfying or failing the evaluation criteria
- **G.2: underspecified criteria** — the grading criteria do not clearly determine what counts as satisfying them
- **G.3: judge errors** — LLM judges make systematic, inconsistent, or otherwise erroneous grading decisions
- **G.4: surface-form sensitivity** — otherwise equivalent responses receive different scores because of differences in wording, formatting, or presentation
- **G.5: incorrect partial credit** — partial scores do not appropriately reflect the quality or correctness of the response
- **G.6: grader exploitation** — submissions can manipulate or exploit the grading procedure
- **H: Resource limits** — Do resource constraints provide an appropriate opportunity for models to perform on the task?
- **H.1: turn/token/time limits** — limits terminate productive attempts before the model can complete the task
- **H.2: resource sensitivity** — performance changes materially with the available resource budget
- **H.3: unequal constraint effects** — the same nominal constraint binds models differently
- **I: Informativeness** — Does the evaluation provide enough variation and signal to distinguish relevant differences between models?
- **I.1: saturation** — frontier models cluster at the attainable ceiling, limiting the benchmark's ability to distinguish between them
- **I.2: floor/ceiling effects** — the attainable score range compresses meaningful differences between models
- **I.3: clustering** — model scores cluster tightly relative to measurement uncertainty, providing little discriminative power between models
