# Terrier desktop release checks

The desktop release uses Geo main and a pinned Melange desktop-tooling commit. A runtime cached from another source revision is not a release input.

Before publication, desktop-windows.yml checks every packaged source file against the reviewed source bytes and runs verify_owner_runtime.py with the bundled Python, source and native WSGI token gate. The latter uses disposable synthetic accounts: the approved existing owner must open a protected route without a password or an invitation, repeated fresh requests must stay password-free, and recipient sign-in and the native request boundary must remain enforced.

To check a staged or extracted installer locally:

```
python desktop/check_package.py --geo <reviewed checkout with frontend built> --sources <runtime>/sources
python desktop/verify_owner_runtime.py --runtime <runtime> --output owner-runtime.json
```

For the next version, update VERSION, RELEASE_TAG, STABLE_TAG and TOOLING_SHA in the workflow. Keep its existing workflow-free reviewed-source commit procedure: REVIEWED_BASE and RELEASE_TARGET identify the source snapshot; only CI files may differ above it. Both packaged checks must remain enabled, and their results are recorded in release provenance. Signing and publication retain their separate signature, hash and sequence checks.
