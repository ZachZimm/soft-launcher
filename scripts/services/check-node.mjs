// Fail once with a useful journal entry after a system Node major upgrade.
if (Number(process.versions.node.split('.')[0]) !== 26) {
  console.error(`soft-if requires Node 26; found ${process.version}. Reinstall with SOFT_LAUNCHER_NODE=/absolute/path/to/node scripts/service.sh install`);
  process.exit(78);
}
await import('node:sqlite');
