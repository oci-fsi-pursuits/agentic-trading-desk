# Scenario Redesign Proposal: Agentic Trading Desk

Date: 2026-04-01
Status: PROPOSED
Scope: Scenario model, scenario differentiation, terminology normalization, and less-deterministic PM behavior

## Why This Matters

The current desk has four scenarios, but they are not yet four meaningfully different workflows.

Current weaknesses:
- scenario differentiation is mostly in ticker, summary text, and constraints
- all scenarios share almost the same seat mix and execution spine
- PM decisions are effectively hardcoded through `scripted_pm_default`
- terminology still drifts between `bull` / `bear` and `long` / `short`
- scenario truth is split between the runtime-backed Python catalog and an older YAML artifact

The result is a demo that looks broader than it feels. The UI says there are multiple scenarios, but the underlying experience is still too uniform.

## Goals

1. Make each scenario answer a distinct investment question.
2. Normalize all business-facing posture language to `long`, `short`, `neutral`.
3. Define explicit scenario-specific execution paths and branch logic.
4. Replace fixed PM outputs with a policy-driven, reproducible decision model.
5. Consolidate scenario truth into one canonical source.

## Canonical Terminology

Use these terms consistently across docs, runtime, and UI:

- `stance`: `long | short | neutral`
- `decision_outcome`: `approved | approved_with_changes | rejected`
- `position_action`: `initiate | add | hold | trim | exit | defer`
- `trade_side`: trader-facing instruction such as `BUY`, `SELL`, `PAIR`, `HOLD`

Rules:
- business-facing recommendation language should always use `long`, `short`, or `neutral`
- role names may remain expressive, but emitted stances must normalize to `long`, `short`, or `neutral`
- if a scenario needs more nuance than stance alone, add `position_action` instead of inventing new stance words

## Scenario Design Principles

Every scenario should define:
- what decision the desk is trying to make
- what initial position state the desk starts from
- what end states are allowed
- what evidence matters most
- what makes PM approval hard
- what trader output must contain
- what monitoring looks like after the decision

Every scenario should differ on at least three dimensions:
- decision question
- branch conditions
- execution or monitoring output shape

Changing only the ticker or the constraint labels is not enough.

## Proposed Scenario Contract v2

The scenario contract should move from a flat descriptive object to an explicit behavioral one.

```yaml
scenario_id: single_name_earnings
name: Earnings Committee
scenario_type: pre_event_initiation
decision_question: Should the desk initiate a pre-earnings position, stay neutral, or defer?
primary_runtime: wayflow
parity_runtime: langgraph

instrument:
  primary: NVDA
  peers: []
  display_label: NVDA

starting_position_state:
  posture: neutral
  size_bps: 0
  existing_position: false

allowed_end_states:
  stances: [long, neutral, short]
  actions: [initiate, defer]

seat_plan:
  required:
    - market_analyst
    - news_analyst
    - fundamentals_analyst
    - bull_researcher
    - bear_researcher
    - research_manager
    - quant_analyst
    - risk_manager
    - portfolio_manager
    - trader
  optional:
    - social_analyst
    - macro_economist
    - geopolitical_analyst
    - aggressive_analyst
    - conservative_analyst
    - neutral_analyst
  scenario_overrides:
    prefer_enabled:
      - aggressive_analyst
      - conservative_analyst
    suppress:
      - geopolitical_analyst

branch_conditions:
  breaking_news_enabled: false
  requires_news_confirmation: false
  pair_trade_mode: false
  thesis_break_mode: false

decision_policy_inputs:
  required:
    - committee_vote_balance
    - synth_recommendation
    - quant_composite_signal
    - risk_summary
    - liquidity_status
  optional:
    - crowding_signal
    - macro_regime

pm_decision_policy:
  posture_rules:
    - if: blocking_constraints_present
      stance: neutral
      action: defer
    - if: risk_status == warning
      outcome: approved_with_changes
      size_multiplier: 0.5
    - if: strong_long_alignment
      stance: long
      action: initiate
      size_range_bps: [40, 90]
    - if: strong_short_alignment
      stance: short
      action: initiate
      size_range_bps: [25, 60]
  variability:
    seed: run_id
    mode: bounded

execution_template:
  mode: single_name
  required_fields:
    - action_now
    - ticket_context
    - entry_conditions
    - exit_conditions
    - checkpoint

monitoring_template:
  review_mode: event_watch
  required_fields:
    - triggers
    - review_cadence
    - add_conditions
    - exit_conditions
```

