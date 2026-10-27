# hermes-nextcloud-upload

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that adds a `nextcloud_upload` tool: it streams a file that already exists on the Hermes host straight to Nextcloud over WebDAV.

Tools that write files through the model need the whole file as text, so binary files (images, videos, archives) get large, slow and easy to corrupt. With this tool the model only sends two paths.

- Uploads only from configured folders; symlinks and `..` cannot escape them.
- Never overwrites: the upload carries `If-None-Match: *`, so Nextcloud rejects it (HTTP 412) when the file exists. `overwrite: true` replaces on purpose.
- Creates missing destination folders.
- Verifies the stored size after the upload.
- Pure Python standard library.

Useful with a remote terminal sandbox: mount a shared "outbox" volume into both the sandbox and Hermes, and list it in `NEXTCLOUD_UPLOAD_ROOTS`.

## Install

```bash
hermes plugins install x-hermes-agent/hermes-nextcloud-upload/nextcloud-upload --enable
```

## Configure

| Env var | Meaning |
|---|---|
| `NEXTCLOUD_HOST` | Nextcloud base URL, e.g. `https://cloud.example.com` |
| `NEXTCLOUD_USERNAME` | Account to upload into |
| `NEXTCLOUD_APP_PASSWORD` | App password for that account (`NEXTCLOUD_PASSWORD` also accepted) |
| `NEXTCLOUD_UPLOAD_ROOTS` | Folders files may be uploaded from, separated by `:` |
| `NEXTCLOUD_UPLOAD_MAX_MB` | Size cap per file (default `512`) |

## Tool

`nextcloud_upload(source, destination, overwrite=false)`

- `source`: absolute path inside an upload folder.
- `destination`: Nextcloud path such as `Projects/poster.png`; end it with `/` to keep the source file name.

Returns the stored path, size and a link to the folder in the Nextcloud web UI.

## License

MIT
