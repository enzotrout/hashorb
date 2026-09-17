# HashOrb Activity

## 2026-09-17

- Repaired the CPU runtime container security gate by upgrading Debian `libpcre2-8-0` during image construction so the fixed security package replaces the vulnerable base-image package, and added a static distribution contract for the remediation.
