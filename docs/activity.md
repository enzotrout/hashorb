# HashOrb Activity

## 2026-10-19

- Added a concise employer-facing architecture and AI-assisted engineering section to the public website, with source links and a responsive CSS diagram.

- Refined the public Engineering section as an employer-facing showcase with direct evidence links to CI runs, merged PRs, tests, and security workflows.

- Added a separate GitHub Actions production website verification that checks the live release badge and download link against site/index.html after website changes, daily, and on manual dispatch. Push checks retry during deployment propagation; daily checks make a single request.

## 2026-09-17

- Repaired the CPU runtime container security gate by upgrading Debian `libpcre2-8-0` during image construction so the fixed security package replaces the vulnerable base-image package, and added a static distribution contract for the remediation.
