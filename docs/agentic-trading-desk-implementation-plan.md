# Implementation Plan: Agentic Trading Desk

Date: 2026-03-31
Source design: `docs/agentic-trading-desk-office-hours-design.md`
Status: READY_FOR_BUILD

## Goal
Build the first credible version of the Agentic Trading Desk as an investment committee simulator for buy-side teams.

The product goal is not “many agents talking.” The product goal is one believable investment decision flow with typed evidence, visible disagreement, quant output, risk gating, PM approval, and trader handoff.

## Build Strategy
Start narrow, but do not build throwaway architecture.

Implementation order:
1. Contracts first
2. Spec authoring second
3. One runtime end-to-end
4. Frontend on canonical AG-UI events
5. Second runtime parity
6. Optional seats and scenario expansion

This avoids the classic mess where the UI is built around ad hoc transcripts and then has to be rewritten once typed objects appear.

## Phase Plan

### Phase 0: Contracts and skeleton
Deliverable: a runnable repo skeleton with typed contracts and one canonical scenario definition.

Scope:
- create repo structure
- define canonical role registry
- define stage registry
- define JSON schemas for core objects and AG-UI envelopes
- define one starter scenario: single-name catalyst / earnings decision
- define participation matrix format
- define conformance test harness inputs and expected outputs

Files/directories to create:
- `contracts/objects/`
- `contracts/agui/`
- `contracts/a2ui/`
- `spec/`
- `authoring/`
- `runtime/wayflow/`
- `runtime/langgraph/`
- `frontend/`
- `scenarios/`
- `tests/conformance/`

Acceptance criteria:
- schemas validate locally
- role IDs, stage IDs, and event IDs are canonicalized in one place
- one scenario file exists with required seats, optional seats, constraints, and expected artifacts

### Phase 1: WayFlow happy path
Deliverable: one complete run on the primary runtime with a minimal but truthful UI.

Scope:
- author the desk in PyAgentSpec 26.1.0
- export the spec artifact
- implement the WayFlow adapter
- implement append-only event logging and object materialization
- implement the minimal frontend with:
  - scenario header
  - seat panel
  - flow rail
  - evidence workspace
  - quant panel
  - risk panel
  - PM approval panel
  - trader handoff panel
- implement demo-mode PM approval behavior

Acceptance criteria:
- one scenario completes end-to-end on WayFlow
- frontend consumes canonical AG-UI events, not runtime-native events
- PM approval changes the final trade ticket
- run completes within demo budget under scripted mode

### Phase 2: Quant execution and risk contract
Deliverable: trustworthy quant and approval behavior.

Scope:
- implement sandboxed quant runner
- preload demo datasets
- capture code, stdout/stderr, metrics, and charts as artifacts
- implement approval state machine
- enforce risk re-check on PM edits
- define partial-metric fallback behavior

Acceptance criteria:
- quant outputs are executed and captured immutably
- PM edits trigger risk re-check deterministically
- failure modes are visible in the UI and event log

### Phase 3: LangGraph parity
Deliverable: same authored spec running on LangGraph with parity checks.

Scope:
- implement LangGraph adapter
- normalize LangGraph events into the same AG-UI stream
- implement parity comparator
- add CI conformance tests across both runtimes

Acceptance criteria:
- same scenario runs on both runtimes
- stage sequence matches
- required object set matches
- CI fails on parity regressions

### Phase 4: Full committee simulator
Deliverable: customer-facing committee experience with optional seats.

Scope:
- define full agent bench in spec
- implement participation matrix controls
- add evidence grouping and dedup rules in the UI
- add scenario selection
- add replay mode backed by event log + object store

Acceptance criteria:
- operator can choose participating seats by scenario
- evidence cards are grouped by theme, not transcript order
- replay works for at least one completed run

### Phase 5: OCI packaging and hardening
Deliverable: deployable demo environment.

Scope:
- OCI deployment path
- secret handling
- audit log surfacing
- artifact retention policy
- packaged demo mode for field use

Acceptance criteria:
- demo can be deployed repeatably
- secrets are not file-based
- audit events exist for runtime choice, approval, and ticket generation

## Recommended Technical Decisions

### Primary runtime sequence
- Build on `WayFlow` first for the initial happy path.
- Add `LangGraph` only after the canonical event contract and object model are proven.

Reason: if both runtimes are built before contracts settle, you double the mess.

### Demo-mode behavior
Use deterministic demo mode by default.

Rules:
- fixed scenario inputs
- cached datasets
- one bull round and one bear round
- scripted PM approval default with optional live override
- fixed stage timeouts
- partial quant fallback allowed, but must be labeled in the UI

### Data strategy
Use bundled, timestamped demo datasets first.

Do not fake live institutional feeds in v1. Label every source with provenance and freshness.

