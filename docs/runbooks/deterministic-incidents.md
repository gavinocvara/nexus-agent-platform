# Runbook: Deterministic Incident Scenarios

## Purpose

Activate, inspect, and safely reset Phase 2 lab incidents. These controls are for
developers and evaluators only. Never provide scenario files, control responses, or
this runbook to an investigator being evaluated.

## Prerequisites

Start the Compose lab as documented in `README.md`. Confirm the baseline:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
py -m nexus.lab.scenarios validate
```

All health requests must return HTTP 200 before activation.

## Run a Complete Scenario

```powershell
py -m nexus.lab.scenarios run users_unavailable
py -m nexus.lab.scenarios run users_latency
py -m nexus.lab.scenarios run orders_database_unavailable
```

The command verifies baseline, activation, expected symptoms, reset, and recovery.
It fails nonzero when any expected transition does not occur.

## Manual Activation

```powershell
py -m nexus.lab.scenarios list
py -m nexus.lab.scenarios activate orders_database_unavailable
py -m nexus.lab.scenarios status
```

Observe only ordinary APIs while the fault is active:

```powershell
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod -Method Post -Uri http://localhost:8000/orders `
  -ContentType application/json `
  -Body '{"user_id":1,"item":"manual-probe","quantity":1}'
```

## Reset and Recovery

Always reset after manual work, including after a failed verification:

```powershell
py -m nexus.lab.scenarios reset
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
```

If reset cannot be confirmed, restart the affected service. Activation state is
in-memory only and never survives a process restart. Database data and schema are
not modified by any Phase 2 scenario.
