# Privacy and threat model

## Guarantees

| Control | Where | What it does |
|---|---|---|
| Loopback-only model endpoint | `config.Settings.validate` | Refuses any model URL whose host is not loopback, unless you explicitly add it to `ESGLOCAL_ALLOWED_HOSTS` |
| Egress guard | `privacy/network_guard.py` | In offline mode, blocks DNS lookups, TCP connects and UDP `sendto`/`sendmsg` to non-local addresses **from the app process** and logs the attempts |
| No browser-side leaks | `privacy/safe_markdown.py` | Remote images, links, autolinks, reference definitions and media/script tags are stripped from every piece of AI- or document-derived text before rendering or export, so injected content cannot make your browser call out |
| Encrypted vault | `security/vault.py` | Workspaces, reports, chats and document text are encrypted with Fernet. The key comes from your passphrase (scrypt) or a 0600 key file. |
| PII redaction | `privacy/redact.py` | Emails, phone numbers, IBANs, Luhn-valid card numbers, SSNs and IPs are masked in prompts and before document indexing |
| Audit log | `security/audit.py` | Hash-chained metadata log (no data values). `esglocal audit verify` detects edits. |
| No telemetry | `cli.py ui` | Streamlit usage stats are off and the UI binds to 127.0.0.1 with XSRF protection |
| Safe formulas | `formula.py` | Catalog formulas cannot execute code |
| Safe HTML export | `reporting.py` | All text is HTML-escaped. There are no external assets. |
| Right to erasure | Models & privacy page | Purge all records and the document index (`secure_delete` + `VACUUM`) |

## What is NOT covered (be aware)

- **Other processes.** The guard covers this Python process only. The Ollama daemon downloads
  models when *you* ask it to pull one. For hard isolation, use an OS firewall (Little Snitch,
  LuLu, `ufw`, Windows Firewall) or the Docker setup with `internal: true` networks after you
  have pulled models.
- **Unencrypted metadata.** Record *names* in the vault (such as `workspace:default`) and the
  document embedding vectors are not encrypted. Embeddings can leak some information about
  the text.
- **The key file.** Without a passphrase, the key lives next to the vault
  (`~/.esglocal/vault.key`, mode 0600). That protects against casual access and other OS
  users. It does **not** protect against anyone who can copy your whole home directory or a
  backup of it. Set a passphrase (scrypt, n=2^17) for real encryption at rest. Changing the
  passphrase also re-encrypts the document index.
- **Document index metadata.** Document file names and a SHA-256 of each document (used for
  de-duplication) are stored unencrypted in `documents.db`.
- **Audit log.** The hash chain detects edits to existing entries. Someone with file access
  could still truncate the log or rebuild it entirely. Treat it as tamper-evident against
  accidents, not against a determined local attacker.
- **Guard scope.** The guard patches Python's socket module. C extensions that open raw
  sockets themselves, `ctypes`, and subprocesses are outside it.
- **Pattern-based redaction.** It does not catch names or free-form identifiers.
- **Exports.** Exported Markdown, HTML and JSON files are not encrypted. Store them carefully.
- **Prompt injection.** Text in uploaded documents can influence what the model writes. Tools
  are read-only and rendered output is sanitised, so injected text cannot exfiltrate data or
  change files. It can still make the report wrong, so always review it.

## Recommended hardening

```bash
export ESGLOCAL_PASSPHRASE='a long unique passphrase'   # or set it in the UI
export ESGLOCAL_OFFLINE=1                               # default
# After pulling models, block Ollama's outbound traffic in your firewall.
```

## GDPR notes

All processing happens on the device of the data controller. No processor or sub-processor
is involved. Data minimisation (redaction), storage limitation (purge), integrity (the audit
chain) and confidentiality (encryption) are supported by the controls above.
