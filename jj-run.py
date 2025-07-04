#!/usr/bin/env python3

"""
JJ Run: Command Script Automation Tool

NAME
    jj-run.py - Executes commands across repository changes in isolated workspaces.

SYNOPSIS
    python jj-run.py -r <revset> -c <command> -e <error_strategy>

DESCRIPTION
    Run arbitrary shell commands across specified changes while preserving repository state:
    - Creates an isolated revisory workspace in a temporary directory for each run session.
    - All `jj` commands are executed with the `--repository` flag pointing to the main repository.
    - Processes changes sequentially:
      1. Executes `jj new` to prepare a mutable copy within the temporary workspace.
      2. Runs the provided command within the temporary workspace.
    - Removes the workspace after the run.

USAGE
    -r: Changes to process.

    -c, --command: Required. The command(s) to execute for each change.

    -e, --err_strategy: Specifies how the script handles command/execution failures.
                        continue: Log errors and move to next change.
                        stop: Stop on the first error but complete already started.
                        fatal: Abort immediately on failure.
                        (default: "continue")

ERROR HANDLING
    - The script will attempt to manage and log errors according to --err_strategy:
      * continue: Logs and continues.
      * stop: Stops on error but completes remaining items in queue.
      * fatal: Quit immediately on detecting a non-zero error code.

    All changes are scoped into their temporary workspace. If the script crashes, workspace cleanup
    is handled per session. Errors during operations will not modify the original repository.

ISOLATION WORKFLOW
    - Workspace Isolation:
      A unique temporary directory is created for each run session, and a `jj` workspace is added
      within this temporary directory. This ensures isolation of operations from the user's ongoing
      state and protects the original branches/commits.

    Progress Feedback:
      During execution, the script lists out each change, its operations, and their
      status, providing users with transparency about processed changes and encountered
      errors.

EXIT STATUS
    Exit codes align:
      0: Success.
      Nonzero: Indicated by an error, based on last command or handled error level.
"""

from dataclasses import dataclass
import os
import subprocess
import argparse
from typing import List
import tempfile
import sys


@dataclass
class Change:
    commit_id: str
    change_id: str
    description: str
    parents: list[str]


def run(*args, **kwargs):
    """
    Wrapper for subprocess.run to handle errors by printing stderr and exiting.

    :param args: Positional arguments for subprocess.run
    :param kwargs: Keyword arguments for subprocess.run
    """
    try:
        result = subprocess.run(*args, **kwargs)
        return result
    except subprocess.CalledProcessError as e:
        print(f"{e.cmd=}, {e.stderr=}, {e.stdout=}", file=sys.stderr)
        raise


def run_jj_command(command: str, revset: str, err_strategy: str = "continue"):
    """
    Main entry point to run `jj run` with command handling and error strategies.

    :param command: User-provided command (e.g., "jj new && jj restore ...")
    :param err_strategy: Error handling strategy ("continue", "stop", "fatal")
    """
    current_operation = run(
        ["jj", "op", "log", "-n1", "-Tid", "--no-graph", "--no-pager"],
        shell=False,
        text=True,
        capture_output=True,
    ).stdout.strip()
    print(f"Current operation: {current_operation}")
    [workspace_path, workspace_name] = create_workspace()
    # get the new empty change created by `jj workspace add`
    [workspace_change] = get_change_list(f"{workspace_name}@")
    changes = get_change_list(f"({revset}) ~ {workspace_change.change_id} ~ root()")
    if not changes:
        print("No changes found to process.")
        forget_workspace(workspace_name)
        abandon_changes([workspace_change.change_id])
        return
    new_changes = process_changes(workspace_path, changes, command, err_strategy)
    rewrite_parents(workspace_path, new_changes)
    run(
        ["jj", "workspace", "update-stale"],
        shell=False,
        text=True,
        check=True,
        capture_output=True,
    )
    run(
        ["jj", "workspace", "update-stale"],
        shell=False,
        text=True,
        check=True,
        capture_output=True,
        cwd=workspace_path,
    )
    forget_workspace(workspace_name)
    abandon_changes([c.change_id for c in new_changes + [workspace_change]])


def rewrite_parents(workspace_path: str, changes: list[Change]):
    """
    For each change, rewrite its parent's snapshot to that commit.
    """
    for change in changes:
        # If the change doesn't exist or is empty, we have to skip it b/c otherwise jj might fail when rewriting.
        is_empty_result = run(
            [
                "jj",
                "log",
                "-T",
                "json(empty)",
                "-r",
                f"present({change.change_id})",
                "--no-graph",
            ],
            shell=False,
            text=True,
            check=True,
            capture_output=True,
            cwd=workspace_path,
        )
        if is_empty_result.stdout.strip() == "false":
            run(
                ["jj", "edit", change.parents[0]],
                shell=False,
                text=True,
                check=True,
                capture_output=True,
                cwd=workspace_path,
            )
            run(
                ["jj", "restore", "--from", change.change_id, "--restore-descendants"],
                shell=False,
                text=True,
                check=True,
                capture_output=True,
                cwd=workspace_path,
            )


