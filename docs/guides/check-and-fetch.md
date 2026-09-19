# Check your computer and choose a model

Start with the small model supplied by [local setup](../getting-started/setup.md).
It lets you check the complete toolchain before downloading a larger model.
To use another checkpoint, check its license, architecture and memory needs first.

## Check local capabilities

```sh
uv run --no-sync foliqant-model doctor
```

The result reports your Python and backend versions, free disk space, and whether
MLX can use Metal. It does not download a model or start training.

| Result | What it tells you |
|---|---|
| `mlxAvailable` | The native MLX libraries can be imported |
| `mlxDeviceAvailable` | The current process can access a Metal device |
| `availableCommands` | Commands with at least one available mode |
| `freeDiskBytes` | Free space on the temporary-workspace filesystem |

Checkpoint export works without Metal; GGUF export needs the MLX backend.

A working GPU does not guarantee that a particular model fits in memory. Leave
room for the model, optimizer, activations, temporary conversion files and final
artifacts. Permission restrictions can prevent a process from accessing Metal
even when the computer has supported hardware.

## Fetch an exact model revision

Create a private output parent directory. The artifact directory itself must not
exist. This example downloads the same small checkpoint used by setup:

```sh
mkdir -p "$HOME/.local/share/foliqant/models"
uv run --no-sync foliqant-model fetch \
  --repo HuggingFaceTB/SmolLM2-135M-Instruct \
  --revision 12fd25f77366fa6b3b4b768ec3050bf629380bac \
  --license Apache-2.0 \
  --output "$HOME/.local/share/foliqant/models/smollm2-135m"
```

Use the full commit identifier, not `main` or a movable tag. The tool downloads
supported Safetensors, tokenizer and configuration files through the Hugging
Face client, then copies verified files into a new immutable artifact. Python
model code and pickle weights are not downloaded. Custom remote model code is
not supported.

The `--license` value records your declaration. It does not grant permission or
accept a gated model agreement. For a model that requires authentication, use
your normal local Hugging Face credentials; never put a token into a recipe or
commit it. Review [licenses and permissions](data-licenses.md) before training or
distributing a derived model.

## Export configuration schemas

Use the generated schemas in your editor or configuration tooling:

```sh
uv run --no-sync foliqant-model schema --output /absolute/path/to/new-schema-directory
uv run --no-sync foliqant-model schema --check /absolute/path/to/new-schema-directory
```

Export requires a new or empty directory. The check command only reads files and
fails if a schema is missing, added or changed. Neither command needs MLX or a
network connection.
