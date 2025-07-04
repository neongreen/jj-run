# jj-run

A script to execute shell commands across multiple repository changes in isolated workspaces using [jj](https://github.com/jj-vcs/jj).

- Runs arbitrary shell commands for each change in a revset, in isolation.
- Uses a temporary workspace for each run, so your main repo doesn't change while the script is running.

## Usage

```sh
python3 jj-run.py -r <revset> [-e <error_strategy>] <command> 
```

- `-r`, `--revset`: **Required.** The revset of changes to process.
- `-e`, `--err-strategy`: How to handle errors. One of:
  - `continue` (default): Log errors and continue to next change.
  - `stop`: Stop on the first error, but finish already started changes.
  - `fatal`: Abort immediately on any error.
- `<command>`: **Required positional argument.** The shell command to execute for each change (runs in the temp workspace).

### Example

```sh
python3 jj-run.py -r 'mutable()' -e continue 'make test'
```

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

2. The default revset should be whatever `jj fix` has
3. Use stderr for all messages except the command output
4. Add a quiet mode that only prints stdout/stderr of the command
5. Provide CHANGE_ID, COMMIT_ID, REPO_PATH as env vars to the command
7. `--json` output
8. Add a `--readonly` flag that doesn't create new changes, just runs the command for each
