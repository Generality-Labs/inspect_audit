"""The 35 evaluations the inspect_evals docs catalogue marks Featured.

Copied from inspect_evals `docs/_templates/evals.ejs` (`featuredEvalIds`) on 2026-09-25. The list
lives nowhere structured upstream; that is recorded as a gap in the spec.
"""

FEATURED: tuple[str, ...] = (
    "cybench", "cybergym", "cve_bench", "mask", "hle", "ape",
    "agentharm", "scicode", "healthbench", "simpleqa", "agentdojo",
    "bigcodebench", "cti_realm", "gaia", "gdpval", "kernelbench", "usaco",
    "alignment_faking",
    "agentic_misalignment", "bfcl", "exploitbench", "frontier_cs", "gpqa",
    "lab_bench", "lab_bench_2", "lingoly", "mle_bench", "mlrc_bench",
    "paperbench", "mmlu_pro", "swe_lancer", "xstest", "strong_reject",
    "fortress", "bixbench",
)
