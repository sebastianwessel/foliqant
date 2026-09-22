# Supply settings and secrets

Keep process rules in YAML and deployment values in the environment. The same
workflow can then use a local model on your laptop and a hosted model in production
without putting credentials in the workflow or prompts.

## Start with two variables

In `config/settings.yaml`, a whole value beginning with `$` refers to an environment
variable:

```yaml
models:
  local:
    provider: openai_compatible
    model: $MODEL_ID
    base_url: $MODEL_BASE_URL
    allow_insecure_http: true
    output_mode: native
    supports_tools: false
```

Set the values in the shell that starts your application:

```sh
export MODEL_ID=your-served-model-id
export MODEL_BASE_URL=http://127.0.0.1:1234/v1
```

`allow_insecure_http` defaults to `false`. Enable it only for an intended HTTP
endpoint, such as a local development server; remote deployments should use HTTPS.

## Use an optional local file

Instead of exporting values, create `config/.env` beside `settings.yaml`:

```dotenv
MODEL_ID=your-served-model-id
MODEL_BASE_URL=http://127.0.0.1:1234/v1
```

Add `config/.env` to your project's `.gitignore`. A `.env.example` can help teammates
discover variable names, but it is only a template: the runtime never reads it.
Neither file is needed when your deployment supplies the environment.

The location follows the selected settings file. If the file is
`deployment/settings.yaml`, the runtime looks for `deployment/.env`. It does not
search the project root or parent directories for another `.env`.

## Understand precedence and timing

1. `prepare_application` compiles without reading `.env` or resolving secrets.
2. `open_application` reads the adjacent `.env` once.
3. The host's explicit environment mapping overrides those file values.
4. Marked fields are resolved and validated before integrations use them.

The CLI passes the process environment. An embedded application makes that choice
explicitly:

```python
import os
from pathlib import Path

from foliqant import open_application, prepare_application

prepared = prepare_application(Path("config/settings.yaml"))
# Inside your async application lifespan:
async with open_application(prepared, environment=os.environ) as application:
    ...  # Keep the application open while accepting requests.
```

Passing `environment={}` uses only the adjacent file. Values are captured when the
application opens; changing a variable later does not reconfigure an existing
application. Close and reopen it deliberately when rotating credentials.

## Know where references work

| Field family | Examples | Environment references? |
| --- | --- | --- |
| Model connection | Model ID, base URL, API key, Azure endpoint/version, Bedrock region | Yes |
| MCP process/connection | Endpoint, executable, arguments, working directory, environment overlay | Yes |
| Telemetry | OTLP endpoints and header values | Yes |
| Process definitions | Prompts, criteria, bindings, schemas, flow names, routes | No |
| Numeric/boolean settings | Concurrency, timeouts, token limits, capabilities | No; use typed YAML values |
| Evaluation | Dataset path | No; use a literal path |

Use `base_url: $MODEL_BASE_URL`, not `base_url: https://$HOST/v1`. References are
whole values, not string templates. In supported fields, `$$` produces a literal
`$`. The local dotenv loader also does not expand nested references. A missing or
blank referenced value fails application opening with `invalid_configuration`;
there is no implicit empty-string fallback.

For provider-specific credential names, see [choose a provider](providers.md).
For MCP OAuth, use the host's [credential provider](mcp.md#configure-authenticated-http-access),
not a bearer token in the tool catalog.

## Keep secrets out of requests

Never place API keys in an input envelope, prompt, tool argument, evaluation case,
or error message. Use environment references for API keys even where named model
profiles accept literals. Inline step profiles require references.

Your host owns secret delivery, access control, rotation, and log policy. A secret
manager can inject the values into the process or provide the mapping passed to
`open_application`; the library does not install a secret-management service.

Continue with [model profiles](models.md) or [deploying your application](../integration/deployment.md).
