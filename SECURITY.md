# Security

The local document application is intended for loopback access. Without credentials it has no user authentication; with `LARM_API_TOKEN` set it requires that shared credential as a Bearer, or a browser session obtained from a one-time ticket. Every authenticated caller has access to all documents: there is no per-user authorization. Without authentication it refuses to start when non-loopback hosts are allowed.

Input limits: PDFs are limited to 2000 pages by default (`DOCLING_MAX_PAGES`). Office files (ZIP) are limited to 250 MiB expanded in total and 100 MiB per entry, with a compression ratio of at most 200 for entries of 10 MiB or more. Spreadsheet XML is parsed with defusedxml. These checks trust the sizes declared in the ZIP directory. The independently deployed knowledge API has its own credentials and access scopes. Documents, extracted HTML, model output and Wiki content are untrusted input.

For a suspected vulnerability, use the repository's private vulnerability reporting feature if available. Otherwise contact the repository maintainer through GitHub without including an exploit, credentials or document content in a public issue. No response SLA is currently promised.

Please include the affected version/commit, execution environment, reproduction using synthetic data, and impact. Avoid uploading original private documents or API keys. Do not test against another user's deployment.
