# Phase 2 archived research prototypes

These files are research artifacts, not production integrations. They do not include model weights, reference galleries, screenshots, datasets, runtime logs, browser runners or model-experiment launchers. No new experiment or runtime test was performed when preparing this publication preview.

- `step10_budget.py`: corrected standalone admission budget. It cannot cancel native calls; prior real experiments used a separate parent process as hard timeout protection, which is not included here.
- `step9_dom_boundary.js`: ordinary page-context collector. Invoke only on safe local controls. No cross-origin/closed-root access bypass and no input-value collection.
- `step10_dom_consistency.js`: requires the collector above. Exposes bounded observations and versions; it is not the full screenshot/request/response gate, production extension protocol or authentication system.
- `step7_text_prototype.py`: strict text-claim diagnostic; imports regex constants from `step6_text_context_variant.py`. The older `diagnose` in that dependency is historical, not the recommended entry point. Keep the two files together.

Known limitations: the DOM scripts do not prove atomic pixel consistency, trust hostile-page globals, or guarantee bounded traversal time merely because outputs are capped. No automatic browser launch, collection, server startup or production import is configured. Synthetic/development results cannot establish independent accuracy. Keep text claims, visual confirmation and risk decisions separate.

The phase is closed. These files are retained for review and provenance only; publication is not a declaration of production readiness. Use the containing repository's licensing policy; no new third-party asset or license is granted by this preview.
