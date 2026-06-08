# YScope Claude Integration

This repository contains the YScope CLP Claude Code integration.

## Layout

```text
claude-code-plugin.sh
             Claude Code bootstrap that downloads the compiled installer binary
installer/   Bun/Ink source for the compiled installer binary
yscope/      Claude Code marketplace payload for the clp plugin
```

The installer is a separate distributable from the Claude marketplace payload.
The small Bash bootstrap downloads the platform-specific compiled installer
binary. The compiled installer handles pre-release terms, access-key entry,
artifact download, platform detection, marketplace registration, and
`clp@yscope` installation.

The `yscope/` directory is the marketplace root that Claude Code validates and
installs from.

## Run The Installer

For a hosted production script with `DEFAULT_INSTALLER_MANIFEST_URL` filled in:

```bash
curl -fsSL https://installer.yscope.ai/claude-code-plugin.sh | bash
```

While URLs are not embedded, pass both manifests explicitly:

```bash
curl -fsSL https://installer.yscope.ai/claude-code-plugin.sh \
  | bash -s -- \
      --installer-manifest-url https://example.yscope.io/clp/installer-manifest.json \
      --manifest-url https://example.yscope.io/clp/manifest.json
```

The installer manifest can point to public or short-lived pre-signed compiled
installer binary URLs. The CLP manifest and artifact downloads are consumed by
the compiled installer after it prompts for the user's access key.

From a local checkout, build and launch the compiled installer through the
bootstrap:

```bash
cd installer
bun install
bun run build:binary
cd ..
./claude-code-plugin.sh --installer-path ./installer/dist/yscope-clp-installer
```

The Bun/Ink TUI can also run directly for development:

```bash
cd installer
bun run dev
```

## Validate The Plugin

From the repository root:

```bash
claude plugin validate ./yscope
claude plugin validate ./yscope/plugins/clp
```

## Deploy The Installer

Build and upload the installer release bundle to an S3-compatible R2 bucket:

```bash
scripts/deploy-installer.sh
```

The script prompts for the R2 access key ID and secret access key if
`YSCOPE_R2_ACCESS_KEY_ID` and `YSCOPE_R2_SECRET_ACCESS_KEY` are not set. It uses
`curl --aws-sigv4` for uploads and does not require the AWS CLI.

The default upload endpoint is hard-coded to
`https://ec8ce0890a62177677a29b472bd25580.r2.cloudflarestorage.com/installer`,
and the default installer download base URL is `https://installer.yscope.ai`.
Pass `--download-base-url` only if downloads should use a different domain.

Use `--skip-build` to upload an existing `dist/release` bundle, and `--dry-run`
to print the curl uploads without sending them.
