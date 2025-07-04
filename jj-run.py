#!/usr/bin/env python3

# Tests:
#
# - test1.py: Basic functionality smoke test.
# - test2.py: Tests what happens when the command fails.

from dataclasses import dataclass
from contextlib import contextmanager
import os
import subprocess
import argparse
from typing import Literal, Tuple
import tempfile
import sys


@dataclass
class Change:
    commit_id: str
    change_id: str
    description: str
    parents: list[str]


def run(
    *args, cwd: str, shell=False, text=True, capture_output=True, check=True, **kwargs
):
    """
    Wrapper for subprocess.run to handle errors by printing stderr and exiting.

    :param args: Positional arguments for subprocess.run
    :param cwd: Working directory (mandatory)
    :param shell: Whether to use the shell (default: False)
    :param text: Return output as text (default: True)
    :param capture_output: Capture stdout and stderr (default: True)
    :param check: Raise error on non-zero exit (default: True)
    :param kwargs: Other keyword arguments for subprocess.run
    """
    try:
        result = subprocess.run(
            *args,
            cwd=cwd,
            shell=shell,
            text=text,
            capture_output=capture_output,
            check=check,
            **kwargs,
        )
        return result
    except subprocess.CalledProcessError as e:
        print(f"{e.cmd=}, {e.stderr=}, {e.stdout=}", file=sys.stderr)
        raise


def print_command_result(result: subprocess.CompletedProcess) -> None:
    """
    Print the stdout and stderr of a subprocess result if present.
    """
    if result.stdout and result.stdout.strip():
        print(f"stdout: {result.stdout.strip()}", end=" ")
    if result.stderr and result.stderr.strip():
        print(f"stderr: {result.stderr.strip()}", end=" ")
    if result.returncode != 0:
        print(f"Command failed with return code {result.returncode}", end=" ")
    print()  # Add a newline after the command output


