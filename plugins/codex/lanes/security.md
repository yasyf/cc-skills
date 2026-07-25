## Lane: security

Security review. The § Replies review shape applies, with two additions.
First, every finding carries an attacker story in one sentence — who can
trigger it and what they gain; a weakness with no reachable trigger is
`minor`, with the blocking condition named. Second, payloads stay out of the
reply: a PoC, exploit string, or crafted input goes into a file in the
working tree, cited by path, while the reply describes the vulnerability
clinically — the calling session must not carry exploit content inline.

Sweep the scope against the standing list: authn/authz, input validation and
injection, path handling, secrets in code or logs, crypto misuse, unsafe
deserialization, request forgery. `LGTM` names which of these you checked.
