# Contributing

First off, thank you for considering contributing to our project! Your support and involvement are greatly appreciated.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
    - [Reporting Bugs](#reporting-bugs)
    - [Suggesting Enhancements](#suggesting-enhancements)
    - [Submitting Pull Requests](#submitting-pull-requests)
- [Style Guides](#style-guides)
    - [Coding Standards](#coding-standards)
    - [Pre-commit Hooks](#pre-commit-hooks)
    - [Commit Messages](#commit-messages)
- [Additional Resources](#additional-resources)

## Code of Conduct

Please note that this project is released with a [Code of Conduct](CODE_OF_CONDUCT.md). By participating in this project, you agree to abide by its terms.

## How Can I Contribute?

### Reporting Bugs

If you discover a bug in the project, please check the [existing issues](https://github.com/jvicg/arch-install/issues) to see if it has already been reported. If not, you can create a new issue with the following details:

- **Title**: A concise summary of the bug.
- **Description**: Detailed information about the issue, including steps to reproduce, expected behavior, and any relevant logs or screenshots.

### Suggesting Enhancements

We welcome suggestions to improve the project. To propose an enhancement:

1. **Check Existing Issues**: Ensure the enhancement hasn't already been suggested.
2. **Open a New Issue**: Provide a clear and descriptive title and explain the enhancement in detail.

### Submitting Pull Requests

If you're ready to contribute code:

1. **Fork the Repository**: Click the "Fork" button at the top right of the repository page.
2. **Clone Your Fork**: Use `git clone` to clone your fork to your local machine.
3. **Create a Branch**: Use `git checkout -b feature/YourFeatureName` to create a new branch.
4. **Make Your Changes**: Implement your feature or fix.
5. **Commit Your Changes**: Use descriptive commit messages (see [Commit Messages](#commit-messages)).
6. **Push to Your Fork**: Use `git push origin feature/YourFeatureName`.
7. **Open a Pull Request**: Navigate to the original repository and click "New Pull Request." Provide a clear description of your changes.

## Style Guides

### Code style

This project follows [PEP 8](https://peps.python.org/pep-0008/>) as the base coding standard,
enforced automatically by [Ruff](https://docs.astral.sh/ruff/). Ruff runs as a pre-commit hook
so most style issues are caught before committing.

Type hints are required for all function signatures.

When adding comments, focus on explaining *why* the code does something rather than *what* it
does. The code itself should be clear enough to convey the what; comments are for capturing
intent, context, or non-obvious decisions that the code alone cannot express.

### Pre-commit Hooks

This project uses [pre-commit](https://pre-commit.com/) to enforce code quality automatically before each commit and push. Setting it up is required before submitting any pull request.

1. **Install pre-commit**:

    ```bash
    pip install pre-commit
    ```

2. **Install the hooks**:
    ```bash
    pre-commit install --install-hooks
    ```

Once installed, the following checks will run automatically:

| Stage      | Hook                      | Description                                   |
| ---------- | ------------------------- | --------------------------------------------- |
| commit-msg | `conventional-pre-commit` | Enforces Conventional Commits format          |
| pre-commit | `trailing-whitespace`     | Removes trailing whitespace from Python files |
| pre-commit | `end-of-file-fixer`       | Ensures all Python files end with a newline   |
| pre-commit | `check-yaml`              | Validates YAML file syntax                    |
| pre-commit | `check-added-large-files` | Prevents large files from being committed     |
| pre-commit | `ruff`                    | Runs the linter and formatter                 |
| pre-commit | `pytest (unit)`           | Runs the unit test suite                      |
| pre-push   | `pytest (full)`           | Runs the full test suite                      |

### Commit Messages

- **Format**: Use the present tense ("Add feature" not "Added feature").
- **Description**: Provide a brief description of the changes made.
- **Style guide**: Follow this style guide for commit messages:

| Commit Type | Title                    | Description                                                                                                 |
| ----------- | ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `feat`      | Features                 | A new feature                                                                                               |
| `fix`       | Bug Fixes                | A bug fix                                                                                                   |
| `docs`      | Documentation            | Documentation only changes                                                                                  |
| `style`     | Styles                   | Changes that do not affect the meaning of the code (white-space, formatting, missing semi-colons, etc)      |
| `refactor`  | Code Refactoring         | A code change that neither fixes a bug nor adds a feature                                                   |
| `perf`      | Performance Improvements | A code change that improves performance                                                                     |
| `test`      | Tests                    | Adding missing tests or correcting existing tests                                                           |
| `build`     | Builds                   | Changes that affect the build system or external dependencies (example scopes: gulp, broccoli, npm)         |
| `ci`        | Continuous Integrations  | Changes to our CI configuration files and scripts (example scopes: Travis, Circle, BrowserStack, SauceLabs) |
| `chore`     | Chores                   | Other changes that don't modify src or test files                                                           |
| `revert`    | Reverts                  | Reverts a previous commit                                                                                   |

## Additional Resources

- [GitHub Documentation](https://docs.github.com/)
