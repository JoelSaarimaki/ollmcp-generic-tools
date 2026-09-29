# Essentials for understanding the project

## Tools

**codebase-mapper.generate_codebase_map**: Use this tool to get a structured map of the codebase before you start working on the project.
**context.read_all_context_files**: Use this tool to understand the already existing plans and records of the project.
**filesystem.get_config**: Use this tool when a tool does not find a file or denies a path, to see which files the tools can access.

## Documents in the root folder

**README.md**: Basic description of what the project is and how to use it.

# Practices

1. Before starting to work on a new feature, ensure you have a clear understanding of the project using the essential tools and documents.¨
2. If you are asked to complete a complex or difficult task, check the context files first to see if there are any already existing plans for the task you have been given. Take into account that those plans might be outdated or already partially implemented. After evaluating the existing plans, first either iterate the existing plans or create a new plan and store it into the context files. Then proceed with the task.
3. While working on a task, ensure that you are following established coding practices. If you find differences practices, make judgements on what practices are best, and if the differences in practices are intentional or not.
4. Tool responses are limited in size. When a response contains "Output limited:", only part of the result is shown. Do not repeat the same call: follow the instructions in that message instead, for example by reading the next part of a file with `start_line`, limiting a map or search to one folder with `path`, or running a narrower command. Never assume that the part you were shown is the whole result.