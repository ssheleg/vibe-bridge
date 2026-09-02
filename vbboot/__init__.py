"""vbboot — the shell's bootstrap, the one part that never updates itself.

It ships INSIDE the signed .app bundle (ADR-0006) and its whole job is to
decide which copy of `vibebridge` to run, then hand over. Two rules follow
from living in the bundle:

* it imports nothing from `vibebridge` — that package is the payload, and the
  bootstrap must be able to run when the payload is missing, half-written or
  broken;
* it uses the standard library only — every third-party dependency lives in
  the bundle beside it, but taking one here would tie the trust anchor to a
  wheel it does not need.
"""
#: Поверхность, которую оболочка обещает payload. Здесь она НАЗВАНА, а
#: что именно из неё зовут и с какой версии — в `vibebridge/shell_api.py`;
#: список висел на одном `layout`, пока payload звал ещё и
#: `runner.shell_version` (F-9).
__all__ = ["layout", "runner"]
