# Engineering decisions and measurement protocol

This is a reproducible infrastructure experiment for an NVIDIA A100 40GB. Its design explores independent model servers on MIG instances, API routing, bounded context recovery, observability, and deliberate memory pressure. It is not a published performance comparison. The current maintenance work was performed without access to an A100; CPU checks cannot validate CUDA execution, model fit, MIG isolation, or GPU throughput.

## MIG configuration

The intended geometry is `3g.20gb + 2g.10gb + 1g.5gb + 1g.5gb`: three inference instances and a separate stress instance. A100 80GB uses different profile names and needs a deliberate configuration change. Profile names describe nominal capacity; available framebuffer memory differs from the number printed in the profile name. [NVIDIA supported profiles](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-mig-profiles.html).

Create `3g.20gb` first. NVIDIA documents an ordering issue with this mixed geometry. Query the selected GPU's profiles and instances, create both GPU Instances and Compute Instances, and export fresh UUIDs after changing the layout. On A100, MIG mode survives reboot, while the created instances must be recreated. Reconfiguration interrupts applications on the selected GPU, so use a dedicated lab host and stop its workloads first. [NVIDIA MIG setup and instance geometry](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/getting-started-with-mig.html).

Pass one explicit MIG UUID to each inference container using `NVIDIA_VISIBLE_DEVICES` and the NVIDIA runtime. `compute,utility` provides CUDA and NVML access. Selecting an instance for computation does not require granting the container MIG configuration privileges. Keep the stress UUID distinct from all inference UUIDs. [NVIDIA Container Toolkit configuration](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html).

## Reproducibility and inference tuning

Pin container versions and record image digests, model revisions, driver version, MIG layout, and command-line options for each run. A named model cache avoids downloading weights again between container recreations. An image tag alone does not freeze the model files downloaded from a mutable model branch.

This lab retains Hugging Face TGI for continuity with the original experiment. TGI is now in maintenance mode; its maintainers recommend engines including vLLM and SGLang for new deployments. A migration would require fresh compatibility and GPU measurements. [TGI project status](https://huggingface.co/docs/text-generation-inference/main/en/index).

Treat each slice as a separate memory budget. Tune maximum input length, total tokens, batch prefill tokens, batch total tokens, and concurrent requests together. Larger per-request contexts reduce batching capacity; increasing concurrency alone can increase queueing or exhaust memory. TGI exposes these controls, including `--revision` for model pinning. Keep the existing limits as starting points until they have been measured on the target card. [TGI launcher arguments](https://huggingface.co/docs/text-generation-inference/basic_tutorials/launcher).

## Interpreting telemetry

Prometheus scrapes and evaluates every 15 seconds; dashboards refresh every 5 seconds. Refreshing a dashboard more often does not produce new GPU observations. Align the DCGM collection interval with the desired measurement resolution and retain the settings with the benchmark record. [DCGM Exporter collection settings](https://docs.nvidia.com/datacenter/dcgm/latest/gpu-telemetry/dcgm-exporter.html), [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/).

MIG compute panels use DCGM profiling activity. A100 MIG utilization is unavailable through the legacy NVML utilization interface. Activity ratios use a 0–1 scale and are formatted as percentages. They are not achieved FLOPS or a direct throughput measurement. Unsupported metrics remain unavailable. [NVIDIA nvidia-smi limitations](https://docs.nvidia.com/deploy/nvidia-smi/), [DCGM profiling](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html).

Framebuffer metrics are in MiB. Occupancy panels calculate `used / (used + free)` from the reported instance metrics instead of assuming a fixed nominal capacity. The 85% and 95% bands are visual thresholds, not proof that the next allocation will fail. The state calculation produces 0, 1, or 2. Missing samples display `N/A`; they are never replaced with a healthy zero. Temperature and board power describe the physical GPU, not independent sensors for each MIG instance. [DCGM Exporter metrics and entity labels](https://docs.nvidia.com/datacenter/dcgm/latest/reference/dcgm-exporter-metrics.html).

LiteLLM's main-port `/metrics` endpoint is scraped with Bearer authentication. Prometheus reads the key from `/run/secrets/litellm_api_key`, supplied by a Compose secret sourced from `OPENAI_API_KEY`. No additional credential file needs to be committed or generated in the checkout. Use Docker Compose v2.23.1 or newer for this lab; environment-backed secrets are a Compose feature and do not work with `docker stack deploy`. The basic Prometheus callback is documented separately from Enterprise-only extensions. An upgrade must recheck endpoint authentication and emitted metric names. [Docker Compose secrets](https://docs.docker.com/reference/compose-file/secrets/), [LiteLLM Prometheus integration](https://docs.litellm.ai/docs/proxy/prometheus).

## Recommended protocol for a future A100 run

1. Save the commit SHA, image digests, model revisions, host CPU/RAM, A100 variant, driver, layout, instance UUIDs, token limits, collection intervals, and power settings. Confirm all scrape targets and model endpoints are healthy.
2. Fix the prompt set, output token budget, generation settings, model mix, and load generator settings. Record actual prompt and generated token counts. Separate model download and startup time from inference measurements.
3. Warm every model with representative requests. Begin measurement only after startup and initial allocation effects have settled; record the warm-up duration and request count.
4. Measure one model at a time, then the same workload with all three active. Sweep concurrency from a small baseline, for example 1, 4, 8, and 16, rather than assuming the largest value is fastest. Use the same duration and repeat each configuration at least three times.
5. Report completed requests/s, generated tokens/s, p50/p95/p99 end-to-end latency, error count, queue duration, and sample count. Label load as closed-loop concurrency or an offered arrival rate, and record think time. Report TTFT and inter-token latency only when the client actually records a streamed response. [TGI benchmarking guide](https://huggingface.co/learn/cookbook/en/benchmarking_tgi), [TGI metric definitions](https://huggingface.co/docs/text-generation-inference/reference/metrics).
6. For the isolation experiment, first record baseline inference health and latency. Apply memory pressure only to the reserved stress instance while continuing identical inference probes. Save the stress process outcome, CUDA OOM message, HTTP results, timestamps, and exported metrics. Stop pressure and record recovery. High occupancy alone does not establish that an OOM occurred.
7. Keep raw results alongside each chart and change one tuning parameter at a time. Claim an improvement only when repeated runs support it; report regressions and variability as well.

MIG isolates assigned resources, but an OOM on one instance does not establish unchanged latency on its neighbours. Workloads still share a physical board and host resources. Compare measurements before, during, and after pressure. Architecture illustrations are explanations, not captured GPU results; any future measured screenshots should identify their run and source data.
