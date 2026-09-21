# Work with data

Foliqant accepts authorized local JSONL records or builds a dataset from pinned
public sources. Both paths produce an immutable prepared-dataset artifact with
normalized records, leakage-aware partitions, source permissions, and checksums.

Choose the path that matches your source:

| Starting point | Guide |
| --- | --- |
| You already have authorized records | [Prepare your own dataset](guides/prepare-data.md) |
| You want the maintained public-source pipeline | [Automated curation](guides/automated-curation.md) |
| You are building native evidence-backed decision records | [Native decision data](guides/native-decision-data.md) |
| You are extending or migrating a completed decision run | [Decision-run maintenance](guides/decision-run-maintenance.md) |
| You are consuming native decision results | [Decision contracts](guides/decision-contracts.md) |
| You need to review usage and redistribution terms | [Licenses and permissions](guides/data-licenses.md) |

## What preparation guarantees

Preparation validates every record and its source declaration, normalizes the
content, rejects exact duplicates, and keeps declared related groups in one
partition. It creates `train`, `validation`, `calibration`, and `test` splits
plus a manifest and leakage index.

These checks do not discover every paraphrase, privacy issue, hidden upstream
training overlap, or incorrect permission declaration. Review source terms and
grouping keys before training. Keep holdouts inaccessible to training and use
the calibration and test partitions for different decisions.

## Where data belongs

Store downloaded sources, prepared datasets, customer records, generated
candidates, and evaluation holdouts outside Git. The default setup workspace is
`~/.local/share/foliqant`. If you select a directory inside a Git worktree, setup
requires that the entire workspace is ignored and contains no tracked files.

Dataset manifests retain source revisions, permission declarations,
attribution, privacy scope, and content identity. Those fields flow into later
model ancestry. They are declarations supplied by the operator, not legal
certification by Foliqant.

After preparation, use [model development](model-development.md) to train and
qualify an adapter or [artifact operations](operations/artifacts.md) to verify
and transfer the completed dataset safely.
