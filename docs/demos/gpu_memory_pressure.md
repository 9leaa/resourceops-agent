# Demo: GPU Memory Pressure

This demo shows how ResourceOps diagnoses GPU memory pressure while preserving the approval boundary for risky actions.

## Command

```bash
python main.py diagnose "为什么 GPU 显存满了？" --resource-type gpu
```

If you want an LLM-written report from the same bounded evidence:

```bash
python main.py diagnose "为什么 GPU 显存满了？" --resource-type gpu --report-mode llm
```

## Expected Flow

1. Planning tools selects GPU-focused diagnostic tools.
2. Tool execution calls GPU snapshot and GPU process list tools through `ToolRegistry`.
3. Detectors convert tool outputs into evidence and findings.
4. Report summarizes GPU memory usage, process ownership, command preview, and likely cause.
5. Dangerous recommendations create approval records instead of executing immediately.

## Expected Signals

- `get_gpu_snapshot` reports GPU memory state when `nvidia-smi` is available.
- `list_gpu_processes` reports GPU process PID, owner, command preview, and memory usage.
- Machines without NVIDIA GPUs return structured unavailable/no-GPU tool results instead of crashing.

## Inspect Artifacts

```bash
python main.py runs
python main.py trace <run_id>
python main.py workspace <run_id>
jq . var/runs/<run_id>/trace/findings.json
jq . var/runs/<run_id>/trace/approvals.json
```

## Safety Boundary

Approving a dangerous recommendation runs dry-run by default:

```bash
python main.py approve <approval_id>
jq . var/runs/<run_id>/trace/action_results.json
```

Real execution is separate and disabled unless explicitly configured with environment gates and `execute-real`.