### UI strategy
The frontend should render typed objects plus stage state.

Never let the transcript become the source of truth. Transcript is audit context only.

## Suggested Repo Layout

```text
agentic-trading-desk/
  authoring/
    desk/
    roles/
    flows/
  spec/
    exported/
  contracts/
    objects/
    agui/
    a2ui/
  scenarios/
    single-name-earnings.json
  runtime/
    common/
    wayflow/
    langgraph/
  frontend/
    app/
    components/
    lib/
  services/
    quant-runner/
    object-store/
  tests/
    conformance/
    integration/
  docs/
```

## First 10 Build Tasks
1. Create canonical `role-registry.json` and `stage-registry.json`.
2. Create versioned schemas for core objects.
3. Create versioned schemas for AG-UI envelopes.
4. Write `single-name-earnings.json` scenario with constraints and seat participation.
5. Author the initial desk in PyAgentSpec with only core seats plus bull/bear.
6. Build the WayFlow adapter that emits canonical AG-UI envelopes.
7. Implement event log append + object-store materialization.
8. Build the minimal frontend shell that renders stage state and typed objects.
9. Implement the PM approval state machine and trader ticket rendering.
10. Add a conformance test that replays one run and validates required artifacts.

## Definition Of Done For First Public Demo
The first public demo is done when:
- a buy-side viewer can watch one name go through the committee flow end-to-end,
- the UI shows evidence, disagreement, quant, risk, PM approval, and trader output clearly,
- the run is auditable from typed objects and event history,
- the scenario is believable without hidden operator patching,
- the same authored spec can at least smoke-test on the second runtime.

## Explicit Non-Goals For First Public Demo
- broad scenario authoring studio
- full live-data integration
- complex multi-asset support
- high-frequency or execution-grade trading logic
- polished design-system work beyond what is needed for trust and clarity

## Immediate Next Move
Start with Phase 0 and Phase 1 together:
- contracts,
- role/stage registries,
- starter scenario,
- WayFlow happy path,
- minimal frontend.

That is the shortest path to something real.

## Design Review Addendum (Current State, 2026-04-01)

Review scope:
- Plan file: this document
- Current UI implementation: `frontend/app/index.html` and `frontend/app/app.js`
- Branch: `main`
- Base branch used for diffing: `main`

Assumption used for this run:
- Focus all 7 design dimensions now (no narrowed focus requested)

### System audit
- UI scope is present and substantial: business-facing committee app, phase tabs, replay, diagnostics drawer, and risk/decision surfaces.
- `DESIGN.md` now exists and is the active design source of truth for business-first UI decisions.
- `CLAUDE.md` exists and routes UI work through the design system before implementation.
- Scenario definitions are currently split between a runtime-backed Python catalog and one older YAML artifact under `scenarios/`.
- Prior design review guidance in this addendum is partially stale relative to the current business-first redesign.

### Step 0 rating
- Initial design completeness: **6/10**
- Why: the build has strong structure and useful panel coverage, but the plan does not fully specify hierarchy priorities, state behavior matrix, accessibility contract, mobile intent per region, or anti-slop constraints.
- What 10/10 looks like for this plan: screen hierarchy, full interaction-state matrix, explicit accessibility and responsive behavior, codified visual system rules, and no ambiguity on unresolved UX decisions.

### Design system status
`DESIGN.md` exists. This addendum should defer to that file for visual and product-surface rules.

### What already exists
Reuse these existing patterns rather than replacing them:
- Business-first shell with demo controls and diagnostics moved into secondary drawers.
- Phase-gated interaction model in the phase navigator (`MACRO_PHASES` in `frontend/app/app.js`).
- Typed-object-first rendering model (evidence, claims, metrics, constraints, decisions, tickets) instead of transcript-only UI.
- Existing tokenized color variables and semantic classes in `frontend/app/index.html` (`:root`, status badges, outcome banners, lean pills).
- Existing responsive breakpoints at `1180px`, `900px`, and `640px`.

### Pass 1: Information Architecture
- Score: **6/10 -> 9/10**
- Gap: IA existed in code, but the plan did not lock content priority and region ownership.
- Fix added below.

#### IA hierarchy (authoritative)
Primary visual order (first-look to last-look):
1. Scenario brief and current business phase
2. Final decision packet / execution / monitoring summaries
3. Risk gate status (is action blocked/adjusted/passed?)
4. Debate + analyst perspectives (why this recommendation exists)
5. Evidence / quant / exposure (supporting confidence details)
6. Demo controls and diagnostics (operator-only context)

