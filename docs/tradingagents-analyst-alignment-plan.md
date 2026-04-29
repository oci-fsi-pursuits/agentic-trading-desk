# Implementation Plan: TradingAgents Analyst Alignment

Date: 2026-04-03
Status: READY_FOR_BUILD
Reference repo: `https://github.com/TauricResearch/TradingAgents`

## Goal
Update the `market_analyst`, `news_analyst`, and `fundamentals_analyst` so they behave like specialist analysts in `TradingAgents`.

That means each analyst should:
- own its own retrieval lane
- use analyst-specific tools instead of receiving a large pre-chewed evidence blob
- synthesize from the data it personally pulled
- return a concise desk note for downstream seats

The `social_analyst` stays as-is.

## What To Copy, What Not To Copy

### Copy
- analyst-owned retrieval
- analyst-specific tool sets
- minimal runtime context
- specialist prompts that tell each analyst what evidence to fetch and how to reason about it

### Do not copy
- serial analyst execution
- markdown-heavy report style
- separate system/user prompt layering
- LangGraph-specific node sequencing

Reason: `TradingAgents` gets the specialist feel from retrieval ownership, not from its graph wiring. Our repo should keep the better runtime behavior we already have: parallel gather and progressive UI updates.

## Current State In This Repo

Today the three target analysts are prompt-fed:
- `runtime/common/engine.py` builds compact prompt context objects for market, news, and fundamentals.
- `runtime/common/oci_genai.py` converts that context into one prompt string and sends it through `complete_with_responses()`.
- only `social_analyst` currently has true prompt-time retrieval behavior via `x_search`.

This is cleaner than the old giant prompt, but it still makes these analysts feel like summarizers of desk-prepared packets rather than specialists running their own lane.

## Target Architecture

### Core principle
Keep one LLM entrypoint:
- every LLM call continues to flow through `complete_with_responses()`

But change what happens inside that path:
- `complete_with_responses()` must support local analyst tool-call rounds for desk-native tools
- prompts become smaller because the analyst can fetch what it needs

### Runtime shape
1. `engine.py` fans out gather analysts in parallel, as it does now.
2. Each analyst gets:
   - a single prompt from `AGENT_SYSTEM_PROMPTS`
   - a very small runtime context
   - a restricted tool set
3. `complete_with_responses()` runs a bounded tool loop:
   - send prompt
   - detect tool call(s)
   - execute allowed local tool(s)
   - feed tool result(s) back into the same response session
   - stop when text output is returned or the max tool-round budget is reached
4. UI continues to emit analyst cards as each future finishes.

## Analyst Design

### 1. Market Analyst

#### TradingAgents reference
`TradingAgents` gives the market analyst:
- `get_stock_data`
- `get_indicators`

The analyst is expected to:
- pull price data first
- choose relevant indicators
- analyze trend, momentum, volatility, and key levels

#### Local target behavior
The local `market_analyst` should:
- fetch recent OHLCV / price-series data through a desk-native `get_stock_data`
- fetch technical indicator outputs through `get_indicators`
- write a concise technical desk note

#### Local tool contract
- `get_stock_data(ticker, lookback_days|start_date/end_date)`
- `get_indicators(ticker, indicators, as_of, lookback_days)`

#### Local data backend
Back these tools with existing provider code in `runtime/common/data_providers.py`:
- price history from the current chart / market domain path
- indicator calculations from local computation over fetched price history

#### Prompt design
Prompt should instruct the analyst to:
- fetch price data first
- retrieve only the indicators needed for the current setup
- stay purely technical
- avoid fundamentals and news opinions
- produce exact levels and timeframe-aware interpretation

#### Remove from prompt context
- most embedded `market_context` fields once equivalent tool access exists
- redundant shared desk metadata

### 2. News Analyst

#### TradingAgents reference
`TradingAgents` gives the news analyst:
- `get_news`
- `get_global_news`

