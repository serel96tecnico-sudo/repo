import subprocess
import sys
from pathlib import Path

from config import RUN_TIME, RUN_TIME_EVENING


TASK_NAME_MORNING = "SwingTradingAgent"
TASK_NAME_EVENING = "SwingTradingAgentEvening"


def create_windows_task(task_name: str, run_time: str, session: str):
    python_path = sys.executable
    script_path = Path(__file__).parent / "main.py"

    cmd = [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", f'"{python_path}" "{script_path}" --session {session}',
        "/sc", "weekly",
        "/d", "MON,TUE,WED,THU,FRI",
        "/st", run_time,
        "/f",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Task '{task_name}' created. Runs weekdays at {run_time} (--session {session}).")
    else:
        print(f"Failed to create task '{task_name}': {result.stderr}")
        print("Try running as Administrator.")


def create_all_tasks():
    create_windows_task(TASK_NAME_MORNING, RUN_TIME, "morning")
    create_windows_task(TASK_NAME_EVENING, RUN_TIME_EVENING, "evening")


def delete_windows_task(task_name: str):
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", task_name, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"Task '{task_name}' deleted.")
    else:
        print(f"Failed to delete task: {result.stderr}")


def delete_all_tasks():
    delete_windows_task(TASK_NAME_MORNING)
    delete_windows_task(TASK_NAME_EVENING)


def check_task_exists(task_name: str) -> bool:
    result = subprocess.run(
        ["schtasks", "/query", "/tn", task_name],
        capture_output=True, text=True,
    )
    return result.returncode == 0


if __name__ == "__main__":
    create_all_tasks()
