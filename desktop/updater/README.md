# VEGA Automatic Updates (Tauri Updater + CF R2)

The desktop app (daemon DMG) queries for the latest version in the background at startup, and if a new version exists,
**silently downloads, installs, and restarts**. (`desktop/src/lib.rs` → `spawn_update_check`)

## Components

| Location | Role |
|------|------|
| `tauri.conf.json` → `plugins.updater.endpoints` | URL for querying the update manifest (JSON). **Currently a PLACEHOLDER** |
| `tauri.conf.json` → `plugins.updater.pubkey` | Public key for signature verification (the real key is already injected) |
| `tauri.conf.json` → `bundle.createUpdaterArtifacts` | Generate `.app.tar.gz` at build time |
| `~/.tauri/vega-updater.key` | **Signing private key** (never commit, keep outside the repo) |
| `capabilities/desktop.json` → `updater:default` | updater permission |
| `updater/latest.json.template` | Manifest template to upload to R2 |

## endpoint pattern

```
https://<R2_PUBLIC_DOMAIN>/vega/updates/{{target}}/{{arch}}/{{current_version}}
```
Tauri substitutes `{{target}}` (`darwin`), `{{arch}}` (`aarch64`), and `{{current_version}}` and issues a GET.
R2 just needs to return the manifest JSON (below) at this path (static file hosting is sufficient).

## Deployment procedure (when releasing a new version)

1. **Build** — if you have the signing key, the updater artifacts are generated automatically:
   ```bash
   bash scripts/build_dmg.sh
   # → build_output/updater/VEGA-<ver>-aarch64.app.tar.gz (+ .sig)
   ```
2. **R2 upload** — upload the `.app.tar.gz` to the public download path.
3. **Write the manifest** — copy `latest.json.template` and fill it in:
   - `version`: new version (SemVer)
   - `platforms.darwin-aarch64.url`: the public URL of the `.app.tar.gz` from step 2 above
   - `platforms.darwin-aarch64.signature`: **the contents of the `.sig` file** (not the path — the build log prints it for you)
4. **Manifest upload** — upload the JSON to the endpoint path.

## ⚠️ Must do before deployment

- [ ] Replace the `endpoints` PLACEHOLDER in `tauri.conf.json` with the **real R2 domain**
- [ ] Set up the R2 bucket + public domain (or Cloudflare CDN)
- [ ] Safely back up the private key (`~/.tauri/vega-updater.key`) — if lost, existing installs cannot be updated

## Key regeneration (if lost — existing users cannot receive updates)

```bash
cargo tauri signer generate -w ~/.tauri/vega-updater.key --password ""
# Replace plugins.updater.pubkey in tauri.conf.json with the new pubkey
```
