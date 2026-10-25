# Friction log

Append-only: every problem hit while auditing benchmarks with Inspect Auditor, newest at the bottom, never rewritten. Each entry: date, symptom, cause (file:line), evidence (run id or measurement), status (verified, reviewer, worked around, fixed <commit>). A later entry can supersede an earlier one; the earlier one stays.

Entries 1-20 were collected 2026-10-22 to 2026-10-24 while running SWE-bench, SciCode, SQAV and CL-bench, plus a four-reviewer code sweep on 2026-10-23.

## inspect_audit

01. **Chat-input benchmarks are always refused by the drift check.** `check_same_items` (`_task.py`) compares the logged `input` with the installed dataset's `input` as objects. Inspect gives every `ChatMessage` a fresh random `id` on each load, so a chat input never compares equal. String inputs (SWE-bench, SciCode, SQAV) were unaffected. Evidence: `inv-clbench-sonnet-rpsayb663av08jnt` failed with `LogMismatchError: the installed benchmark's input differs` on sample 00b6a576; the logged and fresh messages differ only in `id`. Status: verified; fix compares message content without `id`, test `test_a_chat_input_matches_its_log_whatever_its_message_ids` (fails before, passes after).

02. **Judges declared only in task arguments get no credentials on Hawk.** Hawk provisions provider keys and base URLs only for models in `models` and `model_roles`. An investigation's judge lives in `task_args.judge`, so a judge on a different provider from the investigator has no key (e.g. investigator `openrouter/...`, judge `openai/gpt-5.1`). `hawk_config` emits `additional_models` only in restricted mode, and Hawk 3.7.2 has no `additional_models` field. Status: worked around for CL-bench by putting the investigator on the same provider (`openai/gpt-6-luna`). Proper fix needs a Hawk-declared place for judges.

03. **Direct-provider judges cannot be priced, so dollar-capped investigations refuse to start.** `prices_for_models` uses Inspect's model costs, falls back to OpenRouter only for `openrouter/` names, and deliberately refuses others (`test_unknown_direct_models_do_not_fall_back_to_openrouter`). `openai/gpt-5.1` has no Inspect cost. Status: worked around with `model_prices` declared in the job. Open question: keep the refusal or price direct models from the OpenRouter list.

04. **Regrade scores can be filed under the wrong scorer.** `_regrade.py:140,169` pairs scorer names with new score keys using `zip(..., strict=False)`; Inspect only stores a score when it is not None (`inspect_ai/_eval/score.py:528`), so one None shifts every later score onto the previous scorer's name. Status: verified in code.

05. **A `hawk:<set>/<file>.eval` log address is never downloaded.** `log_files` (`_log_sources.py:31`) returns any entry ending in `.eval`/`.json` unchanged before checking for `hawk:`. Status: verified in code.

06. **The auditor's token limit counts cache reads, so it acts as a turn cap.** Inspect's default `all` metering is `total_tokens` (`inspect_ai/util/_limit.py:434`), which includes cached re-reads of the whole context every turn. 20M tokens buys ~200 turns at ~100k context. `Task(cost_limit=...)` would make the budget dollars. Status: verified in code; both real auditor runs ended at the token limit.

07. **Retried runs are indexed twice.** Duplicates are detected on `(eval_id, sample_id, epoch)` (`_logs.py:243`), but a retry has a new eval_id and carries completed samples forward. Status: reviewer.

08. **Regrades ignore the judge's recorded settings.** `_regrade.py:106,79` replays judge role names but not `judge_configs` (temperature, effort), unlike `_task.py`. Status: reviewer.

09. **Copying the benchmark source grabs the whole package.** The `sys.modules` backstop (`_source.py:161-167`) copies every loaded module; for inspect_evals that is 1,596 files / 43 MB instead of 16, and the 100 MB cap can then drop the task's own data. Status: reviewer (measured by the reviewer).

10. **Two operators overwrite each other's audit key.** One shared middleman secret (`dispatch/hawk.py:241,265`); the one-audit-at-a-time check sees only this machine's records (`launcher.py:112`). Status: reviewer.

11. **Benchmark-rerun jobs have no spend cap.** The authoriser bounds only investigation `cost_limit` (`dispatch/policy.py:264-276`). Status: reviewer.

12. **Child jobs are orphaned when the auditor errors or hits a limit.** Only `submit_findings` checks them (`_findings.py:389`). Status: reviewer.

13. **A missing cost cap is refused only after the sandbox is provisioned** (`_investigator.py:146`). Status: reviewer.

14. **The drift check ignores target and choices.** `check_same_items` compares only `input`, so a changed reference answer passes. Status: reviewer.

## Hawk 3.7.2 (deployed)

15. **OpenRouter model names differ between submission and runtime.** The API accepts `openrouter/{org}/{slug}`; Inspect's openrouter provider strips one prefix and sends `{org}/{slug}`, which the proxy does not serve unless a bare alias exists (`openai/gpt-6-luna` does; `anthropic/claude-opus-5.5` and `deepseek/deepseek-v3.2` did not). The newer Hawk source's `openai-api/openrouter/openrouter/...` form is refused at submission by 3.7.2. Status: verified; worked around with Inspect's openai provider (`openai/openrouter/{org}/{slug}`), or by adding a bare alias in the proxy.

16. **Proxy replicas serve stale model lists after a model is added.** After `hawk proxy models add` and one reload, about half of requests still returned `model_not_found`; repeated reloads fixed it. The docs warn that HA without the shared cache diverges. Status: verified (cost the `ia-audit-scicode-opus4` launch).

## Inspect

17. **`openrouter/anthropic/*` never caches the conversation.** `_add_anthropic_cache_markers` (`openrouter.py`) only marks list-content messages and never sets top-level `cache_control`; agent messages are strings. Only system prompt and tools are cached. Upstream issue UKGovernmentBEIS/inspect_ai#5873 (open, accepted, unassigned). Workaround: `cache_prompt: false` plus `extra_body: {cache_control: {type: ephemeral}}` (works on Anthropic and Bedrock routes). Cost: the opus SciCode audit, ~$172 for nothing.

18. **The openai provider uses the Responses API for non-OpenAI models routed through it,** so a system prompt is sent as a `developer` message. Matters when reproducing a chat-completions harness. Workaround: `responses_api: false` model arg.

## CL-bench (Tencent-Hunyuan eval.py)

19. **The GPT-5.1 judge writes `//` comments inside its JSON, which fails parsing and scores 0.** 34 of ~1,600 judgements in our runs (all on long rubric lists); 3 were tasks the judge had marked solved. Our port keeps eval.py's behaviour (count as 0, record `outcome: unparsed`). Status: verified.

## Missing capabilities

20. **The auditor cannot re-judge a model-graded benchmark.** `_regrade.py` (re-score every logged answer, then re-score disagreements to split judge noise from a changed grader) exists only as a command-line tool, not as an auditor job. The auditor's box can run `inspect score`, but has no model credentials by design (`_audit_workspace.configure`: no env, no service account), so judge calls fail. Model-calling work only reaches credentials through jobs that Hawk runs. Evidence: on CL-bench the judge flipped Sonnet's answer on 043f89de from 0 to 1 when re-judged (`inv-clbench-sonnet2-uo49t4ikgyahg6zn`), and the auditor had no way to measure how often this happens across a run. Status: open. Candidate shapes: a regrade job, or a Scout scan that applies the benchmark's judge to each logged answer (Hawk runs scans with credentials); bugs 4 and 8 affect the regrade and need fixing first.

21. **2026-10-24. `hawk_config` writes direct-provider models in a form Hawk's submission check rejects.** It emits each model as `{package: inspect-ai, items: [{name: "openai/gpt-6.1-sol"}]}` (`dispatch/hawk.py` `group`). Hawk 3.7.2 then validates `openai/gpt-6.1-sol` against the proxy's public names, and the proxy serves `gpt-6.1-sol` (bare), not `openai/gpt-6.1-sol`. The same model passed when written as `{package: openai, name: openai, items: [{name: gpt-6.1-sol}]}` (CL-bench run `clbench-sol61-0-100`). Names that happen to exist with the prefix (`openai/gpt-6-luna`) pass either way, which hid this. Evidence: `hawk eval-set run` of the CL-bench audit config failed with `Middleman error: Models not found: ['openai/gpt-6.1-sol']`. Status: verified; worked around in the CL-bench audit config by writing the auditor's model in the provider form. Child jobs launched by the auditor use the same `group` and would hit it for any direct model without a prefixed public name.
