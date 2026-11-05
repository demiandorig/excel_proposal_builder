---
name: Dependency audit resolution
description: Distinguishing installed versions from manifest audit resolution when securing Python dependencies.
---

Check both installed dependency compatibility and the manifest audit; do not assume either proves the other.

**Why:** The dependency audit selected an old Protobuf through a permissive Google parent even when the manifest explicitly required a patched Protobuf and the installed environment passed pip check. Requiring a parent release that supports patched Protobuf resolved the audit as well.

**How to apply:** When a transitive finding persists after adding a direct security floor, inspect the parent requirements and upgrade the relevant parent minimum too. Keep regression checks offline; do not use live AI requests or the shared development database to test dependency compatibility.
