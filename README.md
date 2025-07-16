# jj-run

A script to execute shell commands across multiple repository changes in isolated workspaces using [jj](https://github.com/jj-vcs/jj).

- Runs arbitrary shell commands for each change in a revset, in isolation.
- Uses a temporary workspace for each run, so your main repo doesn't change while the script is running.

## Installation

First, install [uv](https://docs.astral.sh/uv/), the best and greatest Python package manager.

Then add to your jj config:

```shell
jj config set --user aliases.x '["util", "exec", "--", "uvx", "git+https://github.com/neongreen/jj-run.git"]'
```

Or in the file:

```toml
[aliases]
x = ["util", "exec", "--", "uvx", "git+https://github.com/neongreen/jj-run.git"]
```

(Can't use `run` because it's already defined as a stub.)

## Usage

Simplest form:

```sh
jj x <command>    # run a command on all mutable&reachable changes
```

Full form:

```sh
jj x -r <revset> [-e <error_strategy>] <command>
```

- `-r`, `--revset`: The revset of changes to process. If not provided, defaults to `reachable(@, mutable())` (same as `jj fix`).
- `-e`, `--err-strategy`: How to handle errors. One of:
  - `continue` (default): Log errors and continue to next change.
  - `stop`: Stop on the first error, but finish already started changes.
  - `fatal`: Abort immediately on any error.
- `<command>`: **Required positional argument.** The shell command to execute for each change (runs in the temp workspace).

## Limitations

- jj-run can't encapsulate its changes into a single operation, so to undo the changes you will have to use `jj op restore`.
- Doesn't support `--ignore-immutable` yet, so it will fail if the revset contains immutable changes.
- Can't change descriptions of existing commits (it's "for-each-run-and-squash", not "for-each-run").

## How it works

- For each run, a unique temporary directory is created and a new `jj` workspace is added there.
- The script finds the set of changes matching the revset (excluding the workspace's own change and root).
- For each change:
  1. `jj new <change>` is run in the temp workspace to create a mutable copy.
  2. The provided command is run in the temp workspace.
  3. Output and errors are printed.
- After all changes are processed:
  - The script attempts to rewrite parent snapshots for the new changes.
  - The temp workspace is forgotten and all created changes are abandoned.

## Error Handling
- If a command fails, the script follows the selected error strategy:
  - `continue`: Logs the error and moves to the next change.
  - `stop`: Stops processing new changes after the first error, but completes any already started ones.
  - `fatal`: Exits immediately on the first error.
- All changes are isolated in the temp workspace. If the script crashes, cleanup is handled per session. The original repository is never modified by failed runs.

## License

MIT

## TODO

4. Add a quiet mode that only prints stdout/stderr of the command
5. Provide CHANGE_ID, COMMIT_ID, REPO_PATH as env vars to the command
7. `--json` output
8. Add a `--readonly` flag that doesn't create new changes, just runs the command for each
10. Handle `--ignore-immutable`
