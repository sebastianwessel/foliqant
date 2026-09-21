# One-command local setup

## Interface and scope

`./scripts/setup-model [--workspace DIR] [--offline]` is the repository entry point.
uv is the prerequisite. The wrapper locates the checkout from its own path, runs
`uv sync --locked --extra mlx --group dev` there, and invokes the installed
`foliqant-model setup` with the supplied arguments. Offline adds uv's --offline
flag. Do not install global Python, accept gated agreements, launch servers,
start training or upload anything. The wheel exposes the same setup command;
wheel users install the MLX extra separately. Help is offline and needs no MLX.

CLI options: `--workspace DIR` defaults to `./.foliqant` in the CLI working
directory (the repository root for wrappers). Shared `model_workspace` selection
and Git policy checks are reused by setup and both curation paths.
`--offline` defaults false, `--timeout-seconds` integer 1..604800 defaults 3600.
No implicit environment-variable expansion in configuration. Resolve the explicit
workspace against the current working directory and create private directories.
Reject symlink destinations/ancestors within the workspace. User-selected
workspace inside a Git checkout must be ignored; never add generated files to Git.

Initial built-in profile `smoke-v1` downloads SmolLM2-135M-Instruct and Banking77.
It is a small diagnostic exercise, not the selected production model or a
financial accuracy benchmark. The broader lifecycle accepts explicit model and
dataset configurations; setup does not silently select a large financial corpus.
The package contains only the JSON acquisition manifest and conversion code.

## Pinned acquisition manifest

Canonical `SetupProfile` fields: schemaVersion literal 1; name literal smoke-v1;
modelRepo RepoId; modelRevision Commit; modelLicense nonempty string;
datasetRepo nonempty HTTPS URL; datasetRevision Commit; datasetLicense nonempty
string; converter literal banking77-smoke-v1; assets sorted unique SetupAsset
list. SetupAsset: path SafePath, url credential-free HTTPS URL without fragment,
size positive integer, sha256 Digest. Asset paths are relative to downloads/.
Model files use model/<filename>; Banking77 uses banking77/<filename>.
The profile digest is canonical JSON of the fully validated profile.

Pin Banking77 source revision 57ec275d8078af65b7731c2a98be812d844a6d6b from
PolyAI-LDN/task-specific-datasets: train.csv, test.csv, categories.json, LICENSE,
README.md. Pin SmolLM2 revision 12fd25f77366fa6b3b4b768ec3050bf629380bac from
HuggingFaceTB/SmolLM2-135M-Instruct: Safetensors, config, generation_config,
tokenizer, tokenizer_config, special_tokens_map, merges, vocab, README.
Exact researched sizes and SHA256s live in the committed package manifest.
No pickle weights, executable source, dataset scripts or remote model code.

Downloads use HTTPS including redirects, credential-free URLs, bounded response
size, per-request timeout at most 60 seconds and a monotonic total deadline.
Stream to exclusive mode-0600 temporary files; verify exact size and SHA256
before no-replace publication. Do not trust Content-Length alone. Reuse only
regular files with matching size/hash. A mismatching existing file fails integrity;
do not repair it silently. Offline mode never opens a connection and fails when
an asset is absent. Failed partial transfer is removed; other verified assets
remain reusable. No automatic retry, Range resume or gated-login automation.

## Local layout and reruns

Profile root: <workspace>/setups/smoke-v1-<profileDigest first 12>/.
downloads/ contains the exact asset paths; records/ holds generated shared.jsonl
and customer.jsonl; config/ contains resolved dataset recipes, a short training
recipe and an evaluation recipe. artifacts/ contains a verified upstream model
artifact and two prepared dataset artifacts. Later training runs use a separate
<workspace>/runs/ directory and never modify setup artifacts.

Only one setup writes this profile root: exclusive sibling lock contains runId,
pid and workspacePath as LockOwner. No automatic stale lock deletion. Record
success last in setup.json via an atomic private write. Downloads and completed
child artifacts survive interruption and are revalidated on rerun. Incomplete
child artifact workspaces obey ordinary artifact recovery rules; no overwrite.
Do not infer completion from a directory's existence. Refuse an existing profile
receipt whose digest differs or whose inventoried outputs changed.

