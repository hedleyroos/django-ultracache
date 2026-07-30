Implement the improvements roadmap in `docs/plans/improvements.md`. Read that
file first and keep it open throughout — it is the authoritative spec: 6 phases,
40 numbered items, with verified file/line references and a mapping table. Also
read `architecture.md` for orientation. Target: release 3.0, Django ≥ 4.1,
Python ≥ 3.11.

## Ground rules

1. **Branch and baseline.** Create branch `improvements-3.0` from `develop`.
   The working tree contains uncommitted WIP (middleware marker fix + lazy
   recorder, profiling instrumentation in `monkey.py`, `benchmark.py`,
   `toxb.ini`). Commit all of it first as `WIP: performance investigation
   baseline` so every phase starts from a known, committed state. Phase 3
   strips the instrumentation — do not strip it earlier.

2. **One clean subagent per phase, strictly sequential.** Spawn a fresh
   subagent for each phase using the per-phase brief template at the bottom.
   Never run two phases in parallel — they touch the same files and later
   phases depend on earlier ones. After each subagent reports back, YOU (the
   orchestrator) must: read its full diff critically, run the test gate
   yourself (rule 3), and only then commit as `Phase N: <short title>`. Do not
   start phase N+1 until phase N is committed and green. If a subagent's work
   fails your review or the tests, spawn a fresh fix-up subagent with the
   failure details rather than patching large amounts yourself.

3. **Test gate.** Run under the repo virtualenv, in BOTH settings variants:

       ve/bin/python manage.py test ultracache.tests --nomigrations --settings=ultracache.tests.settings.60
       ve/bin/python manage.py test ultracache.tests --nomigrations --settings=ultracache.tests.settings.60_no_sites

   If `tox` is available prefer `tox -e py312-django60,py312-django60-no-sites`
   per phase, and run the FULL `tox` matrix (4.1/4.2/5.0/6.0, sites and
   no-sites) after Phases 1, 3, and 6 at minimum. Never proceed on red. Never
   skip the no-sites variant — code branches on `django.contrib.sites`.

4. **Testing bar — this is the most important rule.** ultracache is deep
   infrastructure: a monkey-patched `Model.__getattribute__`, signal-driven
   invalidation, and cache-side metadata. Bugs here are near impossible to
   debug on a running site, so exhaustive tests are the deliverable as much as
   the fixes:
   - Every bug fix starts with a **failing regression test** written first;
     fix and test land together. The subagent must state in its report that it
     saw the test fail before the fix.
   - Every behavioural change is tested under both sites and no-sites
     settings.
   - Prefer **end-to-end invalidation tests** over pure unit tests where
     feasible: render/cache → mutate or delete the object → assert the cache
     entry is actually gone and re-renders fresh. Same for the content-type
     ("new object") invalidation path.
   - Never delete, skip, or weaken an existing test to get green. If an
     existing expectation legitimately changes, the report must cite the
     roadmap item that justifies it.
   - Phases 1–4 each write tests for what they change; Phase 5 is additionally
     the exhaustive sweep. The Phase 5 subagent must also run
     `coverage run` / `coverage report` and drive `ultracache/` (excluding
     `tests/`) to ≥ 90% line coverage, listing any lines it deliberately left
     uncovered and why.

5. **Decisions already made — do not re-litigate, do not stop to ask:**
   - Item 16: KEEP `UltraCacheMiddleware` and its cleanup, and ALSO add a
     `request_finished` signal cleanup as a safety net for deployments missing
     the middleware. Document the middleware as recommended rather than
     strictly required.
   - Item 19: delete `toxb.ini`.
   - Item 26: implement `cache_alias` (do not just delete the README section).
   - Item 24: the new cache key prefix is `ucache3-`, applied consistently
     everywhere keys are built.
   - Phase 3 benchmarking: run `benchmark.py` and record the numbers BEFORE
     making any Phase 3 change (the instrumentation is still present at that
     point), then again after all Phase 3 items land, and put both sets of
     numbers in `CHANGELOG.rst`.

6. **Finish line.** After Phase 6: bump the version to 3.0, finalize
   `CHANGELOG.rst` (per-phase entries, the `ucache3-` format bump, the
   benchmark numbers), run the entire tox matrix, then spawn one final fresh
   subagent to do an adversarial review of the whole branch diff
   (`git diff develop...HEAD`) hunting specifically for: behaviour changes not
   covered by a test, invalidation paths that could silently stop firing, and
   thread/context leaks. Fix everything it finds, re-run the matrix, and
   present a final summary comparing the diff against the roadmap's 40 items
   (done / deviated / deferred, with reasons).

## Per-phase subagent brief (template)

> You are implementing **Phase N** of `docs/plans/improvements.md` in
> `/data4/projects/django-ultracache`, on branch `improvements-3.0`. Read that
> file fully, plus `architecture.md`, then implement ONLY the Phase N items,
> exactly as specified — including every test those items call for. Rules:
> write a failing regression test before each bug fix and confirm you saw it
> fail; run
> `ve/bin/python manage.py test ultracache.tests --nomigrations --settings=ultracache.tests.settings.60`
> AND the `60_no_sites` variant until both are fully green; do not touch items
> belonging to other phases; do not reformat or "improve" unrelated code; do
> not commit — leave the working tree for orchestrator review. Decisions
> already made for you: [paste the relevant bullets from rule 5]. Report back:
> a list of items completed with a one-line note each, files changed, test
> counts before/after, confirmation each regression test failed pre-fix, and
> any deviation from the spec with justification.
