# Extension templates

The core engine intentionally contains no organization, product, repository,
workflow, or provider values. Create a separate extension repository
by copying and filling these templates:

1. `sdd/policy-pack/catalog.yaml` identifies the discovery universe, per-repository
   authoring permission, and the extension-owned workflow.
2. `sdd/policy-pack/sdd.yaml` declares the lifecycle and gates.
3. `sdd/repository/repo.yaml` is copied into each participating repository as
   `.signal-harness/repo.yaml`; it remains owned by that repository.
4. `sdd/workspace.yaml` is an optional explicit multi-repository feature plan.

The paths outside `sdd/` are compatibility copies for existing consumers.
Maintain templates under `sdd/` and run `python scripts/sync_sdd_assets.py --write`
to refresh those copies. Asset validation rejects differences between them.

Do not register individual endpoints, events, or schemas in the catalog. The
running SDD discovers those boundaries from current source for each feature or
bug, then records the run-specific impact in specification and task artifacts.

Invoke the core with the external pack:

```shell
signal-harness sdd FEATURE --policy /path/to/extension/policy-pack
```

For a released consumer, package the policy and register a distribution entry
point so users can invoke `signal-harness sdd FEATURE --distribution NAME`.
Consumer examples and evaluation evidence stay in the distribution repository.
