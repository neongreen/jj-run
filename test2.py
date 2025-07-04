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

    return subprocess.CompletedProcess(command, return_code, stdout, stderr)


def main():
    os.environ["PAGER"] = "cat"
    script_dir = Path(__file__).parent
    repo_dir = tempfile.mkdtemp()
    original_dir = os.getcwd()
    os.chdir(repo_dir)
    try:
        print()
        demo("jj git init --colocate .")
        Path("one.txt").write_text("First commit\n")
        demo("jj commit -m 'one .txt file' one.txt")
        Path("failme.txt").write_text("This will fail\n")
        demo("jj commit -m 'failme' failme.txt")
        Path("third.txt").write_text("Third commit\n")
        demo("jj commit -m 'another single .txt file' third.txt")
        # Show commit contents before running jj-run
        demo("jj log -p -r '::'")
        # Run jj-run.py with a command that fails if failme.txt exists
        jj_run_command = [
            "python3",
            str(script_dir / "jj-run.py"),
            "-r",
            "::",
            "-e",
            "continue",
            "test -f failme.txt && exit 1",
        ]
        result = demo(jj_run_command)
        # Should report error for failed command
        assert "Error while processing change" in result.stdout or result.stderr, (
            "Should report error for failed command"
        )
        # The command 'test -f failme.txt && exit 1' should have failed with exit code 1
        assert "Command failed with return code 1" in result.stdout, (
            "Should report command failed with return code 1"
        )
        # Should exit 0 with -e continue
        assert result.returncode == 0, "Should exit 0 with -e continue"
        # Now test -e stop (should exit nonzero)
        jj_run_command_stop = [
            "python3",
            str(script_dir / "jj-run.py"),
            "-r",
            "::",
            "-e",
            "stop",
            "test -f failme.txt && exit 1",
        ]
        result_stop = demo(jj_run_command_stop)
        assert result_stop.returncode != 0, (
            "Should exit nonzero with -e stop on failure"
        )
        assert "Command failed with return code 1" in result_stop.stdout, (
            "Should report command failed with return code 1"
        )
        print("test2.py: SUCCESS")
    finally:
        os.chdir(original_dir)
        shutil.rmtree(repo_dir)


if __name__ == "__main__":
    main()