def abandon_changes(changes: list[str]):
    """
    Abandon all changes in the provided list.

    :param changes: List of change IDs to abandon
    """

    # TODO: can batch but must make sure to not run into arg length limits
    for change in changes:
        run(
            ["jj", "abandon", f"present({change})", "--ignore-working-copy"],
            shell=False,
            text=True,
            check=True,
            capture_output=True,
        )


def forget_workspace(workspace_name: str):
    """
    Forget the workspace after processing commits.

    :param workspace_name: Name of the workspace to forget
    """
    run(
        ["jj", "workspace", "forget", workspace_name],
        shell=False,
        text=True,
        check=True,
        capture_output=True,
    )


def get_change_list(revset: str, workspace_path: str = ".") -> List[Change]:
    """
    Parse and return the list of changes in JSON format.

    :returns: Retrieved change history list as a list of dictionaries
    """
    change_process = run(
        ["jj", "log", "-r", revset, "--template", "json(self)", "--no-graph"],
        shell=False,
        text=True,
        capture_output=True,
        cwd=workspace_path,
    )
    if not change_process.stdout:
        return []

    import json

    changes = []
    combined_output = change_process.stdout.strip()
    while combined_output:
        try:
            change_entry, index = json.JSONDecoder().raw_decode(combined_output)
            changes.append(
                Change(
                    change_id=change_entry["change_id"],
                    commit_id=change_entry["commit_id"],
                    description=change_entry.get("description", ""),
                    parents=change_entry.get("parents", []),
                )
            )
            combined_output = combined_output[index:].lstrip()
        except json.JSONDecodeError:
            combined_output = combined_output.lstrip()
            if not combined_output:
                break
            combined_output = combined_output[combined_output.find("\n") + 1 :]
    return changes


def create_workspace() -> tuple[str, str]:
    """
    Add and return a new isolated workspace for the task.

    :returns: Path to the created workspace directory and the name of the workspace
    """
    # Create a temporary directory that will hold the workspace
    temp_dir = tempfile.mkdtemp(prefix="jj-run-")
    # Generate a unique workspace name, same as the temp directory name
    workspace_name = os.path.basename(temp_dir)
    workspace_path = os.path.join(temp_dir, workspace_name)

    # Add the workspace, using the temporary directory as the destination
    run(
        ["jj", "workspace", "add", workspace_path],
        check=True,
        shell=False,
        text=True,
        capture_output=True,
    )
    return workspace_path, workspace_name


def process_changes(
    workspace_path: str, changes: list[Change], command: str, err_strategy: str
) -> list[Change]:
    """
    Process each change sequentially in an isolated workspace.

    :param workspace_path: Path to the workspace directory
    :param command: The command to execute on each change
    :param err_strategy: Strategy for handling errors
    :returns: List of newly created changes
    """
    new_changes = []
    exit_early = False
    for change_data in changes:
        change_id = change_data.change_id
        message = change_data.description.strip()
        print(
            f"Processing change {change_id}: {message or '(no description set)'}",
            end=" ",
        )
        run(
            ["jj", "new", change_id],
            shell=False,
            text=True,
            check=True,
            cwd=workspace_path,
            capture_output=True,
        )
        result = run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            cwd=workspace_path,
        )
        if result.stdout.strip():
            print(f"stdout: {result.stdout.strip()}", end=" ")
        if result.stderr.strip():
            print(f"stderr: {result.stderr.strip()}", end=" ")
        if result.returncode != 0:
            print(f"Command failed with return code {result.returncode}", end=" ")
        print()  # Add a newline after the command output
        exit_early = handle_errors(result, err_strategy, message)
        new_changes += get_change_list("@", workspace_path=workspace_path)
        if exit_early:
            break
    return new_changes


def handle_errors(
    result: subprocess.CompletedProcess, err_strategy: str, change: str
) -> bool:
    """
    Manage error strategy post-command based on exit statuses for changes.

    :param result: Subprocess command execution result
    :param err_strategy: Error handling strategy
    :param change: Change descriptor being processed
    :returns: Boolean indicating whether to exit early
    """
    if result.returncode != 0:
        error_msg = (
            f"Error while processing change [{change}]:\n"
            f"Returncode: {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )
        if err_strategy == "continue":
            print(error_msg)
        elif err_strategy == "stop":
            print(f"Stopped on change [with fail] {change}:\n{error_msg}")
            return True
        elif err_strategy == "fatal":
            print(f"Fatal error at change [{change}]:\n{error_msg}")
            raise SystemExit(result.returncode)
    return False


def parse_args() -> argparse.Namespace:
    """
    Parse arguments using argparse

    :returns: Parsed arguments
    """
    parser = argparse.ArgumentParser(description="JJ Command Script Automation Tool")
    parser.add_argument(
        "-r",
        "--revset",
        required=True,
        help="Revset to process",
    )
    parser.add_argument(
        "-c", "--command", required=True, help="Command to execute on commits"
    )
    parser.add_argument(
        "-e",
        "--err-strategy",
        choices=["continue", "stop", "fatal"],
        default="continue",
        help="Error handling strategy",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_jj_command(
        command=args.command, revset=args.revset, err_strategy=args.err_strategy
    )
