# Security Policy

## Supported Versions

Security fixes are generally applied to the current development version of PWMS.

Because PWMS is actively developed, the latest version of the `main` branch should be considered the primary supported version.

Older versions may not receive security fixes.

## Reporting a Vulnerability

Please do **not** publicly disclose a security vulnerability through a GitHub issue, pull request, discussion, or other public channel.

Security issues should be reported privately to the project maintainer.

When reporting a vulnerability, provide as much of the following information as possible:

- A clear description of the vulnerability.
- The affected component or file.
- Steps required to reproduce the issue.
- The expected behavior.
- The actual behavior.
- The potential security impact.
- Proof-of-concept code or requests, if available.
- The affected version or commit.
- Any suggested remediation, if known.

Do not include real passwords, API keys, financial records, transaction files, or other sensitive information in the report.

## Examples of Security Issues

Please report issues such as:

- Authentication bypasses.
- Authorization bypasses.
- Cross-family data exposure.
- Privilege escalation.
- IDOR / insecure direct object references.
- CSRF vulnerabilities.
- Injection vulnerabilities.
- Sensitive information disclosure.
- Improper handling of credentials or API keys.
- Unsafe file uploads.
- Server-side access-control failures.
- Exposure of private portfolio or transaction data.
- Vulnerabilities in third-party integrations caused by PWMS code.

## Family and Portfolio Data

PWMS contains potentially sensitive financial information.

A security issue that allows one user, family, or role to access data outside their permitted scope should be treated as a security vulnerability.

Do not use real financial data when demonstrating a vulnerability.

Use synthetic or anonymized data instead.

## Secrets

Never commit secrets to the repository.

Examples include:

```text
GEMINI_API_KEY
GOOGLE_API_KEY
SECRET_KEY
POSTGRES_PASSWORD
database credentials
access tokens
session credentials
broker credentials
```

If a secret is accidentally committed:

1. Revoke or rotate the secret immediately.
2. Remove it from the active code/configuration.
3. Review whether it was exposed elsewhere.
4. Report the incident privately if sensitive data may have been exposed.

Simply deleting a secret from the latest commit does not guarantee that it has disappeared from Git history.

## Third-Party Services

PWMS can communicate with external services including market-data, mutual-fund, news, and AI services.

Security reports involving these integrations should identify:

- The external service.
- The PWMS component involved.
- The request or data flow involved.
- Whether sensitive information is transmitted.

## Responsible Disclosure

Please allow reasonable time for the issue to be investigated and addressed before making details public.

The project maintainer may coordinate disclosure depending on the severity and affected components.

## Scope

This policy applies to the PWMS source code, backend API, frontend application, configuration, authentication, authorization, data processing, and integrations maintained by this project.

For general bugs that do not have a security impact, please use the normal GitHub issue process.
