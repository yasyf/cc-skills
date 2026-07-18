export function greeting(name: string): string {
  return `Hello, ${name}! This is {{PROJECT_NAME}}.`
}

if (import.meta.main) {
  const name = Bun.argv[2] ?? "world"
  console.log(greeting(name))
}
