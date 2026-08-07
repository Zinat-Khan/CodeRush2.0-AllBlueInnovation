# AE-03 n8n Workflow Configurations

This directory contains importable n8n workflow JSON files for the three
AE-03 specialized worker agents.

## Workflow Files

| File | Worker | Webhook Endpoint | Description |
|------|--------|------------------|-------------|
| `worker_data_workflow.json` | Worker A / Researcher | `POST /agent-worker-data` | Data ingestion & entity extraction |
| `worker_code_workflow.json` | Worker B / Executor | `POST /agent-worker-code` | Code generation & execution |
| `worker_api_workflow.json` | Worker C / Executor | `POST /agent-worker-api` | External API calls |

## How to Import into n8n Cloud

1. Log into your n8n instance at `https://uzaifah.app.n8n.cloud`
2. Go to **Workflows** > **Add Workflow** > **Import from File**
3. Select the desired `.json` file from this directory
4. Review the workflow nodes and connections
5. **Activate** the workflow using the toggle in the top-right corner
6. The webhook endpoint will now be live at:
   - `https://uzaifah.app.n8n.cloud/webhook/agent-worker-data`
   - `https://uzaifah.app.n8n.cloud/webhook/agent-worker-code`
   - `https://uzaifah.app.n8n.cloud/webhook/agent-worker-api`

## Response Schemas

Each workflow returns a JSON payload that matches the corresponding
Pydantic model in `backend/agents/`:

### DataWorkerResult (`agent-worker-data`)
```json
{
  "status": "success",
  "entities": { "api_endpoints": [...], "schemas": [...] },
  "summary": "Extracted 3 entity categories from input (142 chars).",
  "raw_data": { "input_length": 142, "url_provided": false, "entity_types_requested": [...] }
}
```

### CodeWorkerResult (`agent-worker-code`)
```json
{
  "status": "success",
  "generated_code": "def execute_task(): ...",
  "execution_output": "{'status': 'completed'}",
  "success": true
}
```

### ApiWorkerResult (`agent-worker-api`)
```json
{
  "status_code": 200,
  "response_body": { "message": "Successfully called GET https://...", ... },
  "success": true
}
```

## Customisation

The Code nodes in each workflow contain simulated logic. To connect
real services, replace the Code node with:

- **Worker A**: An HTTP Request node fetching the URL, plus an OpenAI
  node for entity extraction.
- **Worker B**: An Execute Command node or OpenAI node for code generation.
- **Worker C**: An HTTP Request node pointed at `target_api` with the
  provided method, headers, and parameters.
