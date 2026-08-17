# Default audit sandbox, used when the audited task declares none of its own.
# Deliberately minimal: the auditing agent runs host-side as a solver and reaches
# in with sandbox-scoped tools, so this only needs a shell, and Python for reading
# and running the benchmark's own code.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git jq ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /audit
CMD ["sleep", "infinity"]
