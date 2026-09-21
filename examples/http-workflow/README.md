# Authenticated HTTP workflow example

This model-free workflow finishes deterministically. It makes no model, MCP, or
external network calls. From the repository root, prepare the locked service
environment and validate the strict deployment YAML:

```sh
uv sync --project service --locked --no-dev --extra http
uv run --project service --no-sync foliqant validate \
  --config examples/http-workflow/foliqant.yaml
uv run --project service --no-sync foliqant explain \
  --config examples/http-workflow/foliqant.yaml --workflow hello
```

Run the same workflow directly through the nondurable CLI boundary:

```sh
uv run --project service --no-sync foliqant run \
  --config examples/http-workflow/foliqant.yaml \
  --workflow hello \
  --input examples/http-workflow/envelope.json
```

`run` receives trusted identity only from its explicit `--tenant-id` and
`--principal-id` operator options; the HTTP bearer profile does not authenticate
the local CLI invocation.

To exercise the synchronous HTTP boundary, choose a local secret and start the
server in one terminal:

```sh
export FOLIQANT_HTTP_EXAMPLE_TOKEN='replace-with-a-local-secret'
uv run --project service --no-sync foliqant serve \
  --config examples/http-workflow/foliqant.yaml
```

Then submit the envelope from another terminal:

```sh
export FOLIQANT_HTTP_EXAMPLE_TOKEN='replace-with-the-same-local-secret'
curl --fail-with-body \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${FOLIQANT_HTTP_EXAMPLE_TOKEN}" \
  --data-binary @examples/http-workflow/envelope.json \
  http://127.0.0.1:8765/workflows/hello/runs
```

The configured token grants access only to `hello` and establishes the example
principal. The response contains a terminal execution result with that protected
identity added to metadata. The token value stays in the process environment and
must not be committed. This server executes requests synchronously in memory: it
does not create a detached job, persist progress, or provide retrieval/cancel
endpoints after a process restart. Keep this plain-HTTP example on loopback;
nonlocal bearer or JWT deployment requires trusted TLS termination.
