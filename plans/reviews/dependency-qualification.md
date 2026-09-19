# Dependency qualification evidence

2026-09-19, local arm64 macOS. Isolated temporary environment; no global Python packages modified and no model weights downloaded.

- uv 0.7.13 resolved 101 packages from the exact candidate pins; candidate pyproject/lock retained under specs/research/dependencies/.
- `uv sync --extra mlx --extra validation --group dev --no-install-project --locked` succeeded with CPython 3.12.11.
- Imports succeeded: mlx0.32.2, mlx-lm0.31.3, transformers5.17.0, torch2.14.0, pydantic2.13.5, scipy1.18.1, gguf0.19.0; AutoTokenizer, AutoModelForCausalLM, tuner.train and GGUFReader were available.
- Initial sandbox run could not access Metal. An explicitly approved local execution reported Metal available and both MLX and Torch sums of [1,2,3] equaled 6.
- SciPy beta.ppf(.95,1,100) returned 0.029513049607039925, matching the zero-error 100-trial upper-bound fixture.
- Installed llama-cli reports build10180 / commit11b068d06. Full commit 11b068d06605288ce7917534b46d52b47823dc13 was resolved from the official GitHub commit API. Executable SHA-256 c47c683d54c76cefde5d62bbdf352025f66fd7156ea88ffc0f6becc47084238c.

This proves resolution, installation, imports and basic local tensor access only. It does not prove model loading, training, adapter compatibility, export correctness, benchmark quality or production readiness. Those remain mandatory real acceptance tests after implementation.

Subsequent real training, exports and independent runtime acceptance are recorded in [final verification](final-verification.md). The [installed dependency inventory](dependency-inventory.json) retains license metadata without claiming legal clearance or a vulnerability scan.