## Recommended Execution Path Model

Keep the top-level stage order common:

`gather -> quantify -> debate -> synthesize -> risk_review -> pm_review -> trade_finalize -> monitor`

Differentiate scenarios through:
- scenario-specific gather prompts
- scenario-specific branch conditions
- scenario-specific PM decision policy
- scenario-specific execution template
- scenario-specific monitoring template

This keeps the runtime simple while making the product feel meaningfully different.

## Recommended Scenario Set

### 1. Earnings Committee

Core question:
- Should the desk initiate a pre-event position, or stay neutral into earnings?

Primary stance space:
- `long`, `short`, `neutral`

Primary action space:
- `initiate`, `defer`

What should make it feel unique:
- event risk should dominate sizing
- quant should validate whether the setup is statistically attractive into the event
- PM should often shrink size even when direction is approved
- monitoring should focus on earnings print, guidance, and post-event add or exit logic

Execution path:
- gather collects technical, news, and fundamentals around the event setup
- quantify validates event signal strength and gap-risk context
- debate tests whether expectations are too rich or too soft
- risk review caps size because this is a pre-event initiation scenario
- PM can approve a starter, defer, or reject
- trader outputs entry discipline for a pre-event position, not a full-size swing trade
- monitor focuses on event release, call takeaways, and next-session follow-through

### 2. Breaking News Drill

Core question:
- Should the desk act now, defer for confirmation, or stay neutral?

Primary stance space:
- `long`, `short`, `neutral`

Primary action space:
- `initiate`, `defer`

What should make it feel unique:
- breaking-news reroute is mandatory, not optional flavor
- news confirmation matters more than standard quant confidence
- PM should frequently choose `neutral` or reduced size
- trader output should emphasize urgency, spread protection, and cancel conditions

Execution path:
- gather emphasizes headline quality, second-source confirmation, and live market reaction
- quantify validates whether the price response is overextended or still incomplete
- debate should be shorter and more time-sensitive than the earnings scenario
- risk review should apply headline-confirmation and implementation-risk penalties
- PM can approve a reduced-size trade, defer, or reject
- trader outputs urgency-aware instructions and explicit abort conditions
- monitor focuses on follow-up headlines, confirmation, and reversal risk

### 3. Pair Trade Committee

Core question:
- Should the desk put on a relative-value pair, or do nothing?

Primary stance space:
- `long`, `short`, `neutral`

Interpretation:
- `long` means put on the pair in the intended direction
- `short` means put on the reverse pair only if the product explicitly supports inversion
- `neutral` means no pair trade

Primary action space:
- `initiate`, `defer`

What should make it feel unique:
- research should compare both names explicitly
- quant should score spread quality, not just single-name direction
- risk review should enforce borrow, leg balance, and beta-awareness
- trader output should be a two-leg execution plan

Execution path:
- gather compares primary and peer name side by side
- quantify computes relative revisions, spread behavior, and pair-quality metrics
- debate tests the long-leg and short-leg assumptions directly
- risk review blocks the trade if borrow, leg liquidity, or beta alignment fail
- PM should decide gross-per-leg and whether the pair is clean enough to run
- trader must output two coordinated legs and hedge discipline
- monitor should track spread drift, factor divergence, and borrow changes

### 4. Thesis Break Monitoring Review

Core question:
- Should the desk hold, trim, or exit an existing position?

Primary stance space:
- `long`, `neutral`

Primary action space:
- `hold`, `trim`, `exit`

Note:
- if the product later supports flipping from long to short in one motion, expand allowed stances at that time

What should make it feel unique:
- the desk starts from an existing position, not from zero
- this should feel forensic, not exploratory
- PM should be reacting to deterioration, not hunting for a fresh idea
- execution should focus on unwind logic, not new-entry logic

Execution path:
- gather focuses on what broke in the original thesis
- quantify tests whether the deterioration is noise or regime change
- debate should center on hold-versus-exit, not broad initiation arguments
- risk review should ask whether remaining exposure is still justified
- PM decides hold, trim, or exit
- trader outputs unwind or reduction plan
- monitor focuses on re-entry gates only after an exit or trim decision

