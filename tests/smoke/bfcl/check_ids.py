import collections, json, sys
from bench.task.bfcl import bfcl
from inspect_evals.bfcl.bfcl import EVAL_VERSION, BFCL_GITHUB_COMMIT

IDS = """irrelevance_228 live_irrelevance_558-171-0 live_multiple_209-91-3 live_simple_125-81-0 memory_kv_23-customer-23 memory_kv_86-student-6 memory_kv_39-healthcare-9 memory_kv_71-finance-16 memory_kv_87-student-7 memory_kv_91-student-11 memory_kv_97-student-17 multi_turn_base_23 multi_turn_miss_func_55 multi_turn_miss_func_129 multi_turn_miss_func_154 multi_turn_miss_param_6 multi_turn_miss_param_143 multi_turn_miss_param_183 multi_turn_long_context_139 parallel_189 web_search_base_20 web_search_base_57 web_search_base_54 web_search_base_35 simple_python_327 simple_java_14 multiple_6 parallel_multiple_70 live_multiple_285-129-0 live_multiple_457-145-8 live_parallel_multiple_21-18-0 irrelevance_189 live_irrelevance_89-2-77 live_irrelevance_604-193-6 multi_turn_base_7 multi_turn_base_8 multi_turn_base_108 multi_turn_miss_func_59 multi_turn_miss_param_50 multi_turn_long_context_166 multi_turn_long_context_179 web_search_base_0 web_search_base_28 web_search_base_53 web_search_base_75 web_search_base_89 web_search_base_97 memory_kv_24-customer-24 memory_kv_26-customer-26 memory_kv_55-finance-0""".split()
assert len(IDS) == 50 and len(set(IDS)) == 50, (len(IDS), len(set(IDS)))
t = bfcl()
samples = {str(s.id): s for s in t.dataset}
print("task version", t.version, "bfcl commit", BFCL_GITHUB_COMMIT)
print("metadata", json.dumps(t.metadata, default=str)[:600])
print("total samples", len(samples))
cats = collections.Counter(s.metadata["category_name"] for s in samples.values())
print("population by category", dict(cats))
missing = [i for i in IDS if i not in samples]
print("missing", missing)
sub = collections.Counter(samples[i].metadata["category_name"] for i in IDS if i in samples)
strata = collections.Counter(samples[i].metadata["stratum"] for i in IDS if i in samples)
print("subset by category", dict(sub)); print("subset by stratum", dict(strata))
for i in ["memory_kv_23-customer-23", "web_search_base_20", "multi_turn_base_7", "simple_python_327"]:
    s = samples[i]
    print("---", i, "target:", str(s.target)[:200]); print("input:", str(s.input)[:300]); print("meta keys:", list(s.metadata))
