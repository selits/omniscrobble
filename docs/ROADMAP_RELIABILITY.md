# Reliability and Maintainability Plan

This plan develops four priorities identified in the project review: improve existing user flows, make the core easier to maintain, make sync recovery safer, and evaluate direct synchronization between Omniscrobble instances. It is a proposed sequence; implementation scope and release timing can be set when work begins.

## Goals

- Help users diagnose and resolve common setup and sync failures.
- Reduce the cost and risk of changing the route and test code.
- Make retry behavior clear when an upstream service may have accepted a request before timing out.
- Make an evidence-based decision about peer-to-peer (P2P) sync before committing to implementation.

## Phase 1: Reliability and usability pass

**Status: Complete.** The setup, connection, webhook, sync-failure, and runtime/data-recovery flows have been reviewed, and the checklist improvements below are implemented and covered by the project test suite.

### Phase 1 scope

Review the existing flows for initial setup, tracker and media-server connections, webhook delivery, sync failures, and recovery. For each flow, record the visible state, the explanation provided, the available next action, and how success is confirmed.

Use the current System recovery checks and activity delivery details as the foundation. Improve the explanations and recovery actions where users cannot tell what to do next or whether an action worked. Review admin and member access, privacy shielding, and mobile layouts as part of the same flows.

### Phase 1 deliverables

- A short inventory of the most important failure states and their current user-visible behavior.
- Targeted dashboard and recovery improvements for the highest-friction states.
- Updated feature or troubleshooting documentation for changed user workflows.

### Phase 1 flow inventory

| User flow | Existing entry point | Recovery guidance and action |
| --- | --- | --- |
| Initial setup | Dashboard **System** workspace | Read-only checks summarize configuration, connected trackers, listener state, and compatibility. Configuration findings link to the relevant settings tab; environment-only values explain the needed edit and restart. |
| Tracker and server connections | Settings Hub and explicit connection tests | A connection test displays its result and refreshes recorded reachability/version information in the recovery checklist. Secrets, hostnames, and account identifiers remain redacted. |
| Webhook delivery | System recovery check and Webhook Inspector | Missing `WEBHOOK_SECRET` gives setup instructions without opening the inspector. When configured, recorded authentication rejections or payload processing errors can open Webhook Inspector. |
| Sync failures | Activity workspace and offline queue | Activity details show per-destination outcomes and retry guidance. The recovery checklist reports queue counts and opens queue recovery; uncertain history writes require confirmation before retry. |
| Runtime and data recovery | System recovery checks, logs, and backup/restore | Worker states are listed individually; unsupported data/queue schemas are visibly marked as errors; logs and validated backup restore remain available from System. |

The checklist and navigation were reviewed for admin-only access and narrow mobile layouts. The recovery card headings and action rows wrap, and their buttons retain a touch-friendly minimum height.

### Phase 1 acceptance criteria

- Common setup and sync failures explain the cause when it is known and show a relevant next action.
- Completing a recovery action updates the displayed status or reports why it did not.
- Admin-only details remain scoped to admins; member data stays within the member's profile scope.
- The reviewed flows remain usable on mobile.

## Phase 2: Gradual core modularization

**Status: Complete.** The route ownership map is recorded in `docs/ARCHITECTURE.md`. Recovery, watch-list, account, reconciliation, and tracker/acquisition integration route families now register through focused modules under `app/routes/`. Their existing typed handlers and orchestration remain in `app/main.py`, preserving dependency behavior and authorization while separating route declarations. Recovery coverage is in `tests/test_recovery_routes.py`; `tests/test_route_modules.py` checks route registration and authorization; account and watch-list API coverage stays with their focused test modules.

### Phase 2 scope

Inventory route groups in `app/main.py` and identify cohesive boundaries. Candidate groups include accounts and authentication, watch lists, reconciliation, and integrations. Move one group at a time into focused route modules while preserving URLs, request and response formats, dependency behavior, and authorization checks.

As each route group moves, keep its coverage in feature-focused test modules. Keep shared fixtures where they make setup clearer. Update `docs/ARCHITECTURE.md` when files or component boundaries change.

### Phase 2 deliverables

- A route ownership map showing current and intended module boundaries.
- Small, independently reviewable route extractions.
- Feature-focused test modules for the extracted behavior.
- Synchronized architecture documentation.

### Phase 2 acceptance criteria

- Extracted routes keep their existing methods, paths, request and response behavior, and access controls.
- Each extraction has focused coverage for its public behavior and authorization boundaries.
- No route group is moved without updating the architecture manifest.
- The application can still be started and its routes registered after each extraction.

### Sequencing note

Prefer extracting the group that most directly supports Phase 1 improvements first. This allows the reliability work to clarify useful module boundaries instead of imposing a broad restructuring up front.

## Phase 3: Safer sync recovery and duplicate protection

**Status: Complete.** Queue items use an atomic claim with an expiry lease to prevent overlapping workers from dispatching the same item. Uncertain history writes are held for item-level confirmation, retry behavior is documented in the feature guide, and focused coverage covers concurrency, interrupted delivery, transient failures, and schema migration. Existing activity tests cover linked and partial delivery outcomes.

### Phase 3 scope

Trace an event through ingestion, tracker dispatch, queue persistence, activity history, and manual retry. Classify operations by retry safety, especially where a timeout leaves the remote outcome uncertain. Ensure retry details preserve the original account and destination and make uncertainty visible before an action is taken.

Review duplicate and concurrency handling for repeated retry requests, process restarts, partial delivery, and queue recovery. Keep user confirmation for operations where repeating a write could create duplicate watch history.

### Phase 3 deliverables

