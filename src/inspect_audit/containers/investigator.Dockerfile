# The investigator's own box, beside the benchmark's. Built in place for a local Docker
# run; published for Kubernetes (Hawk), where images must be pullable:
#   docker build --platform linux/amd64 --build-arg INSPECT_VERSION=<pinned inspect-ai> \
#     -f investigator.Dockerfile -t ghcr.io/generality-labs/inspect-audit-auditor:investigator .
# then pin its digest as INVESTIGATOR_IMAGE in __init__.py.
# The benchmark's packages are not installed here: its grader runs in the benchmark's
# own task, and its source is copied in for reading (or installed per job, FRICTION 74).
# With internet, the investigator can install anything else it needs.
FROM python:3.12-slim
ARG INSPECT_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
        librsvg2-bin ca-certificates curl git jq ripgrep poppler-utils procps time \
    && rm -rf /var/lib/apt/lists/*
# inspect-ai to read the recorded attempts (/audit/logs/*.eval); pandas + pyarrow to
# analyse them; pillow to measure an image as well as look at one; scipy + sympy for
# independent numerical and symbolic checks
RUN pip install --no-cache-dir "inspect-ai==${INSPECT_VERSION}" pandas pyarrow pillow scipy sympy
# fail the build if the analysis stack is incomplete
RUN python -c "import pandas, pyarrow, PIL, scipy, sympy; from inspect_ai.analysis import samples_df"
WORKDIR /audit
CMD ["sleep", "infinity"]
