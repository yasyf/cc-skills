import { expect, test } from "bun:test"

import { greeting } from "../src/index.ts"

test("greeting names the greeted", () => {
  expect(greeting("Ada")).toBe("Hello, Ada! This is {{PROJECT_NAME}}.")
})

test.each(["world", "you"])("greeting contains the name %p", (name) => {
  expect(greeting(name)).toContain(name)
})
