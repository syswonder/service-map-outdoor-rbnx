---
date: 2026-11-05
mode: files
base: working-tree-before-outdoor-mapping
head: uncommitted
---

# Implementation review

Reviewed the mapping service, shared outdoor contracts and Scene integration against Robonix maintainability, correctness, security and writing guidelines. Separate passes identified the issues below. The fixes are covered by core, Scene and process tests; no reported major issue remains open. No commit was created.

Scope: a new service (about 2,800 lines including extracted filtering, documentation and tests), shared IDL/contracts, Scene integration and the book's Scene guide. Generated code, ROS build outputs, environments and runtime recordings are excluded.

## Maintainability

- `small-functions`, minor, fixed: split outdoor panel rendering into View/Capture/Merge/Plan functions.
- `delete-dead-code`, minor, fixed: removed unused imports and an unused helper parameter.
- Retained the extracted portable filter/planner, core mapping operations and Scene facade because they separate persistent mapping data from Scene annotations. Original source hashes are recorded in `source-provenance.json`.

## Correctness

- `no-silent-failure`, major, fixed: a failed new recording no longer clears the active map; truncated final raw events no longer prevent recovery of the valid prefix.
- `test-the-behaviour`, major, fixed: filtered gaps remain separate even at identical positions; self-crossings split by station; partial overlaps split metadata boundaries and retain separation decisions; shared old junctions use one cut identity across multiple new branches.
- `contract-first`, major, fixed: Scene pins map operations and lifecycle subscriptions to configured providers, checks outdoor identity/version readiness, and holds semantics when the authoritative state cannot be confirmed. Outdoor loading does not wait for occupancy.
- `no-silent-failure`, major, fixed: unknown recording state blocks map changes. Example ROS communication uses explicit CycloneDDS loopback discovery; input receive waits are bounded.

## Security and data identity

- `validate-paths`, major, fixed: outdoor IDs reject trailing dots and Scene's reserved suffix. Saved IDs cannot be reused after deletion.
- `validate-paths`, major, fixed: cross-type duplicate IDs are rejected before map operations. Cached Scene ownership also prevents reusing an ID for another type. Changed coordinate identity marks annotations for confirmation.
- `validate-paths`, major, fixed: semantic holds apply to both `/api/regions` and the legacy `/api/annotations` alias. Stale-version edits are rejected.

## Writing

- `plain-statements`, minor, fixed: documented pytest installation and corrected the deployment setting to `perception.profile`.
- Navigation handoff explicitly documents the colleague service's current accepted fields and its missing gait/identity/DGPS execution support. Field walking and automatic gait execution are not claimed as verified.

## Validation

Both build scripts passed. Core and Scene tests: 65 passed in the ROS-compatible Python 3.10 environment. Complete synthetic Atlas/ROS/gRPC/Scene runs and Chromium checks passed. See `validation.md` for evidence, reproduction and remaining operator field acceptance.

Dropped concern: localization loads should increment generation. `MapLifecycle.msg` explicitly prohibits that increment for stable localization loads; the implementation follows the contract.


Follow-up hardware check: both native builds and live lifecycle activation passed on Lynx ARM64 using the existing Atlas/GNSS and actual gait feedback. Indoor quality 1 positions were recorded raw and excluded from mapping. New-map origin selection follows the user's first usable GNSS decision; Hangzhou and Beijing regression cases pass. Field walking and navigation execution remain pending.


UI follow-up: map type is preserved by server-rendered sidebar links and editor frames. Shared Camera/Logs no longer reset the Outdoor tab; the indoor viewer links are hidden outdoors. The existing loopback viewer proxy is reused for the explicitly configured local camera viewer with a finite timeout and an operation allowlist. Capture cancellation closes the stream while preserving raw events and skips draft generation; the capture panel opens automatically when recording is active. Robot browser checks passed without starting another physical recording. Actual camera video remains unverified because its RTSP sources are not currently publishing usable images.

## New-map navigation follow-up (2026-11-05)

Separate review passes found and resolved: duplicate `guided_path` overriding stairs tracking (`no-silent-failure`), a shadowed regex import breaking bound-map validation (`reuse-first`), and same-config binding skipping activation after a partial failure (`no-silent-failure`). The final Nav2 run also exposed sparse controller paths and blank-map startup waiting for TF; stairs paths now interpolate the reference and Driver INIT waits for process availability while task/command health gates remain strict.

The simplification pass removed the duplicate method/import and dead frozen-test rewrite. The remaining executor hooks serve the outdoor subclass; measured planning reuses the existing graph algorithm. No new security issue was found. Writing corrected the distinction between stop-speed thresholds and the required stopped duration.

Core/Scene regression: 60 passed. Navigation suite: 40 passed (overlaps the core run). Actual Nav2 virtual traversal and failure tests passed in domain 188; actual Atlas/Driver/localization map binding passed in domain 189. No physical motion was commanded.

## Legacy stack retirement review

Removed legacy weighted-map planning, old map backgrounds and the navigation worker's unused web/map/planning/obstacle-injection branches. Scene's map-bound consumer now belongs to `outdoor_nav.scene`; deployment files belong to the existing robot repository's `outdoor/` directory. Runtime imports no old project or campus assets. Synthetic Scene tests construct a recorded road instead of loading an old bundle.

Separate maintainability, correctness and security passes found stale configuration documentation and unbounded request journals. Updated the descriptions and limited the journal to 1024 records of at most 2 MiB while preserving existing IDs for retries. Private GNSS settings, credentials, recordings, generated code and environments are excluded from the robot repository.

Retirement validation (2026-11-06): 84 core/Scene/gRPC checks passed with the old map and adapter directories moved aside. Actual Atlas/localization binding and isolated real Nav2 stairs traversal passed again. Writing corrected stale links to old demo and historical deployment instructions. The installed robot remains in check mode; physical movement was not exercised.
