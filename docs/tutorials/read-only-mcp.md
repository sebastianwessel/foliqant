# 4. Call one read-only MCP tool

Use an `mcp` step when application policy already knows which operation to call.
This stage looks up a synthetic public-request reference without a model call.

Read [MCP connections](../configuration/mcp.md) for transport, catalog, and
authentication setup, and [MCP steps](../steps/mcp.md) for argument and result handling.

## Declare the server and catalog

The runtime never discovers permission from a remote server. Configure a
transport and an operator-reviewed catalog in `config/settings.yaml`:

```yaml
mcp:
  records_office:
    transport:
      type: stdio
      command: $FOLIQANT_EXAMPLE_PYTHON
      args:
        - -m
        - examples.public_request_mcp.server
      cwd: $FOLIQANT_EXAMPLE_ROOT
    catalog:
      tools:
        lookup_request:
          input_schema:
            properties:
              reference:
                title: Reference
                type: string
            required:
              - reference
            title: lookup_requestArguments
            type: object
          output_schema:
            additionalProperties: false
            description: Status of a synthetic public-record request.
            properties:
              reference:
                title: Reference
                type: string
              status:
                const: in_review
                title: Status
                type: string
              due_date:
                title: Due Date
                type: string
              assigned_team:
                title: Assigned Team
                type: string
            required:
              - reference
              - status
              - due_date
              - assigned_team
            title: RequestStatus
            type: object
          effect: read
    concurrency: 1
    queue_limit: 0
    request_timeout: 3
    output_limit_bytes: 4096
```

This is the complete tool catalog used by the bundled server; its input shape is
the reviewed discovery shape and its output is closed. Environment references
resolve only when the application opens.

## Bind the direct call

The step names one server and tool and maps only the reference:

```yaml
type: mcp
server: records_office
tool: lookup_request
arguments:
  reference:
    pointer: /payload/reference
```

Compile-time catalogs and runtime JSON Schema validation constrain arguments and
results. The embedding host owns caller authentication. For Streamable HTTP,
`auth` names a trusted `McpCredentialProvider`; it never contains a credential.

## Test the integration boundary

The bundled stdio server is deterministic, so this example needs no model and
can validate a real MCP session offline:

```sh
python -m examples.public_request_mcp.run
python -m examples.public_request_mcp.evaluate
```

Read
[`examples/public_request_mcp`](https://github.com/sebastianwessel/foliqant/tree/main/examples/public_request_mcp),
then let [a model choose an allowlisted tool](model-tool-loop.md).
