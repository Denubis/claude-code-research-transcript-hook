# Changelog

## transcript-archive 0.4.0

Standalone plugin with full CLI and enriched skill content.

**New:**
- Marketplace and plugin configuration (`.claude-plugin/marketplace.json` + `plugin.json`) for plugin discovery and installation
- UUID support for archiving prior sessions (`/transcript <session-uuid>`)
- SUMMARY.md generation with session statistics after archiving
- Full CLI reference in skill documentation covering all 7 commands: `archive`, `init`, `status`, `bulk`, `update`, `regenerate`, `clean`

**Changed:**
- `/transcript` command now includes `Write` in allowed-tools for SUMMARY.md generation
- Skill description updated to cover bulk archival, status reporting, and metadata updates
- Installation instructions use two-step marketplace add + plugin install
- Minimum Python version raised to 3.12+
