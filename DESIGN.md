# Design System — Agentic Trading Desk

## Product Context
- **What this is:** A buy-side investment committee simulator that turns multi-agent research, quant, risk, and PM approval into one auditable trade decision.
- **Who it's for:** Trading platform leaders, portfolio managers, research managers, and solution engineers demoing institutional AI workflows.
- **Space/industry:** Financial services, institutional research tooling, decision support.
- **Project type:** Operational web app dashboard with replay and audit workflows.

## Aesthetic Direction
- **Direction:** Institutional Command Surface.
- **Decoration level:** Intentional, clean gradients and hierarchy accents, no decorative noise.
- **Mood:** Calm under pressure. The screen should feel deliberate, high-trust, and decision-oriented, not chatty or consumer-fintech flashy.
- **Reference context:** Existing product docs in `docs/agentic-trading-desk-office-hours-design.md` and implementation behavior in `frontend/app/`.

## Typography
- **Display/Hero:** `Avenir Next` (fallback `Plus Jakarta Sans`, `Segoe UI`, sans-serif), compact and executive.
- **Body:** `IBM Plex Sans` (fallback system sans), tuned for dense operational reading.
- **UI/Labels:** Same family as body, uppercase sparingly for badges and metadata.
- **Data/Tables:** `IBM Plex Sans` with tabular numerals where possible (`font-variant-numeric: tabular-nums`).
- **Code:** `IBM Plex Mono` (fallback `SFMono-Regular`, `Menlo`, `Consolas`, monospace).
- **Loading:** Use local/system-first stacks for resilience; if webfont rollout is needed, introduce it behind a performance guard.
- **Scale:** 10, 11, 12, 13, 14, 15, 18, 22, 28, 30 px. Headline emphasis reserved for decision-critical values only.

## Color
- **Approach:** Restrained and semantic. Color means state, not decoration.
- **Primary:** `#0F5CC0` for active workflow controls and system emphasis.
- **Secondary:** `#2B4E7A` for structural metadata and support hierarchy.
- **Neutrals:** `#FFFFFF`, `#F6F8FB`, `#D9E0EA`, `#5A667D`, `#172033`.
- **Semantic:**
  - Success: `#0F7B41`
  - Warning: `#8E4B00`
  - Error: `#B23333`
  - Info: `#355680`
- **Dark mode:** Not in current scope. If introduced, reduce saturation 10-20%, preserve semantic contrast, and keep status polarity unchanged.

## Spacing
- **Base unit:** 4px with 8px rhythm for panel composition.
- **Density:** Comfortable-compact, optimized for high signal without crowding.
- **Scale:** 2, 4, 8, 12, 16, 24, 32, 48, 64 px.

## Layout
- **Approach:** Hybrid. Grid-disciplined shell with section-level emphasis for decision surfaces.
- **Grid:** Three-column desktop (`rail / workspace / context`) collapsing to one column below `1180px`.
- **Max content width:** Full-width operational shell with panelized sections.
- **Border radius scale:** 8px (micro), 10px (inputs/chips), 12px (cards), 16px (panels), 999px (badges).

## Motion
- **Approach:** Minimal-functional.
- **Easing:** Enter `ease-out`, exit `ease-in`, state transitions `ease-in-out`.
- **Duration:** Micro 80-120ms, short 150-220ms, medium 250-320ms.
- **Usage rule:** Motion should indicate live stage progression or state change. Avoid ornamental animation loops.

## Scenario & Phase UX Rules
- Scenario must never be just a dropdown. Always show a brief with thesis, constraints, active seats, and PM default posture.
- The default screen is for business users. Demo controls and diagnostics belong in secondary surfaces, not on the primary page.
- Execution and Monitoring panels must be structured plans:
  - Action now
  - Context
  - Checkpoint
  - Conditions/risk watch lists
- Status messaging (`run status`, `stage gate`, `llm diagnostics`) stays available, but inside diagnostics rather than the business surface.
- Final decision and risk gating remain the primary judgment surfaces. Execution and monitoring support, not overshadow, those decisions.

## Component Principles
- **Committee View:** Business spine. The default page should answer recommendation, risk, confidence, and next action in under 10 seconds.
- **Demo Controls:** Operator-only surface for runtime, replay, debate depth, and run actions.
- **Diagnostics:** Transcript, audit, run state, and LLM health. Useful, but never first.
- **Scenario Brief:** Setup clarity surface. User should understand "what are we deciding?" in under 10 seconds.
- **Execution/Monitoring Blocks:** Checklist-first design, avoid prose-only guidance.
- **Decision Packet:** Human-approval artifact. Keep concise, scannable, and exportable.
- **Audit/Transcript:** Forensics region. Keep lower visual priority than active decision surfaces.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-01 | Created initial design system | Establish a concrete visual contract for scenario clarity and phase guidance |
| 2026-04-01 | Added scenario brief pattern | Fix setup ambiguity before phase execution |
| 2026-04-01 | Refactored execution/monitoring to structured plans | Make late-phase outputs actionable, not narrative-only |
| 2026-04-01 | Split business view from demo controls and diagnostics | Remove simulator clutter from the default PM-facing surface |
