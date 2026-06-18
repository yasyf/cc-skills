// Touch ID consent gate for agent-browser-with-cookies.
//
// exit 0 = approved (Touch ID or password fallback succeeded)
// exit 1 = user cancelled / denied
// exit 2 = unavailable (no Touch ID, no passcode, or no interactive GUI session)
//
// Uses .deviceOwnerAuthentication (Touch ID OR password) so Macs without Touch ID
// still work. Needs no entitlement, so it runs unsigned / ad-hoc signed.

import Foundation
import LocalAuthentication

let reason = ProcessInfo.processInfo.environment["ABWC_TOUCHID_REASON"]
    ?? "access your browser session for agent-browser"

let context = LAContext()
var policyError: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &policyError) else {
    FileHandle.standardError.write(
        Data("touchid-gate: unavailable: \(policyError?.localizedDescription ?? "unknown")\n".utf8))
    exit(2)
}

let semaphore = DispatchSemaphore(value: 0)
var approved = false
var evalError: Error?
context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { ok, error in
    approved = ok
    evalError = error
    semaphore.signal()
}
semaphore.wait()

if approved {
    exit(0)
}

if let laError = evalError as? LAError {
    switch laError.code {
    case .notInteractive, .invalidContext, .biometryNotAvailable, .passcodeNotSet:
        exit(2)
    default:
        exit(1)
    }
}
exit(1)
