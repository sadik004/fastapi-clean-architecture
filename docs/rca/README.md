# Root Cause Analysis (RCA) Directory

This directory logs all errors, test failures, and corrections made by the Lead Architect.

## RCA Entry Format

Each file should be named `YYYY-MM-DD_<topic>.md` and follow this structure:

```markdown
# RCA: [Title of the Error / Bug]

- **Date**: YYYY-MM-DD
- **Trigger**: [Test failure / Lead Architect feedback / Runtime error]
- **Faulty Code / Pattern**:
  \`\`\`python
  # Code that failed
  \`\`\`
- **Root Cause**:
  Detailed explanation of why this was flawed or sub-optimal.
- **Resolution**:
  \`\`\`python
  # Corrected implementation
  \`\`\`
- **Permanent Prevention Rule**:
  Rule added to .agents/skills/fastapi-production/SKILL.md to ensure zero recurrence.
```
