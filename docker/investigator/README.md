# Investigator image

The investigator's container image for Kubernetes sandboxes (e.g. METR's Hawk), where the `build:`-based sandbox that `investigation_compose` generates does not work because k8s can only pull published images. Build and push it once, then pass its name as the `investigator_image` argument to `investigation_values`:

```sh
docker build -t ghcr.io/OWNER/inspect-audit-auditor:latest docker/investigator
docker push ghcr.io/OWNER/inspect-audit-auditor:latest
```
