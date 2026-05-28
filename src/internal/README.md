# Internal — Proprietary Code

This directory holds **proprietary logic that should never be shared** in
public code reviews, blog posts, demos, or open-source releases:

- The exact credibility scoring formula
- Fluke detection thresholds and weights
- Convergence detection scoring rules
- Any algorithmic logic that constitutes the product's moat

## Rules

- **Never** include code from `src/internal/` in screenshots shared publicly
- **Never** describe these algorithms in detail in blog posts or marketing
- When showing the codebase to investors, this directory can be shown
  privately under appropriate confidentiality
- When pair-programming with someone external (consultant, advisor),
  avoid this directory unless you have explicit confidentiality in place

Other code can call into this directory, but the calling code should
avoid embedding the same logic so the proprietary parts are isolated here.
