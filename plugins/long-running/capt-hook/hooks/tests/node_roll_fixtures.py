from __future__ import annotations

from pathlib import Path

BODIES = Path(__file__).parent / "fixtures" / "node-rolls"
COUNTED = BODIES / "counted.md"
UNCOUNTED = BODIES / "uncounted.md"

CLASS_CHANGE = """\
diff --git a/infra/rows/k8s/nodeClasses.ts b/infra/rows/k8s/nodeClasses.ts
--- a/infra/rows/k8s/nodeClasses.ts
+++ b/infra/rows/k8s/nodeClasses.ts
@@ -40 +40 @@ export const forgeDefault = {
-  ephemeralStorage: { size: "128Gi" },
+  ephemeralStorage: { size: "160Gi" },
"""

POOL_CHANGE = """\
diff --git a/infra/rows/k8s/nodePools.ts b/infra/rows/k8s/nodePools.ts
--- a/infra/rows/k8s/nodePools.ts
+++ b/infra/rows/k8s/nodePools.ts
@@ -52 +52 @@ function statelessCompute(nodeClassName: NodeClassName): ZonelessPool {
-    instanceFamilies: ["c6a"],
+    instanceFamilies: ["c7a"],
"""

POOL_TEST_ONLY = """\
diff --git a/infra/rows/k8s/nodePools.test.ts b/infra/rows/k8s/nodePools.test.ts
--- a/infra/rows/k8s/nodePools.test.ts
+++ b/infra/rows/k8s/nodePools.test.ts
@@ -70,0 +71 @@ test("every node pool paces its drifted nodes", () => {
+  expect(pools).toHaveLength(11)
"""
