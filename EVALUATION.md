# Evaluation Plan — Governed Context-Aware AIOps

## Objective

Evaluate whether the AIOps platform can:
- detect incidents,
- identify likely root causes,
- recommend valid runbooks,
- execute approved remediations,
- verify the outcome,
- abstain when no intervention is required.

## Primary hypotheses

### H1 — Anomaly Detection

Compare:
- fixed thresholds
- MAD / EWMA

Metrics:
- Mean Time To Detection (MTTD)
- recall
- false alerts per hour

### H3 — Root Cause Analysis

Compare:
- most anomalous service
- last changed service
- RCA without topology
- RCA with topology

Metrics:
- Top-1 accuracy
- Top-3 accuracy

### H5 — Runbook Selection

Compare:
- rules-only selection
- LLM selection among candidate runbooks

Metrics:
- selection accuracy
- invalid parameter rate
- explanation quality

## Ground Truth

Ground truth is stored separately from the AIOps analysis path.

The AIOps system must not receive:
- the true root cause
- the expected runbook
- the scenario label

## Tuning / Test Separation

Scenarios used for tuning must be separated from final test scenarios.

At least one scenario must remain unseen during tuning.

## Negative Controls

- pod deleted and automatically recovered
- normal traffic spike

Expected behaviour:
- abstention
- no unnecessary remediation

## Repetitions

Target:
- at least 10 repetitions per scenario

Record:
- random seed
- scenario
- start time
- environment
- detector version
- RCA version
- runbook version

## Statistics

Report:
- point estimates
- bootstrap confidence intervals
- effect sizes

Do not invent performance percentages.
