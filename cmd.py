import sys
import subprocess as sp
from typing import Any

# def execute(cmd):
#     if isinstance(cmd, str):
#         cmd = cmd.split(" ")

#     print(f"# {cmd} is ready for execution\n")
#     output = sp.run(cmd, capture_output=True, text=True)
    
#     # Split output into lines
#     lines = output.stdout.strip().splitlines()
    
#     # Extract only the site names before " ("
#     for line in lines:
#         name = line.split(" (")[0].strip()
#         print(name)

def execute_cmd(cmd: str) -> dict[str, Any] | None:
    """ executes command on the command line """
    if isinstance(cmd, str):
        cmd = cmd.split(" ")
    
    try :
        output = sp.run(cmd, capture_output=True, text=True)
        
        return {
            "cmd": cmd,
            "stdout": output.stdout.strip(),
            "stderr": output.stderr.strip(),
            "returncode": output.returncode,
        }

    except Exception:
        return None

if __name__ == "__main__":
    args = " ".join(sys.argv[1:])
    result = execute_cmd(args)
    if result is not None:
        print(result.get("stdout", "").splitlines())
