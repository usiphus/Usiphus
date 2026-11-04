# Security Policy

## Scope

Usiphus is a local-first medical education content, curriculum, evidence
governance, and release toolkit. It operates on local files and inherits the
security of the host, filesystem, backups, and operator accounts. It is not an
identity provider, clinical decision-support system, or compliance
certification.

## Supported versions

Security fixes are prioritized for the latest code on the default branch.
Older snapshots should be upgraded to the latest reviewed version.

## Reporting a vulnerability

**Do not report security vulnerabilities in public GitHub issues or pull
requests.**

Use the repository's private GitHub vulnerability reporting feature
(Security → Report a vulnerability) when enabled. If private reporting is not
available, contact the maintainers through a private channel listed on the
repository profile.

Include:
- Affected commit/version and component.
- Impact and realistic attack conditions.
- Reproduction steps or a proof of concept using synthetic data.
- Suggested mitigation, if known.

Do not include passwords, signing keys, HMAC keys, access tokens, private
learner records, patient-identifiable information, or other sensitive data.
Allow maintainers reasonable time to investigate and coordinate remediation
before public disclosure.

## Secrets and local files

- Never commit private signing keys, trusted-key material that is intended to
  remain private, HMAC secrets, credentials, learner-level records, or local
  build/review files.
- Keep private keys outside the repository and generated output directories.
- Protect audit ledgers, release ledgers, salts, backups, and their hashes as
  sensitive records. Hashes and UUIDs are not anonymization.
- Rotate credentials or keys that may have been exposed. Removing a secret
  from the latest commit does not remove it from Git history.
- Review logs, reports, examples, and screenshots before sharing them.

## Medical and educational content

Do not upload identifiable patient information. Use synthetic teaching cases.
Generated medical content remains an educational draft until qualified faculty
review it against current, jurisdiction-appropriate sources. Structural or
automated quality checks do not establish clinical accuracy, guideline
currency, exam validity, or patient safety.

## Security maintenance

- Review dependency and GitHub Actions changes.
- Keep CI permissions least-privilege.
- Run tests on the exact commit proposed for release.
- Document security-relevant configuration and key-management changes.
- Follow the repository's safety/privacy guidance in
  [docs/SAFETY_AND_PRIVACY.md](docs/SAFETY_AND_PRIVACY.md).
