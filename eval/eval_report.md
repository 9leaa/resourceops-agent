# ResourceOps Agent Fixture Eval

## Overall
- cases: 4
- passed: 4
- pass_rate: 1.0
- finding_recall: 1.0
- approval_match_rate: 1.0

## Failed Cases
- none

## Cases
- gpu_memory_pressure: passed=True, expected=['gpu_memory_pressure'], actual=['gpu_memory_pressure'], approval=True
- cpu_saturation: passed=True, expected=['cpu_saturation'], actual=['cpu_saturation', 'gpu_unavailable'], approval=False
- memory_pressure: passed=True, expected=['memory_pressure', 'swap_pressure'], actual=['gpu_unavailable', 'memory_pressure', 'swap_pressure'], approval=False
- mixed_training_slow: passed=True, expected=['cpu_bottleneck_for_gpu', 'cpu_saturation'], actual=['cpu_bottleneck_for_gpu', 'cpu_saturation'], approval=False