SetupReceipt: schemaVersion literal 1, profileName literal smoke-v1,
profileSha256 Digest, assets sorted FileEntry list relative to downloads,
generatedFiles sorted FileEntry list for records/config, modelArtifactId Digest,
sharedDatasetArtifactId Digest, customerDatasetArtifactId Digest.
Verify asset/recipe/record bytes and all three child artifact inventories before
returning success, both on initial setup and reuse. Receipt changes never repair
corruption. Model creation uses the same snapshot-inspection implementation as
fetch and records producer command fetch; dataset creation uses prepare.

SetupResult: command literal setup; workspacePath LocalPath; profilePath LocalPath;
receiptPath LocalPath; profileSha256 Digest; downloadedFiles and reusedFiles
nonnegative integers; modelPath, sharedDatasetPath, customerDatasetPath,
trainConfigPath, evaluationConfigPath LocalPath. Wrap in normal CliSuccess;
Command gains setup. Use existing errors: invalid options CONFIG/ARGUMENT_INVALID,
missing offline asset INPUT_NOT_FOUND, locked/existing conflicted output
OUTPUT_EXISTS, altered content INTEGRITY_FAILED, network NETWORK_FAILED,
timeout TIMEOUT, unsupported environment ENVIRONMENT_UNSUPPORTED.

## Deterministic diagnostic conversion

Read only Banking77 official train.csv with headers exactly text,category.
Require 10003 train rows, 77 distinct categories matching categories.json,
nonempty text/category and no duplicate text. Verify official test has 3080 rows,
40 per category and no text overlap; retain test as download only. Neither test
data nor test labels enter converted data, prompts, training or smoke selection.

For each category, order official train rows by SHA256 of UTF-8
`foliqant/banking77-smoke-v1 + NUL + category + NUL + text`, breaking an improbable
hash tie by exact text. Ranks 0..1 enter shared; ranks 2..3 enter customer. Each
pool has 154 examples; they are disjoint. Record ID is `banking77-` plus the full
SHA256 of UTF-8 category + NUL + text; groupKeys contains that ID. sourceId is
banking77; language en; tags banking77 and lifecycle-smoke; origin human;
reviewed false. Canonical user message is the original text. System message:
`Classify the banking request. Return only the intent label. Allowed labels: `
plus lexically sorted categories joined by `, `, then `.`. Assistant content is
the exact category. Do not trim/normalize at conversion; prepare applies its
specified normalization. Output full records in ID order, canonical JSON + LF.

Source rights: CC-BY-4.0; trainingAllowed true; sharedTrainingAllowed true;
redistributionAllowed true; privacy public; commercialUse allowed; restrictions
contains `Retain attribution and indicate modifications.`; licenseEvidence is the immutable source LICENSE URL, with its exact bytes
retained under downloads/banking77/LICENSE. Attribution names Casanueva et al., Efficient Intent
Detection with Dual Sentence Encoders, PolyAI-LDN/task-specific-datasets and the
pinned revision. No claim this clears other privacy or downstream use obligations.
Dataset configs differ by name/path only: smoke-shared, smoke-customer, seed42,
default split fractions and bounds. Both remain diagnostic because reviewed false.
Training recipe: name smoke-shared; steps2; batchSize1; gradientAccumulation1;
maxSequenceLength2048; numLayers2; rank4; validationEvery1; validationBatches1;
saveEvery1; all other canonical defaults. Evaluation recipe uses maxTokens32,
maxExamples4, all other defaults. Users create separate configs for substantive
experiments. Setup finishes without running either recipe.

## Acceptance

Test exact download integrity, offline hit/miss, corrupt-cache refusal, HTTPS
redirect enforcement, size bound, failed partial cleanup and no-overwrite races.
Prove deterministic/disjoint conversion and official-test exclusion. Run setup
against real pinned upstream assets, then run again offline without downloads.
Verify receipt and child artifacts and ensure no dataset/model file is tracked.
Cancellation must retain valid cached files without declaring setup complete.