The analyst is expected to combine:
- ticker-specific news
- broader macro / market news

#### Local target behavior
The local `news_analyst` should:
- fetch ticker-specific news via `get_news`
- fetch broader macro / market context via `get_global_news`
- synthesize immediate market implications in one desk note

#### Local tool contract
- `get_news(ticker, start_date, end_date, limit)`
- `get_global_news(curr_date, look_back_days, limit)`

#### Local data backend
Implement the tool interface so backend providers remain swappable:
- default backend should use the repo's preferred live source configuration
- ticker-news and global-news should be separate retrieval paths

Important design choice:
- mimic `TradingAgents` at the interface level
- do not hardcode the analyst prompt to one provider implementation
- if Google News remains the preferred live source in this repo, `get_news` and `get_global_news` should still exist, but resolve through local provider routing

#### Prompt design
Prompt should instruct the analyst to:
- fetch both company-specific and market-wide news
- focus on what matters for trading now
- avoid article lists, links, and markdown clutter
- explicitly separate confirmed developments from thin or ambiguous evidence

#### Remove from prompt context
- embedded headline arrays
- embedded article URL lists
- large news payloads pre-appended by `engine.py`

### 3. Fundamentals Analyst

#### TradingAgents reference
`TradingAgents` gives the fundamentals analyst:
- `get_fundamentals`
- `get_balance_sheet`
- `get_cashflow`
- `get_income_statement`

The analyst is expected to synthesize:
- company profile
- valuation
- financial statement quality
- earnings quality and risks

#### Local target behavior
The local `fundamentals_analyst` should:
- fetch company overview / valuation data
- pull financial statement details as needed
- write a concise but evidence-based fundamental desk note

#### Local tool contract
- `get_fundamentals(ticker, as_of)`
- `get_balance_sheet(ticker, period)`
- `get_cashflow(ticker, period)`
- `get_income_statement(ticker, period)`

#### Local data backend
Back these with existing local fundamentals provider logic and expand where needed:
- current summary metrics can come from the present fundamentals domain
- statements can come from the same vendor path or a compatible extension

#### Prompt design
Prompt should instruct the analyst to:
- start with the company overview / key metrics
- pull statements only where needed to verify quality
- focus on revenue, EPS, margins, cash generation, leverage, and valuation
- state clearly when data is stale, sparse, or incomplete

#### Remove from prompt context
- large prefilled `fundamentals` blobs once tool access covers the same ground

## Social Analyst
No design change.

Keep:
- `x_search`
- prompt-time X lookup
- Stocktwits snapshot append
- current social analyst prompt style

Reason: the social analyst already follows the desired specialist pattern better than `TradingAgents` does.

## `complete_with_responses()` Design

### Must keep
- all LLM traffic continues to go through `complete_with_responses()`

### Required change
Extend `runtime/common/oci_genai.py` so `complete_with_responses()` can handle:
- local function-style tools for market/news/fundamentals
- OCI-native tools such as `x_search`
- bounded iterative tool execution

### Tool loop design
For local tools:
1. send the prompt and tool schema
2. inspect the model response for requested tool calls
3. execute only allowlisted tools for that analyst
4. append tool outputs back into the response chain
5. repeat until final text or round cap

### Guardrails
- max 3 tool rounds per analyst
- max 6 tool calls total per analyst run
- per-tool timeout
- deterministic fallback to the current `fallback` text on failure
- strict analyst-to-tool allowlist

### Logging
Logs should show:
- role
- tool count
- round count
- final mode

But should not dump giant serialized prompt bodies by default in normal operation.

## Prompt Strategy

### Keep
- one prompt only
- prompt text defined in `runtime/common/agent_prompts.py`

### Change
Prompts for market, news, and fundamentals should become:
- more directive about what to fetch
- lighter on embedded context assumptions
- explicit about tool usage order
- strict about output cleanliness

