# Security

- No credentials, API keys, or client material are committed to this repository. CI runs secret scanning on every push.
- Prompts and model outputs are never logged by default; all public benchmark data is synthetic.
- The private vLLM service is ClusterIP-only and never exposed publicly.
- See TECHNICAL_SPEC.md §15 for the full threat model.

To report a vulnerability, open a private security advisory on this repository.
