"""Build the audit Task from the eval-set yaml without running it (no Hawk launch)."""
import os, sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
args = dict(cfg["tasks"][0]["items"][0]["args"])
args["output_dir"] = sys.argv[2]
os.environ.setdefault("HAWK_API_URL", args["hawk_api_url"])
from inspect_audit._audit import audit
t = audit(**args)
print("task built:", t.name if hasattr(t, "name") else t)
import json, pathlib
for p in pathlib.Path(sys.argv[2]).rglob("policy.json"):
    pol = json.loads(p.read_text()); print(p); print({k: pol[k] for k in ("task", "task_package", "investigation_package", "checks", "limits", "models", "supplied_logs")}); print("samples", len(pol["samples"])); break
