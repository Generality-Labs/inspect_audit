# Extend the image that runs the Python publisher (the Hawk runner on Hawk).
# Supply a preverified cache as a BuildKit named context, not a token/build arg.
ARG BASE_IMAGE=python:3.13-slim
FROM ${BASE_IMAGE}
COPY --from=template-cache / /opt/inspect-audit/templates/
ENV INSPECT_AUDIT_TEMPLATE_CACHE=/opt/inspect-audit/templates
