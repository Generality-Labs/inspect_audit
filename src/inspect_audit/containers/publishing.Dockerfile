FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends \
    latexmk texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra texlive-science \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /report
ENTRYPOINT ["latexmk"]
