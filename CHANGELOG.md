# Changelog

## Unreleased

- Renamed the project and GitHub repository to **A100 MIG Lab** (`a100-mig-lab`); updated English/Russian documentation, badges, the architecture diagram and release links.

## v0.2.0 — 2026-11-11

### Fixed

- Context guard now reuses an async HTTP client, forwards native SSE incrementally, closes cancelled streams, and records full stream lifetime in metrics.
- Context overflow recovery retries once with per-tier input/output budgets; preserves supported instructions, never increases requested output length, and leaves tool/multimodal requests intact.
- Invalid input and upstream connection/time-out errors return controlled responses; unrelated errors no longer trigger context loss.
- MIG operations use one selected physical GPU, validate available profiles, protect existing layouts behind `MIG_RESET=1`, and no longer stop unrelated Docker containers.
- UUID export selects instances from the chosen GPU and verifies distinct serving/stress UUIDs.
- Shell entry points resolve their repository paths consistently. Smoke checks fail on invalid HTTP/completion results.
- Grafana provisioning now has a stable datasource UID. MIG activity ratios, framebuffer units, occupancy calculations and state thresholds are corrected; unavailable metrics show N/A.
- LiteLLM metrics scraping now uses Bearer authentication through a Compose secret.

### Changed

- English README is the default; Russian documentation remains one click away.
- Added inline historical screenshots, an accessible architecture SVG, colorful live badges, reproduction instructions, explicit validation limits and a sourced measurement protocol.
- Replaced floating LiteLLM/Open WebUI tags with version pins and pinned default model revisions.
- Updated context-guard framework dependencies and Python container to 3.12.
- Reduced Prometheus scrape/evaluation from 500ms to 15s and aligned DCGM collection to 15s.
- Persistent app/monitoring data uses named volumes; shutdown preserves data and model cache.
- Ports bind to loopback by default, with SSH tunnel instructions for remote access.
- Load generation reuses HTTP connections; the stress worker touches allocated memory and exposes unexpected failures instead of silently passing them.
- The large CUDA/PyTorch stress image is built on demand instead of delaying normal stack startup.

### Added

- GPU-free regression tests for the guard and host scripts, dashboard/document validation, and GitHub Actions checks for Compose and Prometheus.
- Automated release publication after successful validation of a main-branch push, with a real Git tag and release notes.
- Suggested repository topics in `.github/topics.json`.

### Validation limits

No A100 was available for this maintenance release. Existing screenshots belong to the original experiment. CUDA execution, model memory fit, GPU OOM isolation, throughput and latency of these changes require a fresh hardware run. Software changes do not include a measured speedup claim.

## Initial import — 2026-03-24

Original A100 40GB experiment: four MIG instances, three TGI/Qwen model tiers, LiteLLM, Open WebUI, context guard, DCGM, Prometheus/Grafana and load/failure demos.