def format_error_msg(result: subprocess.CompletedProcess, change: str) -> str:
    """
    Format an error message for a failed subprocess command.
    """
    return (
        f"Error while processing change [{change[:12]}]:\n"
        f"Return code: {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


@contextmanager
def managed_workspace():
    """
    Context manager to create and clean up a temporary workspace.
    Yields (workspace_path, workspace_name).
    """
    workspace_path, workspace_name = create_workspace()
    try:
        yield workspace_path, workspace_name
    finally:
        forget_workspace(workspace_name)


def run_jj_command(
    command: str,
    revset: str,
    err_strategy: Literal["continue", "stop", "fatal"] = "continue",
) -> None:
    """
    Main entry point to run `jj run` with command handling and error strategies.

    :param command: User-provided command (e.g., "jj new && jj restore ...")
    :param err_strategy: Error handling strategy ("continue", "stop", "fatal")
    """
    current_operation = get_current_op_id()
    print(f"Current operation: {current_operation[:12]}")
    with managed_workspace() as (workspace_path, workspace_name):
        [workspace_change] = get_change_list(
            f"{workspace_name}@", workspace_path=workspace_path
        )
        changes = get_change_list(
            f"({revset}) ~ {workspace_change.change_id} ~ root()",
            workspace_path=workspace_path,
        )
        total_changes = len(changes)
        if not changes:
            print("No changes found to process.")
            abandon_changes([workspace_change.change_id])
            return
        new_changes: list[Change] = []
        try:
            new_changes, all_successful = process_changes(
                workspace_path, changes, command, err_strategy
            )
            modified_count = rewrite_parents(workspace_path, new_changes)
            run(["jj", "workspace", "update-stale"], cwd=".")
            run(["jj", "workspace", "update-stale"], cwd=workspace_path)
        finally:
            abandon_changes(
                [c.change_id for c in new_changes] + [workspace_change.change_id]
            )
        print(f"Rewrote {modified_count}/{total_changes} commits.")
        if not all_successful:
            print("Not all changes were processed successfully.")


def is_change_empty(workspace_path: str, change_id: str) -> bool:
    """
    Check if a change is empty (does not exist or has empty content).
    """
    result = run(
        ["jj", "log", "-T", "json(empty)", "-r", f"present({change_id})", "--no-graph"],
        cwd=workspace_path,
    )
    return result.stdout.strip() != "false"


def rewrite_parents(workspace_path: str, changes: list[Change]) -> int:
    """
    For each change, rewrite its parent's snapshot to that commit.

    :returns: Number of commits modified (empty changes are skipped)
    """
    modified_count = 0
    for change in changes:
        # If the change doesn't exist or is empty, we have to skip it b/c otherwise jj might fail when rewriting.
        if not is_change_empty(workspace_path, change.change_id):
            run(
                ["jj", "edit", change.parents[0]],
                cwd=workspace_path,
            )
            run(
                ["jj", "restore", "--from", change.change_id, "--restore-descendants"],
                cwd=workspace_path,
            )
            modified_count += 1

    return modified_count


def abandon_changes(changes: list[str]) -> None:
    """
    Abandon all changes in the provided list.

    :param changes: List of change IDs to abandon
    """

    # TODO: can batch but must make sure to not run into arg length limits
    for change in changes:
        # Only print first 12 chars of change id when abandoning
        run(
            ["jj", "abandon", f"present({change[:12]})", "--ignore-working-copy"],
            cwd=".",
        )


def forget_workspace(workspace_name: str) -> None:
    """
    Forget the workspace after processing commits.

    :param workspace_name: Name of the workspace to forget
    """
    run(["jj", "workspace", "forget", workspace_name], cwd=".")


def get_change_list(revset: str, workspace_path: str = ".") -> list[Change]:
    """
    Parse and return the list of changes in JSON format.

    :returns: Retrieved change history list as a list of dictionaries
    """
    change_process = run(
        ["jj", "log", "-r", revset, "--template", "json(self)", "--no-graph"],
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


def create_workspace() -> Tuple[str, str]:
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
    run(["jj", "workspace", "add", workspace_path], cwd=".")
    return workspace_path, workspace_name


def process_changes(
    workspace_path: str,
    changes: list[Change],
    command: str,
    err_strategy: Literal["continue", "stop", "fatal"],
) -> tuple[list[Change], bool]:
    """
    Process each change sequentially in an isolated workspace.

    :param workspace_path: Path to the workspace directory
    :param command: The command to execute on each change
    :param err_strategy: Strategy for handling errors
    :returns: Tuple of (List of newly created changes, all_successful: bool)
    """
    new_changes = []
    exit_early = False
    all_successful = True
    import subprocess as sp

    total_changes = len(changes)
    for idx, change_data in enumerate(changes, 1):
        change_id = change_data.change_id
        message = change_data.description.strip()
        print(
            f"Processing change {idx}/{total_changes} {change_id[:12]}: {message or '(no description set)'}"
        )
        run(["jj", "new", change_id], cwd=workspace_path)
        try:
            result = run(command, shell=True, cwd=workspace_path)
        except sp.CalledProcessError as e:
            # Create a CompletedProcess-like object for error handling
            result = sp.CompletedProcess(e.cmd, e.returncode, e.output, e.stderr)
        print_command_result(result)
        if result.returncode != 0:
            all_successful = False
        exit_early = handle_errors(result, err_strategy, change_id[:12])
        new_changes += get_change_list("@", workspace_path=workspace_path)
        if exit_early:
            if err_strategy == "stop":
                raise SystemExit(result.returncode)
            break
    return new_changes, all_successful


def handle_errors(
    result: subprocess.CompletedProcess,
    err_strategy: Literal["continue", "stop", "fatal"],
    change: str,
) -> bool:
    """
    Manage error strategy post-command based on exit statuses for changes.

    :param result: Subprocess command execution result
    :param err_strategy: Error handling strategy
    :param change: Change descriptor being processed
    :returns: Boolean indicating whether to exit early
    """
    if result.returncode != 0:
        error_msg = format_error_msg(result, change)
        match err_strategy:
            case "continue":
                print(error_msg)
            case "stop":
                print(f"Stopped on change [with fail] {change}:\n{error_msg}")
                return True
            case "fatal":
                print(f"Fatal error at change [{change}]:\n{error_msg}")
                raise SystemExit(result.returncode)
    return False


def parse_args() -> argparse.Namespace:
    """
    Parse arguments using argparse

    :returns: Parsed arguments
    """
    parser = argparse.ArgumentParser(description="Run commands across jj changes")
    parser.add_argument(
        "-r",
        "--revset",
        required=True,
        help="Revset to process",
    )
    parser.add_argument(
        "-e",
        "--err-strategy",
        choices=["continue", "stop", "fatal"],
        default="continue",
        help="Error handling strategy",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to execute on commits (positional, required)",
    )
    args = parser.parse_args()
    if not args.command:
        parser.error("the following arguments are required: command")
    return args


def get_current_op_id() -> str:
    """
    Returns the current operation id (full hash) as a string.
    """
    return run(
        ["jj", "op", "log", "-n1", "-Tid", "--no-graph", "--no-pager"], cwd="."
    ).stdout.strip()


if __name__ == "__main__":
    args = parse_args()
    before_op = None
    after_op = None
    try:
        # Get the operation id before running
        before_op = get_current_op_id()
        run_jj_command(
            command=" ".join(args.command),
            revset=args.revset,
            err_strategy=args.err_strategy,
        )
        # Get the operation id after running
        after_op = get_current_op_id()
    except SystemExit as _e:
        # Only propagate nonzero exit if err_strategy is 'fatal' or 'stop'
        match args.err_strategy:
            case "fatal" | "stop":
                raise
            case "continue":
                pass

    # Output for the user: how to compare before/after states
    if before_op and after_op:
        # TODO: this is useless b/c operation IDs are always gonna be different
        if before_op != after_op:
            print(
                "\nTo compare the changes between the 'before' and 'after' repo states, run:"
            )
            # TODO: not 100% sure about off by one errors
            print(
                f"  jj operation diff --from {before_op[:12]} --to {after_op[:12]} -p\n"
            )
        else:
            print("\nNo changes were made to the repository.\n")
    else:
        print("\nCouldn't get operation IDs before and after. Likely a bug in jj-run.")

    # actually run
    # print(
    #     run(
    #         ["jj", "op", "diff", "--from", before_op, "--to", after_op, "-p"],
    #         cwd=".",
    #     ).stdout
    # )
