# Customer and domain customization

Adapt a released Foliqant model for a specific organization or domain only when workflow configuration, prompts, catalogs, and retrieval do not meet measured requirements.

Future recipes must identify the parent release, adaptation method, authorized dataset manifest, shared capability regression results, and customer-specific validation/calibration profile. Do not mix customer datasets or publish customer weights through the shared model pipeline by default.

Deploy a supported merged/exported artifact or a serving-runtime-supported adapter after validation. Dynamic tenant-specific adapter loading is not assumed. No customization pipeline exists yet.

See [local training and model lineage](../../docs/local-training-and-model-lineage.md) for Apple Silicon and sequential adaptation guidance.
