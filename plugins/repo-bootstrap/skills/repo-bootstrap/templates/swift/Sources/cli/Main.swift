import ArgumentParser
import {{MODULE_NAME}}

@main
struct Root: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "{{PROJECT_NAME}}",
        abstract: "{{DESCRIPTION}}",
        version: "0.0.0-dev",
        subcommands: [Hello.self]
    )
}

struct Hello: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Print a friendly greeting."
    )

    @Argument(help: "Who to greet.")
    var name: String = "world"

    func run() async throws {
        print(helloMessage(name: name))
    }
}