- A retry-safety map for supported queued operations.
- Clear activity explanations for definite failure, pending retry, partial delivery, and uncertain outcome.
- Focused automated coverage for timeout, partial-delivery, repeated-retry, concurrent-retry, and restart-recovery cases.
- Any necessary updates to API and feature documentation.

### Phase 3 acceptance criteria

- Admins can distinguish a known failure from an unknown remote outcome.
- History writes with uncertain outcomes require an explicit confirmation before retry.
- Retries preserve the account and destination selected when the event was first dispatched.
- Concurrent or repeated retry requests cannot silently dispatch the same operation twice.
- Retry outcomes remain linked to the originating activity record.

## Phase 4: Application bloat and maintainability review

**Status: Complete.** Stages 1–3 are implemented, and Stage 4's evidence-based keep/simplify/defer/retire decisions are documented in `docs/APP_BLOAT_REVIEW.md`. The review recommends no integration retirements on current evidence and ranks follow-up maintainability work. Phase 5 P2P sync discovery may now begin as a separate decision phase.

### Phase 4 scope

Build an evidence-based picture of where application size creates real maintenance or user costs. Separate maintained source from generated bundles and demo copies. Review the largest modules, overlapping services, dashboard feature surface, dependency footprint, and the cost of supporting less-used integrations. Use repository structure and code ownership, tests, and available usage or support evidence; do not treat line count alone as a reason to remove or rewrite code.

Identify options that reduce complexity while preserving important user workflows. Consider focused module extraction, consolidation of duplicate logic, clearer feature boundaries, dependency removal, and retirement only where usage and maintenance evidence support it. Estimate the change risk and ongoing savings for each option.

### Phase 4 deliverables

The repository and feature-surface discovery, ranked work, and integration support decisions are documented in [`APP_BLOAT_REVIEW.md`](./APP_BLOAT_REVIEW.md).

- A concise inventory of complexity hotspots, distinguishing source code from generated or copied assets.
- A feature and integration map that identifies overlap, support burden, and available usage evidence.
- A ranked set of improvement options with expected benefit, effort, risk, and user impact.
- An agreed staged bloat reduction plan, including explicit decisions about what to keep, simplify, defer, or consider retiring.

### Phase 4 acceptance criteria

- The review identifies concrete maintenance costs and supporting evidence rather than relying on size metrics alone.
- Each proposed change preserves named user workflows or explains the effect of changing them.
- The plan favors small, reversible steps and includes verification and documentation needs.
- No broad rewrite or feature removal begins as part of discovery; implementation follows the agreed plan.
- The review is complete before Phase 5 begins.

## Phase 5: P2P sync discovery and decision — deferred

**Status: Deferred by maintainer.** There is no confirmed use case for sharing data between separate Omniscrobble installations. Do not start discovery or implementation unless a concrete user need is raised.

### Phase 5 scope

Begin with a design and user-problem review, not implementation. Define the target scenarios and the data that would move between instances. Assess peer identity, authentication, discovery or connection setup, unavailable peers, conflict resolution, duplicate handling, retry behavior, and backup or restore interactions.

Compare direct P2P communication with simpler options such as scheduled export/import or a shared relay. Assess privacy and operational burden for self-hosted installations.

### Phase 5 deliverables

- A concise problem statement and target-user description.
- A data-flow and trust-boundary design.
- A conflict and failure-handling proposal.
- A comparison with simpler alternatives, including maintenance and deployment costs.
- A written go/no-go recommendation.

### Phase 5 decision gate

Proceed to a limited prototype only if the design identifies a clear user need, defines safe conflict and retry behavior, and offers a meaningful advantage over simpler alternatives. Otherwise, document the decision and defer P2P implementation.

## Program order and dependencies

1. Inventory high-friction user flows and select reliability improvements.
2. Extract the route group needed to support those improvements, then continue modularization incrementally.
3. Map retry safety and implement the recovery changes with focused coverage.
4. Review application bloat and agree on a staged maintainability plan.
5. Keep P2P deferred unless a concrete cross-installation use case is identified.

Phases 1–3 are complete reliability and maintainability work. Phase 4 sets the maintainability priorities. Phase 5 remains deferred because its user problem is unconfirmed.

## Risks to manage

- **Behavior drift during extraction:** preserve endpoint contracts and authorization checks, and move one cohesive route group at a time.
- **Unsafe retry assumptions:** treat ambiguous upstream outcomes as uncertain unless the integration provides a reliable way to verify success.
- **Scope growth:** prioritize the most common or costly failure states; defer broad dashboard redesign and unrelated feature additions.
- **P2P complexity:** keep discovery bounded and use the decision gate before prototyping distributed state or peer authentication.

## Follow-up discussion

The screenshots were from a previous release. The fixes below are implemented in the current branch, which is based on v3.3.0 and includes earlier mobile activity layout work.

- **App size and maintainability:** Review which parts of the app have grown hardest to understand or change, identify practical boundaries for reducing that complexity, and agree on a staged approach that fits alongside feature work.
- **Fixed — Quick-Mark modal on mobile:** The modal now scrolls internally when its content exceeds the phone viewport, keeping lower fields reachable.
- **Fixed — activity type/action presentation:** Type and action values now use plain read-only text styling instead of filled, input-like badges.
- **Fixed — manual scrobble recovery and feedback:** Transient Trakt and Simkl history failures are retained for recovery. Ambiguous history outcomes require confirmation before retry, rate limits retry automatically, and the modal reports failed or partial delivery instead of claiming success.

### Initial code review notes

- The prior-release modal issue came from a height-limited dialog without internal overflow scrolling.
- The prior-release activity values used filled backgrounds and rounded corners, which made static values resemble form controls.
- Manual scrobble previously recorded transient tracker errors without queueing them and returned top-level `success` regardless of tracker delivery. Recovery now preserves tracker identity and activity linkage, and the UI exposes the actual outcome.
