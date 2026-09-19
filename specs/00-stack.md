# Implementation stack

The model lifecycle is Python 3.12 with a typed local CLI. [Backend and dependency contract](06-backend-and-dependencies.md) owns exact packages, APIs and qualification evidence. No database, cloud service, frontend or application server is required. The future Go workflow service is outside this implementation.

Use uv.lock for reproducible installation, Pydantic for closed runtime contracts and JSON Schema generation, argparse for CLI parsing, MLX LM for actual Apple Silicon LoRA/QLoRA, SciPy for the explicitly defined risk bound, and standard Transformers/llama.cpp for independent export qualification. No optional backend may silently substitute another engine.
