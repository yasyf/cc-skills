# Research notes (2025-2026)

Findings that ground the prompting principles in this skill. Each row pairs a finding with its source and the practical impact on how you write prompts.

| Finding | Source | Impact |
|---------|--------|--------|
| Complex prompts that help weaker models hurt stronger ones | Prompting Inversion, arXiv:2510.22251 | Match prompt complexity to model capability |
| Explicit CoT gives only +2.9% for reasoning models at 20-80% more latency; can be negative | Wharton Report, June 2025; ICML 2025 | Don't add CoT to reasoning models |
| JSON-mode causes up to -63pp on reasoning tasks | "Let Me Speak Freely", EMNLP 2024 | Free-text reasoning first, structured extraction second |
| Performance degrades as context grows, even on simple tasks | "Context Rot", Chroma, July 2025 | Keep prompts focused; don't bloat context |
| Models exploit patterns in >99% of cases but verbalize in <2% of CoT traces | Anthropic 2025 | Don't trust CoT as faithful reasoning explanation |
| XML tags outperform JSON/YAML for prompt structure | arXiv:2509.08182 | Confirms the XML-first approach |
| Up to 76pp accuracy variation from minor formatting changes | ICLR 2024 | Test prompt variations; don't assume stability |
| LLMs struggle with negation at the token-prediction level | arXiv:2503.22395 | Use positive framing |
| Role/persona prompting has no/small negative effect on accuracy | Large-scale study, 4 LLM families, arXiv:2311.10054 | Personas for tone/style, not correctness |
| Contrastive examples (good+bad paired) outperform positive-only | arXiv:2401.17390 | Use labeled pairs, not separate sections |
