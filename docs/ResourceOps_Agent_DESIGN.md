# ResourceOps Agent 方案设计

> 基于原 `IncidentOps Agent` 的工程底座，重新收敛为面向 **GPU / CPU / Memory** 三类真实资源问题的本地诊断 Agent。第一版只要求能稳定、正确、可测试地处理三类问题；第二版再逐步引入 learn-claude-code 中的 hooks、TodoWrite、subagent、compact、skills、memory、error recovery、task system、background tasks、agent team、autonomous agents、workspace isolation 等 Agent Harness 机制。

---

![Todo Overview](./images/resourceops_agent.png)




## 目录

- [0. 项目定位](#0-项目定位)
- [1. 回顾原 IncidentOps Agent](#1-回顾原-incidentops-agent)
- [2. 新项目目标](#2-新项目目标)
- [3. 产品形态](#3-产品形态)
- [4. 第一版功能范围](#4-第一版功能范围)
- [5. 第一版 Agent 工作流程](#5-第一版-agent-工作流程)
- [6. 核心模块设计](#6-核心模块设计)
- [7. ResourceAgent 设计](#7-resourceagent-设计)
- [8. Detector 设计](#8-detector-设计)
- [9. 报告设计](#9-报告设计)
- [10. Approval 设计](#10-approval-设计)
- [11. Trace 设计](#11-trace-设计)
- [12. Eval 设计](#12-eval-设计)
- [13. CLI 设计](#13-cli-设计)
- [14. API 设计](#14-api-设计)
- [15. 第一版目录结构](#15-第一版目录结构)
- [16. 为第二版预留的扩展点](#16-为第二版预留的扩展点)
- [17. 第一版开发阶段](#17-第一版开发阶段)
- [18. 第二版开发阶段](#18-第二版开发阶段)
- [19. 为什么这样设计](#19-为什么这样设计)
- [20. 简历表述](#20-简历表述)

---

# 0. 项目定位

原项目 `IncidentOps Agent` 面向企业线上故障诊断，核心是：

> 接收服务告警，查询 logs、metrics、deploys、runbooks、historical incidents，生成根因报告，并对高风险操作走人工审批。

这个方向工程化程度高，但对当前阶段有一个明显问题：

> 场景依赖大量模拟数据，短期内缺少真实生产日志、指标和发布记录，导致产品感不强。

因此，新项目方向调整为：

# ResourceOps Agent：面向 GPU / CPU / Memory 的本地资源诊断 Agent

一句话定义：

> ResourceOps Agent 是一个面向开发者和 AI 训练场景的本地资源诊断 Agent。它能够自动检查 GPU、CPU、内存状态，定位异常进程，结合诊断规则和 skills 生成诊断报告，并对危险操作进行人工审批。

第一版只处理三类问题：

```text
1. GPU 问题
2. CPU 问题
3. Memory 问题
```

第二版再逐步加入：

```text
hooks
TodoWrite
subagent
compact
skills
memory
error recovery
task system
background tasks
agent team
autonomous agents
workspace isolation
```

第一版不能为了简单而写死成无法扩展的脚本。它必须保留原 IncidentOps 的核心工程结构：

```text
ToolRegistry
TraceStore
Approval
Eval
FastAPI
CLI
AgentRun / AgentStep / ToolCall
```

---

# 1. 回顾原 IncidentOps Agent

## 1.1 原项目已经做对的地方

原 IncidentOps Agent 的核心优点是：

```text
1. 有统一输入对象 Incident
2. 有 AgentRun 记录一次诊断
3. 有 AgentStep 记录每一步 thought/action/observation
4. 有 ToolRegistry 管理工具
5. 有 ToolExecutionResult 统一工具返回
6. 有 permission_level 区分 safe / write / dangerous
7. 有 Approval 处理危险操作
8. 有 TraceStore 支持复盘
9. 有 Eval 支持回归测试
10. 有 FastAPI 支持 HTTP 调用
```

这些结构都应该保留。

因为它们不是 AIOps 专属，而是通用 Agent Harness 的底座。

---

## 1.2 原项目的问题

原项目的问题不是工程结构，而是产品范围。

原来的问题域是：

```text
payment-api
order-api
user-api
logs
metrics
deploys
runbooks
historical incidents
```

这些东西大多是模拟出来的。

所以它更像：

```text
高质量工程 demo
```

而不是：

```text
当前服务器上真的能用的诊断工具
```

这会带来几个问题：

```text
1. 真实感弱
2. 数据可信度弱
3. 很难验证 Agent 是否真的有效
4. 不容易扩展成日常可使用的工具
5. LLM 接入后也容易变成“对模拟世界的智能”
```

因此，项目应该收窄到能真实采集、真实制造、真实验证的问题域。

---

## 1.3 可复用的工程底座

原项目中以下模块可以保留或轻微改名后继续使用：

| 原模块 | 新项目中的用途 |
|---|---|
| `app/schemas.py` | 定义 ResourceIncident、DiagnosisRun、DiagnosisStep、Approval 等 schema |
| `tools/registry.py` | 继续作为所有系统诊断工具的统一注册和执行层 |
| `trace/store.py` | 继续保存 run、step、tool_call、approval、finding |
| `approval/` | 继续处理 dangerous action 的人工审批 |
| `eval/` | 从 incident case 改为 resource diagnosis case |
| `app/api.py` | 从 `/incident` 改为 `/diagnose` |
| `main.py` | 从 `incident` 命令改为 `diagnose` 命令 |

---

# 2. 新项目目标

## 2.1 第一版目标

第一版目标不是做一个全能运维 Agent，而是做一个能稳定处理三类本地资源问题的 Agent：

```text
GPU
CPU
Memory
```

第一版必须做到：

```text
1. 能采集当前机器真实资源状态
2. 能识别 GPU / CPU / Memory 相关异常
3. 能定位可疑进程
4. 能生成证据链报告
5. 能区分安全建议和危险操作
6. 危险操作必须走 approval
7. 每次诊断都有 trace
8. 能用真实 stress 脚本测试
9. 能通过 eval 回归测试
```

第一版暂时不追求：

```text
1. 多 agent 协作
2. 长期后台自治
3. 完整 LLM ReAct
4. 大规模 RAG
5. Kubernetes / Prometheus / Loki
6. 自动 kill 进程
7. 复杂 UI
```

---

## 2.2 第二版目标

第二版再引入 learn-claude-code 风格的 harness 能力：

```text
1. hooks：在工具调用前后、诊断开始结束、审批前后插入控制逻辑
2. TodoWrite：让 Agent 显式维护诊断任务列表
3. subagent：GPU / CPU / Memory 各自成为隔离诊断子 Agent
4. compact：压缩长 trace 和大量工具结果
5. skills：按需加载诊断知识
6. memory：记录机器历史基线、常见进程、用户偏好
7. error recovery：工具失败时自动 fallback
8. task system：支持长任务、依赖任务、采样任务
9. background tasks：后台持续监控资源变化
10. agent team：多个诊断 Agent 协作
11. autonomous agents：后台 Agent 自动发现异常并创建任务
12. workspace isolation：每次诊断拥有独立 run workspace
```

第二版的核心不是推翻第一版，而是在第一版的 harness 上叠加这些机制。

---

# 3. 产品形态

## 3.1 用户是谁

第一版目标用户是：

```text
1. 你自己
2. 使用 GPU 服务器的开发者
3. 做模型训练 / 推理实验的人
4. 需要排查本地资源瓶颈的工程师
```

它更偏：

```text
AI Infra / ML Infra / Developer Productivity
```

而不是传统 SRE 平台。

---

## 3.2 典型使用场景

### 场景 1：训练任务很慢

用户输入：

```text
我的训练任务为什么很慢？
```

Agent 需要检查：

```text
GPU 利用率
GPU 显存
CPU load
内存和 swap
进程列表
Python 进程命令行
```

可能结论：

```text
GPU 利用率很低，但 CPU load 很高，可能是 dataloader / 数据预处理瓶颈。
```

---

### 场景 2：GPU 显存满了

用户输入：

```text
为什么 GPU 显存满了？
```

Agent 需要检查：

```text
nvidia-smi
GPU memory used / total
GPU process list
PID
进程命令行
用户
启动时间
```

可能结论：

```text
GPU 0 显存占用 23.1GB / 24GB，主要由 PID 12345 的 python 进程占用。
```

建议：

```text
先确认该进程是否是当前训练任务。
如需终止进程，必须走 approval。
```

---

### 场景 3：CPU 很高

用户输入：

```text
服务器 CPU 为什么打满了？
```

Agent 需要检查：

```text
CPU load average
CPU core count
top CPU processes
进程命令行
是否有异常多进程
```

可能结论：

```text
load average 高于 CPU 核数，多个 python worker 占用 CPU，可能是数据加载或多进程任务过多。
```

---

### 场景 4：内存不够

用户输入：

```text
为什么内存快满了？
```

Agent 需要检查：

```text
total memory
available memory
swap usage
top memory processes
OOM events
进程 RSS / VMS
```

可能结论：

```text
系统内存使用率 92%，swap 使用率 70%，PID 12345 的 Python 进程占用 48GB RSS，疑似大对象缓存或内存泄漏。
```

---

# 4. 第一版功能范围

## 4.1 支持的问题类型

第一版定义三大类问题类型。

---

### GPU 类

```text
gpu_memory_pressure
gpu_low_utilization
gpu_process_hogging
gpu_unavailable
gpu_unknown
```

第一版重点处理：

```text
1. 显存占满
2. 多进程抢 GPU
3. GPU 利用率低但 CPU 很高
4. nvidia-smi 不可用
```

暂不处理：

```text
1. GPU 温度异常自动降频
2. CUDA driver mismatch
3. NCCL 分布式通信问题
4. 多机多卡网络瓶颈
```

这些可以第二版用 skills 扩展。

---

### CPU 类

```text
cpu_saturation
cpu_single_process_hot
cpu_load_high
cpu_bottleneck_for_gpu
cpu_unknown
```

第一版重点处理：

```text
1. CPU 使用率高
2. load average 高
3. 某个进程 CPU 占用高
4. GPU 利用率低但 CPU 高，判断为 CPU / dataloader 瓶颈
```

暂不处理：

```text
1. NUMA 绑定
2. kernel-level 调度问题
3. cgroup 限制
4. 容器 CPU quota
```

---

### Memory 类

```text
memory_pressure
swap_pressure
oom_event
memory_process_hogging
memory_leak_candidate
memory_unknown
```

第一版重点处理：

```text
1. 系统内存使用率高
2. swap 使用率高
3. 某进程 RSS 很高
4. dmesg 中存在 OOM killed 记录
5. 连续采样时某进程内存持续增长
```

暂不处理：

```text
1. Python 对象级别泄漏定位
2. PyTorch CUDA memory fragmentation 细节
3. JVM / Node.js 专用内存分析
4. eBPF 级内存追踪
```

---

# 5. 第一版 Agent 工作流程

## 5.1 总流程

```text
用户输入问题
  ↓
ResourceIncident 标准化
  ↓
ResourceAgent 创建 DiagnosisRun
  ↓
判断问题类型：gpu / cpu / memory / mixed / unknown
  ↓
生成诊断计划
  ↓
通过 ToolRegistry 执行工具
  ↓
记录 DiagnosisStep / ToolCall
  ↓
Detector 分析证据
  ↓
生成 DiagnosisFinding
  ↓
生成 final_report
  ↓
需要危险操作时创建 Approval
  ↓
保存 Trace
  ↓
用户 approve / reject
```

---

## 5.2 第一版不让 LLM 主导

第一版不应该让 LLM 自由决定所有工具调用。

第一版采用：

```text
规则分类
固定 plan
真实工具采集
规则 detector
可选 LLM report writer
```

也就是：

```text
deterministic core + optional LLM explanation
```

原因：

```text
1. 资源诊断需要稳定
2. 第一版要可测
3. 危险操作不能依赖 LLM 自觉
4. 先建立确定性基线，第二版再加入 LLM planner
```

---

## 5.3 第一版 Agent 模式

建议保留两种模式：

```text
deterministic
llm_report
```

### deterministic

```text
规则选择 plan
规则分析证据
模板生成报告
```

### llm_report

```text
规则选择 plan
规则分析证据
LLM 只负责把 evidence 写成自然语言报告
```

LLM report writer 必须遵守：

```text
1. 不能编造证据
2. 不能新增工具没有返回的事实
3. 不能新增危险操作
4. 不能把 pending approval 写成已执行
5. 必须保留 evidence 列表
```

实现边界：

```text
1. planner / ToolRegistry / detectors / approval 仍然全部由 deterministic core 负责
2. LLM 输入只能来自 tool summaries、evidence_items、findings、recommendations、approvals
3. LLM 输出只允许替换 final_report
4. LLM 不允许修改 run.status、findings、approvals、tool_results
5. LLM 失败、超时、缺少 API key 或输出不合规时，必须 fallback 到模板报告
```

---

# 6. 核心模块设计

## 6.1 app/schemas.py

保留原有思想，但改成资源诊断语义。

核心 schema：

```text
ResourceIncident
DiagnosisRun
DiagnosisStep
ToolCall
EvidenceItem
DiagnosisFinding
Recommendation
Approval
```

---

### ResourceIncident

字段：

```text
incident_id
description
resource_type
severity
source
created_at
host
```

resource_type：

```text
gpu
cpu
memory
mixed
unknown
```

---

### DiagnosisRun

字段：

```text
run_id
incident_id
status
user_input
agent_mode
final_report
root_cause
summary
started_at
ended_at
error
```

status：

```text
pending
running
waiting_approval
completed
failed
```

---

### DiagnosisStep

字段：

```text
step_id
run_id
step_index
thought
action
args
observation
observation_preview
latency_ms
status
error
created_at
```

注意：

```text
observation 存完整工具结果
observation_preview 存人类可读摘要
```

这能让 trace 更好读。

---

### EvidenceItem

字段：

```text
evidence_id
run_id
source_tool
category
level
message
data
confidence
created_at
```

category：

```text
gpu
cpu
memory
process
system
oom
skill
```

level：

```text
info
warning
critical
```

---

### DiagnosisFinding

字段：

```text
finding_type
title
description
evidence_ids
confidence
recommended_actions
requires_approval
```

---

### Recommendation

字段：

```text
action
description
risk
requires_approval
command_preview
reason
```

risk：

```text
safe
write
dangerous
```

---

## 6.2 tools/registry.py

ToolRegistry 继续保留。

工具执行仍然必须经过：

```text
1. 根据 name 找 ToolSpec
2. input_model 参数校验
3. permission_level 检查
4. timeout 控制
5. handler 执行
6. ToolExecutionResult 归一化
7. hook event 预留
```

ToolSpec 建议包含：

```text
name
description
input_model
handler
permission_level
timeout_seconds
retry
tags
```

tags 用于第二版 tool search / skill activation。

---

## 6.3 tools/gpu.py

第一版工具：

```text
get_gpu_snapshot
list_gpu_processes
```

---

### get_gpu_snapshot

功能：

```text
调用 nvidia-smi 查询 GPU 状态
```

返回：

```text
available
driver_version
cuda_version
gpus[]
```

每张 GPU：

```text
index
name
utilization_gpu_percent
memory_used_mb
memory_total_mb
memory_used_percent
temperature_c
power_draw_w
```

如果没有 GPU 或 nvidia-smi 不存在，返回：

```text
available=false
error=nvidia-smi not found
```

不能直接失败退出。

---

### list_gpu_processes

功能：

```text
列出占用 GPU 的进程
```

返回：

```text
pid
gpu_index
process_name
used_memory_mb
username
command
```

`command` 可以通过 `/proc/{pid}/cmdline` 补充。

---

## 6.4 tools/cpu.py

第一版工具：

```text
get_cpu_snapshot
list_top_cpu_processes
```

### get_cpu_snapshot

返回：

```text
cpu_count
load_avg_1m
load_avg_5m
load_avg_15m
overall_cpu_percent
per_cpu_percent
```

### list_top_cpu_processes

返回：

```text
pid
username
cpu_percent
memory_percent
rss_mb
command
started_at
```

---

## 6.5 tools/memory.py

第一版工具：

```text
get_memory_snapshot
list_top_memory_processes
check_oom_events
```

### get_memory_snapshot

返回：

```text
total_mb
available_mb
used_mb
used_percent
swap_total_mb
swap_used_mb
swap_used_percent
```

### list_top_memory_processes

返回：

```text
pid
username
rss_mb
vms_mb
memory_percent
command
```

### check_oom_events

第一版可以通过：

```text
dmesg
journalctl
/var/log/syslog
```

尝试读取 OOM 记录。

没有权限时返回：

```text
available=false
reason=permission denied
```

而不是让工具失败。

---

## 6.6 tools/process.py

第一版工具：

```text
inspect_process
```

输入：

```text
pid
```

返回：

```text
pid
ppid
username
status
cmdline
cwd
create_time
cpu_percent
memory_info
open_files_count
num_threads
children
```

危险工具先只做 schema，不默认开放：

```text
kill_process
```

`kill_process` 必须是：

```text
permission_level = dangerous
```

第一版可以只创建 approval，不真正 kill。

---

## 6.7 skills/

第一版可以先不用复杂 RAG，但目录要设计好。

```text
skills/
  gpu_memory_pressure.md
  gpu_low_utilization.md
  cpu_saturation.md
  memory_pressure.md
  oom_event.md
  dataloader_bottleneck.md
```

每个 skill 文件结构固定：

```markdown
# Skill: GPU Memory Pressure

## When to use
...

## Signals
...

## Checks
...

## Diagnosis
...

## Safe actions
...

## Dangerous actions
...

## Notes
...
```

第一版可以用关键词匹配加载 skill。

第二版再做 embedding / RAG。

---

# 7. ResourceAgent 设计

## 7.1 类结构

```text
agent/
  resource_agent.py
  planner.py
  detectors.py
  report.py
```

---

## 7.2 ResourceAgent.diagnose()

主流程：

```text
1. 创建 DiagnosisRun
2. infer_resource_type
3. build_plan
4. 执行 plan
5. collect evidence
6. run detectors
7. build report
8. create approvals
9. save trace
10. return ResourceAgentResult
```

---

## 7.3 infer_resource_type()

第一版规则：

```text
包含 gpu / cuda / 显存 / nvidia → gpu
包含 cpu / load / 卡顿 / 打满 → cpu
包含 memory / 内存 / swap / oom → memory
包含 slow / 训练慢 / bottleneck → mixed
否则 → mixed
```

建议默认 `mixed`，而不是 `unknown`。

因为很多真实问题，例如“训练很慢”，需要同时查 GPU、CPU、Memory。

---

## 7.4 build_plan()

### GPU plan

```text
1. get_gpu_snapshot
2. list_gpu_processes
3. get_cpu_snapshot
4. get_memory_snapshot
5. list_top_cpu_processes
6. list_top_memory_processes
7. load_skill(gpu_memory_pressure / gpu_low_utilization)
```

### CPU plan

```text
1. get_cpu_snapshot
2. list_top_cpu_processes
3. get_memory_snapshot
4. get_gpu_snapshot
5. load_skill(cpu_saturation)
```

### Memory plan

```text
1. get_memory_snapshot
2. list_top_memory_processes
3. check_oom_events
4. get_cpu_snapshot
5. get_gpu_snapshot
6. load_skill(memory_pressure / oom_event)
```

### Mixed plan

```text
1. get_gpu_snapshot
2. get_cpu_snapshot
3. get_memory_snapshot
4. list_gpu_processes
5. list_top_cpu_processes
6. list_top_memory_processes
7. check_oom_events
8. load_skill(dataloader_bottleneck / resource_bottleneck)
```

---

# 8. Detector 设计

## 8.1 Detector 输入

Detector 不直接调用工具。

Detector 只接收：

```text
tool_results
```

这样后面容易测试。

---

## 8.2 Detector 输出

每个 detector 输出：

```text
DiagnosisFinding | None
```

---

## 8.3 GPU detector

### detect_gpu_memory_pressure

条件示例：

```text
gpu.memory_used_percent >= 90
```

输出：

```text
finding_type = gpu_memory_pressure
confidence = high
evidence = GPU memory used >= 90%
recommendation = identify GPU processes
```

如果某个进程占用显存超过 70%：

```text
recommendation = check process owner and command
```

危险建议：

```text
kill_process
```

必须 `requires_approval=true`。

---

### detect_gpu_low_utilization_cpu_bottleneck

条件示例：

```text
gpu.utilization < 20%
and cpu.load_avg_1m > cpu_count
```

输出：

```text
finding_type = cpu_bottleneck_for_gpu
description = GPU utilization is low while CPU load is high
```

可能原因：

```text
dataloader bottleneck
CPU preprocessing too slow
too many workers
disk IO not yet monitored
```

---

## 8.4 CPU detector

### detect_cpu_saturation

条件示例：

```text
load_avg_1m > cpu_count * 1.2
or overall_cpu_percent > 85
```

输出：

```text
finding_type = cpu_saturation
```

---

### detect_single_process_cpu_hot

条件示例：

```text
top_process.cpu_percent > 150
```

在多核机器上，Python 多进程或多线程可能超过 100%。

输出：

```text
finding_type = cpu_single_process_hot
```

---

## 8.5 Memory detector

### detect_memory_pressure

条件示例：

```text
memory.used_percent > 85
or available_mb < 1024
```

输出：

```text
finding_type = memory_pressure
```

---

### detect_swap_pressure

条件示例：

```text
swap_total_mb > 0
and swap_used_percent > 30
```

输出：

```text
finding_type = swap_pressure
```

---

### detect_oom_event

条件示例：

```text
check_oom_events 返回 OOM killed
```

输出：

```text
finding_type = oom_event
```

---

### detect_memory_hogging_process

条件示例：

```text
top_memory_process.rss_mb > total_memory * 0.4
```

输出：

```text
finding_type = memory_process_hogging
```

---

# 9. 报告设计

最终报告必须包含：

```text
1. 问题概览
2. 资源快照
3. 关键证据
4. 可能根因
5. 建议操作
6. 风险与审批
7. 后续排查建议
```

示例：

```markdown
## Resource Diagnosis Report

### 1. 问题概览
用户问题：为什么训练任务很慢？
诊断类型：mixed_resource_pressure

### 2. 资源快照
- GPU 0: utilization 8%, memory 23.1GB / 24GB
- CPU: load_avg_1m 32.4, cpu_count 16
- Memory: used 91%, swap used 64%

### 3. 关键证据
- GPU 利用率较低，但显存接近满载。
- CPU load 高于核心数，说明 CPU 侧存在排队。
- Top CPU processes 中多个 python dataloader worker 占用 CPU。
- 系统 swap 使用率较高，可能进一步拖慢数据加载。

### 4. 可能根因
当前训练慢的主要原因可能不是 GPU 算力不足，而是 CPU / 内存压力导致的数据加载瓶颈。

### 5. 建议操作
- 降低 dataloader num_workers。
- 检查数据预处理是否过重。
- 释放无关 GPU 进程。
- 观察 swap 是否持续增长。

### 6. 风险与审批
未自动执行危险操作。
如需终止进程，需要人工审批。

### 7. 后续排查
建议增加 60 秒资源采样，以确认 CPU、内存和 GPU 利用率趋势。
```

---

# 10. Approval 设计

第一版保留 approval，但默认不真正执行危险操作。

危险动作：

```text
kill_process
terminate_training_job
clear_gpu_process
```

第一版只支持：

```text
create approval
approve 后输出模拟执行
reject 后记录拒绝
```

第二版再考虑真实 kill。

审批对象：

```text
approval_id
run_id
action
args
reason
risk
status
created_at
decided_at
executed_at
```

---

# 11. Trace 设计

第一版 Trace 必须比原项目更清楚。

每个 run 保存：

```text
run
steps
tool_calls
evidence_items
findings
approvals
```

`show_trace.py` 输出：

```text
run_id=...
status=completed
user_input=...
resource_type=mixed

steps:
#0 get_gpu_snapshot
  thought: 检查 GPU 是否存在显存或利用率异常
  observation_preview: GPU0 util=8%, mem=23100/24576MB

#1 get_cpu_snapshot
  thought: 检查 CPU 是否成为瓶颈
  observation_preview: load_avg_1m=32.4, cpu_count=16

findings:
- cpu_bottleneck_for_gpu confidence=high
- memory_pressure confidence=medium

approvals:
- none
```

---

# 12. Eval 设计

ResourceOps 的 eval 分两类：

```text
1. fixture eval
2. live smoke eval
```

---

## 12.1 fixture eval

用录制好的工具输出作为 fixture，不依赖当前机器状态。

目录：

```text
eval/fixtures/
  gpu_memory_pressure.json
  cpu_saturation.json
  memory_pressure.json
  mixed_training_slow.json
```

case 文件：

```text
eval/resource_cases.jsonl
```

每条 case：

```json
{
  "case_id": "gpu_memory_pressure_single_process",
  "description": "为什么 GPU 显存满了？",
  "fixture": "gpu_memory_pressure.json",
  "expected_findings": ["gpu_memory_pressure"],
  "expected_evidence_keywords": ["memory", "GPU", "process"],
  "forbidden_actions": ["kill_process"],
  "requires_approval": false
}
```

为什么需要 fixture eval？

因为真实机器状态每天都不同，不能用实时资源作为单元测试基础。

---

## 12.2 live smoke eval

真实执行工具，验证 Agent 能不能跑通。

命令：

```bash
python eval/run_live_smoke.py
```

检查：

```text
get_gpu_snapshot 不崩
get_cpu_snapshot 不崩
get_memory_snapshot 不崩
ResourceAgent 能输出报告
Trace 能保存
```

Live smoke 不要求固定根因，只要求系统可用。

---

## 12.3 stress eval

可选，通过脚本制造压力。

```text
scripts/stress_cpu.py
scripts/stress_memory.py
scripts/stress_gpu_memory.py
```

注意：

```text
stress_memory.py 必须限制最大内存，避免把服务器打挂
stress_gpu_memory.py 必须要求用户确认
stress_cpu.py 必须能 Ctrl+C 干净退出
```

---

# 13. CLI 设计

第一版命令：

```bash
python main.py diagnose "为什么 GPU 显存满了？"
```

指定类型：

```bash
python main.py diagnose "系统 CPU 很高" --resource-type cpu
python main.py diagnose "内存快满了" --resource-type memory
python main.py diagnose "训练任务很慢" --resource-type mixed
```

查看 trace：

```bash
python main.py trace <run_id>
```

查看 approvals：

```bash
python main.py approvals
```

批准：

```bash
python main.py approve <approval_id>
```

拒绝：

```bash
python main.py reject <approval_id>
```

采样：

```bash
python main.py sample --duration 60 --interval 5
```

采样可以第二版做，第一版先保留命令设计。

---

# 14. API 设计

第一版保留 FastAPI。

接口：

```text
GET  /health
POST /diagnose
GET  /runs
GET  /runs/{run_id}
GET  /approvals
POST /approvals/{approval_id}/approve
POST /approvals/{approval_id}/reject
```

POST `/diagnose` 请求：

```json
{
  "description": "为什么 GPU 显存满了？",
  "resource_type": "gpu",
  "severity": "warning",
  "agent_mode": "deterministic"
}
```

返回：

```json
{
  "run": {},
  "steps": [],
  "findings": [],
  "final_report": "...",
  "requires_approval": false,
  "approvals": []
}
```

---

# 15. 第一版目录结构

```text
resourceops-agent/
├── README.md
├── requirements.txt
├── .env.example
├── main.py
├── docs/
│   ├── DESIGN.md
│   ├── DEVELOPMENT_HISTORY.md
│   ├── ROADMAP.md
│   ├── ResourceOps_Agent_DESIGN.md
│   ├── STAGE_PAUSE_SUMMARY.md
│   ├── demos/
│   └── images/
├── app/
│   ├── api.py
│   ├── cli.py
│   └── schemas.py
├── agent/
│   ├── resource_agent.py
│   ├── planner.py
│   ├── detectors.py
│   └── report.py
├── tools/
│   ├── registry.py
│   ├── gpu.py
│   ├── cpu.py
│   ├── memory.py
│   ├── process.py
│   └── system.py
├── skills/
│   ├── gpu_memory_pressure.md
│   ├── gpu_low_utilization.md
│   ├── cpu_saturation.md
│   ├── memory_pressure.md
│   ├── oom_event.md
│   └── dataloader_bottleneck.md
├── approval/
│   ├── store.py
│   └── service.py
├── trace/
│   ├── store.py
│   ├── models.py
│   └── replay.py
├── eval/
│   ├── resource_cases.jsonl
│   ├── fixtures/
│   ├── run_eval.py
│   └── run_live_smoke.py
├── scripts/
│   ├── stress_cpu.py
│   ├── stress_memory.py
│   └── stress_gpu_memory.py
├── tests/
│   ├── test_tools_cpu.py
│   ├── test_tools_memory.py
│   ├── test_tools_gpu.py
│   ├── test_detectors.py
│   ├── test_agent.py
│   └── test_api.py
└── var/
    ├── resourceops.sqlite3
    └── runs/
```

---

# 16. 为第二版预留的扩展点

第一版虽然不实现 learn-claude-code 的高级机制，但必须提前预留接口。

---

## 16.1 hooks 预留

第一版 ToolRegistry 内部预留 HookManager。

事件类型：

```text
DiagnosisStart
BeforePlan
AfterPlan
PreToolUse
PostToolUse
ToolError
EvidenceAdded
BeforeReport
ApprovalRequested
RunCompleted
RunFailed
```

第一版可以先只做 no-op。

第二版接入：

```text
PreToolUse：阻止危险命令
PostToolUse：记录审计
ToolError：触发 error recovery
BeforeReport：注入 memory / skills
RunCompleted：写入长期 memory
```

---

## 16.2 TodoWrite 预留

第一版的 plan 不要只是 list。

应该定义：

```text
DiagnosisTodo
```

字段：

```text
todo_id
run_id
title
status
tool_name
args
depends_on
created_at
updated_at
```

第一版可以从 plan 自动生成 todos。

第二版让 LLM 或 Agent 自己维护 TodoWrite。

后续要把 TodoWrite 从“工具调用列表”升级为“分层任务面板”：

```text
大任务：run 级阶段进度，固定显示在 CLI 顶部
小任务：当前阶段内部任务，显示在大任务下面，随阶段切换
```

大任务建议固定为：

```text
Planning tools
Tool execution
Report
Approval
Action execution
```

小任务建议从当前阶段展开：

```text
Tool execution:
  get_cpu_snapshot
  list_top_cpu_processes
  get_memory_snapshot

Action execution:
  kill_process dry-run
  release_cache dry-run
  restart_service dry-run
```

Action execution 在 V1 先预留，只展示结构和状态，不执行真实危险命令。

---

## 16.3 subagent 预留

第一版 ResourceAgent 统一调度。

第二版拆成：

```text
GpuDiagnosticAgent
CpuDiagnosticAgent
MemoryDiagnosticAgent
ProcessInspectionAgent
ReportAgent
```

每个 subagent 有独立 context，只返回结构化结果。

第一版要避免把所有逻辑写在一个巨大 `diagnose()` 里。

---

## 16.4 compact 预留

第一版工具结果可能已经很大。

因此每个 `ToolExecutionResult` 必须有：

```text
data
preview
summary
```

Trace 里展示 preview。

第二版做 compact：

```text
raw observation → compacted observation → evidence summary
```

---

## 16.5 skills 预留

第一版 skill 可以用 markdown + 关键词匹配。

第二版升级为：

```text
skill manifest
skill search
skill activation
skill result injection
```

skill manifest：

```yaml
name: gpu_memory_pressure
triggers:
  - gpu
  - memory
  - cuda oom
  - nvidia-smi
tools:
  - get_gpu_snapshot
  - list_gpu_processes
```

---

## 16.6 memory 预留

第一版先不做 memory，但 schema 预留：

```text
memory/
  store.py
```

第二版存：

```text
1. machine baseline
2. historical diagnoses
3. known safe processes
4. user preferences
5. ignored processes
6. recurring problems
```

例如：

```text
用户偏好：不要建议 kill jupyter
机器基线：GPU 空闲时显存通常 500MB
历史问题：昨天同一个 PID 模式造成过内存压力
```

---

## 16.7 error recovery 预留

第一版工具失败要返回结构化错误，而不是抛崩。

错误类型：

```text
command_not_found
permission_denied
timeout
parse_error
unsupported_platform
no_gpu
```

第二版 ErrorRecoveryPolicy：

```text
nvidia-smi 不存在 → 返回 no_gpu，不再继续 GPU 深查
dmesg 权限不足 → 尝试 journalctl
psutil 失败 → fallback 到 ps
工具超时 → 降级为 lighter command
```

---

## 16.8 task_system 预留

第一版一次诊断是同步执行。

第二版支持长任务：

```text
sample_resource_for_60s
watch_process_memory_growth
monitor_gpu_utilization
```

Task schema：

```text
task_id
run_id
title
status
depends_on
assigned_agent
workspace
created_at
updated_at
result
```

---

## 16.9 background_tasks 预留

第二版新增：

```text
background sampler
periodic monitor
long-running diagnosis
```

例如：

```text
每 5 秒采样一次 GPU / CPU / Memory，持续 60 秒。
```

这对 memory leak 和 GPU low utilization 很关键。

---

## 16.10 agent_team 预留

第二版可以设计：

```text
LeadResourceAgent
GpuAgent
CpuAgent
MemoryAgent
ProcessAgent
ReportAgent
```

协作方式：

```text
LeadResourceAgent 创建任务
各 subagent 领取任务
结果写入 trace / task board
Lead 汇总报告
```

---

## 16.11 autonomous_agents 预留

第二版做 always-on 模式：

```text
ResourceMonitorAgent 每隔 30 秒检查资源
发现异常自动创建 diagnosis task
后台 Agent 自动 claim task
生成告警或日报
```

第一版不做，但数据结构不要阻碍这个方向。

---

## 16.12 workspace isolation 预留

原 learn-claude-code 的 worktree isolation 是为了代码任务隔离。

ResourceOps 不一定需要 git worktree，但需要 run workspace isolation。

每次 run 创建：

```text
var/runs/run_xxx/
  raw/
  compact/
  report.md
  tool_calls.jsonl
  evidence.jsonl
```

好处：

```text
1. 每次诊断结果隔离
2. 方便 replay
3. 方便上传 debug bundle
4. 方便 background task 写入采样数据
```

---

# 17. 第一版开发阶段

第一版的主线是：

```text
先做真实可用、可追踪、可评测的单 Agent 闭环，
再逐步加入 LLM 报告、LLM 工具规划、计划校验和任务面板。
```

第一版不做多 Agent 自治，也不让 LLM 绕过系统边界。LLM 可以逐步参与，但所有工具执行、危险动作、trace 和 eval 都必须由系统接管。

## V1 已完成阶段

### V1-P0：项目重命名和 schema 调整

实现内容：

```text
1. 从 IncidentOps 收敛到 ResourceOps
2. 保留 ToolRegistry / TraceStore / Approval 等 Agent Harness 底座
3. 新增 ResourceIncident、EvidenceItem、DiagnosisFinding 等资源诊断 schema
4. CLI 从 incident 改为 diagnose
5. README 更新为本地资源诊断项目
```

完成标准：

```bash
python main.py diagnose "为什么 CPU 很高？"
```

能够创建一次诊断 run。

### V1-P1：真实资源工具

实现内容：

```text
1. get_cpu_snapshot
2. list_top_cpu_processes
3. get_memory_snapshot
4. list_top_memory_processes
5. check_oom_events
6. get_gpu_snapshot
7. list_gpu_processes
8. inspect_process
```

完成标准：

```bash
python -m pytest tests/test_tools_*.py
```

### V1-P2：确定性 ResourceAgent

实现内容：

```text
1. infer_resource_type
2. build_gpu_plan
3. build_cpu_plan
4. build_memory_plan
5. build_mixed_plan
6. 通过 ToolRegistry 执行工具
7. 保存 DiagnosisStep 和 ToolCall
```

完成标准：

```bash
python main.py diagnose "为什么 GPU 显存满了？"
```

能够执行完整工具链。

### V1-P3：Detectors

实现内容：

```text
1. GPU 显存压力识别
2. GPU 不可用识别
3. CPU 饱和识别
4. CPU 限制 GPU 利用率识别
5. 内存压力识别
6. swap 压力识别
7. OOM 事件识别
8. 单进程高占用识别
```

完成标准：

```text
fixture eval 能识别 expected_findings。
```

### V1-P4：报告和审批

实现内容：

```text
1. 根据 evidence / findings 生成报告
2. 危险建议生成 Approval
3. approve / reject
4. run.status 支持 waiting_approval
5. trace 保存 approvals
```

完成标准：

```text
危险操作不会自动执行，必须先人工审批。
```

### V1-P5：Eval 和真实测试脚本

实现内容：

```text
1. fixture eval
2. live smoke eval
3. stress_cpu.py
4. stress_memory.py
5. stress_gpu_memory.py
```

完成标准：

```bash
python eval/run_eval.py
python eval/run_live_smoke.py
```

### V1-P6：FastAPI 和 Demo

实现内容：

```text
1. POST /diagnose
2. GET /runs
3. GET /runs/{run_id}
4. GET /approvals
5. POST /approvals/{approval_id}/approve
6. POST /approvals/{approval_id}/reject
7. Dockerfile / docker-compose
```

完成标准：

```bash
uvicorn app.api:app --host 0.0.0.0 --port 18000 --workers 1
```

然后用 curl 能跑完整 HTTP 诊断和审批流程。

当前 API 的后台 report job 使用进程内线程池和内存 job registry，因此只支持单
Uvicorn worker。多进程部署需要先引入外部队列或 SQLite job lease，避免一个
worker 在启动恢复时误处理另一个 worker 正在生成的 report。

### V1-P6.5：稳定化和 trace 一致性

实现内容：

```text
1. CLI approve / reject 和 HTTP approve / reject 使用同一套 trace 同步逻辑
2. 普通 CLI trace 输出展示 approvals 状态
3. ResourceAgentResult 从手写 class 收敛为 schema
4. 修正 run summary 的 findings / evidence / approvals 计数格式
5. 增加测试覆盖，确认 CLI approval 后 trace 会同步更新
```

完成标准：

```bash
python main.py trace <run_id>
python main.py approve <approval_id>
python main.py trace <run_id>
```

第二次 trace 能看到：

```text
run.status=completed
approval.status=executed
```

---

## V1 后续阶段

### V1-P7：LLM 报告生成器（已实现）

目标：

```text
在不改变确定性诊断核心的前提下，增加可选 LLM 报告生成模式。
```

实现功能：

```text
1. 新增 agent/llm_client.py
2. 新增 agent/llm_report.py
3. ResourceAgent 支持 agent_mode="llm_report"
4. LLM 只根据已有 tool summaries / evidence / findings / approvals 改写 final_report
5. LLM 失败时 fallback 到模板报告
```

边界：

```text
1. LLM 不选择工具
2. LLM 不调用工具
3. LLM 不新增 finding
4. LLM 不创建或执行危险动作
5. LLM 不修改 approval / run status
```

完成标准：

```bash
python main.py diagnose "为什么 GPU 显存满了？" --agent-mode deterministic
python main.py diagnose "为什么 GPU 显存满了？" --agent-mode llm_report
python -m pytest tests/test_llm_report.py tests/test_agent.py tests/test_api.py
```

测试要求：

```text
1. fake LLM 正常返回时，final_report 使用 LLM 报告
2. fake LLM 报错时，fallback 到确定性模板报告
3. llm_report 不改变 findings / approvals / run.status
4. pending approval 不会被 LLM 写成 executed
```

### V1-P7.5：Report Context Builder（已实现）

目标：

```text
让 LLM 报告拿到更丰富但受控的上下文，避免只看到 tool preview / summary 而缺少关键明细。
```

实现功能：

```text
1. 新增 agent/report_context.py
2. 按工具类型从 ToolExecutionResult.data 中提取关键字段
3. 对进程 command 做截断
4. 限制 top process / GPU process / OOM event 数量
5. llm_report prompt 使用 report_context，而不是只使用 tool summary
6. trace 增加 build_report_context step，记录 LLM 实际看到的 compact context
```

边界：

```text
1. 不把完整 raw tool data 直接交给 LLM
2. 不让 LLM 重新决定 findings
3. 不让 LLM 修改 approvals
4. report_context 只用于报告生成，不用于工具执行决策
```

完成标准：

```text
1. LLM prompt 中能看到 top CPU / memory / GPU process 的关键字段
2. trace 中能看到 build_report_context step
3. build_report_context step 的 observation 保存 compact context
4. llm_report step 继续记录 used_llm / fallback_reason / prompt_length / response_length
```

### V1-P8：工具目录和计划 schema

目标：

```text
为 LLM 决定工具调用打基础，但还不让 LLM 真正接管规划。
```

当前状态：

```text
已完成。
P8 只标准化计划层，不改变工具执行、detectors、approval 和 report 的核心行为。
```

实现功能：

```text
1. ToolRegistry.list_tools() 作为工具元信息来源
2. 新增 ToolCatalog / ToolCatalogItem：给 planner / 未来 LLM planner 暴露可用工具清单
3. 新增 PlannedToolCall / ToolPlan schema
4. ToolPlan 支持 planner_mode、resource_type、user_question、steps、budget、fallback_plan、tool_catalog_version
5. ResourceAgent 按 ToolPlan.steps 执行工具
6. trace 增加 build_tool_plan step，保存 tool_plan 和 tool_catalog 快照
7. CLI trace 普通视图展示 planned tools
```

实际 schema：

```text
PlannedToolCall:
  planned_call_id
  step_index
  tool_name
  args
  reason
  expected_result
  permission_level
  requires_approval
  required
  tags

ToolPlan:
  plan_id
  planner_mode
  resource_type
  user_question
  steps
  max_steps
  budget
  fallback_plan
  tool_catalog_version
  created_at

ToolCatalogItem:
  name
  description
  input_schema
  permission_level
  requires_approval
  timeout_seconds
  retry
  tags
  resource_types
```

完成标准：

```text
1. 现有 deterministic plan 可以转换成 ToolPlan
2. ToolRegistry.list_tools() 能生成给 LLM 使用的工具目录
3. trace 中可以看到 build_tool_plan step
4. tool_calls 能正确关联到真实工具 step，而不是计划 step
5. P8 测试覆盖 ToolCatalog、ToolPlan 和 trace 关联
```

### V1-P9：LLM Planner 和 PlanValidator

目标：

```text
让 LLM 在安全边界内提出工具调用计划。
```

当前状态：

```text
已完成。
LLM planner 只提出候选 ToolPlan；候选计划必须通过 PlanValidator。
校验失败、LLM 不可用、LLM 输出无法解析时，执行 deterministic fallback plan。
```

实现功能：

```text
1. 新增 agent/llm_planner.py
2. 新增 agent/plan_validator.py
3. 新增 agent_mode="llm_planner"
4. LLM 根据用户问题、工具目录、预算和安全规则输出 ToolPlan
5. PlanValidator 校验 ToolPlan
6. 校验失败时 fallback 到 deterministic plan
7. trace 增加 llm_planner step，记录候选计划、校验错误、fallback 原因和最终选择的 plan
```

PlanValidator 必须检查：

```text
1. tool_name 是否存在于 ToolRegistry
2. args 是否符合对应 input_model
3. step 数量是否超过预算
4. 是否重复调用重工具
5. 是否包含 write / dangerous / approval-required 工具
6. 工具权限是否符合当前 agent_mode
7. resource_type 是否与本次诊断一致
```

完成标准：

```bash
python main.py diagnose "训练很慢，帮我看看瓶颈" --agent-mode llm_planner
python -m pytest tests/test_llm_planner.py tests/test_plan_validator.py
```

测试要求：

```text
1. fake LLM 输出合法 plan 时，Agent 按 plan 执行 safe 工具
2. fake LLM 输出未知工具时，PlanValidator 拒绝并 fallback
3. fake LLM 输出非法参数时，PlanValidator 拒绝并 fallback
4. fake LLM 输出 dangerous / approval-required 工具时，PlanValidator 拒绝并 fallback
5. 所有 LLM 候选 plan、校验结果、fallback 原因和 selected plan 写入 trace
```

### V1-P10：TodoWrite / 任务面板

目标：

```text
把 plan 从“内部列表”升级为可展示、可追踪、可恢复的任务面板。
```

定位：

```text
P10 先作为后续开发阶段保留。
它不新增诊断算法，不做真实动作执行，只增加任务状态层。
后续 P11 workspace、P12 action executor、V2 subagents 都复用这个任务状态层。
```

实现功能：

```text
1. 新增 TodoStatus / DiagnosisTodo schema
2. ToolPlan 自动转换成 todo 列表
3. 每个 todo 有 pending / running / completed / failed / skipped 状态
4. trace 展示 todos
5. CLI/API 可以查看某个 run 的任务状态
6. ResourceAgent 执行工具时同步更新 todo 状态
7. ResourceAgentResult 返回 todos
8. TraceStore 持久化 todos
```

建议字段：

```text
todo_id
run_id
todo_index
title
status
source
tool_name
args
planned_call_id
depends_on
assigned_agent
created_at
updated_at
result_preview
error
```

完成标准：

```bash
python main.py trace <run_id>
```

普通 trace 能看到：

```text
todos:
- completed get_gpu_snapshot
- completed list_gpu_processes
- pending approval kill_process
```

P10 不做：

```text
1. 不做真实 kill / restart / clean cache
2. 不做任务并行
3. 不做任务恢复/重试
4. 不做多 Agent 分配
5. 不做 workspace 文件落盘增强
6. 不做 Rich Live 刷新式终端 UI，这部分放到 P10.5
```

### V1-P10.5：分层任务面板 / Live Todo UI

目标：

```text
把 P10 的普通 todos 展示升级为类似 Claude Code 的刷新式任务面板。
```

设计动机：

```text
P10 只跟踪工具执行阶段，所以当 llm_planner 或 llm_report 很慢时，
用户看到的是“终端卡住”，而不是“Agent 正在规划 / 正在写报告”。

P10.5 要把整个 run 拆成大任务阶段，再把当前阶段内部拆成小任务。
大任务始终固定在终端顶部，小任务根据当前阶段变化。
```

大任务阶段：

```text
1. Planning tools
   - 推断资源类型
   - 构建 ToolCatalog
   - deterministic / LLM planner 生成 ToolPlan
   - PlanValidator 校验

2. Tool execution
   - 从 ToolPlan 展开每个工具调用
   - 每个工具调用是一个小任务
   - 工具完成后显示 result_preview 或 error

3. Report
   - 构建 report_context
   - template_report 或 llm_report
   - LLM 超时、失败时显示 fallback 原因

4. Approval
   - 根据 finding/recommendation 创建 approval
   - 展示 pending / approved / rejected / executed

5. Action execution
   - 后续 P12/P13 使用
   - 当前只预留阶段和 schema，不执行真实命令
```

小任务范围：

```text
P10.5 先做 Tool execution 小任务。
Action execution 小任务只预留字段，不接入真实执行。
Planning / Report / Approval 阶段先只作为大任务展示，不强制展开细粒度小任务。
```

建议扩展 DiagnosisTodo：

```text
level: phase | task
parent_todo_id: str | None
display_group: planning | tools | report | approval | actions
sort_order: int
title: str
status: pending | running | completed | failed | skipped | waiting_approval
result_preview: str | None
error: str | None
```

推荐的固定大任务：

```text
phase_planning_tools
phase_tool_execution
phase_report
phase_approval
phase_action_execution
```

事件边界：

```text
ResourceAgent 只发事件，不负责终端 UI。
CLI 负责接收事件并渲染 Live 面板。
trace 只保存最终状态，不保存每一次刷新帧。
```

建议事件：

```text
on_phase_snapshot(phases)
on_phase_started(phase)
on_phase_completed(phase)
on_phase_failed(phase)
on_todo_snapshot(todos)
on_todo_updated(todo, todos)
```

CLI 渲染方案：

```text
使用 rich.live.Live 每次重新渲染完整 frame。

frame =
  大任务面板：固定在上方
  任务详情面板：显示在下方
```

任务详情面板保留策略：

```text
底部任务详情不能只展示“当前阶段”，否则进入 Report / Approval 后，
用户会看不到前面 Tool execution 到底执行了哪些工具。

因此底部面板按分组保留：

1. Tool execution
   - 始终保留
   - 展示所有已执行/失败/跳过的工具任务

2. Approval
   - 有 approval task 时展示
   - y / n / s / q 后刷新为 completed / skipped / waiting_approval

3. Action execution
   - 始终保留分组
   - P10.8 先显示 reserved for action executor
   - P12/P13 接入真实 action task 后显示 action execution 小任务
```

颜色规范：

```text
大任务标题：bright_blue / bold blue
小任务标题：blue / dim blue
running：cyan
completed：green
failed：red
waiting_approval：yellow
pending：dim
```

示例展示：

```text
ResourceOps Agent

Phases
  [✓] Planning tools
  [●] Tool execution
  [ ] Report
  [ ] Approval
  [ ] Action execution

Tool execution
  [✓] get_cpu_snapshot        cpu=15.2%, load1=0.8
  [●] list_top_cpu_processes  running...
  [ ] get_memory_snapshot
```

实现要求：

```text
1. 不能继续用多次 print(todo_panel) 堆叠输出
2. 不能把 rich / ANSI 颜色写进 ResourceAgent
3. ResourceAgent 只更新状态并发事件
4. CLI Live renderer 负责颜色、刷新、布局
5. --json 模式必须禁用 Live UI，只输出结构化 JSON
6. conda run 手动测试时建议使用 --no-capture-output 或 python -u
```

完成标准：

```bash
python main.py diagnose "为什么 CPU 很高？" --planner-mode llm --report-mode llm
```

预期体验：

```text
1. LLM planner 慢时，顶部显示 Planning tools running
2. ToolPlan 生成后，Tool execution 阶段展开小任务
3. 工具执行时，小任务原地刷新状态
4. LLM report 慢时，顶部显示 Report running
5. 最终输出 report，并在 trace 中保存最终 phase/todo 状态
```

### V1-P10.6：Rich Live 刷新式 CLI 面板

目标：

```text
把 P10.5 的分层任务状态真正接入 CLI，做到同一块终端区域刷新，
而不是每次状态变化都 print 一份新的 todo 列表。
```

实现功能：

```text
1. CLI diagnose 在非 --json 模式下创建 RichTodoEventSink
2. ResourceAgent 通过 AgentEventSink 发 phase / task 状态快照
3. CLI 使用 rich.live.Live 渲染两个面板：
   - ResourceOps Agent：大任务阶段
   - Current tasks：保留 Tool execution / Approval / Action execution 的任务详情
4. phase 使用 bright_blue，task 使用 blue / dim blue
5. completed / running / failed / waiting_approval / skipped 使用不同图标和颜色
6. --json 模式禁用 Live UI，避免破坏结构化输出
```

注意：

```text
1. Rich UI 只属于 CLI 展示层
2. ResourceAgent 不直接 import rich，也不写 ANSI 颜色
3. trace 不保存每一帧刷新，只保存最终 todo 状态
4. conda run 测试实时刷新时建议加 --no-capture-output 和 python -u
```

完成标准：

```bash
conda run --no-capture-output -n zcj_hello python -u main.py diagnose \
  "为什么 CPU 很高？" \
  --resource-type cpu \
  --planner-mode llm \
  --report-mode llm
```

预期体验：

```text
1. 规划阶段：Planning tools 显示 running
2. 工具执行阶段：Current tasks 展示每个工具的执行状态
3. 报告阶段：Report 显示 running
4. 进入审批阶段后，Current tasks 仍保留 Tool execution 的历史工具任务
5. 最终 report 输出前，面板显示最终 phase / task 状态
```

### V1-P10.7：Approval / Action execution 阶段展示和 trace 同步

目标：

```text
把审批也纳入任务面板和 trace，而不是只在 report 文本里展示 approval_id。
```

实现功能：

```text
1. 根据 approvals 生成 approval task todo
2. Approval 大任务阶段支持 waiting_approval / completed
3. approval task 记录 approval_id、action、risk、status
4. CLI trace 的 todos 展示 approval_id
5. approve / reject 命令执行后同步更新 trace 中：
   - approvals 表
   - approval task todo
   - Approval phase todo
   - run.status
6. Action execution 阶段继续预留：
   - 当前没有 action executor
   - 默认显示 skipped / reserved for action executor
```

状态规则：

```text
approval pending  -> approval task waiting_approval
approval executed -> approval task completed
approval rejected -> approval task skipped

只要还有 pending approval：
  run.status = waiting_approval

所有 approval 都 resolved 后：
  run.status = completed
```

完成标准：

```bash
python main.py diagnose "为什么内存快满了？" --resource-type memory
python main.py approve <approval_id>
python main.py trace <run_id>
```

trace 中应该能看到：

```text
Approval phase completed
approval task completed / skipped
run_status completed
```

### V1-P10.8：Interactive Approval / 批量交互审批

目标：

```text
在 CLI diagnose 后提供可选的交互审批体验。
当一次诊断产生一个或多个 pending approval 时，用户可以在同一个终端里逐个批准、拒绝、跳过或退出。
```

设计边界：

```text
默认行为不变：
  python main.py diagnose ...

仍然是非阻塞诊断：
  1. 生成 report
  2. 创建 pending approvals
  3. run.status = waiting_approval
  4. 输出 run_id 后退出

只有显式加参数时才进入交互审批：
  python main.py diagnose ... --interactive-approval
```

为什么不默认等待审批：

```text
1. API / 脚本 / 自动化任务不能被 input() 阻塞
2. 诊断和审批是两个不同生命周期
3. 有些审批需要用户先看 trace、确认 PID、确认上下文后再决定
4. 后续 Web UI / API approval 仍然要复用同一套异步审批机制
```

交互规则：

```text
diagnose 完成并保存 trace 后：

1. CLI 先列出本次 run 的所有 pending approvals
2. 然后按顺序询问用户：
   y / yes / approve：批准，并模拟执行 dangerous action
   n / no / reject：拒绝
   s / skip：跳过，保持 pending
   q / quit：退出交互审批，剩余 pending 保持不变
3. 每处理一个 approval，立即调用 sync_approval_trace()
4. 最后输出 pending_approvals 和 run_status
```

Live UI 行为：

```text
如果 diagnose 使用 --interactive-approval 且不是 --json：

1. Rich Live 面板从诊断阶段一直保留到交互审批结束
2. 诊断阶段结束时，Approval phase 显示 waiting_approval
3. 用户选择 y / n 后：
   - approval 写入 ApprovalStore
   - sync_approval_trace() 更新 TraceStore
   - CLI 从 TraceStore 重新读取 todos
   - Live 面板刷新 Approval phase 和 approval task
4. 用户选择 s / q 时：
   - approval 保持 pending
   - 面板继续显示 waiting_approval
5. 交互审批结束后，CLI 关闭 Live，并打印最终面板快照
```

注意：

```text
交互审批阶段的 UI 以 trace 中的最终 todo 状态为准。
ResourceAgent 不参与审批后的 UI 刷新，因为 ResourceAgent.diagnose() 已经结束。
CLI 在需要 input() 时会临时暂停 Rich Live，显示审批提示并读取输入；
输入结束后再恢复 Live，避免全屏面板盖住输入提示。
审批提示使用颜色区分风险和状态：
  dangerous / rejected：red
  pending / skipped / waiting_approval：yellow
  approved / executed / completed：green
输入提示使用 y=批准 / n=拒绝 / s=跳过 / q=退出，避免 Rich markup 吃掉 [y] 这类文本。
```

多个审批的处理方式：

```text
一次性列出：

- [1/3] appr_xxx action=kill_process risk=dangerous status=pending
- [2/3] appr_yyy action=restart_service risk=dangerous status=pending
- [3/3] appr_zzz action=renice_process risk=write status=pending

然后逐个进入审批提示。
这样用户先知道本次 run 一共有多少危险操作，再逐项决策。
```

CLI 示例：

```bash
python main.py diagnose "为什么内存快满了？" \
  --resource-type memory \
  --interactive-approval
```

示例输出：

```text
待审批操作 run_id=run_xxx count=1
- [1/1] appr_xxx action=kill_process risk=dangerous status=pending

审批 [1/1]
approval_id=appr_xxx
action=kill_process
risk=dangerous
reason=Killing a process is destructive and must be approved.
args={"pid": 12345, "command_preview": "kill 12345"}
选择：[y]批准 / [n]拒绝 / [s]跳过 / [q]退出 >
```

实现要求：

```text
1. API 不进入交互审批
2. --json 模式不进入交互审批
3. 默认 diagnose 不进入交互审批
4. approve / reject 继续保留为独立命令
5. 每次 approve / reject 后立即同步 trace
6. skip / quit 不修改 approval 状态
7. 当前 approve 仍然只是 simulated execution，不真实 kill
```

完成标准：

```text
1. y：approval.status=executed，approval todo=completed，run.status=completed
2. n：approval.status=rejected，approval todo=skipped，run.status=completed
3. s：approval.status=pending，approval todo=waiting_approval，run.status=waiting_approval
4. q：未处理的 approval 保持 pending
```

### V1-P11：Workspace Isolation 增强

目标：

```text
把每次诊断的原始数据、计划、任务、压缩上下文和报告都隔离保存。
```

设计原则：

```text
1. workspace 是 SQLite trace 的补充，不替代 TraceStore
2. SQLite 负责查询；workspace 负责人类可读、调试、归档、打包
3. 每个 run 一个独立目录，删除一个 run workspace 不影响其它 run
4. workspace 内容必须来自 ResourceAgentResult / TraceStore / ApprovalStore 等结构化数据
5. 不在 workspace 里保存 API key、完整 .env、敏感系统信息
6. P11 只做落盘和查看，不做 replay，不做真实 action execution
```

最终目录目标：

```text
var/runs/run_xxx/
  metadata.json
  plan.json
  todos.json
  report.md
  raw/
    tool_outputs.jsonl
  compact/
    report_context.json
  trace/
    steps.json
    evidence.json
    findings.json
    approvals.json
```

文件职责：

```text
metadata.json
  本次 run 的顶层元数据：
  run_id、incident_id、user_input、resource_type、agent_mode、planner_mode、
  report_mode、status、started_at、ended_at、summary。

plan.json
  本次使用的 ToolPlan：
  plan_id、planner_mode、resource_type、steps、budget、fallback_plan、
  tool_catalog_version。

todos.json
  最终 phase/task todo 状态：
  Planning tools、Tool execution、Report、Approval、Action execution，
  以及工具任务和审批任务的最终状态。

report.md
  最终诊断报告。template report 或 LLM report 都写这里。

raw/tool_outputs.jsonl
  每行一个 ToolExecutionResult：
  tool_name、permission_level、status、validated_args、data、preview、summary、
  error、latency_ms。

compact/report_context.json
  给 LLM report 使用的受控上下文。
  只有 report_context 存在时才写；template-only run 可以跳过或写 null metadata。

trace/steps.json
  DiagnosisStep 列表，保留 Agent 执行路径和 observation_preview。

trace/evidence.json
  EvidenceItem 列表，保留 detector 提取的证据。

trace/findings.json
  DiagnosisFinding 列表，保留诊断结论和 recommended_actions。

trace/approvals.json
  Approval 快照，保留危险操作审批状态。
```

为什么 P11 要拆小阶段：

```text
workspace 会影响 CLI、TraceStore、approval sync、未来 action executor。
如果一次性做完整 debug bundle / replay / action artifacts，容易把边界做乱。
所以 P11 只做“稳定落盘 + 可查看 + 可打包”的最小闭环。
```

#### V1-P11.1：Workspace Writer 基础落盘

目标：

```text
诊断完成后，把 ResourceAgentResult 的核心内容写入 var/runs/<run_id>/。
```

新增模块：

```text
workspace/
  __init__.py
  writer.py
```

核心接口：

```python
class WorkspaceWriter:
    def write_agent_result(self, result: ResourceAgentResult) -> Path:
        ...
```

P11.1 落盘内容：

```text
metadata.json
plan.json
todos.json
report.md
raw/tool_outputs.jsonl
trace/steps.json
trace/evidence.json
trace/findings.json
trace/approvals.json
```

接入点：

```text
CLI diagnose:
  trace_store.save_agent_result(result)
  workspace_writer.write_agent_result(result)

API diagnose:
  trace_store.save_agent_result(result)
  workspace_writer.write_agent_result(result)
```

注意：

```text
1. P11.1 不要求 report_context.json，因为它只在 llm_report 模式稳定存在
2. 写文件要使用结构化 model_dump(mode="json")
3. JSON 文件使用 ensure_ascii=False + indent=2，方便人看
4. JSONL 用一行一个 tool result，方便后续追加和 grep
5. 写入失败不能让诊断主流程崩掉，至少要返回可读错误或记录 warning
```

完成标准：

```bash
python main.py diagnose "为什么 CPU 很高？" --resource-type cpu
ls var/runs/<run_id>/
```

应该看到：

```text
metadata.json
plan.json
todos.json
report.md
raw/tool_outputs.jsonl
trace/steps.json
trace/evidence.json
trace/findings.json
trace/approvals.json
```

#### V1-P11.2：Compact Context / LLM Report 上下文落盘

目标：

```text
把 LLM report 真正看到的 report_context 保存下来，方便定位 LLM 报告质量问题。
```

背景：

```text
P7.5 解决了“LLM 上下文太 compact，看不到 top process 明细”的问题。
P11.2 要把这个上下文持久化到 workspace，方便回答：
  LLM 到底看到了哪些 tool details？
  LLM 是基于什么 evidence / findings 写报告的？
```

落盘内容：

```text
compact/report_context.json
```

数据来源：

```text
优先从 DiagnosisStep(action="build_report_context").observation 获取。
如果没有该 step：
  - template report 模式：可以不生成 compact/report_context.json
  - 或写 compact/report_context.json 为 {"available": false, "reason": "report_mode=template"}
```

完成标准：

```bash
python main.py diagnose "为什么 CPU 很高？" --report-mode llm
cat var/runs/<run_id>/compact/report_context.json
```

应该能看到：

```text
context_version
resource_type
tool_summaries
selected_details
evidence_items
findings
approvals
```

#### V1-P11.3：CLI Workspace 查看命令

目标：

```text
用户不需要手动 ls 目录，也能通过 CLI 找到一次 run 的 workspace。
```

新增命令：

```bash
python main.py workspace <run_id>
```

输出：

```text
workspace=var/runs/run_xxx
metadata.json
plan.json
todos.json
report.md
raw/tool_outputs.jsonl
compact/report_context.json
trace/steps.json
trace/evidence.json
trace/findings.json
trace/approvals.json
```

可选参数：

```bash
python main.py workspace <run_id> --json
python main.py workspace <run_id> --show-report
python main.py workspace <run_id> --show-context
```

行为：

```text
--json:
  输出 workspace 文件清单和路径，方便脚本使用

--show-report:
  直接打印 report.md 内容

--show-context:
  直接打印 compact/report_context.json，方便检查 LLM report 实际看到的上下文
```

完成标准：

```bash
python main.py workspace <run_id>
python main.py workspace <run_id> --show-report
python main.py workspace <run_id> --show-context
```

#### V1-P11.4：Approval Sync 后更新 workspace

目标：

```text
approve / reject / interactive approval 改变审批状态后，workspace 里的 approvals 和 todos 也要更新。
```

为什么需要：

```text
P10.8 后，审批不只存在于 approval store 和 SQLite trace；
workspace 如果不更新，就会出现：
  trace 里 approval=executed
  workspace trace/approvals.json 里还是 pending
这会破坏 debug bundle 的可信度。
```

接入点：

```text
approve command:
  sync_approval_trace(...)
  workspace_writer.update_from_trace(run_id)

reject command:
  sync_approval_trace(...)
  workspace_writer.update_from_trace(run_id)

interactive approval:
  每次 sync_approval_trace(...) 后
  workspace_writer.update_from_trace(run_id)
```

建议接口：

```python
class WorkspaceWriter:
    def update_from_trace(self, run_id: str, trace_store: TraceStore) -> Path:
        ...
```

更新内容：

```text
metadata.json          # run.status 可能变化
todos.json             # approval task 状态变化
trace/approvals.json   # approval.status 变化
```

实际约束：

```text
1. update_from_trace 只同步审批后会变化的文件
2. 不重写 plan.json / raw/tool_outputs.jsonl / compact/report_context.json
3. 如果旧 run 没有 workspace，CLI/API approve/reject 不失败，只跳过 workspace sync
4. workspace_version 随 writer 当前版本写入 metadata.json
```

完成标准：

```bash
python main.py diagnose "为什么内存快满了？" --resource-type memory
python main.py approve <approval_id>
cat var/runs/<run_id>/trace/approvals.json
cat var/runs/<run_id>/todos.json
```

应该看到：

```text
approval.status=executed
approval task status=completed
run.status=completed
```

#### V1-P11.5：Debug Bundle 打包

目标：

```text
把一次 run 的 workspace 打包成一个可分享的调试包。
```

新增命令：

```bash
python main.py bundle <run_id>
python main.py bundle <run_id> --json
```

输出：

```text
var/bundles/run_xxx.tar.gz
```

可通过环境变量覆盖 bundle 输出目录：

```bash
RESOURCEOPS_BUNDLE_ROOT=/tmp/resourceops-bundles python main.py bundle <run_id>
```

打包内容：

```text
var/runs/<run_id>/
```

安全规则：

```text
1. 不包含 .env
2. 不包含 API key
3. 不包含 var/approvals.jsonl 全局文件
4. 只打包当前 run workspace
5. 后续如有敏感字段，需要在 writer 层做 redaction
```

当前实现：

```text
1. bundle 只添加 var/runs/<run_id>/ 目录
2. tar 内部路径为 runs/<run_id>/...
3. tar filter 会排除 .env 和 approvals.jsonl
4. 找不到 workspace 时返回错误，不创建空包
```

完成标准：

```bash
python main.py bundle <run_id>
tar -tzf var/bundles/run_xxx.tar.gz
```

能看到 workspace 文件树。

P11 不做：

```text
1. 不做 replay
2. 不做从 workspace 恢复 run
3. 不做真实危险动作执行
4. 不做 action pre-check / post-check
5. 不做多 Agent workspace 合并
6. 不做长期归档清理策略
```

P11 总完成标准：

```text
1. 每次 diagnose 都产生独立 workspace
2. workspace 中能看到 metadata、plan、todos、report、tool outputs、steps、evidence、findings、approvals
3. llm_report run 能看到 compact/report_context.json
4. approve / reject 后 workspace 审批状态同步更新
5. CLI 可以查看 workspace 和 report
6. 可以把 workspace 打成 debug bundle
```

### V1-P12：Action Executor dry-run

状态：已完成 P12.1 / P12.2。

目标：

```text
把 recommendation / approval 后面的“动作执行”抽象出来，但仍然只做 dry-run。
```

为什么 P12 才做：

```text
P10 有任务状态，P11 有 workspace 产物隔离之后，动作执行才有足够审计基础。
真实执行之前必须先有 dry-run、pre-check、post-check 和 trace 记录。
```

P12 只拆成两个阶段：

```text
P12.1：Action Executor dry-run 核心
P12.2：Action execution 接入 trace / todo / workspace / CLI/API
```

#### V1-P12.1：Action Executor dry-run 核心

目标：

```text
先把“动作执行”从 ApprovalService 里抽出来，形成独立执行边界。
这一阶段只产生 dry-run 结果，不改变系统状态。
```

实现内容：

```text
1. 新增 ActionSpec，描述允许的动作、参数 schema、风险等级、审批要求
2. 新增 ActionResult，标准化记录 action、args、mode、status、pre_check、execution、post_check、preview、error
3. 新增 ActionExecutor，统一接收 action + args + mode
4. 首批只支持 kill_process 的 dry-run
5. kill_process dry-run 只检查参数和生成预览，不真实 kill
6. 未注册 action 返回 blocked / failed，不允许随意执行
7. requires_approval=True 的 action 必须带有已通过的 approval 记录
```

建议 schema：

```text
ActionSpec:
  name
  description
  input_schema
  risk
  requires_approval
  dry_run_supported
  real_execution_supported

ActionResult:
  action
  args
  mode              # dry_run / real
  status            # success / failed / blocked
  pre_check
  execution
  post_check
  preview
  error
  created_at
```

P12.1 完成标准：

```text
1. ActionExecutor 可以执行 kill_process dry-run
2. dry-run 返回 ActionResult(mode=dry_run)
3. 不会真实终止进程
4. 无 approval 或 approval 非 executed/approved 时，dangerous action 被 blocked
5. 单元测试覆盖 success / blocked / unknown action
```

#### V1-P12.2：Action execution 接入 trace / todo / workspace / CLI/API

目标：

```text
把 P12.1 的 ActionExecutor 接入现有审批流，让 approve 后的 dry-run 成为完整可审计产物。
```

实现内容：

```text
1. approve 后不再只返回 simulated tool_result，而是调用 ActionExecutor(dry_run=True)
2. CLI/API approve 输出 action_result
3. 新增 action execution task，和 Approval task 分开展示
4. TraceStore 保存 action_result
5. workspace 保存 action_results，例如 trace/action_results.json 或 actions/action_results.json
6. workspace approval sync 后也同步 action_result 和 action todo
7. main.py trace / workspace 能看到 action execution 结果
8. Action execution 阶段从 reserved/skipped 变成 completed/failed/blocked
```

P12.2 完成标准：

```text
1. approve kill_process 后仍不真实 kill
2. CLI/API 返回 action_result.mode=dry_run
3. trace 中能看到 action_result
4. workspace 中能看到 action_result
5. todo 面板能看到 action execution 小任务状态
6. 出错时 run 不会误标为 completed
```

P12 总完成标准：

```text
1. approval approve 后走 ActionExecutor，而不是散落的模拟逻辑
2. dangerous action 默认只 dry-run
3. action_result 同步进入 trace、workspace、CLI/API 输出和 todo 状态
4. 仍然没有真实危险动作执行
```

当前实现：

```text
1. actions/executor.py 定义 ActionSpec / ActionResult / ActionExecutor
2. ApprovalService.approve_with_action_result() 调用 ActionExecutor dry-run
3. TraceStore 保存 action_results 表，并创建 Action execution todo
4. WorkspaceWriter 同步 trace/action_results.json
5. CLI/API approve 返回 action_result
6. main.py trace 展示 action_results
```

验证方式：

```bash
python main.py diagnose "为什么内存快满了？" --resource-type memory --interactive-approval
python main.py trace <run_id> --json | jq '.action_results'
jq . var/runs/<run_id>/trace/action_results.json
jq '.[] | select(.source == "action_executor")' var/runs/<run_id>/todos.json
```

期望：

```text
action_result.mode=dry_run
action_result.status=success
execution.changed_system_state=false
Action execution todo status=completed
```

### V1-P13：真实安全动作执行

目标：

```text
在极小白名单内开放真实动作执行。
```

安全边界：

```text
1. 默认关闭真实执行，需要显式配置 RESOURCEOPS_ENABLE_REAL_ACTIONS=true
2. 只允许 allowlist 中的动作
3. dangerous 动作必须 approval
4. args 必须通过 schema 校验
5. pre-check 必须通过
6. dry-run 必须先成功
7. post-check 必须记录
8. 禁止操作当前进程、系统关键进程、root-owned 关键服务
9. 所有真实执行都写 trace 和 workspace
```

首批建议只开放：

```text
1. inspect_process：safe，已存在，只读
2. renice_process：write，需要 approval
3. kill_process：dangerous，需要 approval + 二次确认 + allowlist
```

完成标准：

```text
1. 默认配置下真实执行不可用
2. 开启配置后，只能执行 allowlist 动作
3. 未审批 dangerous 动作会被拒绝
4. action_result 记录 pre-check / dry-run / execution / post-check
5. 出错时不会误标 completed
```

当前实现：

```text
1. P13.1/P13.2 已完成 kill_process gated real execution
2. P13.3 已完成 renice_process write-level gated real execution
3. P13.4 已完成 inspect_process safe read-only action surface
4. approve 仍然只执行 ActionExecutor dry-run
5. CLI 新增 execute-real <approval_id> --confirm-real
6. API 新增 POST /approvals/{approval_id}/execute-real
7. write/dangerous real execution 默认关闭，需要 RESOURCEOPS_ENABLE_REAL_ACTIONS=true
8. write/dangerous real action 必须出现在 RESOURCEOPS_REAL_ACTION_ALLOWLIST 中
9. real write/dangerous action 必须已有 approval 且显式 confirm_real
10. safe inspect_process 可直接只读执行，不需要 approval/env/allowlist，不改变系统状态
11. real kill_process 会先执行 dry-run，再做 pre-check，再 terminate，再 post-check
12. real renice_process 会先执行 dry-run，再做 pre-check，再修改 nice，再 post-check
13. trace/workspace 会记录 mode=real 的 ActionResult
14. 测试覆盖 mocked process、只终止测试自有子进程的 kill smoke、只调整测试自有子进程 nice 的 renice smoke，以及 inspect_process safe action
```

验证方式：

```bash
python -m compileall -q actions app agent approval trace tools scripts eval tests workspace
python -m pytest -q tests/test_actions_p13.py tests/test_api.py tests/test_action_execution_p12.py tests/test_cli.py
python -m pytest -q
```

---

# 18. 第二版开发阶段

第二版的主线是：

```text
在 V1 的“可控 LLM 工具规划”基础上，扩展成更完整的 Agent Harness：
hooks、skills、memory、subagents、agent team、background tasks、autonomous agents。
```

第二版不是推翻第一版。第二版必须继续保留：

```text
1. ToolRegistry 作为唯一工具执行边界
2. PlanValidator 作为 LLM plan 的安全边界
3. Approval 作为 dangerous action 的人工审批边界
4. TraceStore 作为复盘边界
5. Workspace Isolation 作为运行隔离边界
```

## V2-P1：Hooks 和 Error Recovery

目标：

```text
把诊断流程中的关键节点开放为可插拔 hook，并加入工具失败恢复策略。
```

实现功能：

```text
1. 新增 hooks/manager.py
2. 支持 DiagnosisStart / BeforePlan / AfterPlan
3. 支持 PreToolUse / PostToolUse / ToolError
4. 支持 BeforeApproval / BeforeReport / RunCompleted / RunFailed
5. 新增 ErrorRecoveryPolicy
```

典型用途：

```text
1. PreToolUse：阻止不安全工具调用
2. ToolError：nvidia-smi 失败后降级为 no_gpu 结论
3. AfterPlan：记录 LLM 原始计划和校验结果
4. BeforeReport：注入 memory / skills / compact context
5. RunCompleted：写入历史 memory
```

完成标准：

```text
1. hook 可以注册和触发
2. hook 失败不会拖垮主流程
3. 工具失败能触发 fallback 或结构化错误
```

## V2-P2：Skills

目标：

```text
把诊断经验从代码规则中拆出来，让 planner 可以按场景加载技能。
```

实现功能：

```text
1. 新增 skills/ 目录
2. 定义 skill manifest
3. 支持 skill search
4. 支持 skill activation
5. LLM Planner 可以把相关 skill 作为上下文
```

skill 示例：

```yaml
name: gpu_cuda_oom
triggers:
  - cuda out of memory
  - 显存爆了
tools:
  - get_gpu_snapshot
  - list_gpu_processes
  - inspect_process
detectors:
  - gpu_memory_pressure
safe_actions:
  - inspect_gpu_processes
dangerous_actions:
  - kill_process
```

完成标准：

```text
1. 用户问 CUDA OOM 时能激活 gpu_cuda_oom skill
2. skill 能影响 LLM Planner 的工具选择
3. skill 不允许绕过 ToolRegistry / Approval
```

## V2-P3：Memory 和基线

目标：

```text
让 Agent 记住机器基线、历史问题和用户偏好。
```

实现功能：

```text
1. 新增 memory/store.py
2. 记录机器资源基线
3. 记录历史诊断摘要
4. 记录常见安全进程
5. 记录用户偏好和禁止建议
6. 把 memory 作为 planner/report 的只读上下文
```

优先记录：

```text
1. GPU 空闲显存基线
2. 常驻进程白名单
3. 用户不希望 kill 的进程名
4. 最近重复出现的问题
5. 某些工具在当前机器上的失败模式
```

完成标准：

```text
1. RunCompleted 后可以写入历史 memory
2. 下一次诊断可以读取相关 memory
3. memory 只能影响建议，不允许直接执行危险动作
```

## V2-P4：Subagents

目标：

```text
把不同资源域拆成独立诊断子 Agent，每个子 Agent 只处理自己的上下文。
```

实现功能：

```text
1. GpuDiagnosticAgent
2. CpuDiagnosticAgent
3. MemoryDiagnosticAgent
4. ProcessInspectionAgent
5. ReportAgent
6. 定义 SubagentResult schema
```

边界：

```text
1. 子 Agent 不直接写最终 run.status
2. 子 Agent 不直接执行 dangerous action
3. 子 Agent 输出结构化 evidence / finding / recommendation
4. LeadResourceAgent 负责汇总
```

完成标准：

```text
1. mixed 诊断可以拆给 GPU / CPU / Memory 子 Agent
2. 每个子 Agent 的结果能单独写入 trace
3. LeadResourceAgent 能汇总成统一报告
```

## V2-P5：Agent Team

目标：

```text
从“一个 Agent 调多个函数”升级为“Lead Agent 协调多个专职 Agent”。
```

实现功能：

```text
1. LeadResourceAgent 创建任务
2. GPU / CPU / Memory / Process / Report Agent claim task
3. Task Board 记录 assigned_agent
4. 每个 agent 有自己的 compact context
5. Lead 汇总所有 agent 结果
```

工作流：

```text
用户问题
  ↓
LeadResourceAgent 创建任务
  ↓
各专职 Agent 执行自己的任务
  ↓
结果写回 trace / task board
  ↓
Lead 汇总 evidence / findings / approvals
  ↓
ReportAgent 生成报告
```

完成标准：

```text
1. trace 能看到每个 task 由哪个 agent 完成
2. 某个子 Agent 失败不会导致整个 run 崩溃
3. Lead 能根据部分结果生成降级报告
```

## V2-P6：Background Tasks 和长任务采样

目标：

```text
支持持续采样和异步诊断，用来发现瞬时瓶颈和趋势问题。
```

实现功能：

```text
1. sample_resource_for_60s
2. watch_process_memory_growth
3. monitor_gpu_utilization
4. 后台任务状态：queued / running / completed / failed / cancelled
5. 采样结果写入 run workspace
```

适用场景：

```text
1. 训练任务很慢，但瞬时 GPU 利用率不稳定
2. 内存泄漏需要观察增长趋势
3. CPU load 周期性抖动
4. GPU 利用率长期低于预期
```

完成标准：

```text
1. 可以启动 60 秒资源采样任务
2. trace 能看到任务状态变化
3. 采样结果能被 detector 或 report 使用
```

## V2-P7：Autonomous Resource Monitor Agent

目标：

```text
让 Agent 可以在后台监控资源，发现异常后自动创建诊断任务。
```

实现功能：

```text
1. ResourceMonitorAgent 周期性采样
2. 异常阈值触发 diagnosis task
3. rate limit，避免重复创建 run
4. quiet hours，避免打扰用户
5. 自动生成告警摘要
6. dangerous action 仍然必须 approval
```

安全要求：

```text
1. 后台 Agent 只能自动采集 safe 工具
2. 后台 Agent 不能自动执行 kill_process
3. 每类异常需要冷却时间
4. 所有后台 run 必须写 trace
5. 用户可以关闭 autonomous 模式
```

完成标准：

```text
1. 后台监控发现 GPU / CPU / Memory 异常后能创建 run
2. 重复异常不会无限创建 run
3. 用户能查看后台 run 的 trace 和报告
```

## V2-P8：Workspace Isolation 完整化和 Debug Bundle

目标：

```text
把 workspace isolation 从“每个 run 一个目录”升级为完整调试和复盘能力。
```

实现功能：

```text
1. 每个 subagent 有独立 context 目录
2. 每个 background task 有独立采样目录
3. compact context 可复用给 report / memory / skills
4. 支持导出 debug bundle
5. 支持 replay 某次 run 的 plan / tool_result / finding
```

完整目录：

```text
var/runs/run_xxx/
  metadata.json
  plan.json
  raw/
  compact/
  artifacts/
  tasks/
  agent_contexts/
    gpu/
    cpu/
    memory/
    process/
    report/
  background/
  debug_bundle.zip
```

完成标准：

```text
1. 可以从 workspace 打包一次完整诊断材料
2. 可以 replay fixture 化的工具结果
3. 多 Agent 和后台任务的上下文互不污染
```

---

# 19. 为什么这样设计

这个设计的核心是：

```text
第一版聚焦真实可测。
第二版扩展 agent harness。
```

第一版不追求炫技，而是要证明：

```text
1. Agent 能查真实机器状态
2. Agent 能基于证据诊断
3. Agent 能克制，不乱执行危险操作
4. Agent 有 trace 和 eval
```

第二版再逐步变成：

```text
有 hooks
有 skills
有 memory
有 subagents
有 task system
有 background autonomous agents
```

这条路线比继续扩展模拟 IncidentOps 更稳，也更有产品感。

---

# 20. 简历表述

## 项目一句话

> 设计并实现 ResourceOps Agent，一个面向 GPU / CPU / Memory 资源异常的本地诊断 Agent。系统通过 Tool Registry 采集 nvidia-smi、psutil、进程、OOM 等真实运行时信息，基于 evidence detectors 生成资源瓶颈诊断报告，并对 kill process 等危险操作引入 Human-in-the-loop 审批。项目支持 CLI / FastAPI 调用、Trace / Replay、Eval 回归测试，并预留 hooks、skills、memory、subagent、background task 等 Agent Harness 扩展机制。

## 简历 bullet

```text
- 构建 GPU/CPU/Memory 资源诊断 Agent，支持真实系统指标采集、异常进程定位和证据链报告生成。
- 设计 ToolRegistry、permission level、Approval、TraceStore 等 Agent Harness 基础设施，实现安全可审计的工具调用流程。
- 实现 deterministic detectors 识别 GPU 显存压力、CPU saturation、Memory pressure、Swap pressure、OOM event 等问题。
- 设计 fixture eval 与 live smoke eval，区分可复现测试和真实环境测试，保证 Agent 在动态机器状态下仍可验证。
- 预留 hooks、skills、memory、subagent、background task、workspace isolation 等扩展点，为第二阶段 LLM-driven Agent Harness 演进做准备。
```

---

# 21. 最终建议

不要直接在原 IncidentOps 上继续堆服务故障规则。

建议正式开一个清晰分支或新目录，把项目收敛成：

```text
ResourceOps Agent
```

第一版把 GPU / CPU / Memory 做到真实可测。

第二版再按 learn-claude-code 的 harness 机制逐个加入：

```text
hooks
TodoWrite
skills
memory
subagents
background tasks
agent team
autonomous agents
workspace isolation
```

最终目标不是做一个普通脚本，而是做一个：

```text
真实可用 + 可追踪 + 可评测 + 可扩展的本地资源诊断 Agent Harness
```
