# HarnessBuddy Plans

This directory holds scoped implementation plans for HarnessBuddy. Future
agents should choose the plan that matches the feature they are implementing
instead of treating any single plan as the whole project roadmap.

## Plans
- [`tasks/`](tasks/): one file per implementation task. Each file contains
  the full requirements and acceptance criteria for that task, its current
  status, and a link to the corresponding GitHub issue when one exists.
- [`python_code_standards.md`](python_code_standards.md): coding, linting,
  formatting, typing, testing, and safety standards for Python implementation
  work.

## Finding the Plan for an Issue

Each open GitHub issue includes a link to its task plan file in the issue
body. You can also look up the task plan directly:

1. Find the task number in the issue title (e.g. "Task 4: CLI Wiring").
2. Open `plans/tasks/task-NN-slug.md` for the matching task number.
3. Read the overview file (`library_builder_oss_fuzz.md`) for the design
   decisions and constraints that apply to all tasks.
