# Instagram Monitoring Project Notes

## Current Goal

Build a small, configurable Instagram account monitoring tool for a client. The client needs to know when monitored accounts become unavailable and when they come back. The project is currently in the **overnight testing** phase, not final production.

## What the bot should do

- Monitor a list of authorized Instagram usernames.
- Detect whether a profile is visible or unavailable.
- Track state changes over time.
- Save raw request/response data for later analysis.
- Log every request, response, error, retry, and restart.
- Run continuously overnight without dying from a single failure.
- Support multiple modes through config.

## Current findings from testing

### Instagram internal API endpoint

The internal endpoint currently works with the right headers:

- `User-Agent: Instagram 320.0.0.0 Android`
- `x-ig-app-id: 936619743392459`

Observed behavior:

- Active account returns full JSON with a `data.user` object.
- Deactivated account and banned account both appear to return no usable `data.user` object.
- Fake / nonexistent username returns Instagram page not found content externally.

### Meaning for classification

For now, the practical classification is:

- `ACTIVE` when `data.user` exists.
- `MISSING` when `data.user` is absent or the profile is unavailable.
- `UNKNOWN` when the response is partial, weird, or unclear.

This means the system is currently focused on **visibility status**, not on perfectly distinguishing banned vs deactivated vs deleted.

## Useful test outputs collected so far

### Active profile example

A working response returned profile data including:

- username
- internal numeric id
- follower count
- following count
- private/public status
- profile picture URL

### Missing / unavailable examples

For banned and deactivated accounts, the public page content showed:

> Sorry, this page isn't available. The link you followed may be broken, or the page may have been removed. Go back to Instagram.

For the fake username test, Instagram returned a page-not-found style HTML response.

## Current architecture direction

### Preferred structure for the overnight test

- **Primary source:** Instagram internal API endpoint.
- **Verification / fallback:** Playwright, only when needed.
- **Proxy support:** configurable toggle, not mandatory for the first run.
- **Logging:** store everything.
- **Storage:** SQLite + JSONL logs + raw response files.
- **Runtime:** infinite loop with crash recovery.

### Modes being considered

- `api_direct`
- `api_proxy`
- `playwright_direct`
- `playwright_proxy`
- optional verification mode where Playwright confirms suspicious API results

## Overnight test plan

The first test is meant to answer these questions:

1. Does the internal API stay stable over many hours?
2. Does direct traffic get rate limited?
3. Does proxy traffic behave better or worse?
4. How much slower / heavier is Playwright compared to API checks?
5. Which mode is best for the real project?

## Logging requirements

The script should log:

- timestamp
- username
- mode used
- proxy enabled or not
- user agent
- status code
- latency
- response size
- raw response body
- response headers
- parsed classification
- previous classification
- transition yes/no
- screenshot path if taken
- exceptions and tracebacks
- retry count
- worker ID or run ID

## Storage requirements

At minimum keep:

- `raw.jsonl` for detailed check records
- `metrics.jsonl` for periodic summaries
- `errors.jsonl` for failures and exceptions
- SQLite database for current status and event history
- screenshots directory if screenshots are enabled
- checkpoint file or table for resume support

## Metrics to collect

Track at least:

- total requests
- successful requests
- failed requests
- ACTIVE count
- MISSING count
- UNKNOWN count
- ERROR count
- rate limit count
- timeout count
- proxy failure count
- browser failure count
- average latency
- min latency
- max latency
- restart count
- transition count
- last successful time
- last error time

## Reliability rules

- Never let one failed request stop the whole process.
- Catch exceptions around every request.
- Catch exceptions around Playwright setup and teardown.
- Catch database write failures.
- Catch JSON parsing failures.
- Catch file write failures.
- Keep running even if one account is broken.
- Save checkpoints periodically so the run can recover.
- Use random delays, not perfectly fixed timing.
- Avoid spammy screenshots unless explicitly enabled.

## Randomization / timing idea

The checks should not happen on a perfect timer. The plan is to use:

- random delays
- jitter
- different intervals for ACTIVE / MISSING / UNKNOWN states
- stable but non-robotic user-agent handling

## Proxy plan

A proxy toggle will be built into config so the same script can be run in multiple modes:

- direct no proxy
- direct with proxy
- Playwright no proxy
- Playwright with proxy

The proxy support is mainly a future-proofing toggle because Meta is unpredictable.

## Config approach

A single config file should control:

- mode
- proxy on/off
- proxy URL
- headers / user-agent list
- timeouts
- retries
- delay ranges
- screenshot behavior
- logging level
- output folders
- account list
- checkpoint interval
- runtime mode

## Current implementation plan

The next step is to generate a **single Python codebase** that can:

- run overnight without crashing
- log everything
- save raw response data
- switch modes by config
- compare API vs Playwright vs proxy behavior
- keep running until manually stopped

## What is NOT the focus yet

- No full dashboard
- No microservices
- No Redis
- No Kafka
- No heavy distributed architecture
- No overengineering
- No fancy final product polish

## Practical notes

- The overnight run is mostly for data collection.
- The morning analysis will be done manually from logs and raw responses.
- The raw data matters more than the elegance of the code.
- Storage space is not a major concern for one-night testing.
- If the script dies, that is bad, so crash recovery is important.

## Next step

Generate the Python implementation with:

- config loading
- API checker
- Playwright checker
- proxy toggle
- JSONL logging
- SQLite persistence
- checkpointing
- infinite loop
- error recovery
- raw response capture
- metrics collection

## Decisions already made

- Python will be used.
- The project is in overnight test mode.
- The internal Instagram API is currently promising.
- Playwright is primarily a backup / confirmation method.
- Logging everything is non-negotiable.
- The script should keep running forever unless manually stopped.
