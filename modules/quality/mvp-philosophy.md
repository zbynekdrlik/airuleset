### MVP Philosophy

- This is a focused application for a specific use case, not a general-purpose framework.
- Code that adds complexity for unused features should be removed, not kept "just in case".
- No backward compatibility concerns — delete unused code aggressively.
- Prefer simple, direct implementations over abstraction layers that have only one consumer.
- If a feature is not actively used, remove it. Dead code is a maintenance burden and a source of confusion.
- **MVP is a SCOPE decision (fewer features), never a QUALITY or architecture exemption (#414).** Production-classification and framework-first apply to MVP code in full — see `architecture-first.md`'s "production-by-default" rule.
