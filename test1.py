#!/usr/bin/env python3

import os
import subprocess
import tempfile
import shutil
import sys
from pathlib import Path


def demo(command):
    """Prints, executes a command, streams its output, and returns the captured output."""
    print(command if isinstance(command, str) else " ".join(command))
    print("-----------------------------------------------------------------")

    if isinstance(command, list):
        process = subprocess.Popen(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    else:
        process = subprocess.Popen(
            command,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    stdout_capture = []
    stderr_capture = []

    if process.stdout:
        for line in iter(process.stdout.readline, ""):
            sys.stdout.write(line)
            stdout_capture.append(line)
        process.stdout.close()

    if process.stderr:
        for line in iter(process.stderr.readline, ""):
            sys.stderr.write(line)
            stderr_capture.append(line)
        process.stderr.close()

    return_code = process.wait()

    print("-----------------------------------------------------------------\n")

    stdout = "".join(stdout_capture)
    stderr = "".join(stderr_capture)

    if return_code != 0:
        raise subprocess.CalledProcessError(
            return_code, command, output=stdout, stderr=stderr
        )

    return subprocess.CompletedProcess(command, return_code, stdout, stderr)


def main():
    # Set PAGER to cat for non-interactive jj log
    os.environ["PAGER"] = "cat"

    # Determine the directory containing this script for relative paths
    script_dir = Path(__file__).parent

    # Create a new jj repository in a temporary directory
    repo_dir = tempfile.mkdtemp()
    original_dir = os.getcwd()
    os.chdir(repo_dir)

    try:
        print()
        demo("jj git init --colocate .")

        # Create several commits
        Path("one.txt").write_text("First commit\n")
        demo("jj commit -m 'one .txt file' one.txt")

        Path("multi1.txt").write_text("Line A\nLine B\n")
        Path("multi2.txt").write_text("Another file\n")
        demo("jj commit -m 'multiple .txt files' multi1.txt multi2.txt")

        Path("third.txt").write_text("Third commit\n")
        demo("jj commit -m 'another single .txt file' third.txt")

        # Show commit contents before merging and capture for verification
        result_before = demo("jj log -p -r '::'")

        # Golden snapshot: before merging
        expected_before = """
│  Added regular file third.txt:
│          1: Third commit
│  Added regular file multi1.txt:
│          1: Line A
│          2: Line B
│  Added regular file multi2.txt:
│          1: Another file
│  Added regular file one.txt:
│          1: First commit
""".strip()

        actual_before_lines = [
            line
            for line in result_before.stdout.splitlines()
            if not line.startswith(("@", "○", "◆")) and line.strip()
        ]
        actual_before = "\n".join(actual_before_lines)

        if actual_before.strip() != expected_before:
            print("test1.py: Before log snapshot mismatch", file=sys.stderr)
            # A simple diff-like output
            print("--- Expected ---", file=sys.stderr)
            print(expected_before, file=sys.stderr)
            print("--- Actual ---", file=sys.stderr)
            print(actual_before.strip(), file=sys.stderr)
            sys.exit(1)

        # Use jj-run to merge all .txt files
        jj_run_command = [
            "python3",
            str(script_dir / "jj-run.py"),
            "-r",
            "::",
            'for f in *.txt; do cat "$f" >> merged.txt; rm "$f"; done',
        ]
        demo(jj_run_command)

        # Show commit contents after merging and capture for verification
        result_after = demo("jj log -p -r '::'")

        # Golden snapshot: after merging
        expected_after = """
│  Modified regular file merged.txt:
│     1    1: Line A
│     2    2: Line B
│     3    3: Another file
│     4    4: First commit
│          5: Third commit
│  Modified regular file merged.txt:
│          1: Line A
│          2: Line B
│          3: Another file
│     1    4: First commit
│  Added regular file merged.txt:
│          1: First commit
""".strip()

        actual_after_lines = [
            line
            for line in result_after.stdout.splitlines()
            if not line.startswith(("@", "○", "◆")) and line.strip()
        ]
        actual_after = "\n".join(actual_after_lines)

        if actual_after.strip() != expected_after:
            print("test1.py: After log snapshot mismatch", file=sys.stderr)
            print("--- Expected ---", file=sys.stderr)
            print(expected_after, file=sys.stderr)
            print("--- Actual ---", file=sys.stderr)
            print(actual_after.strip(), file=sys.stderr)
            sys.exit(1)

        # Verify results
        merged_log = result_after.stdout
        if "merged.txt" not in merged_log:
            print(
                "Test failed: merged.txt not found in log after merge", file=sys.stderr
            )
            sys.exit(1)

        for f in ["one.txt", "multi1.txt", "multi2.txt", "third.txt"]:
            if f in merged_log:
                print(
                    f"Test failed: original file {f} still present in log after merge",
                    file=sys.stderr,
                )
                sys.exit(1)

        print("test1.py: SUCCESS")

    finally:
        # Clean up the temporary directory
        os.chdir(original_dir)
        shutil.rmtree(repo_dir)


if __name__ == "__main__":
    main()