#### IA screen map
```text
HEADER
  Scenario | Ticker | Demo Controls | Diagnostics

MAIN
  TOP BUSINESS STRIP
    Desk View | Risk Status | Confidence | Vote counts
    Phase navigator

  LEFT COLUMN
    Scenario Brief

  CENTER WORKSPACE
    Execution Summary
    Monitoring Summary
    Final Decision Packet
    Run Conclusion
    Risk Gate
    Agent Debate
    Analyst Perspectives

    Quant Analyst Summary

  RIGHT COLUMN
    Evidence (support/risk)
    Quant Metrics
    Portfolio Exposure Preview
    Stock Price + chart

  SECONDARY DRAWERS
    Demo Controls
      Breaking News | Debate Depth | Replay | Runtime | Run actions | Seat selection
    Diagnostics
      Status | Stage gate | LLM status | Live handoffs | Transcript | Audit Trail
```

### Pass 2: Interaction State Coverage
- Score: **4/10 -> 8/10**
- Gap: many fallback strings exist in code, but the plan did not define state behavior as contract-level UX.
- Fix added below.

#### Interaction state contract
| Feature | Loading | Empty | Error | Success | Partial |
|---|---|---|---|---|---|
| Scenario + seat setup | Controls disabled while scenarios load | "No scenarios available" + retry | "Scenario load failed" with retry CTA | Scenario and seats render | Required seats shown, optional load delayed |
| Run state rail | Running badge + disabled start | "Phase 0 of 5 · Waiting to run" | "Current phase still running" / transport errors | Phase transitions and completions visible | Paused after boundary with explicit next-phase CTA |
| Analyst perspectives | "Queued for information gathering" | "No info-gathering analysts active" | Upstream object parse failures logged, panel stays visible | Cards show stance, confidence, summary | Some seats active, others waiting |
| Agent debate | "Waiting for sequential call-and-response" | No turns yet message | Event stream parse warning in status line | Turn stream with role, stance, response chain | Debate complete but synthesis still pending |
| Risk gate | Pending with neutral note | No constraints yet message | Risk recheck failed callout | Passed/warning/blocked with constraints | Constraints visible but PM decision pending |
| Final decision packet | Waiting for PM stage | "Final decision package appears after PM review" | Missing packet after run complete flagged | Outcome + claims + constraints + actions | Decision exists but ticket fields incomplete |
| Stock price module | "Loading Yahoo Finance price data..." | "Waiting for price data" | "Market data unavailable: ..." | Price, change pill, trendlines render | Price available, trendlines unavailable |
| Replay + audit | "Loading replay ..." | Empty event log message | Replay/audit load failed with retry path | Replay loaded with event timeline | Replay metadata loaded without event log |

### Pass 3: User Journey and Emotional Arc
- Score: **5/10 -> 8/10**
- Gap: emotional journey was implied, not specified.
- Fix added below.

#### Storyboard
| Step | User does | User feels | Plan now specifies |
|---|---|---|---|
| 1 | Lands on app, sees scenario brief + current phase | Oriented, cautious | First-look hierarchy prioritizes business value over simulator controls |
| 2 | Chooses scenario, ticker, and optionally opens controls | In control | Explicit business surface versus operator surface split |
| 3 | Starts run and watches staged progression | Trust building | Phase-gate semantics and partial-state behavior |
| 4 | Reviews debate, evidence, quant, risk | Analytical confidence | Object-first rendering and support/risk split |
| 5 | Reviews PM outcome + trade ticket | Decision clarity | Final packet contract and required change summaries |
| 6 | Opens diagnostics or replay when needed | Verifiability | Audit/replay state handling without cluttering the main business page |

Time horizon:
- 5 seconds: user understands run status and current phase.
- 5 minutes: user trusts recommendation path from evidence to risk to PM verdict.
- Long-term: user can replay and audit decisions consistently across runtimes.

### Pass 4: AI Slop Risk
- Score: **7/10 -> 9/10**
- Classifier: **APP UI**
- Litmus checks:
  - Brand/product unmistakable in first screen: **Yes**
  - One strong visual anchor: **Yes** (run-state rail + decision region)
  - Scannable by headings only: **Mostly**
  - One job per section: **Mostly**
  - Cards necessary: **Partially** (some decorative density remains)
  - Motion supports hierarchy: **Yes, limited**
  - Premium without shadows: **Mostly**

Current risks to manage:
- Card density is high across almost every region, which can flatten hierarchy.
- Scenario truth is split across runtime code and YAML artifacts, which creates requirements drift.
- PM review still resolves via scripted demo defaults rather than live human intervention.

Guardrails for future UI updates:
- Preserve app-workspace framing, avoid landing-page tropes and generic hero patterns.
- Keep one dominant action context per panel, do not add decorative cards without interaction value.
- Prefer data hierarchy and typography contrast over additional gradients and chrome.

### Pass 5: Design System Alignment
- Score: **3/10 -> 7/10**
- Gap: the system source of truth now exists, but this plan addendum still reflects an older IA.
- Interim fix added below.

