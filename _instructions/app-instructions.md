This is an example file. Do not use these contents.

# Essentials for understanding the project

## Tools

**generate_codebase_map**: Use this tool to get a structured map of the codebase before you start working on the project.
**context.read_all_context_files**: Use this tool to understand the already existing AI-generated context for the project. It can contain misinformation.

## Documents in the root folder

**"README.md"**: "Project description, terminology and basic data flow for when the application is started",
**"pyproject.toml"**: "Project dependencies and commands."

# Practices

1. Before starting to work on a new feature, ensure you have a clear understanding of the project using the essential tools and documents.
2. When editing existing code, avoid rewriting an entire file unless the change is significant enough to justify it. This is to prevent Git from indicating that an entire file would have been changed when only a small part of it has been changed.
3. Before starting to write code for a large task, check the project context first and store the plan using the context management tools.
4. After completing a task, if larger changes were made to the code, update context records to describe the changes and to include the new understanding of the project, using the context management tools.