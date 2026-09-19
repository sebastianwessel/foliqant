# Model serving

Deployment profiles for standard vLLM, Ollama, LM Studio, or a later managed model endpoint belong here. The workflow service calls the endpoint over HTTP and has no training/GPU dependencies.

Pin tested model and runtime versions before adding runnable launch or Compose files. Declare structured-output, streaming, reasoning, scoring, and context capabilities explicitly. A shared API shape does not prove identical behavior. Never promise that advertised maximum context fits the local 24 GB budget.

No serving profile is implemented; repository setup does not download weights or start a server.

See [local training and model lineage](../docs/local-training-and-model-lineage.md) for Apple Silicon and sequential adaptation guidance.