#### Current design contract
- Follow `DESIGN.md` for scenario brief requirements, operator-versus-business surface rules, and execution/monitoring structure.
- Keep primary decision panels prominent and diagnostics visually secondary.
- Preserve typed-object rendering as the primary UI model; transcript remains forensic context only.
- Rule: if this addendum conflicts with `DESIGN.md`, the design system wins.

### Pass 6: Responsive and Accessibility
- Score: **5/10 -> 8/10**
- Gap: breakpoints exist, but behavior intent and a11y contract were not explicitly documented.
- Fix added below.

#### Responsive behavior contract
- `>=1180px`: three-column desk layout (rail/workspace/sidebar).
- `900px-1179px`: single-column flow with preserved section order.
- `640px-899px`: compressed controls grid, static header, simplified panel spacing.
- `<640px`: one-column controls, larger body text in narrative blocks, reduced visual density.

Mobile intent:
- Phase controls and run status remain above fold.
- Decision packet and risk gate remain above transcript/audit.
- Transcript and audit collapse lower in flow to avoid burying action surfaces.

#### Accessibility contract
- Status and gate messaging must be visible and announced:
  - diagnostics messaging should remain announced via `aria-live="polite"` even when moved off the primary business surface.
- Preserve keyboard-first path:
  - scenario input -> phase tab action -> decision actions -> diagnostics/replay.
- Minimum touch target remains `44px` for primary controls.
- Maintain semantic headings and region labeling for panel groups.
- Contrast rule: status pills and muted metadata must maintain readable contrast on light backgrounds.

### Pass 7: Unresolved design decisions
- Score: **5 decisions surfaced, 3 resolved by default assumptions, 2 deferred**

Resolved now (assumed defaults):
1. Business-surface primacy over operator-surface primacy.
2. Keep evidence split as "Top Support" and "Top Risk" instead of mixed feed.
3. Keep stage-gated progression model, not free-running stream.

Deferred (needs explicit product call):
| Decision needed | If deferred, what happens |
|---|---|
| Should transcript auto-scroll on every new event or respect manual scroll lock? | Users may lose context during live runs or miss newest events |
| Should live PM approval replace the scripted demo default in the primary flow? | Product will remain partially demo-biased during the approval stage |

### Not in scope
- Visual mockup generation and approval board flow (design binary unavailable in this environment).
- Full design-system rebrand or typography overhaul.
- Re-layout of runtime orchestration mechanics.
- Multi-scenario nav IA beyond current single-session desk shell.

### Proposed TODO candidates
1. Consolidate scenario source of truth so runtime catalog and `scenarios/` artifacts do not drift.
2. Update stage-contract docs to match implemented order: `gather -> quantify -> debate -> synthesize -> risk_review -> pm_review -> trade_finalize -> monitor`.
3. Decide whether PM review should stay demo-scripted or become truly human-paced in the main flow.
4. Define transcript auto-scroll behavior with explicit user override.

### Completion summary
```
+====================================================================+
|         DESIGN PLAN REVIEW - COMPLETION SUMMARY                    |
+====================================================================+
| System Audit         | UI scope present, DESIGN.md missing         |
| Step 0               | 6/10 initial, all 7 dimensions reviewed     |
| Pass 1  (Info Arch)  | 6/10 -> 9/10 after fixes                    |
| Pass 2  (States)     | 4/10 -> 8/10 after fixes                    |
| Pass 3  (Journey)    | 5/10 -> 8/10 after fixes                    |
| Pass 4  (AI Slop)    | 7/10 -> 9/10 after fixes                    |
| Pass 5  (Design Sys) | 3/10 -> 7/10 after fixes                    |
| Pass 6  (Responsive) | 5/10 -> 8/10 after fixes                    |
| Pass 7  (Decisions)  | 3 resolved, 2 deferred                      |
+--------------------------------------------------------------------+
| NOT in scope         | written (4 items)                           |
| What already exists  | written                                     |
| TODOS.md updates     | 4 items proposed                            |
| Approved Mockups     | 0 generated, 0 approved                     |
| Decisions made       | 3 added to plan                             |
| Decisions deferred   | 2 listed above                              |
| Overall design score | 6/10 -> 8/10                                |
+====================================================================+
```

Plan status:
- **Design-complete enough to continue build with constraints.**
- Recommended next review gate before shipping: `/plan-eng-review` (required gate).
- If visual direction is being reconsidered, run `/design-shotgun` before implementation polish.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture and tests (required) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | ISSUES_OPEN | score: 6/10 -> 8/10, 3 decisions made, 2 deferred |

**UNRESOLVED:** 2 deferred design decisions.
**VERDICT:** DESIGN REVIEW RECORDED, eng review required before shipping.
