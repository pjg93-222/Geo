# Terrier district data from GitHub

Terrier 1.0.14 checks this repository when the approved owner opens a district,
changes districts, or chooses **Check GitHub now**. It also checks every five
minutes while the workspace is open. The in-app source panel shows the dataset
revision, check time, verification time, and download progress.

The mutable entry point is `docs/district-testsets/current.json` on `main`.
Terrier first resolves `main` to an immutable commit, verifies the pointer with
the existing packaged Terrier release public key, then verifies the manifest
and selected district archive. A separate protected sequence record refuses
catalogue rollback and conflicting reuse of a sequence.

GitHub supplies the data; queries run against verified local SQLite files.
Matching existing files are reused after checking all six runtime file hashes
and district source metadata. Changed files download into a separate cache.
Updates discovered while a workspace is open are staged and applied on a normal
district transition or reopen. Existing accounts, passwords, secrets, recorded
opt-ins, saved work, and original district files stay in their existing locations.
Previously verified data remains usable when the GitHub connection is unavailable.

This provider applies to the approved existing-owner installation. Recipient
installations retain their approved private district download path. No approval
credential or device token is sent to GitHub.

## Publishing another data revision

1. Build new archives and the complete manifest with per-file sizes and SHA-256
   hashes. Preserve original provenance and the owner's data classification.
   Exclude account databases, credentials, secrets, sessions, logs, and backups.
2. Publish a new immutable `district-testsets-YYYY-MM-DD` data prerelease (use a
   distinct suffix for another revision on that date). Verify every uploaded
   asset by downloading it and checking its full size and hash. Keep the data
   prerelease out of the application's latest stable release slot.
3. With the reviewed Melange desktop tooling and the existing protected publisher,
   run `ops/desktop/packaging/prepare_github_district_catalogue.py`. Supply the
   existing publisher state and public trust file, reviewed manifest, exact data
   release tag/commit, the next catalogue sequence, and a new output path. This
   command verifies the public manifest/tag, signs using the existing Terrier
   release key, validates the native protocol, and records the sequence. It does
   not initialize a publisher, export keys, upload files, or modify app releases.
4. Review and commit the resulting public envelope as
   `docs/district-testsets/current.json` on `main`. The next check discovers it;
   replacing local files by hand or releasing a new application is unnecessary.

Application release sequences and district catalogue sequences are independent.
The initial district catalogue is sequence 1 for the 2026-10-21 release.
