# Shoonya Node.js SDK GetQuotes Validator

This folder contains a Node.js validator that mirrors `shoonya_sdk_test/python/test.py`.

It does the following:
- Reads active Shoonya session token from OpenBull DB (`broker_auth`).
- Decrypts token using the same OpenBull Fernet derivation (`ENCRYPTION_PEPPER`).
- Resolves instrument tokens from `symtoken` table.
- Polls Shoonya `get_quotes` via official Node.js SDK and classifies response integrity.
- Writes text log + JSONL forensic logs.

## Run

From this folder:

```bash
npm install
node test.js --user-id 1 --duration-sec 300 --per-call-sleep-sec 0.4 --symbols BSXOPT02JUL2678400CE,BSXOPT02JUL2677900CE
```

Optional args:
- `--log-dir shoonya_sdk_test/nodejs/logs`
- `--duration-sec 30` (smoke test)
