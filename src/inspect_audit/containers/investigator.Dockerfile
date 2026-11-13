# The investigator's own box, beside the benchmark's, pulled by digest on Docker and
# Kubernetes alike (INVESTIGATOR_IMAGE in __init__.py). The `images` workflow
# (.github/workflows/images.yml) builds it, first exporting investigator.requirements.txt
# with hashes from uv.lock (group `investigator-box`); the base image is pinned by digest;
# only the Debian packages float with the base image's apt.
# The benchmark's packages are not installed here: its grader runs in the benchmark's
# own task, and its source is copied in for reading (or installed per job, FRICTION 74).
# With internet, the investigator can install anything else it needs.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN apt-get update && apt-get install -y --no-install-recommends \
        librsvg2-bin ca-certificates curl git jq ripgrep poppler-utils procps time \
    && rm -rf /var/lib/apt/lists/*
# inspect-ai to read the recorded attempts (/audit/logs/*.eval); pandas + pyarrow to
# analyse them; pillow to measure an image as well as look at one; scipy + sympy for
# independent numerical and symbolic checks
COPY investigator.requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
# fail the build if the analysis stack is incomplete
RUN python -c "import pandas, pyarrow, PIL, scipy, sympy; from inspect_ai.analysis import samples_df"
WORKDIR /audit
CMD ["sleep", "infinity"]
