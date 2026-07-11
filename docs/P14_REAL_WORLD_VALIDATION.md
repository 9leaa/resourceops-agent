# P14 Real-World Validation Log

Date: 2026-07-11  
Host: 28 CPU cores, 64 GB RAM, NVIDIA GeForce RTX 4090

This log records the real terminal stress tests used to stabilize scoped LLM planning,
LLM report validation, report size, transient gateway recovery, and P14 trace output.

## Initial Failure

Run: `run_1876c387a5f8`

- Scenario: 28 GB bounded memory pressure.
- LLM plan selected five tools, including two CPU tools for an explicit memory request.
- Planner succeeded, but report fell back with `LlmReportValidationError`.
- The report validator searched the whole report for `已执行`; negative text such as
  "no executed action" could therefore be interpreted as an executed pending approval.
- Planner prompt exposed the complete multi-resource catalog, so the model could add CPU
  tools even when `resource_type=memory` was explicit.

Initial metrics:

| Metric | Value |
|---|---:|
| Planner tools | 5 |
| Planner prompt / response | 4865 / 858 chars |
| Planner latency | 42612 ms |
| Report context / prompt / response | 2306 / 3202 / 1025 chars |
| Report latency | 14747 ms |
| Total LLM latency | 57359 ms |
| `trace/steps.json` | 8201 bytes |
| `raw/tool_outputs.jsonl` | 9911 bytes |
| Result | deterministic fallback |

## Changes Made

1. Explicit resource scope now filters the catalog shown to the LLM planner.
   `memory` sees memory tools, `cpu` sees CPU tools, while `mixed` keeps all resource tools.
   A scope validation check rejects cross-resource tools that were not allowed.
2. Pending approval validation is now local to the matching `approval_id` and requires a
   pending/unexecuted marker. It only rejects structured states such as
   `审批状态=已执行` or `status=executed`; it no longer guesses Chinese negation semantics.
3. Report instructions now forbid repeated metrics across sections and request a concise
   700-1100 Chinese-character report. Report output budget changed from 768 to 640 tokens.
4. LLM requests retry once for transient `429`, `502`, `503`, `504`, timeout, and transport
   failures with exponential backoff. Validation failures are never retried.

## Real Test Rounds

### Round 1: Memory pressure after resource scoping

Run: `run_215e1c279fdb`

```bash
python -u scripts/stress_memory.py \
  --mb 28672 --max-mb 32768 --duration 180 --chunk-mb 256

python -u main.py diagnose "为什么内存快满了？" \
  --resource-type memory --planner-mode llm --report-mode llm --json
```

- Planned tools: `get_memory_snapshot`, `list_top_memory_processes`, `check_oom_events`.
- Finding: `memory_process_hogging`.
- Pending kill approval remained pending and unexecuted.
- LLM planner and report both succeeded.
- Report length: 1319 chars.

Compared with the initial failed run:

| Metric | Before | After | Improvement |
|---|---:|---:|---:|
| Tool count | 5 | 3 | 40.00% reduction |
| Planner prompt | 4865 | 2198 | 54.82% reduction |
| Planner response | 858 | 488 | 43.12% reduction |
| Planner latency | 42612 ms | 9481 ms | 77.75% reduction |
| Report context | 2306 | 2070 | 10.23% reduction |
| Report prompt | 3202 | 2976 | 7.06% reduction |
| Total LLM latency | 57359 ms | 40857 ms | 28.77% reduction |
| `trace/steps.json` | 8201 B | 7021 B | 14.39% reduction |
| Raw tool output | 9911 B | 4874 B | 50.82% reduction |

The LLM report response grew from 1025 to 1318 chars, an increase of 28.59%, because the
new result was complete and valid while the original result failed validation.

### Round 2: CPU saturation

Run: `run_af082804fa4c`

```bash
python -u scripts/stress_cpu.py --workers 28 --duration 180

python -u main.py diagnose "为什么 CPU 很高？" \
  --resource-type cpu --planner-mode llm --report-mode llm --json
```

- Planned tools: `get_cpu_snapshot`, `list_top_cpu_processes`.
- Finding: `cpu_saturation`, confidence `0.9`.
- CPU snapshot observed `overall_cpu_percent=100.0`.
- LLM planner/report succeeded.
- Planner/report latency: 8849 / 21395 ms.
- Planner/report prompt: 1752 / 2385 chars.
- Report length: 1471 chars. Correct, but judged more repetitive than necessary.

### Round 3: Combined CPU and memory pressure

