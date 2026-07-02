@testable import {{MODULE_NAME}}
import Testing

@Test func greetsByName() {
    #expect(helloMessage(name: "Ada") == "Hello, Ada! This is {{PROJECT_NAME}}.")
}

@Test(arguments: ["world", "you"])
func greetingContainsTheName(name: String) {
    #expect(helloMessage(name: name).contains(name))
}
