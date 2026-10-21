"""Launch an investigation on its own capped OpenRouter key, and always revoke it.

An operator tool, not part of the audit or the investigator. For each investigation it
mints a fresh OpenRouter key with a credit limit, delivers it, launches the
investigation, and at the end records what the key spent and disables it. Every model
call the investigation and its jobs make bills to that key, which OpenRouter caps.

Delivery is per destination. On Hawk the key is installed in middleman as the provider
key assigned to a dedicated, non-admin investigator user (Hawk's per-user keys), and the
investigation is submitted as that user; nothing in the investigation sees the key.

Revocation does not depend on this process surviving: the investigation's record is
written before its key exists, `finish` can be repeated, and `reap` disables the key of
any investigation no longer running, including keys this machine has no record of.
"""
