# HAR2MCP Evaluation Benchmark Suite

This directory contains evaluation datasets, ground-truth specifications, and automated benchmarking scripts to evaluate HAR2MCP across 8-10 real-world or synthetic website API captures.

---

## Directory Structure

```text
eval/
  README.md                  # This documentation
  models.py                  # Evaluation metrics & summary data models
  ground_truth.py            # Parsers for ground_truth.json and openapi.json
  metrics.py                 # Precision, Recall, Field F1, and Auth accuracy
  reporter.py                # Markdown table and JSON file writers
  run_eval.py                # Benchmark execution script
  results.md                 # Generated Markdown results table
  results.json               # Generated full JSON results
  cases/
    case_01_synthetic_cloud/ # Fully working reference dataset
      recording.har          # Captured browser network traffic
      ground_truth.json      # Expected endpoints & schema fields
    case_02_placeholder/     # Placeholder awaiting your HAR & Ground Truth
      recording.har
      ground_truth.json
    ...
    case_10_placeholder/
```

---

## How to Add Your Test Cases

Each subfolder in `eval/cases/` represents a single evaluation scenario. To add your own recording and ground truth:

### 1. Add the HAR Recording
Export a HAR file from your browser (Chrome DevTools -> Network -> Export HAR) and place it in the case directory as `recording.har` (or any `*.har` filename).

### 2. Supply the Ground Truth
You can supply ground truth in **either of two formats**:

#### Option A: Hand-Written `ground_truth.json`
A lightweight, human-readable JSON file:
```json
{
  "name": "my_api_case",
  "expected_auth": "bearer_token",
  "description": "Short description of the API",
  "endpoints": [
    {
      "method": "GET",
      "path": "/api/v1/users/{id}",
      "fields": ["id", "username", "email", "created_at"]
    },
    {
      "method": "POST",
      "path": "/api/v1/items",
      "fields": ["title", "quantity", "price"]
    }
  ]
}
```

*Valid `expected_auth` values*: `bearer_token`, `cookie_session`, `api_key_header`, `api_key_query`, `none`.

#### Option B: Standard OpenAPI / Swagger Specification
Drop an official `openapi.json` (or save your Swagger/OpenAPI 2.0, 3.0, or 3.1 specification as `openapi.json` or `ground_truth.json`). The runner will automatically extract paths, parameters, and schema fields.

---

## Running the Benchmark

### Using `make eval`:
```bash
make eval
```

### Or using Python directly:
```bash
python eval/run_eval.py
```

### Options:
- `--cases-dir <path>`: Custom path to benchmark cases directory (default: `eval/cases/`).
- `--output-dir <path>`: Custom output folder for `results.md` and `results.json` (default: `eval/`).
- `--allow-writes`: Allow state-mutating tools during evaluation.

---

## Evaluated Metrics

1. **Endpoint Precision**: Fraction of generated MCP tool endpoints that exist in ground truth (`TP / (TP + FP)`).
2. **Endpoint Recall**: Fraction of ground truth endpoints correctly discovered and generated (`TP / (TP + FN)`).
3. **Endpoint F1**: Harmonic mean of endpoint precision and recall.
4. **Schema Accuracy (Field-level F1)**: Micro-averaged precision, recall, and F1 comparing inferred schema parameter/body property names against ground-truth fields.
5. **Auth Correctness**: Binary classification (`Yes`/`No`) indicating whether detected authentication matches expected ground-truth mechanism.
6. **Tools Passing (First Try)**: Count of generated tools that pass live replay verification without repair.
7. **Tools Passing (After Repair)**: Count of tools that pass live replay verification following LLM repair loop.
8. **Total LLM Cost ($)** & **Execution Time**: Real-time tracking of token expenses and elapsed duration.