## PM Decision Model: Less Deterministic, Still Reproducible

The PM should no longer use a single fixed object like:

```yaml
scripted_pm_default:
  outcome: approved_with_changes
  position_size_bps: 75
  approval_notes: Start half-size ahead of earnings, add only on confirmation.
```

Instead, the PM should use a bounded policy model.

### PM Policy Inputs

- scenario type
- starting position state
- final synthesized recommendation
- vote breakdown
- quant metrics
- risk status
- blocking and warning constraint counts
- liquidity status
- confidence bucket

### PM Policy Outputs

- `decision_outcome`
- `stance`
- `position_action`
- `position_size_bps`
- `approval_notes`
- `requires_risk_recheck`

### PM Policy Rules

Baseline rules:
- any unresolved blocking constraint forces `neutral`
- warning constraints shrink size and often produce `approved_with_changes`
- a split committee vote should bias toward smaller size or `defer`
- scenario type controls how aggressive the PM is allowed to be
- starting position state matters for hold, trim, and exit scenarios

Reproducibility rules:
- use a seed derived from `run_id`, `scenario_id`, and ticker
- allow variation only inside a bounded range
- never vary across impossible states
- the same run should replay to the same PM outcome

### Example PM Policy

```yaml
pm_decision_policy:
  seed_basis: [run_id, scenario_id, primary_ticker]
  default_requires_risk_recheck: true
  rules:
    - if: blocking_constraints_present
      outcome: rejected
      stance: neutral
      action: defer
      size_bps: 0

    - if: scenario_type == breaking_news and headline_confirmation_missing
      outcome: approved_with_changes
      stance: neutral
      action: defer
      size_bps: 0

    - if: scenario_type == pre_event_initiation and vote_balance == strong_long and quant_signal == strong
      outcome: approved_with_changes
      stance: long
      action: initiate
      size_range_bps: [35, 80]

    - if: scenario_type == thesis_break and thesis_damage == severe
      outcome: approved
      stance: neutral
      action: exit
      size_bps: 0
```

## Recommended Scenario Differentiation Matrix

| Scenario | Starting state | Main decision | Primary PM bias | Trader output | Monitoring focus |
|----------|----------------|---------------|-----------------|---------------|------------------|
| Earnings Committee | No position | Initiate or defer | Small starter, event-risk-aware | Pre-event entry plan | Earnings print and add-exit gates |
| Breaking News Drill | No position | Act now, defer, or stay neutral | Reduced size or neutral until confirmed | Urgent execution and cancel conditions | Headline confirmation and reversal risk |
| Pair Trade Committee | No position | Put on pair or no trade | Balanced gross-per-leg sizing | Two-leg coordinated ticket | Spread drift and borrow |
| Thesis Break Review | Existing long | Hold, trim, or exit | Defensive and forensic | Reduction or unwind plan | Re-entry gates and residual risk |

## Source Of Truth Recommendation

Use one canonical scenario source.

Recommended path:
- move all scenarios into versioned YAML or JSON artifacts under `scenarios/`
- generate the Python runtime catalog from those artifacts
- do not maintain parallel handwritten scenario definitions in both places

Why:
- scenario iteration becomes product work, not code archaeology
- scenario reviews become easier
- design, runtime, and QA can point to the same object

## Suggested Rollout

### Phase 1

- normalize all emitted stance language to `long`, `short`, `neutral`
- add `position_action` to scenario and PM output objects
- replace `scripted_pm_default` with `pm_decision_policy`

### Phase 2

- move the four scenarios to the new contract
- add scenario-specific branch conditions and execution templates
- differentiate seat plans where appropriate

### Phase 3

- consolidate scenario truth under `scenarios/`
- generate runtime catalog from those files
- add scenario validation tests

## Recommended Immediate Next Step

Implement the contract first, not the UI.

The next concrete engineering move should be:
1. define the v2 scenario schema
2. convert one scenario end to end, preferably `single_name_breaking_news`
3. replace hardcoded PM defaults with the bounded PM policy
4. only then update the remaining three scenarios

`single_name_breaking_news` is the best pilot because it has the clearest need for differentiated branching and the strongest case for making PM less deterministic.
