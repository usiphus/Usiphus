# Safety, privacy, and security checklist

Usiphus is a local-first workflow and data-validation toolkit. It is not a
clinical decision-support system, a learning-outcome validator, an identity
provider, or a compliance certification. Automated results are evidence about
the checks the software performed, not proof of facts those checks cannot
observe.

## Clinical and educational safety

Before any release containing medical teaching content:

- Have qualified faculty review each clinical claim against the current,
  jurisdiction-appropriate primary source.
- Record who reviewed the claim, what source/version they used, and when they
  reviewed it; revisit claims when sources change or become superseded.
- Independently review mappings, answer keys, contradictions, and machine-
  generated similarity/quality flags. Heuristics are triage aids, not clinical
  adjudication.
- Confirm that learning objectives, assessment items, and teaching material
  have the intended educational meaning. Software checks do not prove
  effectiveness, learner competence, accreditation, or fitness for a
  particular course.
- Do not use a "ready", "passed", or "checks_passed" status as clinical
  approval or as a substitute for institutional review.

## Privacy and data handling

- Treat learner progress, schedules, audit records, and release ledgers as
  sensitive records even when identifiers are pseudonymous or UUID-shaped.
- Minimize collected fields, restrict access to the data and its backups, use
  protected storage and transfer, and define retention/deletion procedures
  with the responsible institution.
- A release ledger stores a salt alongside salted hashes and is sensitive
  with the learner data it protects. Protect, back up, and delete them
  together.
- Cohort suppression and release-ledger checks reduce selected disclosure
  risks; they do not prevent all inference or outside-knowledge attacks and
  are not differential privacy.
- Review every output and log before sharing it. A privacy check covers only
  the fields and outputs implemented by that version; it is not a general
  anonymization guarantee.

## Security assumptions and key custody

The tool operates on local files and inherits the security of the host,
filesystem, backups, and operator accounts. It does not defend a compromised
host, authenticate a person merely because an event contains their actor ID,
or securely distribute trusted keys.

- Keep signing private keys outside source control and shared output folders;
  limit access and use encrypted storage or an institution-approved key
  manager. Apply filesystem ACLs on Windows and restrictive permissions on
  POSIX systems.
- Distribute trusted public keys through a separately controlled process.
  Verify actor-to-key assignments out of band and promptly replace
  compromised keys while retaining old public keys as needed to verify
  historical events.
- Store signed external anchors somewhere claim editors cannot silently
  rewrite, such as separately controlled immutable storage. A signature
  without a trusted anchor does not by itself detect deletion of the newest
  valid events or prove when they existed.
- Protect release directories, configuration, source files, audit files, and
  ledgers from unauthorized write access. Hashes and manifests detect changes
  only when compared with a trusted copy.
- Back up keys and data according to institutional policy and rehearse
  recovery. Usiphus does not rotate keys, guarantee backup availability, or
  provide a remote audit service.

## Validation and release checklist

Before a production-like deployment:

1. Run the full test suite and engine self-tests on the actual deployment
   platform; inspect and understand every skip.
2. Use synthetic data for setup and testing. Passing tests do not validate
   real clinical material or learner records.
3. Review the V14 release report, faculty queue, V13 findings, and manifest;
   investigate blocked or unexpected results.
4. Confirm the selected publishing mode and destination. Test restore from a
   backup and ensure release outputs cannot overwrite source artifacts.
5. Confirm access control, secure transfer, retention, incident response,
   key custody, and responsibilities with the organization that owns the
   records.
6. Preserve test logs and trusted release manifests according to policy,
   while avoiding unnecessary copies of sensitive inputs.

## Test and platform limits

The CI workflow is configured for Python 3.11–3.13 on Linux, macOS, and
Windows. Matrix configuration does not mean all jobs have passed; inspect the
CI run for the commit being released. Tests that require POSIX permission
semantics are expected to skip on Windows. The workflow currently uses only
standard-library test dependencies.