### Output style target
- plain desk-note prose
- no markdown headers unless explicitly needed
- no markdown tables
- no source lists or raw links in analyst output
- keep `STANCE:` and `CONFIDENCE:` because the rest of this repo depends on them

## `engine.py` Changes

### Keep
- gather analyst parallel fan-out
- progressive emission as each analyst completes

### Change
Reduce per-analyst context to a thin runtime envelope:
- `ticker`
- `display_instrument` if needed
- `trade_date` or analysis window
- minimal provider / freshness hints
- scenario objective only if truly needed

Remove large appended evidence blocks for:
- market
- news
- fundamentals

The engine should define:
- which tools each analyst is allowed to use
- what minimal seed context is provided

The engine should not pre-summarize the analyst's lane.

## Data Provider Design

The local equivalent of `TradingAgents` vendor routing should live behind a desk-native tool layer.

### Design requirement
Expose stable analyst tools even if underlying providers change:
- analyst prompt talks about `get_news`, not `google_news`
- analyst prompt talks about `get_fundamentals`, not `yfinance`

### Benefit
This avoids future prompt churn every time a provider preference changes.

## UI / Product Behavior

No major UI redesign is required for this change.

Expected visible behavior:
- gather analysts still appear progressively
- each analyst note should feel more specialist and less templated
- news output should be less weirdly formatted because we will stop feeding raw lists directly into the prompt
- logs should be easier to reason about because prompt context gets smaller and tool activity becomes explicit

## Phase Plan

### Phase 1: Tool contracts and routing
- add desk-native tool definitions for market, news, and fundamentals
- map each tool to local provider functions
- add tests for provider routing and response shape

### Phase 2: `complete_with_responses()` tool loop
- add support for local function-style tool execution
- preserve current `x_search` behavior
- add bounded retry / fallback logic

### Phase 3: Analyst prompt rewrite
- rewrite market/news/fundamentals prompts around tool-driven retrieval
- keep social unchanged
- validate prompt coverage in `tests/validate_agent_prompts.py`

### Phase 4: Engine slimming
- replace embedded evidence blobs with minimal runtime envelopes
- pass tool allowlists per analyst
- keep gather parallelism and progressive completion behavior

### Phase 5: QA and cleanup
- inspect raw logs to ensure prompt size dropped materially
- verify analysts no longer receive the same appended text
- verify outputs are cleaner and less markdown-heavy
- verify fallbacks still work when provider/tool calls fail

## Acceptance Criteria
- `market_analyst`, `news_analyst`, and `fundamentals_analyst` each own a distinct retrieval lane
- all LLM calls still go through `complete_with_responses()`
- `social_analyst` behavior is unchanged
- gather analysts still run in parallel
- analyst prompts are materially smaller
- news output no longer includes raw article/link clutter unless explicitly required by the UI
- logs clearly show tool usage without giant prompt dumps

## Non-Goals
- copying `TradingAgents` serial graph sequencing
- changing debate, PM, trader, or monitoring architecture in this pass
- changing the social analyst's X / Stocktwits workflow
- switching the entire repo to LangChain or LangGraph style tool orchestration

## First Build Tasks
1. Add a local tool registry for analyst-only tools.
2. Implement `get_stock_data` and `get_indicators` over the current market data path.
3. Implement `get_news` and `get_global_news` over the current news provider path.
4. Implement `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, and `get_income_statement`.
5. Extend `complete_with_responses()` to support bounded local tool-call loops.
6. Rewrite the three analyst prompts around tool-first behavior.
7. Reduce `engine.py` analyst contexts to thin runtime envelopes.
8. Add regression tests for tool allowlists, prompt size, and gather parallel behavior.

## Definition Of Done
This effort is done when:
- the three target analysts feel like independent specialists rather than packet summarizers
- prompt payloads are much smaller than the current appended-data version
- the runtime still preserves parallel gather and progressive UI updates
- the social analyst remains untouched and functional
- the implementation is local, explicit, and debuggable without layering more prompt hacks on top
