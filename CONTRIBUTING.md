# Contribution Guidelines

Anyone is welcome to contribute to this project. Feel free to get in touch with
other community members on [Matrix][matrix], the mailing list or through issues here on
GitHub.

[matrix]: https://chat.mozilla.org

## Getting Started

Merino uses `uv` as its python configuration management system. To initialize a new developer
environment, please refer to
[Developer documentation for working on Merino](https://mozilla-services.github.io/merino-py/dev/index.html)

## Bug Reports

You can file issues here on GitHub. Please try to include as much information as
you can and under what conditions you saw the issue.

## Sending Pull Requests

Patches should be submitted as pull requests (PR).

Before submitting a PR:

- Ensure you are pulling from the most recent `main` branch and install dependencies with uv.
- Ideally, your patch should include new tests that cover your changes. It is your and
  your reviewer's responsibility to ensure your patch includes adequate tests.

When submitting a PR:

- You agree to license your code under the project's open source license ([MPL 2.0][license]).
- Base your branch off the current `main`.
- Add both your code and new tests if relevant.
- [Sign][sign] your git commit.
- Please do not include merge commits in pull requests; include only commits
  with the new relevant code.

[license]: /LICENSE
[sign]: https://docs.github.com/en/github/authenticating-to-github/managing-commit-signature-verification/signing-commits

## Code Review

This project is production Mozilla code and subject to our
[committing rules and responsibilities][committing_rules_and_responsibilities].
Every patch must be peer reviewed.

[committing_rules_and_responsibilities]: https://firefox-source-docs.mozilla.org/contributing/committing_rules_and_responsibilities.html

## Git Commit Guidelines & Branch Naming

We loosely follow the [Angular commit guidelines][angular_commit_guidelines]
of `<type>: <subject>` where `type` must be one of:

* **feat**: A new feature
* **fix**: A bug fix
* **docs**: Documentation only changes
* **style**: Changes that do not affect the meaning of the code (white-space, formatting, missing
  semi-colons, etc)
* **refactor**: A code change that neither fixes a bug or adds a feature
* **perf**: A code change that improves performance
* **test**: Adding missing tests, test maintenance, and adding test features
* **chore**: Changes to the build process or auxiliary tools and libraries such as documentation
  generation

### Subject

The subject contains succinct description of the change:

* use the imperative, present tense: "change" not "changed" nor "changes"
* don't capitalize first letter
* no dot (.) at the end

### Body

In order to maintain a reference to the context of the commit, add
`Closes #<issue_number>` if it closes a related issue or `Issue #<issue_number>`
if it's a partial fix.

You can also write a detailed description of the commit: Just as in the
**subject**, use the imperative, present tense: "change" not "changed" nor
"changes" It should include the motivation for the change and contrast this with
previous behavior.

### Footer

The footer should contain any information about **Breaking Changes** and is also
the place to reference GitHub issues that this commit **Closes**.

### Example

A properly formatted commit message should look like:

```
feat: give the developers a delicious cookie

Properly formatted commit messages provide understandable history and
documentation. This patch will provide a delicious cookie when all tests have
passed and the commit message is properly formatted.

BREAKING CHANGE: This patch requires developer to lower expectations about
    what "delicious" and "cookie" may mean. Some sadness may result.

Closes #314, Closes #975
```

## Testing Guidelines & Best Practices

All test contributions should conform to the documented [Test Strategy][test_strategy] and have the
following qualities (also known as the F.I.R.S.T. principles):

**Fast**

* Test suites should be optimized to execute quickly, which encourages use by contributors and is
  essential for rapid pipelines.
* If tests are taking too long, consider breaking them up into multiple smaller tests and executing
  them with a parallel test runner.
* For reference, unit tests should take milliseconds to run.

**Isolated**

* Tests should be independently executable or standalone, not relying on an execution order with
  other tests.
* Tests should clean up after themselves
    * Tests may perform actions that have persistent effects, like setting a program state. If not
      cleaned up properly, this can impact the execution of subsequent tests causing intermittent
      failures.
    * Helper methods or fixtures are a preferable architectural design when compared to setup and
      teardown methods. As test suites scale, setup and teardown methods become a common source of
      state issues and bloat. Tests will grow to have increasingly specific requirements that don't
      have common relevancy or compatibility.
* Tests should not rely on resources from sites whose content Mozilla doesn't control or where
  Mozilla has no SLA. This is a security concern as well as a source of intermittent failures.
* Tests should avoid file system or database dependencies, their changes or outages can be a source
  of intermittent failures.

**Repeatable**

* Tests should have consistent results when no code changes are made between test runs.
* Tests should not contain time bombs. Meaning they should not be susceptible to failure given
  execution during a given datetime or due to time comparisons.
* Tests should avoid using _magical_ time delays when waiting for operations to finish executing.
* Tests should not assume an execution order for asynchronous methods, this may lead to intermittent
  failures.
* Tests should not depend on objects through weak references, which may be garbage collected during
  test execution causing intermittent failures.
* Tests should avoid using logical operations, such as `if`, `while`, `for`, and `switch`.
    * Decision points are a nexus for introducing bugs. Bugs found in test code erodes trust and
      diminishes value.
    * Consider splitting up a test or using a data driven test framework before using logical
      operations.
* Test CI/CD jobs should avoid pulling the latest language or tooling dependencies. New dependency
  versions can cause unintuitive failures.

**Self-Deterministic**

* No human intervention should be required to conclude if a test has passed or failed.

**Timely**

* Production code should be crafted to be testable, so that writing and maintaining tests doesn't
  take an unreasonable amount of time.
* Test code should be written with the same quality standard as production code and subject to the
  same linters and formatters.
* Consider using a test-first approach when developing production code.