Run: `run_b0a253652dd2`

- Started 28 CPU workers and 28 GB memory pressure together.
- `mixed` correctly kept the cross-resource plan with seven tools.
- Findings: `cpu_saturation` and `memory_process_hogging`.
- GPU and recent OOM pressure were ruled out.
- LLM report succeeded but was 1767 chars, motivating the lower report token budget and
  stronger deduplication instruction.

### Round 4: First shorter-report validation

Run: `run_8cfefd6969bb`

- Report budget was reduced from 768 to 640 tokens, a 16.67% reduction.
- Output decreased to 1209 chars, but the broad executed-state heuristic still produced a
  false `LlmReportValidationError`.
- Fix: remove free-text `已执行` scanning and validate only explicit structured approval
  state near the matching `approval_id`.

### Round 5: Pending approval regression test

Run: `run_4752b49faa7c`

- Same 28 GB memory-pressure scenario.
- Planned memory tools: 3.
- `memory_process_hogging` and pending approval were preserved.
- LLM report succeeded with all six required sections.
- Successful report output decreased from 1318 to 1216 chars: 7.74% reduction.
- Report latency decreased from 31376 to 19298 ms: 38.49% in these samples.
- Total LLM latency decreased from 40857 to 30051 ms: 26.45% in these samples.
- The stricter prompt grew from 2976 to 3026 chars: 1.68% increase.

Latency percentages are single-run observations and include upstream queue variance; they
are useful operational evidence, not a controlled model benchmark.

### Round 6: Mixed pressure with upstream 502

Run: `run_44000b21c1eb`

- Planner succeeded and selected seven mixed-resource tools.
- Report endpoint returned `502 Bad Gateway`; deterministic fallback worked correctly.
- Fix: add one bounded retry for transient gateway and transport failures.
- Unit regression test explicitly verifies `502 -> retry -> 200`.

### Round 7: Final mixed-pressure validation

Run: `run_2667a25033a0`

- Final configuration: 640 report tokens and one transient retry.
- Seven mixed-resource tools were selected.
- Memory finding and pending approval were preserved.
- LLM planner/report both succeeded; no fallback.
- Report length: 1257 chars.
- Planner/report latency: 27527 / 20750 ms.
- Context/report prompt: 2684 / 3862 chars.

Compared with Round 3, noting that Round 3 had two findings while Round 7 had one:

| Metric | Round 3 | Round 7 | Change |
|---|---:|---:|---:|
| Report output | 1766 | 1256 | 28.88% reduction |
| Report prompt | 5041 | 3862 | 23.39% reduction |
| Report context | 3681 | 2684 | 27.09% reduction |
| `trace/steps.json` | 9256 B | 9172 B | 0.91% reduction |
| Raw tool output | 9202 B | 8815 B | 4.21% reduction |
| Total LLM latency | 47427 ms | 48277 ms | 1.79% increase |

The latency increase is within observed upstream variance and is not attributed to prompt
size. Prompt and output size improved materially.

## Final Report Length Judgment

- Memory pressure report: 1217 chars.
- Final mixed report: 1257 chars.
- Both include six required sections, evidence, findings, approval ID, pending state, and
  risk boundaries without large raw process lists.
- This is not considered too long for an auditable diagnosis report. Lowering the budget
  further would increase the risk of truncating approval and risk sections.

## Final Trace Commands

```bash
# Human-readable deterministic summary
python main.py trace <run_id>

# Detailed steps, findings, approvals, and actions
python main.py trace <run_id> --full

# One action or phase
python main.py trace <run_id> --step llm_report

# LLM model, latency, sizes, hashes, previews, and errors
python main.py trace <run_id> --llm

# Summary JSON for scripts
python main.py trace <run_id> --summary-json

# Full SQLite trace, retained for backward compatibility
python main.py trace <run_id> --json

# Workspace files
python main.py workspace <run_id>
cat var/runs/<run_id>/summary/run_summary.md
cat var/runs/<run_id>/compact/llm_calls_summary.json
cat var/runs/<run_id>/compact/report_context.json
cat var/runs/<run_id>/report.md
```

Full LLM prompts/responses remain disabled by default. To record redacted full payloads for
a deliberate debug run only:

```bash
RESOURCEOPS_STORE_LLM_PAYLOADS=true python main.py diagnose ...
```

They are written to `raw/llm_calls.jsonl` and remain excluded from debug bundles unless
`bundle --include-llm-payloads` is explicitly used.
