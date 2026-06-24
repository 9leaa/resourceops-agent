# ResourceOps Agent Design

The long-form design is currently maintained at:

```text
/home/zcj/incidentops-agent/ResourceOps_Agent_DESIGN.md
```

This project implements that design as a separate ResourceOps codebase.

Current implementation stage: **V1-P3**.

Implemented through V1-P3:

- V1-P0: project rename and schema adjustment.
- V1-P1: real local GPU / CPU / Memory / Process tools.
- V1-P2: deterministic ResourceAgent plans and ToolRegistry execution.
- V1-P3: detectors that produce `EvidenceItem` and `DiagnosisFinding` records from tool results.

Next stage: V1-P4 report and approval wiring for dangerous recommendations such as `kill_process`.
