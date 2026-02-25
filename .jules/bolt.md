## 2025-05-14 - Redaction Performance Optimization
**Learning:** Repeatedly scanning the same text for multiple patterns (O(N*M)) is a major bottleneck during export. Combining custom strings into a single regex and using list-based join for string construction provides a ~4x speedup even for small string lists.
**Action:** Use combined regexes for multi-pattern redaction and avoid repeated string slicing/concatenation in tight loops.
